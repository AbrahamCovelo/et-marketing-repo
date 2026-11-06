"""External CC0 cover search and explicitly requested, journaled media imports.

Search only reads public catalogues. Importing uploads a file to the fixed target
site; it never edits or publishes posts. Ambiguous imports cannot be retried.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlsplit
import warnings as python_warnings

from bs4 import BeautifulSoup
import requests

from .repository import DeploymentRepository, _id
from .settings import CONNECTIONS, load_credentials


CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
CATALOGUES = {
    "wordpress_photos": "https://wordpress.org/photos/wp-json/wp/v2/photos",
    "openverse": "https://api.openverse.org/v1/images/",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000
# Leave room for multipart fields below common 2 MB request limits. Downloads
# may be larger; this limit applies to the file actually sent to WordPress.
MAX_UPLOAD_BYTES = 1_800_000
HEADERS = {"User-Agent": "ET-Marketing/1.0", "Accept": "application/json"}
_SCHEMAS = (
    """CREATE TABLE IF NOT EXISTS cover_candidates (
        id INTEGER PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL,
        source_url TEXT NOT NULL, image_url TEXT NOT NULL, creator TEXT NOT NULL,
        license TEXT NOT NULL CHECK(license='cc0'), license_url TEXT NOT NULL,
        title TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
        metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(provider,external_id))""",
    """CREATE TABLE IF NOT EXISTS cover_imports (
        id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL REFERENCES deployment_targets(id),
        candidate_id INTEGER NOT NULL REFERENCES cover_candidates(id),
        site_base_url TEXT NOT NULL, provider TEXT NOT NULL, external_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('importing','ready','failed','unknown')),
        wp_media_id INTEGER, wp_media_url TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(site_base_url,provider,external_id))""",
)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _initialize(db_path):
    DeploymentRepository(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        for schema in _SCHEMAS:
            connection.execute(schema)


def _plain(value, limit=600):
    if not isinstance(value, str):
        return ""
    text = BeautifulSoup(value[:10000], "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())[:limit]


def _url(value):
    if (not isinstance(value, str) or len(value) > 2000 or "\\" in value
            or any(c.isspace() or ord(c) < 32 for c in value)):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return None
        return parsed
    except ValueError:
        return None


def _image_url(value, provider):
    parsed = _url(value)
    if parsed is None or parsed.query or parsed.path.lower().endswith(".svg"):
        return False
    host = parsed.hostname
    allowed = host == "pd.w.org" if provider == "wordpress_photos" else (
        host == "pd.w.org" or host == "upload.wikimedia.org" or host.endswith(".staticflickr.com"))
    return allowed and parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))


def _cc0_url(value):
    parsed = _url(value)
    return bool(parsed and parsed.hostname == "creativecommons.org" and not parsed.query
                and re.fullmatch(r"/publicdomain/zero/1\.0(?:/deed\.[a-zA-Z_-]+)?/?", parsed.path))


def _dimensions(width, height):
    return (type(width) is int and type(height) is int and width >= 960 and height >= 540
            and width > height and width * height <= MAX_IMAGE_PIXELS)


def _candidate_valid(candidate):
    provider = candidate.get("provider")
    origin = _url(candidate.get("source_url"))
    external_id = candidate.get("external_id")
    valid_id = isinstance(external_id, str) and bool(re.fullmatch(
        r"[0-9]{1,30}" if provider == "wordpress_photos" else r"[a-zA-Z0-9-]{1,100}", external_id))
    return (provider in CATALOGUES and candidate.get("license") == "cc0"
            and _cc0_url(candidate.get("license_url")) and origin is not None
            and (provider != "wordpress_photos" or
                 origin.hostname == "wordpress.org" and origin.path.startswith("/photos/photo/"))
            and _image_url(candidate.get("image_url"), provider)
            and _dimensions(candidate.get("width"), candidate.get("height"))
            and valid_id and isinstance(candidate.get("title"), str) and bool(candidate["title"]))


def _record(row):
    result = dict(row)
    for field in ("metadata", "provenance"):
        if field + "_json" in result:
            result[field] = json.loads(result.pop(field + "_json"))
    return result


def _read_one(db_path, table, sql, params):
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return None
        row = connection.execute(sql, params).fetchone()
        return _record(row) if row else None


def get_candidate(db_path, candidate_id):
    """Read a saved local candidate, without creating or changing the database."""
    _id(candidate_id)
    return _read_one(db_path, "cover_candidates", "SELECT * FROM cover_candidates WHERE id=?", (candidate_id,))


def get_import_record(db_path, target_id, candidate_id):
    """Find an import by actual site, sharing assets between EN/FR connections."""
    _id(target_id)
    _id(candidate_id)
    target = _read_one(db_path, "deployment_targets", "SELECT connection_key FROM deployment_targets WHERE id=?", (target_id,))
    candidate = get_candidate(db_path, candidate_id)
    if not target or not candidate or target["connection_key"] not in CONNECTIONS:
        return None
    base = CONNECTIONS[target["connection_key"]][1]
    return _read_one(db_path, "cover_imports", """SELECT * FROM cover_imports
        WHERE site_base_url=? AND provider=? AND external_id=?""", (base, candidate["provider"], candidate["external_id"]))


class _NoAuth(requests.auth.AuthBase):
    """Suppress Session credentials, cookies and netrc for public catalogue GETs."""
    def __call__(self, request):
        for key in ("Authorization", "Cookie", "Proxy-Authorization"):
            request.headers.pop(key, None)
        return request


def _get(client, url, **kwargs):
    return client.request("GET", url, headers=HEADERS, auth=_NoAuth(), timeout=(10, 30),
                          verify=True, allow_redirects=False, **kwargs)


def _tokens(text):
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]{3,}", normalized))


def _score(candidate, query):
    tokens = _tokens(query)
    overlap = len(tokens & _tokens(candidate["title"])) / max(1, len(tokens))
    ratio_fit = max(0, 1 - abs(candidate["width"] / candidate["height"] - 16 / 9))
    return round(overlap * 90 + ratio_fit * 10, 2)


def _parse_candidates(provider, payload):
    rows = payload if provider == "wordpress_photos" else payload.get("results", []) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("El catálogo devolvió una respuesta ilegible.")
    candidates = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        if provider == "wordpress_photos":
            embedded = row.get("_embedded", {})
            if not isinstance(embedded, dict):
                continue
            media_list = embedded.get("wp:featuredmedia", [])
            if not isinstance(media_list, list) or not media_list or not isinstance(media_list[0], dict):
                continue
            media = media_list[0]
            dimensions = media.get("media_details", {})
            if not isinstance(dimensions, dict):
                continue
            caption = media.get("caption", {})
            authors = embedded.get("author", [])
            author = authors[0].get("name", "") if isinstance(authors, list) and authors and isinstance(authors[0], dict) else ""
            external_id = row.get("id")
            if type(external_id) is not int or external_id <= 0:
                continue
            item = {"provider": provider, "external_id": str(external_id), "source_url": row.get("link"),
                    "image_url": media.get("source_url"), "creator": _plain(author, 200), "license": "cc0",
                    "license_url": CC0_URL, "title": _plain(caption.get("rendered") if isinstance(caption, dict) else ""),
                    "width": dimensions.get("width"), "height": dimensions.get("height"),
                    "metadata": {"external_media_id": media.get("id"), "license_evidence_url": "https://wordpress.org/photos/license/"}}
        else:
            external_id = row.get("id")
            if not isinstance(external_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,100}", external_id):
                continue
            item = {"provider": provider, "external_id": external_id, "source_url": row.get("foreign_landing_url"),
                    "image_url": row.get("url"), "creator": _plain(row.get("creator"), 200),
                    "license": row.get("license"), "license_url": row.get("license_url"),
                    "title": _plain(row.get("title")), "width": row.get("width"), "height": row.get("height"),
                    "metadata": {"license_version": _plain(row.get("license_version"), 20)}}
        if _candidate_valid(item):
            candidates.append(item)
    return candidates, len(rows) - len(candidates)


def search_candidates(db_path, query, provider="wordpress_photos", session=None):
    """Search one external provider. Openverse is an explicit optional fallback.

    Every candidate needs visual review: keyword overlap is only a ranking hint.
    This function performs no WordPress requests and cannot upload an image.
    """
    if provider not in CATALOGUES:
        raise ValueError("Proveedor de imágenes no admitido.")
    if not isinstance(query, str) or not query.strip() or len(query) > 150 or any(ord(c) < 32 for c in query):
        raise ValueError("La búsqueda debe tener entre 1 y 150 caracteres.")
    query = query.strip()
    params = {"search": query, "per_page": 30, "_embed": "true"} if provider == "wordpress_photos" else {
        "q": query, "page_size": 20, "license": "cc0"}
    client = session if session is not None else requests.Session()
    if session is None:
        client.trust_env = False
    try:
        try:
            response = _get(client, CATALOGUES[provider], params=params)
        except requests.RequestException:
            raise ValueError("No se pudo consultar el catálogo de imágenes.") from None
        try:
            if response.status_code != 200:
                raise ValueError(f"El catálogo respondió HTTP {int(response.status_code)}.")
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("El catálogo devolvió una respuesta ilegible.") from None
        finally:
            response.close()
        candidates, discarded = _parse_candidates(provider, payload)
    finally:
        if session is None:
            client.close()
    _initialize(db_path)
    with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
        connection.row_factory = sqlite3.Row
        for item in candidates:
            now = _now()
            values = [item[k] for k in ("provider", "external_id", "source_url", "image_url", "creator", "license", "license_url", "title", "width", "height")]
            connection.execute("""INSERT INTO cover_candidates
                (provider,external_id,source_url,image_url,creator,license,license_url,title,width,height,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,external_id) DO UPDATE SET
                source_url=excluded.source_url,image_url=excluded.image_url,creator=excluded.creator,
                license=excluded.license,license_url=excluded.license_url,title=excluded.title,
                width=excluded.width,height=excluded.height,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (*values, _json(item["metadata"]), now, now))
            item["id"] = connection.execute("SELECT id FROM cover_candidates WHERE provider=? AND external_id=?",
                                            (provider, item["external_id"])).fetchone()[0]
            item["score"] = _score(item, query)
    candidates.sort(key=lambda item: (-item["score"], item["id"]))
    warnings = ["Revise visualmente la relación con el artículo antes de importar; la puntuación solo compara palabras y formato."]
    if discarded:
        warnings.append(f"Se descartaron {discarded} resultados por licencia, origen, formato, tamaño o datos insuficientes.")
    if not candidates:
        warnings.append("No hay una portada válida. Pruebe otra búsqueda o proveedor; el artículo necesita imagen.")
    return {"provider": provider, "query": query, "candidates": candidates,
            "selected_candidate_id": candidates[0]["id"] if candidates else None,
            "status": "candidates" if candidates else "needs_image", "requires_visual_review": True,
            "warnings": warnings}


def suggest_queries(article_title, categories=()):
    """Conservative visual terms grounded in a city or topic actually present."""
    title = _plain(article_title, 1000)
    category_text = " ".join(_plain(c, 100) for c in categories if isinstance(c, str)) if isinstance(categories, (list, tuple)) else _plain(categories)
    tokens = _tokens(title + " " + category_text)
    cities = (("barcelona", "Barcelona"), ("madrid", "Madrid"), ("valencia", "Valencia"),
              ("malaga", "Malaga"), ("sevilla", "Seville"), ("seville", "Seville"),
              ("alicante", "Alicante"), ("bilbao", "Bilbao"), ("granada", "Granada"))
    result = [query for token, query in cities if token in tokens]
    topics = (({"school", "schools", "education", "colegios", "educacion"}, "classroom"),
              ({"visa", "visas", "residency", "residencia", "tramites", "tax", "taxes", "impuestos"}, "paperwork"),
              ({"housing", "rent", "rental", "vivienda", "alquiler"}, "Spain apartments"),
              ({"train", "transport", "transporte", "tren"}, "Spain train"),
              ({"beach", "playa", "playas"}, "Spain beach"))
    result.extend(query for terms, query in topics if tokens & terms)
    return list(dict.fromkeys(result))[:4]


def _download_image(client, candidate):
    from PIL import Image, UnidentifiedImageError
    try:
        response = _get(client, candidate["image_url"], stream=True)
    except requests.RequestException:
        raise ValueError("No se pudo descargar la fotografía; no se envió a WordPress.") from None
    try:
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if response.status_code != 200 or mime not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("El archivo no es una imagen JPEG, PNG o WebP válida.")
        try:
            length = int(response.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ValueError("El tamaño anunciado de la imagen no es válido.") from None
        if length < 0 or length > MAX_IMAGE_BYTES:
            raise ValueError("La imagen supera el límite de 10 MB.")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("La imagen supera el límite de 10 MB.")
    except requests.RequestException:
        raise ValueError("La descarga de la imagen quedó incompleta; no se envió a WordPress.") from None
    finally:
        response.close()
    formats = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format not in formats or not _dimensions(*picture.size):
                    raise ValueError("La fotografía debe ser horizontal, de al menos 960 × 540 y hasta 30 megapíxeles.")
                actual_mime, extension = formats[picture.format]
                width, height = picture.size
                if actual_mime != mime or getattr(picture, "n_frames", 1) != 1:
                    raise ValueError("El tipo de imagen no coincide o contiene una animación.")
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                picture.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("Los bytes de la imagen no superan la verificación; no se envió a WordPress.") from None
    return bytes(data), actual_mime, extension, width, height


def _prepare_upload(binary, mime, extension, width, height):
    """Optimize oversized files without choosing, cropping or generating photos.

    Preserve the original format and transparency. Small files retain their
    exact bytes; every imported file records both original and uploaded hashes.
    """
    from PIL import Image, ImageOps

    original = {"sha256": hashlib.sha256(binary).hexdigest(), "bytes": len(binary),
                "width": width, "height": height, "mime_type": mime}
    processing = {"applied": False, "max_upload_bytes": MAX_UPLOAD_BYTES}
    if len(binary) <= MAX_UPLOAD_BYTES:
        return binary, width, height, original, processing
    formats = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}
    if extension not in formats:
        raise ValueError("No se puede preparar este formato para la subida.")
    format_name = formats[extension]
    try:
        with Image.open(io.BytesIO(binary)) as source:
            orientation = source.getexif().get(274, 1)
            picture = ImageOps.exif_transpose(source)
            picture.load()
            if not _dimensions(*picture.size):
                raise ValueError("La orientación real no permite una portada horizontal de al menos 960 × 540.")
            if format_name == "JPEG" and picture.mode not in {"RGB", "L"}:
                picture = picture.convert("RGB")
            elif format_name in {"PNG", "WEBP"} and picture.mode == "P":
                picture = picture.convert("RGBA" if "transparency" in picture.info else "RGB")
            # Re-encode from the original on every attempt, never from a prior
            # lossy result. No operation changes the selected source photo.
            attempts = ((2048, 85), (2048, 75), (1600, 75), (1280, 75), (960, 75))
            seen = set()
            for edge, quality in attempts:
                scale = min(1, edge / max(picture.size))
                size = (round(picture.width * scale), round(picture.height * scale))
                if not _dimensions(*size):
                    continue
                options = {"optimize": True} if format_name == "PNG" else {"quality": quality}
                if format_name == "JPEG":
                    options["optimize"] = True
                if format_name == "WEBP":
                    options["method"] = 6
                marker = (size, None if format_name == "PNG" else quality)
                if marker in seen:
                    continue
                seen.add(marker)
                prepared = picture.resize(size, Image.Resampling.LANCZOS) if size != picture.size else picture.copy()
                try:
                    buffer = io.BytesIO()
                    prepared.save(buffer, format=format_name, **options)
                    candidate_bytes = buffer.getvalue()
                finally:
                    prepared.close()
                if len(candidate_bytes) <= MAX_UPLOAD_BYTES:
                    processing = {"applied": True, "operation": "proportional_resize_and_reencode",
                                  "format": format_name, "max_edge": edge, "resample": "LANCZOS",
                                  "max_upload_bytes": MAX_UPLOAD_BYTES,
                                  "original_orientation": orientation, "exif_orientation_applied": orientation not in (1, None),
                                  "cropped": False, "generated": False, "transparency_preserved": format_name != "JPEG"}
                    if format_name != "PNG":
                        processing["quality"] = quality
                    return candidate_bytes, size[0], size[1], original, processing
    except (OSError, SyntaxError) as error:
        raise ValueError("No se pudo optimizar el archivo de portada; no se envió a WordPress.") from None
    raise ValueError("La imagen no cabe en el límite de subida sin bajar de 960 × 540; no se envió a WordPress.")


def _finish(db_path, import_id, status, *, media_id=None, media_url="", error=""):
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("""UPDATE cover_imports SET status=?,wp_media_id=?,wp_media_url=?,error=?,updated_at=?
            WHERE id=? AND status='importing'""", (status, media_id, media_url, error, _now(), import_id))
    return _read_one(db_path, "cover_imports", "SELECT * FROM cover_imports WHERE id=?", (import_id,))


def import_candidate(db_path, target_id, candidate_id, env_path=None, session=None, *, retry_failed=False):
    """Upload a reviewed candidate; retry a definite failure only when requested.

    Calling this function is the explicit external-write action. It does not set
    featured_media on any post. HTTP errors and timeouts never archive raw bodies.
    Unknown, interrupted and successful imports can never be retried. An explicit
    retry reserves the same record and retains the previous failure's provenance.
    """
    _id(target_id)
    _id(candidate_id)
    if type(retry_failed) is not bool:
        raise ValueError("La confirmación de reintento debe ser un booleano.")
    previous = get_import_record(db_path, target_id, candidate_id)
    if previous and not (retry_failed and previous["status"] == "failed"):
        return previous
    candidate = get_candidate(db_path, candidate_id)
    if not candidate or not _candidate_valid(candidate):
        raise ValueError("La candidata guardada no es una fotografía CC0 admitida.")
    repository = DeploymentRepository(db_path, env_path=env_path)
    target = repository.get_target(target_id)
    base = CONNECTIONS[target["connection_key"]][1]
    destination = _read_one(db_path, "generation_destinations", "SELECT site_url,locale,enabled FROM generation_destinations WHERE id=?", (target["destination_id"],))
    origin = _url(destination["site_url"]) if destination else None
    if not destination or not destination["enabled"] or origin is None or origin.netloc != urlsplit(base).netloc or destination["locale"] != target["locale"]:
        raise ValueError("El destino no coincide con la conexión WordPress configurada.")
    credentials = load_credentials(target["connection_key"], env_path)
    if credentials.base_url != base or not credentials.username or not credentials.application_password:
        raise ValueError("Faltan credenciales válidas para WordPress.")
    secrets = [credentials.application_password, credentials.application_password.replace(" ", "")]
    if any(secret and secret in _json(candidate) for secret in secrets):
        raise ValueError("La candidata contiene información de autenticación.")
    client = session if session is not None else requests.Session()
    if session is None:
        client.trust_env = False
    try:
        binary, mime, extension, width, height = _download_image(client, candidate)
        binary, width, height, original_file, processing = _prepare_upload(binary, mime, extension, width, height)
        _initialize(db_path)
        provenance = {key: candidate[key] for key in ("provider", "external_id", "source_url", "image_url", "creator", "license", "license_url", "title")}
        provenance.update({"width": width, "height": height, "sha256": hashlib.sha256(binary).hexdigest(),
                           "mime_type": mime, "bytes": len(binary), "metadata": candidate["metadata"],
                           "original_file": original_file, "processing": processing,
                           "attempt": 1, "previous_failures": []})
        with closing(sqlite3.connect(db_path, timeout=30)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM cover_imports WHERE site_base_url=? AND provider=? AND external_id=?",
                                          (base, candidate["provider"], candidate["external_id"])).fetchone()
            now = _now()
            if existing:
                # Recheck the exact observed failure under the writer lock.
                # Another request may already have reserved or finished a retry.
                if (not retry_failed or not previous or existing["status"] != "failed"
                        or existing["id"] != previous["id"] or existing["updated_at"] != previous["updated_at"]
                        or json.loads(existing["provenance_json"]) != previous["provenance"]):
                    return _record(existing)
                old_provenance = json.loads(existing["provenance_json"])
                provenance["previous_failures"] = old_provenance.get("previous_failures", []) + [{
                    "error": existing["error"], "updated_at": existing["updated_at"],
                    "provenance_sha256": hashlib.sha256(existing["provenance_json"].encode("utf-8")).hexdigest(),
                }]
                provenance["attempt"] = len(provenance["previous_failures"]) + 1
                import_id = existing["id"]
                connection.execute("""UPDATE cover_imports SET target_id=?,candidate_id=?,status='importing',
                    wp_media_id=NULL,wp_media_url='',error='',provenance_json=?,updated_at=? WHERE id=?""",
                    (target_id, candidate_id, _json(provenance), now, import_id))
            else:
                cursor = connection.execute("""INSERT INTO cover_imports
                    (target_id,candidate_id,site_base_url,provider,external_id,status,provenance_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,'importing',?,?,?)""", (target_id, candidate_id, base, candidate["provider"], candidate["external_id"], _json(provenance), now, now))
                import_id = cursor.lastrowid
        filename = "et-cover-" + candidate["provider"] + "-" + candidate["external_id"] + "." + extension
        description = ("Fotografía CC0. Autor: " + candidate["creator"] + ". Origen: " + candidate["source_url"]
                       + ". Licencia: " + candidate["license_url"])
        form = {"title": candidate["title"][:200], "alt_text": candidate["title"], "description": html.escape(description)}
        try:
            response = client.request("POST", base + "/", params={"rest_route": "/wp/v2/media"},
                auth=(credentials.username, credentials.application_password), headers=HEADERS, data=form,
                files={"file": (filename, binary, mime)}, timeout=(10, 90), verify=True, allow_redirects=False)
        except requests.RequestException:
            return _finish(db_path, import_id, "unknown", error="WordPress pudo guardar la imagen; no se confirmó la respuesta ni se reintentará automáticamente.")
        try:
            if response.status_code not in (200, 201):
                status = "failed" if 400 <= response.status_code < 500 and response.status_code not in (408, 409) else "unknown"
                return _finish(db_path, import_id, status, error=f"WordPress respondió HTTP {int(response.status_code)}. No se reintentó la importación.")
            try:
                result = response.json()
            except ValueError:
                result = None
            media_url = result.get("source_url") if isinstance(result, dict) else None
            parsed = _url(media_url)
            if (not isinstance(result, dict) or type(result.get("id")) is not int or result["id"] <= 0
                    or parsed is None or parsed.netloc != urlsplit(base).netloc
                    or result.get("mime_type") != mime or any(secret and secret in media_url for secret in secrets)):
                return _finish(db_path, import_id, "unknown", error="WordPress no confirmó un medio válido del sitio; requiere comprobación manual, sin reintento.")
            return _finish(db_path, import_id, "ready", media_id=result["id"], media_url=media_url)
        finally:
            response.close()
    finally:
        if session is None:
            client.close()
