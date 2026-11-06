"""Public catalogue filtering and durable, once-only media upload boundaries."""
import io
import hashlib
import json
import random
import sqlite3

from PIL import Image
import pytest
import requests

from src.deployment import images
from src.deployment.images import get_candidate, get_import_record, import_candidate, search_candidates, suggest_queries


SECRET = "fixture-application-password-never-archive"


class Response:
    def __init__(self, body=None, *, status=200, binary=b"", mime="image/jpeg", headers=None):
        self.body = body
        self.status_code = status
        self.binary = binary
        self.headers = {"Content-Type": mime, **(headers or {})}
        self.closed = False

    def json(self):
        return self.body

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.binary), chunk_size):
            yield self.binary[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses, before_post=None):
        self.responses = list(responses)
        self.calls = []
        self.before_post = before_post

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST" and self.before_post:
            self.before_post()
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def wp_photo(**overrides):
    item = {"id": 192650, "link": "https://wordpress.org/photos/photo/example/",
        "title": {"rendered": "Photo detail"},
        "_embedded": {"author": [{"name": "Elena Zheng"}], "wp:featuredmedia": [{
            "id": 192651, "source_url": "https://pd.w.org/2026/04/example.jpg",
            "caption": {"rendered": "<p>Barcelona &amp; Park Güell panorama</p>"},
            "media_details": {"width": 1200, "height": 700}}]}}
    item.update(overrides)
    return item


def openverse_photo(**overrides):
    item = {"id": "public-cc0-photo", "foreign_landing_url": "https://www.flickr.com/photos/fixture/123",
            "url": "https://live.staticflickr.com/1/fixture.jpg", "creator": "Fixture Photographer",
            "license": "cc0", "license_url": images.CC0_URL, "license_version": "1.0",
            "title": "Barcelona skyline", "width": 1200, "height": 700}
    item.update(overrides)
    return item


def binary_image(size=(1200, 700), format="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", size, "#aaaaaa").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def saved(tmp_path, monkeypatch):
    for prefix, _ in images.CONNECTIONS.values():
        monkeypatch.delenv(prefix + "_USERNAME", raising=False)
        monkeypatch.delenv(prefix + "_APPLICATION_PASSWORD", raising=False)
    path = tmp_path / "images.db"
    env = tmp_path / ".env"
    env.write_text("\n".join(f'{prefix}_USERNAME="fixture-user"\n{prefix}_APPLICATION_PASSWORD="{SECRET}"'
                            for prefix, _ in images.CONNECTIONS.values()), encoding="utf-8")
    result = search_candidates(path, "Barcelona", session=Session(Response([wp_photo()])))
    return path, env, result["candidates"][0]["id"]


def uploaded(base="https://colombianosespana.com"):
    return Response({"id": 9001, "mime_type": "image/jpeg", "source_url": base + "/wp-content/uploads/photo.jpg",
                     "ignored_secret": SECRET}, status=201)


def test_search_reads_catalogue_and_uses_caption_not_generic_title(tmp_path):
    path = tmp_path / "articles.db"
    session = Session(Response([wp_photo()]), Response([wp_photo()]))
    result = search_candidates(path, "Barcelona", session=session)
    candidate = result["candidates"][0]
    assert candidate["title"] == "Barcelona & Park Güell panorama"
    assert candidate["external_id"] == "192650" and candidate["id"] != 192651
    assert candidate["metadata"]["external_media_id"] == 192651
    assert result["selected_candidate_id"] == candidate["id"]
    assert result["requires_visual_review"] is True
    assert get_candidate(path, candidate["id"])["license"] == "cc0"
    repeated = search_candidates(path, "Barcelona", session=session)
    assert repeated["candidates"][0]["id"] == candidate["id"]
    assert all(call[0] == "GET" and call[1] == images.CATALOGUES["wordpress_photos"] for call in session.calls)
    assert session.calls[0][2]["allow_redirects"] is False
    assert session.calls[0][2]["verify"] is True
    request = requests.Request("GET", "https://pd.w.org/a.jpg", headers={"Authorization": SECRET, "Cookie": SECRET}).prepare()
    assert "Authorization" not in session.calls[0][2]["auth"](request).headers
    assert "Cookie" not in request.headers


def test_openverse_rechecks_license_size_format_and_host(tmp_path):
    bad_rows = [openverse_photo(id="non-commercial", license="by-nc-nd"),
                openverse_photo(id="unverified", license_url="https://other.example/license"),
                openverse_photo(id="vector", url="https://upload.wikimedia.org/image.svg"),
                openverse_photo(id="unknown-width", width=None),
                openverse_photo(id="portrait", width=700, height=1200),
                openverse_photo(id="small", width=800, height=600),
                openverse_photo(id="bad-host", url="https://live.staticflickr.com.evil.test/x.jpg"),
                openverse_photo(id="userinfo", url="https://evil.test@pd.w.org/x.jpg"),
                openverse_photo(id="local", url="https://127.0.0.1/x.jpg")]
    session = Session(Response({"results": [*bad_rows, openverse_photo()]}))
    result = search_candidates(tmp_path / "images.db", "Barcelona", "openverse", session)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["external_id"] == "public-cc0-photo"
    assert session.calls[0][2]["params"]["license"] == "cc0"
    assert any("9 resultados" in warning for warning in result["warnings"])


def test_no_results_leaves_needs_image_not_fabricated_fallback(tmp_path):
    result = search_candidates(tmp_path / "images.db", "unfindable", session=Session(Response([])))
    assert result["status"] == "needs_image"
    assert result["selected_candidate_id"] is None and result["candidates"] == []


def test_read_helpers_never_create_database(tmp_path):
    path = tmp_path / "absent.db"
    assert get_candidate(path, 1) is None
    assert get_import_record(path, 3, 1) is None
    assert not path.exists()


def test_successful_upload_is_reserved_first_and_reuses_actual_target_media(saved):
    path, env, candidate_id = saved
    def assert_reserved():
        assert get_import_record(path, 3, candidate_id)["status"] == "importing"
        assert import_candidate(path, 3, candidate_id, env, session)["status"] == "importing"
    session = Session(Response(binary=binary_image()), uploaded(), before_post=assert_reserved)
    result = import_candidate(path, 3, candidate_id, env, session)
    assert result["status"] == "ready" and result["wp_media_id"] == 9001
    assert result["wp_media_id"] != get_candidate(path, candidate_id)["metadata"]["external_media_id"]
    assert result["provenance"]["license"] == "cc0"
    assert result["provenance"]["width"] == 1200
    post = session.calls[-1]
    assert post[0] == "POST" and post[1] == "https://colombianosespana.com/"
    assert post[2]["params"] == {"rest_route": "/wp/v2/media"}
    assert post[2]["allow_redirects"] is False and post[2]["verify"] is True
    assert post[2]["auth"] == ("fixture-user", SECRET)
    assert post[2]["files"]["file"][2] == "image/jpeg"
    assert post[2]["data"]["alt_text"] == "Barcelona & Park Güell panorama"
    assert "CC0" in post[2]["data"]["description"]
    env.unlink()
    assert import_candidate(path, 3, candidate_id, env, session) == result
    assert len(session.calls) == 2
    assert SECRET not in json.dumps(result) and SECRET.encode() not in path.read_bytes()


def test_canadian_connections_share_one_import(saved):
    path, env, candidate_id = saved
    session = Session(Response(binary=binary_image()), uploaded("https://canadiansinspain.com"))
    english = import_candidate(path, 1, candidate_id, env, session)
    french = import_candidate(path, 2, candidate_id, env, session)
    assert english == french and english["wp_media_id"] == 9001
    assert len(session.calls) == 2


@pytest.mark.parametrize("failure,expected", [
    (requests.Timeout(SECRET), "unknown"), (Response({"message": SECRET}, status=500), "unknown"),
    (Response({"message": SECRET}, status=401), "failed"), (Response({}, status=302), "unknown"),
    (Response({"id": 123, "source_url": "https://other.test/a.jpg", "mime_type": "image/jpeg"}, status=201), "unknown")])
def test_failed_or_ambiguous_uploads_are_durable_and_not_automatically_retried(saved, failure, expected):
    path, env, candidate_id = saved
    session = Session(Response(binary=binary_image()), failure)
    result = import_candidate(path, 3, candidate_id, env, session)
    assert result["status"] == expected
    assert SECRET not in json.dumps(result)
    assert import_candidate(path, 3, candidate_id, env, session) == result
    assert len([call for call in session.calls if call[0] == "POST"]) == 1


def test_explicit_retry_of_definite_failure_reuses_reservation_and_preserves_history(saved):
    path, env, candidate_id = saved
    failed_session = Session(Response(binary=binary_image()), Response({"message": SECRET}, status=401))
    failed = import_candidate(path, 3, candidate_id, env, failed_session)
    assert failed["status"] == "failed"
    assert failed["provenance"]["attempt"] == 1

    def assert_reserved():
        pending = get_import_record(path, 3, candidate_id)
        assert pending["id"] == failed["id"] and pending["status"] == "importing"
        assert pending["provenance"]["attempt"] == 2
        assert import_candidate(path, 3, candidate_id, env, retry, retry_failed=True)["status"] == "importing"

    retry = Session(Response(binary=binary_image()), uploaded(), before_post=assert_reserved)
    ready = import_candidate(path, 3, candidate_id, env, retry, retry_failed=True)
    assert ready["id"] == failed["id"] and ready["status"] == "ready"
    assert ready["created_at"] == failed["created_at"]
    assert ready["error"] == "" and ready["wp_media_id"] == 9001
    assert ready["provenance"]["previous_failures"] == [{
        "error": failed["error"], "updated_at": failed["updated_at"],
        "provenance_sha256": hashlib.sha256(images._json(failed["provenance"]).encode("utf-8")).hexdigest(),
    }]
    assert import_candidate(path, 3, candidate_id, env, retry, retry_failed=True) == ready
    assert len(retry.calls) == 2 and SECRET not in json.dumps(ready)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT count(*) FROM cover_imports').fetchone()[0] == 1


@pytest.mark.parametrize("failure", [requests.Timeout(SECRET), Response({}, status=500),
                                    Response({}, status=408), Response({}, status=409), Response({}, status=302)])
def test_explicit_retry_cannot_repeat_ambiguous_upload(saved, failure):
    path, env, candidate_id = saved
    session = Session(Response(binary=binary_image()), failure)
    record = import_candidate(path, 3, candidate_id, env, session)
    assert record["status"] == "unknown"
    assert import_candidate(path, 3, candidate_id, env, session, retry_failed=True) == record
    assert len(session.calls) == 2


@pytest.mark.parametrize("invalid", [None, "true", "false", 0, 1, [], {}])
def test_retry_confirmation_requires_a_boolean_before_any_network(saved, invalid):
    path, env, candidate_id = saved
    session = Session()
    with pytest.raises(ValueError, match="booleano"):
        import_candidate(path, 3, candidate_id, env, session, retry_failed=invalid)
    assert session.calls == [] and get_import_record(path, 3, candidate_id) is None


def test_each_explicit_failed_attempt_is_retained_in_order(saved):
    path, env, candidate_id = saved
    first = import_candidate(path, 3, candidate_id, env,
                             Session(Response(binary=binary_image()), Response({}, status=401)))
    second = import_candidate(path, 3, candidate_id, env,
                              Session(Response(binary=binary_image()), Response({}, status=403)), retry_failed=True)
    third = import_candidate(path, 3, candidate_id, env,
                             Session(Response(binary=binary_image()), uploaded()), retry_failed=True)
    assert third["provenance"]["attempt"] == 3
    assert [failure["error"] for failure in third["provenance"]["previous_failures"]] == [first["error"], second["error"]]
    assert first["id"] == second["id"] == third["id"]


def test_concurrent_retry_cannot_retry_a_newer_failure_under_an_old_confirmation(saved, monkeypatch):
    path, env, candidate_id = saved
    monkeypatch.setattr(images, "_now", lambda: "2026-11-05T12:00:00.000000+00:00")
    original = import_candidate(path, 3, candidate_id, env,
                                Session(Response(binary=binary_image()), Response({}, status=401)))
    original_download = images._download_image
    outer = Session(Response(binary=binary_image()))
    inner = Session(Response(binary=binary_image()), Response({}, status=403))

    def download(client, candidate):
        if client is outer:
            # A second request finishes while the first is downloading. Its new
            # failure must not consume the first request's earlier confirmation.
            newer = import_candidate(path, 3, candidate_id, env, inner, retry_failed=True)
            assert newer["status"] == "failed" and newer["provenance"]["attempt"] > original["provenance"]["attempt"]
        return original_download(client, candidate)

    monkeypatch.setattr(images, "_download_image", download)
    result = import_candidate(path, 3, candidate_id, env, outer, retry_failed=True)
    assert result["status"] == "failed" and result["provenance"]["attempt"] == 2
    assert all(call[0] == "GET" for call in outer.calls)
    assert len([call for call in inner.calls if call[0] == "POST"]) == 1


@pytest.mark.parametrize("response", [
    Response(binary=b"<svg/>", mime="image/svg+xml"), Response(binary=b"not a picture"),
    Response(binary=binary_image(), mime="image/png"), Response(binary=binary_image((700, 1200))),
    Response(binary=binary_image(), headers={"Content-Length": str(images.MAX_IMAGE_BYTES + 1)}),
    Response(binary=binary_image(), status=302),
])
def test_invalid_downloads_never_reserve_or_post(saved, response):
    path, env, candidate_id = saved
    session = Session(response)
    with pytest.raises(ValueError):
        import_candidate(path, 3, candidate_id, env, session)
    assert all(call[0] == "GET" for call in session.calls)
    assert get_import_record(path, 3, candidate_id) is None


def test_stream_size_is_enforced_even_without_content_length(saved, monkeypatch):
    path, env, candidate_id = saved
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 100)
    session = Session(Response(binary=b"x" * 101))
    with pytest.raises(ValueError, match="10 MB"):
        import_candidate(path, 3, candidate_id, env, session)
    assert len(session.calls) == 1


def test_decoded_pixel_limit_is_checked_before_decoding_or_post(saved, monkeypatch):
    path, env, candidate_id = saved
    # Catalogue dimensions pass; a different decoded size must be checked again.
    monkeypatch.setattr(images, "MAX_IMAGE_PIXELS", 900_000)
    session = Session(Response(binary=binary_image((1500, 900))))
    with pytest.raises(ValueError, match="30 megapíxeles"):
        import_candidate(path, 3, candidate_id, env, session)
    assert len(session.calls) == 1
    assert get_import_record(path, 3, candidate_id) is None


def test_interrupted_upload_keeps_reservation_and_cannot_repeat(saved):
    path, env, candidate_id = saved
    session = Session(Response(binary=binary_image()), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        import_candidate(path, 3, candidate_id, env, session)
    assert get_import_record(path, 3, candidate_id)["status"] == "importing"
    assert import_candidate(path, 3, candidate_id, env, session)["status"] == "importing"
    assert len(session.calls) == 2


def test_changed_candidate_and_target_cannot_redirect_network(saved):
    path, env, candidate_id = saved
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE cover_candidates SET image_url='https://evil.test/a.jpg' WHERE id=?", (candidate_id,))
    session = Session()
    with pytest.raises(ValueError):
        import_candidate(path, 3, candidate_id, env, session)
    assert session.calls == []


def test_query_hints_are_grounded_not_invented_people_topics():
    assert suggest_queries("Moving to Barcelona: visas and schools")[:1] == ["Barcelona"]
    assert "paperwork" in suggest_queries("Visas for Spain")
    assert suggest_queries("Asylum experiences") == []
    assert suggest_queries("Financial support", ["Tax"]) == ["paperwork"]


def test_oversized_jpeg_is_resized_before_upload_with_original_and_uploaded_provenance(saved):
    path, env, candidate_id = saved
    size = (2400, 1350)
    picture = Image.frombytes("RGB", size, random.Random(17).randbytes(size[0] * size[1] * 3))
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG", quality=95)
    picture.close()
    original = buffer.getvalue()
    assert images.MAX_UPLOAD_BYTES < len(original) < images.MAX_IMAGE_BYTES
    session = Session(Response(binary=original), uploaded())
    result = import_candidate(path, 3, candidate_id, env, session)
    assert result["status"] == "ready"
    sent = session.calls[-1][2]["files"]["file"]
    assert sent[0].endswith(".jpg") and sent[2] == "image/jpeg"
    assert len(sent[1]) <= images.MAX_UPLOAD_BYTES
    with Image.open(io.BytesIO(sent[1])) as photo:
        assert photo.size == (2048, 1152) and photo.format == "JPEG"
        assert abs(photo.width / photo.height - size[0] / size[1]) < 0.002
    provenance = result["provenance"]
    assert provenance["original_file"] == {"sha256": hashlib.sha256(original).hexdigest(),
        "bytes": len(original), "width": size[0], "height": size[1], "mime_type": "image/jpeg"}
    assert provenance["width"] == 2048 and provenance["height"] == 1152
    assert provenance["bytes"] == len(sent[1])
    assert provenance["sha256"] == hashlib.sha256(sent[1]).hexdigest()
    assert provenance["sha256"] != provenance["original_file"]["sha256"]
    assert provenance["processing"]["applied"] is True
    assert provenance["processing"]["quality"] in (85, 75)
    assert provenance["processing"]["cropped"] is False
    assert provenance["processing"]["generated"] is False


def test_png_optimization_preserves_format_and_transparency():
    picture = Image.new("RGBA", (1200, 700), (25, 50, 75, 64))
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG", compress_level=0)
    picture.close()
    original = buffer.getvalue()
    assert len(original) > images.MAX_UPLOAD_BYTES
    uploaded_bytes, width, height, source, processing = images._prepare_upload(
        original, "image/png", "png", 1200, 700)
    assert len(uploaded_bytes) <= images.MAX_UPLOAD_BYTES
    assert source["bytes"] == len(original) and processing["format"] == "PNG"
    assert processing["transparency_preserved"] is True
    with Image.open(io.BytesIO(uploaded_bytes)) as photo:
        assert photo.format == "PNG" and photo.mode == "RGBA"
        assert photo.getchannel("A").getextrema() == (64, 64)
        assert photo.size == (width, height) == (1200, 700)


def test_small_file_is_uploaded_byte_for_byte():
    original = binary_image()
    result, width, height, source, processing = images._prepare_upload(original, "image/jpeg", "jpg", 1200, 700)
    assert result == original and (width, height) == (1200, 700)
    assert source["sha256"] == hashlib.sha256(original).hexdigest()
    assert processing["applied"] is False


def test_upload_limit_never_forces_image_below_minimum_or_reserves_a_post(saved, monkeypatch):
    path, env, candidate_id = saved
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 100)
    session = Session(Response(binary=binary_image((960, 540))))
    with pytest.raises(ValueError, match="960 × 540"):
        import_candidate(path, 3, candidate_id, env, session)
    assert all(call[0] == "GET" for call in session.calls)
    assert get_import_record(path, 3, candidate_id) is None
