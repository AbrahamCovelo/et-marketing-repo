"""Validate portable WordPress body HTML and resolve configured CTA text locally.

No styles, theme-specific markup, media creation or remote publishing live here.
Invalid input is reported rather than silently stripped or repaired.
"""
from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString


MAX_HTML_CHARACTERS = 500_000
CTA_MARKERS = (
    "CTA_IMMIGRATION_EN", "CTA_TAXES_EN", "CTA_IMMIGRATION_FR",
    "CTA_TAXES_FR", "CTA_INMIGRACION_ES", "CTA_IMPUESTOS_ES",
)
ALLOWED_TAGS = frozenset({
    "p", "h2", "h3", "ul", "ol", "li", "strong", "em", "a", "blockquote",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "th", "td", "br",
})
_INLINE = frozenset({"strong", "em", "a", "br"})
_ROOT_BLOCKS = frozenset({"p", "h2", "h3", "ul", "ol", "blockquote", "table"})
_CTA = re.compile(r"\[(CTA_[A-Za-z0-9_]+)\]")
_SHORTCODE = re.compile(r"\[/?[A-Za-z][A-Za-z0-9_-]*(?:\s[^\]\r\n]*)?/?\]")
_MARKDOWN = re.compile(r"```|~~~|(?:^|\n)\s{0,3}(?:#{1,6}\s|[-*+]\s)|\*\*[^*]+\*\*|\[[^\]\n]+\]\([^\n)]+\)")


def _add(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _https_url(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > 2048:
        return False
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        hostname = hostname.encode("idna").decode("ascii")
        return (parsed.scheme == "https" and bool(hostname) and not parsed.username and not parsed.password
                and bool(re.fullmatch(r"[A-Za-z0-9.-]+", hostname))
                and (parsed.port is None or 0 < parsed.port <= 65535))
    except (ValueError, UnicodeError):
        return False


class _SemanticParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: set[str] = set()
        self.text: list[str] = []
        self.markers: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        if tag not in ALLOWED_TAGS:
            _add(self.errors, f"La etiqueta <{tag}> no está permitida en el cuerpo semántico.")
        parent = self.stack[-1] if self.stack else None
        if parent is None and tag not in _ROOT_BLOCKS:
            _add(self.errors, "El cuerpo debe estar organizado en párrafos, encabezados, listas o tablas.")
        if parent in {"p", "h2", "h3", "strong", "em", "a"} and tag not in _INLINE:
            _add(self.errors, "El HTML contiene bloques anidados dentro de texto o encabezados.")
        if tag == "a" and "a" in self.stack:
            _add(self.errors, "No se permiten enlaces anidados.")
        required_parent = {"li": {"ul", "ol"}, "thead": {"table"}, "tbody": {"table"},
                           "tfoot": {"table"}, "caption": {"table"}, "tr": {"table", "thead", "tbody", "tfoot"},
                           "th": {"tr"}, "td": {"tr"}}
        allowed_child = {"ul": {"li"}, "ol": {"li"}, "table": {"caption", "thead", "tbody", "tfoot", "tr"},
                         "thead": {"tr"}, "tbody": {"tr"}, "tfoot": {"tr"}, "tr": {"th", "td"}}
        if (tag in required_parent and parent not in required_parent[tag]) or (parent in allowed_child and tag not in allowed_child[parent]):
            _add(self.errors, "La estructura de listas o tablas no es válida.")
        allowed_attrs = {"a": {"href", "title"}, "th": {"scope", "colspan", "rowspan"}, "td": {"colspan", "rowspan"}}.get(tag, set())
        seen = set()
        for name, value in attrs:
            if name in seen:
                _add(self.errors, "No se permiten atributos duplicados.")
            seen.add(name)
            if name not in allowed_attrs:
                _add(self.errors, f"El atributo {name} no está permitido; el tema debe controlar la presentación.")
            if value and _CTA.search(value):
                _add(self.errors, "Los marcadores CTA deben aparecer como texto, nunca en atributos.")
            if value and _SHORTCODE.search(value):
                _add(self.errors, "No se permiten shortcodes ni marcadores en atributos HTML.")
            if name == "href" and not _https_url(value):
                _add(self.errors, "Los enlaces deben usar una URL HTTPS completa sin credenciales.")
            if name == "scope" and value not in {"row", "col", "rowgroup", "colgroup"}:
                _add(self.errors, "El atributo scope de una tabla no es válido.")
            if name in {"colspan", "rowspan"} and (not value or not re.fullmatch(r"[1-9][0-9]?|100", value)):
                _add(self.errors, "Las dimensiones semánticas de tabla deben ser enteros entre 1 y 100.")
        if tag == "a" and "href" not in seen:
            _add(self.errors, "Todo enlace necesita una URL HTTPS en href.")
        if tag != "br":
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != "br":
            _add(self.errors, "Solo <br> puede ser una etiqueta autocerrada.")
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            _add(self.errors, "El HTML tiene etiquetas sin cerrar o cerradas fuera de orden.")
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)
        if data.strip() and (not self.stack or self.stack[-1] in {"ul", "ol", "table", "thead", "tbody", "tfoot", "tr"}):
            _add(self.errors, "El texto debe estar dentro de un bloque semántico apropiado.")
        for marker in _CTA.findall(data):
            if marker not in CTA_MARKERS:
                _add(self.errors, "El contenido tiene un marcador CTA no admitido.")
            else:
                _add(self.markers, marker)
                if "a" in self.stack:
                    _add(self.errors, "Los marcadores CTA no pueden estar dentro de enlaces existentes.")
        without_ctas = _CTA.sub("", data)
        if _SHORTCODE.search(without_ctas):
            _add(self.errors, "No se permiten shortcodes de WordPress en el cuerpo HTML.")
        if _MARKDOWN.search(data):
            _add(self.errors, "El cuerpo debe ser HTML semántico, sin formato Markdown.")

    def handle_comment(self, data):
        _add(self.errors, "No se permiten comentarios o bloques específicos de temas/plugins en el cuerpo.")

    def handle_decl(self, decl):
        _add(self.errors, "Envíe únicamente el cuerpo del artículo, sin declaraciones de documento.")

    def handle_pi(self, data):
        _add(self.errors, "No se permiten instrucciones de procesamiento en el cuerpo.")

    def unknown_decl(self, data):
        _add(self.errors, "No se permiten declaraciones especiales en el cuerpo.")


def validate_semantic_html(html: str) -> dict:
    """Report incompatible markup without stripping or rewriting the article."""
    result = {"valid": False, "errors": [], "warnings": [], "cta_markers": []}
    if not isinstance(html, str) or not html.strip():
        result["errors"].append("El cuerpo HTML debe contener texto.")
        return result
    if len(html) > MAX_HTML_CHARACTERS:
        result["errors"].append(f"El cuerpo supera el máximo de {MAX_HTML_CHARACTERS} caracteres.")
        return result
    if any(ord(character) < 32 and character not in "\t\n\r" for character in html):
        result["errors"].append("El cuerpo contiene caracteres de control no permitidos.")
        return result
    parser = _SemanticParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        result["errors"].append("No se pudo interpretar el cuerpo HTML.")
        return result
    result["errors"].extend(parser.errors)
    if parser.stack:
        _add(result["errors"], "El HTML tiene etiquetas sin cerrar o cerradas fuera de orden.")
    if not _CTA.sub("", "".join(parser.text)).strip():
        _add(result["errors"], "El cuerpo no contiene texto editorial.")
    result["cta_markers"] = parser.markers
    if parser.markers:
        result["warnings"].append("Hay marcadores CTA que requieren enlaces configurados antes de preparar el envío.")
    if "h2" not in parser.tags:
        result["warnings"].append("El artículo no contiene encabezados h2 para organizar sus secciones.")
    result["valid"] = not result["errors"]
    return result


def prepare_wordpress_content(html: str, cta_links: dict) -> dict:
    """Resolve explicit CTA text; incomplete or invalid input yields no payload body.

    ``cta_links`` maps marker names WITHOUT brackets to ``{"url": ..., "text": ...}``.
    Both fields are mandatory: a URL or anchor label is never guessed.
    """
    validation = validate_semantic_html(html)
    result = {"ready": False, "content": None, "validation": validation, "replaced_ctas": []}
    if not validation["valid"]:
        return result
    if not isinstance(cta_links, dict):
        validation["errors"].append("La configuración de CTAs debe ser un objeto.")
    elif set(cta_links) - set(CTA_MARKERS):
        validation["errors"].append("La configuración contiene un marcador CTA no admitido.")
    else:
        for marker in validation["cta_markers"]:
            link = cta_links.get(marker)
            if not isinstance(link, dict) or set(link) != {"url", "text"}:
                validation["errors"].append(f"Configure URL y texto para [{marker}].")
            elif not _https_url(link["url"]):
                validation["errors"].append(f"La URL de [{marker}] debe ser HTTPS y no incluir credenciales.")
            elif not isinstance(link["text"], str) or not link["text"].strip() or len(link["text"]) > 2000:
                validation["errors"].append(f"El enlace de [{marker}] necesita un texto de hasta 2000 caracteres.")
    if validation["errors"]:
        validation["valid"] = False
        return result
    if not validation["cta_markers"]:
        return {**result, "ready": True, "content": html}
    soup = BeautifulSoup(html, "html.parser", preserve_whitespace_tags=ALLOWED_TAGS)
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment) or not _CTA.search(str(node)):
            continue
        text = str(node)
        position = 0
        for match in _CTA.finditer(text):
            marker = match[1]
            node.insert_before(NavigableString(text[position:match.start()]))
            anchor = soup.new_tag("a", href=cta_links[marker]["url"])
            anchor.string = cta_links[marker]["text"]
            node.insert_before(anchor)
            position = match.end()
            _add(result["replaced_ctas"], marker)
        node.insert_before(NavigableString(text[position:]))
        node.extract()
    prepared = soup.decode(formatter="minimal")
    validation = validate_semantic_html(prepared)
    if validation["cta_markers"]:
        validation["errors"].append("La configuración de enlaces ha dejado marcadores CTA sin resolver.")
        validation["valid"] = False
    result["validation"] = validation
    if validation["valid"]:
        result.update(ready=True, content=prepared)
    return result
