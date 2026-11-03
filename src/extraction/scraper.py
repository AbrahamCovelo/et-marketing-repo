"""Bounded, text-only HTTP collection for the Moving to Spain adapter."""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import re
import time
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import requests


SOURCE_URL = "https://movingtospain.com/guides/"
SOURCE_HOST = "movingtospain.com"
USER_AGENT = "Mozilla/5.0 (compatible; ETContentResearch/0.1)"
_HOSTS = {SOURCE_HOST, "www." + SOURCE_HOST}
_RETRY_CODES = {429, 500, 502, 503, 504}
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_INDEX_PATH = re.compile(r"^/guides/(?:page/([1-9][0-9]*)/)?$")


class ScrapeError(RuntimeError):
    """A response cannot safely be treated as an article or index."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HtmlResponse:
    url: str
    html: str
    status_code: int = 200


def normalize_url(url: str, base_url: str = SOURCE_URL) -> str:
    """Canonical source URL; unsupported hosts/schemes are rejected explicitly.

    Fragments and tracking parameters do not identify different articles.
    Other query parameters are retained because they may change the resource.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("La URL está vacía.")
    value = urljoin(base_url, url.strip())
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("Solo se permiten URL HTTP/HTTPS del sitio fuente.")
    if parts.username or parts.password or parts.hostname not in _HOSTS:
        raise ValueError(f"El adaptador no admite este sitio: {parts.hostname or value}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("Puerto inválido en la URL.") from exc
    if port not in {None, 80, 443}:
        raise ValueError("El adaptador no admite puertos alternativos.")
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if not path.endswith("/") and not re.search(r"\.[a-zA-Z0-9]{1,8}$", path):
        path += "/"
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_")
             and key.lower() not in {"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid"}]
    return urlunsplit(("https", SOURCE_HOST, path, urlencode(sorted(query)), ""))


class HttpClient:
    """One session, paced requests, bounded retries and validated redirects.

    A redirected host is checked *before* requesting it. Only HTML is read;
    images, videos, JavaScript and other page resources are never requested.
    """

    def __init__(self, *, delay: float = 1.0, timeout: float = 30.0,
                 max_retries: int = 2, max_redirects: int = 5,
                 max_bytes: int = 8 * 1024 * 1024,
                 session=None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if delay < 0 or timeout <= 0 or max_retries < 0 or max_redirects < 0 or max_bytes <= 0:
            raise ValueError("Demora, timeout y límites HTTP inválidos.")
        self.delay, self.timeout = delay, timeout
        self.max_retries, self.max_redirects, self.max_bytes = max_retries, max_redirects, max_bytes
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        self._sleep, self._clock = sleep, clock
        self._last_request: float | None = None

    def close(self):
        if self._owns_session:
            self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _pace(self):
        if self._last_request is not None:
            remaining = self.delay - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "") if response is not None else ""
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                seconds = 2 ** attempt
        return min(30.0, max(0.0, seconds))

    def get(self, url: str) -> HtmlResponse:
        current = normalize_url(url)
        visited = {current}
        redirects = 0
        attempt = 0
        while True:
            self._pace()
            try:
                response = self.session.get(current, timeout=self.timeout,
                                            allow_redirects=False, stream=True)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise ScrapeError(f"No se pudo descargar {current}: {exc}") from exc
                self._sleep(self._retry_delay(None, attempt))
                attempt += 1
                continue
            try:
                status = response.status_code
                if status in _RETRY_CODES and attempt < self.max_retries:
                    wait = self._retry_delay(response, attempt)
                    attempt += 1
                    response.close()
                    self._sleep(wait)
                    continue
                if status in _REDIRECT_CODES:
                    location = response.headers.get("Location")
                    if not location:
                        raise ScrapeError("Redirección sin destino.", status_code=status)
                    try:
                        target = normalize_url(location, current)
                    except ValueError as exc:
                        raise ScrapeError(f"Redirección fuera del sitio fuente: {location}", status_code=status) from exc
                    if target in visited or redirects >= self.max_redirects:
                        raise ScrapeError("Bucle o exceso de redirecciones.", status_code=status)
                    visited.add(target)
                    current = target
                    redirects += 1
                    attempt = 0
                    continue
                if not 200 <= status < 300:
                    raise ScrapeError(f"HTTP {status} al descargar {current}.", status_code=status)
                content_type = response.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() not in {"text/html", "application/xhtml+xml"}:
                    raise ScrapeError(f"Respuesta no HTML: {content_type or 'sin Content-Type'}.", status_code=status)
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise ScrapeError("HTML supera el límite de descarga.", status_code=status)
                charset = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type, re.I)
                if charset:
                    encoding = charset.group(1)
                else:
                    declaration = re.search(br"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", body[:8192], re.I)
                    encoding = declaration.group(1).decode("ascii") if declaration else "utf-8"
                try:
                    html = bytes(body).decode(encoding, errors="strict")
                except (LookupError, UnicodeDecodeError) as exc:
                    raise ScrapeError(f"No se pudo decodificar el HTML como {encoding}.", status_code=status) from exc
                soup = BeautifulSoup(html, "html.parser")
                title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
                if re.search(r"security verification|just a moment|access denied|verify (?:you are|you're) human|attention required", title):
                    raise ScrapeError("El servidor devolvió una página de verificación, no contenido.", status_code=status)
                if not html.strip() or soup.html is None:
                    raise ScrapeError("Respuesta HTML vacía o incompleta.", status_code=status)
                return HtmlResponse(current, html, status)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                if attempt >= self.max_retries:
                    raise ScrapeError(f"Descarga interrumpida: {exc}", status_code=response.status_code) from exc
                self._sleep(self._retry_delay(None, attempt))
                attempt += 1
            finally:
                response.close()


def discover_guides(start_url: str = SOURCE_URL, max_pages: int | None = 3,
                    client: HttpClient | None = None) -> list[str]:
    """Follow the guide index pagination, never arbitrary article/body links.

    ``None`` follows pagination until exhaustion; a positive limit is explicit.
    A missing card selector or a pagination loop raises instead of masquerading
    as a successfully exhausted catalog.
    """
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages debe ser positivo o None.")
    current = normalize_url(start_url)
    if not _INDEX_PATH.fullmatch(urlsplit(current).path) or urlsplit(current).query:
        raise ValueError("El descubrimiento debe comenzar en /guides/ o su paginación.")
    own_client = client is None
    client = client or HttpClient()
    pages: set[str] = set()
    found: dict[str, None] = {}
    page_count = 0
    try:
        while current:
            if current in pages:
                raise ScrapeError("Bucle en la paginación del índice.")
            response = client.get(current)
            final = normalize_url(response.url)
            if not _INDEX_PATH.fullmatch(urlsplit(final).path) or urlsplit(final).query:
                raise ScrapeError("El índice redirigió fuera de la sección de guías.")
            if final in pages:
                raise ScrapeError("La paginación redirigió a una página ya visitada.")
            pages.add(current)
            pages.add(final)
            page_count += 1
            soup = BeautifulSoup(response.html, "html.parser")
            cards = soup.select("h2.entry-title a[href]")
            if not cards:
                raise ScrapeError("No se encontraron tarjetas de guías; revisar el selector del índice.")
            for card in cards:
                try:
                    article_url = normalize_url(card["href"], final)
                except ValueError:
                    continue
                if not _INDEX_PATH.fullmatch(urlsplit(article_url).path):
                    found.setdefault(article_url, None)
            # Explicitly avoid treating the index's marketing links as pagination.
            current_number = int(_INDEX_PATH.fullmatch(urlsplit(final).path).group(1) or 1)
            next_link = soup.select_one("a.next.page-numbers[href], a[rel~=next][href]")
            next_url = None
            if next_link is not None:
                try:
                    next_url = normalize_url(next_link["href"], final)
                except ValueError as exc:
                    raise ScrapeError("Paginación fuera del sitio fuente.") from exc
                match = _INDEX_PATH.fullmatch(urlsplit(next_url).path)
                if not match or urlsplit(next_url).query:
                    raise ScrapeError("El enlace siguiente no pertenece al índice de guías.")
                if int(match.group(1) or 1) <= current_number:
                    raise ScrapeError("La paginación no avanza.")
            else:
                candidates = []
                for link in soup.select("a.page-numbers[href]"):
                    try:
                        candidate = normalize_url(link["href"], final)
                    except ValueError:
                        continue
                    match = _INDEX_PATH.fullmatch(urlsplit(candidate).path)
                    if match and not urlsplit(candidate).query:
                        number = int(match.group(1) or 1)
                        if number > current_number:
                            candidates.append((number, candidate))
                if candidates:
                    next_url = min(candidates)[1]
            if max_pages is not None and page_count >= max_pages:
                break
            current = next_url
        return list(found)
    finally:
        if own_client:
            client.close()


def scrape_articles(urls: Iterable[str], db, *, refresh: bool = False,
                    delay: float = 1.0, timeout: float = 30.0,
                    client: HttpClient | None = None, extractor=None) -> dict:
    """Fetch, clean and store each URL independently, with a persistent run log."""
    if extractor is None:
        from .cleaner import extract_html
        extractor = extract_html
    urls = list(urls)
    own_client = client is None
    client = client or HttpClient(delay=delay, timeout=timeout)
    run_id = db.start_run("scrape", {"urls": urls, "refresh": refresh, "delay": delay, "timeout": timeout})
    stats = {"run_id": run_id, "success": 0, "skipped": 0, "failed": 0, "errors": []}
    seen = set()
    interrupted = True
    try:
        for original_url in urls:
            requested_url = original_url
            status_code = None
            try:
                requested_url = normalize_url(original_url)
                if requested_url in seen or (not refresh and db.has_article(requested_url)):
                    stats["skipped"] += 1
                    continue
                seen.add(requested_url)
                response = client.get(requested_url)
                status_code = response.status_code
                result = extractor(response.html, response.url)
                result.canonical_url = normalize_url(result.canonical_url, response.url)
                if not result.title.strip() or not result.clean_text.strip():
                    raise ScrapeError("Extracción sin título o sin texto útil.", status_code=status_code)
                db.store_article(result, raw_html=response.html, requested_url=requested_url,
                                 status_code=status_code, run_id=run_id)
                stats["success"] += 1
            except Exception as exc:
                stats["failed"] += 1
                error = {"url": str(requested_url), "error": str(exc)}
                stats["errors"].append(error)
                db.record_fetch(run_id, str(requested_url), status_code=getattr(exc, "status_code", None) or status_code,
                                error=str(exc))
        interrupted = False
        return stats
    finally:
        status = "failed" if interrupted or (stats["failed"] and not stats["success"]) else "partial" if stats["failed"] else "completed"
        try:
            db.finish_run(run_id, stats, status)
        finally:
            if own_client:
                client.close()
