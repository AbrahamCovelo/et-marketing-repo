"""A saved adaptation can create at most one WordPress draft, never publish."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from src.deployment.repository import DeploymentRepository
from src.deployment.trial import get_deployment_trial, send_trial_draft
from src.deployment.categories import refresh_category_catalog, save_category_policy
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult
from src.generation.presets import load_editorial_prompts
from src.generation.repository import GenerationRepository
from src.generation.trial import _SCHEMA as GENERATION_SCHEMA


SECRET = "test-application-password-not-for-storage"


@pytest.fixture
def saved_trial(tmp_path, monkeypatch):
    for name in ("WP_COLOMBIANS_ES_USERNAME", "WP_COLOMBIANS_ES_APPLICATION_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "articles.db"
    env = tmp_path / ".env"
    env.write_text(f'WP_COLOMBIANS_ES_USERNAME="fixture-user"\nWP_COLOMBIANS_ES_APPLICATION_PASSWORD="{SECRET}"\n', encoding="utf-8")
    with Database(path) as db:
        db.store_article(ExtractionResult(url="https://movingtospain.com/barcelona/",
            canonical_url="https://movingtospain.com/barcelona/", title="Barcelona", original_text="Barcelona",
            blocks=[{"key":"p1", "position":0, "source_order":1, "kind":"paragraph", "text":"Barcelona",
                     "section_path":[], "data":{}, "flags":[]}]), raw_html="<p>Barcelona</p>")
    load_editorial_prompts(path)
    context = GenerationRepository(path).configuration_context(3)
    with sqlite3.connect(path) as connection:
        connection.execute(GENERATION_SCHEMA)
        connection.execute("""INSERT INTO generation_trials
            (id,run_key,article_id,source_version_id,configuration_id,configuration_version_id,prompt_version_id,
             destination_id,model,status,request_json,snapshot_json,html,created_at)
            VALUES (1,'fixture',1,1,3,?,?,3,'gpt-test','completed','{}','{}',?,'2026-11-04T00:00:00Z')""",
            (context["configuration"]["current_version_id"],context["prompt"]["version_id"],"<h2>Barcelona</h2><p>Una guía.</p>"))
    DeploymentRepository(path, env)
    catalog = refresh_category_catalog(path, 3, env_path=env, session=Session())
    save_category_policy(path, 3, {"allowed_category_ids": [19, 23], "excluded_default_category_id": 77,
                                  "catalog_revision": catalog["catalog_revision"]})
    return path, env


class Session:
    def __init__(self, *, status=201, post_body=None, exception=None, before_post=None,
                 user=None, media=None, categories=None, get_status=200):
        self.calls = []
        self.status = status
        self.get_status = get_status
        self.post_body = post_body if post_body is not None else {
            "id": 1510, "status": "draft", "featured_media": 1369, "categories": [19, 23],
            "link": "https://colombianosespana.com/?p=1510", "ignored_secret": SECRET}
        self.exception = exception
        self.before_post = before_post
        self.user = user if user is not None else {"id":5, "capabilities":{"edit_posts":True}}
        self.media = media if media is not None else {"id":1369,"mime_type":"image/webp", "source_url":"https://colombianosespana.com/wp-content/uploads/1.webp"}
        self.categories = categories if categories is not None else [{"id":19},{"id":23}]

    def request(self, method, url, **kwargs):
        self.calls.append((method,url,kwargs))
        if method == "POST":
            if self.before_post:
                self.before_post()
            if self.exception:
                raise self.exception
            return SimpleNamespace(status_code=self.status, json=lambda:self.post_body)
        route = kwargs["params"]["rest_route"]
        if route.endswith("categories"):
            body = [{"id": item["id"], "name": f"Category {item['id']}", "slug": f"cat-{item['id']}",
                     "description": "", "parent": 0} for item in self.categories]
            body.append({"id": 77, "name": "Default", "slug": "default", "description": "", "parent": 0})
        elif route.endswith("settings"):
            body = {"default_category": 77}
        else:
            body = self.user if route.endswith("users/me") else self.media
        return SimpleNamespace(status_code=self.get_status, json=lambda:body, headers={"X-WP-TotalPages": "1"})


def send(saved_trial, session, **kwargs):
    path,env = saved_trial
    return send_trial_draft(path,1,title="Mudarse a Barcelona",featured_media=1369,category_ids=[19,23],
                            env_path=env,session=session,**kwargs)


def test_only_one_draft_is_sent_and_result_has_no_credentials(saved_trial):
    path, env = saved_trial
    session = Session(before_post=lambda:assert_reserved(path))
    result = send(saved_trial,session)
    assert result["status"] == "draft"
    assert result["result"]["post_id"] == 1510
    assert result["result"]["edit_url"] == "https://colombianosespana.com/wp-admin/post.php?post=1510&action=edit"
    post = session.calls[-1]
    assert post[0] == "POST" and post[1] == "https://colombianosespana.com/"
    payload = post[2]["json"]
    assert payload["status"] == "draft" and payload["featured_media"] == 1369
    assert payload["categories"] == [19,23] and payload["slug"] == "et-trial-1-article-1"
    assert post[2]["allow_redirects"] is False
    assert post[2]["auth"] == ("fixture-user",SECRET)
    assert post[2]["headers"]["User-Agent"] == "ET-Marketing/1.0"
    assert all("_et_check" in call[2]["params"] for call in session.calls if call[0] == "GET")
    assert SECRET not in json.dumps(result) and SECRET.encode() not in path.read_bytes()
    assert "ignored_secret" not in result["result"]
    env.unlink()
    assert send(saved_trial,session) == result
    assert len(session.calls) == 5


def assert_reserved(path):
    assert get_deployment_trial(path,1)["status"] == "sending"


def test_timeout_is_durable_and_cannot_duplicate(saved_trial):
    session = Session(exception=requests.Timeout(SECRET))
    result = send(saved_trial,session)
    assert result["status"] == "unknown" and SECRET not in result["error"]
    assert send(saved_trial,session) == result
    assert len([c for c in session.calls if c[0] == "POST"]) == 1


def test_interruption_preserves_reservation(saved_trial):
    session = Session(exception=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        send(saved_trial,session)
    assert send(saved_trial,session)["status"] == "sending"
    assert len([c for c in session.calls if c[0] == "POST"]) == 1


@pytest.mark.parametrize("status,expected",[(302,"failed"),(401,"failed"),(500,"unknown")])
def test_remote_errors_never_archive_raw_response_or_retry(saved_trial,status,expected):
    session = Session(status=status,post_body={"message":SECRET})
    result = send(saved_trial,session)
    assert result["status"] == expected and result["result"] == {}
    assert SECRET not in json.dumps(result)
    assert send(saved_trial,session) == result
    assert len([c for c in session.calls if c[0] == "POST"]) == 1


@pytest.mark.parametrize("options",[
    {"get_status":401}, {"user":{"id":5,"capabilities":{"edit_posts":False}}},
    {"media":{"id":1369,"mime_type":"image/webp","source_url":"https://other.test/a.webp"}},
    {"categories":[{"id":19}]},
])
def test_preflight_failure_sends_nothing_and_does_not_reserve(saved_trial,options):
    session = Session(**options)
    with pytest.raises(ValueError):
        send(saved_trial,session)
    assert not any(c[0] == "POST" for c in session.calls)
    assert get_deployment_trial(saved_trial[0],1) is None


def test_missing_cta_requires_explicit_draft_exception_before_network(saved_trial):
    path,_ = saved_trial
    body = "<p>Lea [CTA_INMIGRACION_ES].</p>"
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE generation_trials SET html=? WHERE id=1",(body,))
    session=Session()
    with pytest.raises(ValueError,match="CTA"):
        send(saved_trial,session)
    assert session.calls == []
    result=send(saved_trial,session,allow_pending_ctas=True)
    assert result["payload"]["content"] == body
    assert any("marcadores CTA" in w for w in result["warnings"])
    assert result["payload"]["status"] == "draft"


@pytest.mark.parametrize("body",["<p style='color:red'>Texto</p>","<p>[CTA_TAXES_EN]</p>"])
def test_pending_cta_exception_never_bypasses_html_or_locale(saved_trial,body):
    path,_=saved_trial
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE generation_trials SET html=? WHERE id=1",(body,))
    session=Session()
    with pytest.raises(ValueError):
        send(saved_trial,session,allow_pending_ctas=True)
    assert session.calls == []


@pytest.mark.parametrize("field,value",[("site_url","https://other.test"),("locale","en-CA"),("enabled",0)])
def test_destination_changes_cannot_redirect_credentials(saved_trial,field,value):
    path,_=saved_trial
    with sqlite3.connect(path) as connection:
        connection.execute(f"UPDATE generation_destinations SET {field}=? WHERE id=3",(value,))
    session=Session()
    with pytest.raises(ValueError,match="destino"):
        send(saved_trial,session)
    assert session.calls == []


def test_unexpected_remote_status_is_recorded_for_manual_reconciliation(saved_trial):
    session=Session(post_body={"id":1510,"status":"publish","featured_media":1369,"link":"https://other.test/"})
    result=send(saved_trial,session)
    assert result["status"] == "unknown"
    assert result["result"]["post_status"] == "publish"
    assert "link" not in result["result"]
    assert session.calls[-1][2]["json"]["status"] == "draft"
    send(saved_trial,session)
    assert len(session.calls) == 5


def test_completed_trial_required_before_network(saved_trial):
    with sqlite3.connect(saved_trial[0]) as connection:
        connection.execute("UPDATE generation_trials SET status='incomplete' WHERE id=1")
    session=Session()
    with pytest.raises(ValueError,match="completada"):
        send(saved_trial,session)
    assert session.calls == []
