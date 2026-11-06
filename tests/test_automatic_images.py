"""Automatic cover policy and its no-duplicate import boundary, without network."""
from contextlib import closing
import io
import json
import sqlite3

from PIL import Image
import pytest
import requests

from src.deployment import automatic_images as automatic
from src.deployment import images
from src.extraction.db_manager import Database
from src.extraction.models import ExtractionResult


class Response:
    def __init__(self, body=None, *, status=200, binary=b"", mime="application/json"):
        self.body, self.status_code, self.binary = body, status, binary
        self.headers = {"Content-Type": mime}

    def json(self):
        return self.body

    def close(self):
        pass

    def iter_content(self, chunk_size):
        yield self.binary


def wp_photo(external_id=44, title="A laptop computer on a remote work desk", width=1600, height=900):
    return {"id": external_id, "link": f"https://wordpress.org/photos/photo/example{external_id}/",
        "_embedded": {"author": [{"name": "Fixture photographer"}], "wp:featuredmedia": [{
            "id": external_id + 100, "source_url": f"https://pd.w.org/2026/01/{external_id}.jpg",
            "caption": {"rendered": title}, "media_details": {"width": width, "height": height}}]}}


class Session:
    def __init__(self, *, catalogue=None, post_error=None, before_catalogue=None):
        self.calls = []
        self.catalogue = catalogue or (lambda provider, query: [wp_photo()])
        self.post_error = post_error
        self.before_catalogue = before_catalogue

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            if self.post_error:
                raise self.post_error
            return Response({"id": 9500, "mime_type": "image/jpeg",
                             "source_url": url.rstrip("/") + "/wp-content/uploads/automatic-cover.jpg"}, status=201)
        if url in images.CATALOGUES.values():
            if self.before_catalogue:
                callback, self.before_catalogue = self.before_catalogue, None
                callback()
            provider = next(k for k, v in images.CATALOGUES.items() if v == url)
            query = kwargs["params"].get("search", kwargs["params"].get("q"))
            result = self.catalogue(provider, query)
            if isinstance(result, BaseException):
                raise result
            return Response(result if provider == "wordpress_photos" else {"results": result})
        stream = io.BytesIO()
        Image.new("RGB", (1600, 900), "#cccccc").save(stream, format="JPEG")
        return Response(binary=stream.getvalue(), mime="image/jpeg")


class NoNetwork:
    def request(self, *args, **kwargs):
        pytest.fail("A replay must not make an external request")


def store_article(path, title):
    with Database(path) as db:
        db.store_article(ExtractionResult(url="https://movingtospain.com/dnv/",
            canonical_url="https://movingtospain.com/dnv/", title=title, original_text=title,
            blocks=[{"key": "b1", "position": 0, "source_order": 1, "kind": "paragraph",
                     "text": title, "section_path": [], "data": {}, "flags": []}]), raw_html="<p>fixture</p>")


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    path = tmp_path / "automatic.db"
    env = tmp_path / ".env"
    for prefix, _ in images.CONNECTIONS.values():
        monkeypatch.delenv(prefix + "_USERNAME", raising=False)
        monkeypatch.delenv(prefix + "_APPLICATION_PASSWORD", raising=False)
    env.write_text("\n".join(f'{prefix}_USERNAME=fixture\n{prefix}_APPLICATION_PASSWORD=fixture-password-only'
                              for prefix, _ in images.CONNECTIONS.values()), encoding="utf-8")
    store_article(path, "How to Avoid Spain Digital Nomad Visa Renewal Problems")
    return path, env


def test_automatic_topic_policy_prefers_remote_work_over_paperwork():
    assert automatic.automatic_queries("How to Avoid Spain Digital Nomad Visa Renewal Problems")[:2] == ["remote work", "laptop"]
    assert automatic.automatic_queries("Moving to Barcelona") == ["Barcelona"]
    assert automatic.automatic_queries("Unrelated commentary") == []


def test_selects_best_caption_and_imports_without_human_picker(corpus):
    path, env = corpus
    session = Session(catalogue=lambda *args: [wp_photo(60, "Office desk"), wp_photo(44)])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["status"] == "ready" and result["wp_media_id"] == 9500
    assert result["provider"] == "wordpress_photos" and result["query"] == "remote work"
    assert result["decision"]["candidate"]["external_id"] == "44"
    assert result["decision"]["human_image_selection"] is False
    assert result["decision"]["visual_model_used"] is False
    assert result["source_version_id"] == 1 and result["selection_mode"] == "automatic"
    assert [method for method, *_ in session.calls].count("POST") == 1
    assert all(call[2].get("params", {}).get("rest_route") == "/wp/v2/media"
               for call in session.calls if call[0] == "POST")
    repeated = automatic.auto_import_cover(path, 3, 1, env_path=env, session=NoNetwork())
    assert repeated == result


def test_tie_is_broken_by_external_identity_not_catalogue_order(corpus):
    path, env = corpus
    session = Session(catalogue=lambda *args: [wp_photo(90), wp_photo(12)])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["decision"]["candidate"]["external_id"] == "12"


def test_query_fallback_avoids_unrelated_remote_control_image(corpus):
    path, env = corpus
    session = Session(catalogue=lambda provider, query: [wp_photo(1, "A remote control")]
                      if query == "remote work" else [wp_photo(2, "Laptop on a table")])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["query"] == "laptop" and result["decision"]["candidate"]["external_id"] == "2"
    assert result["decision"]["searches"][0]["topic_candidates"] == 0


def test_openverse_is_only_used_after_primary_has_no_eligible_image(corpus):
    path, env = corpus
    row = {"id": "remote-work", "foreign_landing_url": "https://www.flickr.com/photos/fixture/50",
           "url": "https://live.staticflickr.com/1/fixture.jpg", "creator": "Fixture photographer",
           "license": "cc0", "license_url": images.CC0_URL, "title": "Remote work laptop desk",
           "width": 1600, "height": 900}
    session = Session(catalogue=lambda provider, query: [] if provider == "wordpress_photos" else [row])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["provider"] == "openverse" and result["status"] == "ready"
    assert len(result["decision"]["searches"]) == 5
    assert session.calls[4][2]["params"]["license"] == "cc0"


def test_no_result_is_persisted_without_upload_or_manual_substitution(corpus):
    path, env = corpus
    session = Session(catalogue=lambda *args: [])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["status"] == "needs_image" and result["candidate_id"] is None
    assert len(session.calls) == 8 and all(method == "GET" for method, *_ in session.calls)
    assert automatic.auto_import_cover(path, 3, 1, env_path=env, session=NoNetwork()) == result


def test_catalogue_failure_can_use_next_query_without_exposing_error(corpus):
    path, env = corpus
    session = Session(catalogue=lambda provider, query: requests.Timeout("withheld diagnostic")
                      if query == "remote work" else [wp_photo(44, "Laptop computer")])
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["status"] == "ready" and result["query"] == "laptop"
    assert "withheld diagnostic" not in json.dumps(result)


def test_unknown_import_never_retries_or_selects_another_candidate(corpus):
    path, env = corpus
    session = Session(post_error=requests.Timeout("after upload"))
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["status"] == "unknown"
    repeated = automatic.auto_import_cover(path, 3, 1, env_path=env, session=NoNetwork())
    assert repeated["candidate_id"] == result["candidate_id"] and repeated["status"] == "unknown"
    assert len([call for call in session.calls if call[0] == "POST"]) == 1


def test_interrupted_import_stays_reserved_and_cannot_repost(corpus):
    path, env = corpus
    session = Session(post_error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=NoNetwork())
    assert result["status"] == "importing" and result["import_id"]


def test_concurrent_call_observes_reserved_search_and_does_not_duplicate(corpus):
    path, env = corpus
    concurrent = []
    session = Session(before_catalogue=lambda: concurrent.append(
        automatic.auto_import_cover(path, 3, 1, env_path=env, session=NoNetwork())))
    result = automatic.auto_import_cover(path, 3, 1, env_path=env, session=session)
    assert result["status"] == "ready" and concurrent[0]["status"] == "searching"
    assert concurrent[0]["selection_id"] == result["selection_id"]


def test_same_image_between_canadian_locales_uses_existing_import(corpus):
    path, env = corpus
    first = automatic.auto_import_cover(path, 1, 1, env_path=env, session=Session())
    second_session = Session()
    second = automatic.auto_import_cover(path, 2, 1, env_path=env, session=second_session)
    assert first["wp_media_id"] == second["wp_media_id"]
    assert first["import_id"] == second["import_id"]
    assert all(method == "GET" for method, *_ in second_session.calls)


def test_source_update_retains_old_selection_and_creates_new_policy_record(corpus):
    path, env = corpus
    first = automatic.auto_import_cover(path, 3, 1, env_path=env, session=Session())
    store_article(path, "Avoid Digital Nomad Renewal Problems: updated")
    second = automatic.auto_import_cover(path, 3, 1, env_path=env, session=Session())
    assert first["selection_id"] != second["selection_id"]
    assert first["source_version_id"] != second["source_version_id"]
    assert automatic.get_auto_selection(path, first["selection_id"])["wp_media_id"] == first["wp_media_id"]


@pytest.mark.parametrize("target_id,article_id", [(True, 1), (3, False), (0, 1), (3, -1)])
def test_invalid_ids_fail_before_requests(corpus, target_id, article_id):
    path, env = corpus
    with pytest.raises(ValueError):
        automatic.auto_import_cover(path, target_id, article_id, env_path=env, session=NoNetwork())
