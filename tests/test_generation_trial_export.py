"""Offline exports retain originals but never execute invalid generated HTML."""
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest
import requests

from scripts import run_generation_trial as cli


def trial(html="<p>Barcelona tiene <strong>transporte público</strong>.</p><p>[CTA_INMIGRACION_ES]</p>"):
    return {
        "id": 7, "run_key": "example-trial", "article_id": 1, "source_version_id": 429,
        "configuration_id": 3, "configuration_version_id": 9, "prompt_version_id": 9,
        "destination_id": 3, "model": "gpt-5-nano-2025-08-07", "response_model": "gpt-5-nano-2025-08-07",
        "status": "completed", "usage": {"input_tokens": 100, "output_tokens": 50},
        "created_at": "2026-11-04T00:00:00Z", "completed_at": "2026-11-04T00:00:10Z",
        "html": html, "response": {"service_tier": "flex", "private": "PRIVATE FULL RESPONSE"},
        "request": {"service_tier": "flex", "input": "PRIVATE PROMPT"},
        "snapshot": {"private": "PRIVATE SNAPSHOT"},
    }


def test_valid_export_separates_title_and_image_from_body(tmp_path):
    record = trial()
    result = cli.export_trial(record, title="Mudarse a Barcelona", image_url="https://example.com/1.webp",
                              featured_media=1369, output_dir=tmp_path)
    artifacts = {key: Path(path) for key, path in result["artifacts"].items()}
    assert artifacts["body"].read_text(encoding="utf-8") == record["html"]
    page = BeautifulSoup(artifacts["preview"].read_text(encoding="utf-8"), "html.parser")
    assert page.h1.get_text() == "Mudarse a Barcelona"
    assert page.img["src"] == "https://example.com/1.webp"
    assert page.article.img is None
    assert page.article.strong.get_text() == "transporte público"
    assert "Content-Security-Policy" in str(page.head)
    metadata_text = artifacts["metadata"].read_text(encoding="utf-8")
    assert "PRIVATE" not in metadata_text
    metadata = json.loads(metadata_text)
    assert metadata["featured_media"] == 1369
    assert metadata["excerpt"] == "Barcelona tiene transporte público."
    assert metadata["editorial_review"] == "pending"
    assert metadata["cta_pending"] == ["CTA_INMIGRACION_ES"]
    assert metadata["semantic_validation"]["valid"]
    assert metadata["service_tier"] == metadata["requested_service_tier"] == "flex"


def test_unsafe_html_is_preserved_raw_but_only_displayed_as_escaped_text(tmp_path):
    body = '<p onclick="alert(1)">Texto.</p><script>alert("unsafe")</script>'
    result = cli.export_trial(trial(body), title='<script>alert("title")</script>',
                              image_url='https://example.com/photo.webp?x="onerror="alert(1)', output_dir=tmp_path)
    paths = result["artifacts"]
    assert Path(paths["body"]).read_text(encoding="utf-8") == body
    page = BeautifulSoup(Path(paths["preview"]).read_text(encoding="utf-8"), "html.parser")
    assert page.find("script") is None
    assert page.find(attrs={"onclick": True}) is None
    assert "onerror" not in page.img.attrs
    assert page.article.pre.get_text() == body
    assert page.h1.get_text() == '<script>alert("title")</script>'
    assert not result["semantic_valid"]


def test_export_mode_never_calls_generation_or_network(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Exportar intentó generar o acceder a la red.")
    monkeypatch.setattr(cli, "run_trial", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    calls = []
    monkeypatch.setattr(cli, "get_trial", lambda db, identifier: calls.append((db, identifier)) or trial())
    code = cli.main(["--db", "read-only.db", "--export-trial", "7", "--title", "Barcelona",
                     "--output-dir", str(tmp_path)])
    assert code == 0 and calls == [("read-only.db", 7)]
    assert json.loads(capsys.readouterr().out)["trial_id"] == 7


def test_paid_mode_passes_exact_single_trial_and_flex_limits(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "run_trial", lambda *args, **kwargs: calls.append((args, kwargs)) or trial())
    assert cli.main(["--article-id", "1", "--configuration-id", "3", "--model", "gpt-5-nano-2025-08-07",
                     "--run-key", "example-trial", "--title", "Barcelona", "--output-dir", str(tmp_path)]) == 0
    args, kwargs = calls[0]
    assert args == ("data/source_articles.db", 1, 3, "gpt-5-nano-2025-08-07", "example-trial")
    assert kwargs == {"max_output_tokens": 8000, "reasoning_effort": "minimal", "service_tier": "flex"}
    assert len(calls) == 1


@pytest.mark.parametrize("image", ["http://example.com/a.png", "javascript:alert(1)", "https://user:secret@example.com/a.png"])
def test_invalid_image_prevents_paid_request(image, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_trial", lambda *a, **k: pytest.fail("Paid call before validation"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--article-id", "1", "--configuration-id", "3", "--model", "gpt-5-nano",
                  "--run-key", "test", "--title", "Barcelona", "--image-url", image, "--output-dir", str(tmp_path)])
    assert error.value.code == 2 and list(tmp_path.iterdir()) == []


def test_paid_mode_requires_all_identifiers_and_export_cannot_take_generation_flags(tmp_path):
    for args in (["--article-id", "1"], ["--export-trial", "7", "--model", "gpt-5-nano"]):
        with pytest.raises(SystemExit) as error:
            cli.main([*args, "--title", "Barcelona", "--output-dir", str(tmp_path)])
        assert error.value.code == 2
