"""Normalize recoverable API formatting without rewriting editorial content.

This is deliberately not a generic HTML sanitizer. It cannot approve unsafe
links, invent missing structure, discard content or turn arbitrary output into
an article. The existing semantic validator remains the final authority.
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import re

from bs4 import BeautifulSoup, NavigableString, Tag

from src.deployment.content import ALLOWED_TAGS, MAX_HTML_CHARACTERS, validate_semantic_html


NORMALIZATION_POLICY = "semantic-format-v1"
_INLINE = {"strong", "em", "a", "br"}
_BLOCK_WRAPPERS = {"div", "section", "article", "main", "body", "html"}
_ALIASES = {"b": "strong", "i": "em"}
_NORMALIZABLE_TAGS = ALLOWED_TAGS | _BLOCK_WRAPPERS | {"span", *_ALIASES}
_MARKER = re.compile(r"\[CTA_[A-Za-z0-9_]+\]")
_FENCE = re.compile(r"\A\s*(```|~~~)(?:html)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n\1[ \t]*\s*\Z", re.I)


class _BalancedMarkup(HTMLParser):
    """Do not let a forgiving DOM parser silently fix uncertain HTML.

Only a small list of presentation wrappers and equivalent inline aliases is
accepted in addition to the final HTML contract. Attribute validation still
happens in the semantic validator; wrapper attributes cannot disappear here.
"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.safe = True
        self.doctypes = 0

    def handle_starttag(self, tag, attrs):
        if tag not in _NORMALIZABLE_TAGS or len(self.stack) >= 128:
            self.safe = False
        if len({name for name, _ in attrs}) != len(attrs):
            self.safe = False
        # Erasing wrapper attributes could erase CTA markers, anchor targets,
        # hidden-content semantics or plugin instructions. Only document lang
        # is transport metadata: the destination already defines the locale.
        if tag in _BLOCK_WRAPPERS | {"span"}:
            if any(name != "lang" or tag != "html" or not value or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value)
                   for name, value in attrs):
                self.safe = False
        if tag == "html" and self.stack:
            self.safe = False
        if tag == "body" and self.stack not in ([], ["html"]):
            self.safe = False
        if tag != "br":
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag != "br":
            self.safe = False
        self.handle_starttag(tag, attrs)
        if tag != "br":
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.safe = False
        else:
            self.stack.pop()

    def handle_comment(self, data):
        self.safe = False

    def handle_decl(self, decl):
        # An ordinary HTML doctype has no content; external declarations are
        # not transport formatting and are never removed by this normalizer.
        self.doctypes += 1
        if decl.strip().lower() != "doctype html" or self.stack or self.doctypes > 1:
            self.safe = False

    def handle_pi(self, data):
        self.safe = False

    def unknown_decl(self, data):
        self.safe = False


def _signature(soup):
    """Retain wording, CTA multiplicity/order, links and table semantics."""
    text = "".join(soup.strings)
    links = tuple((tag.get("href"), tag.get("title"), " ".join(tag.get_text().split())) for tag in soup.find_all("a"))
    cells = tuple((tag.name, tuple(sorted((name, str(value)) for name, value in tag.attrs.items())))
                  for tag in soup.find_all(["th", "td"]))
    return (" ".join(text.split()), tuple(_MARKER.findall(text)), links, cells)


def _wrap_inline_runs(container, soup, operations):
    """Wrap orphan text and inline nodes without joining separate blocks."""
    pending = []

    def flush():
        nonlocal pending
        if not pending:
            return
        meaningful = any(isinstance(child, Tag) or str(child).strip() for child in pending)
        if meaningful:
            paragraph = soup.new_tag("p")
            pending[0].insert_before(paragraph)
            for child in pending:
                paragraph.append(child.extract())
            operations["wrap_orphan_text_or_inline"] += 1
        pending = []

    for child in list(container.contents):
        if isinstance(child, NavigableString) or isinstance(child, Tag) and child.name in _INLINE:
            pending.append(child)
        else:
            flush()
    flush()


def normalize_generated_html(html):
    """Return a validated formatting-only candidate, or the untouched original.

The result always includes ``html``, ``changed``, ``valid``, ``validation``,
``original_validation``, ``operations``, ``policy`` and ``reason``. Valid input
is returned byte-for-byte. Failed attempts never expose a partially normalized
body as usable content. This function does not persist or make network calls.
"""
    original_validation = validate_semantic_html(html)
    result = {"html": html, "changed": False, "valid": original_validation["valid"],
              "validation": original_validation, "original_validation": original_validation,
              "operations": [], "policy": NORMALIZATION_POLICY, "reason": ""}
    if original_validation["valid"]:
        return result
    if not isinstance(html, str) or not html.strip() or len(html) > MAX_HTML_CHARACTERS or any(ord(char) < 32 and char not in "\t\r\n" for char in html):
        return {**result, "reason": "invalid_input"}
    operations = Counter()
    candidate = html
    fence = _FENCE.fullmatch(candidate)
    if fence:
        candidate = fence["body"]
        operations["remove_outer_html_fence"] += 1
    markup = _BalancedMarkup()
    try:
        markup.feed(candidate)
        markup.close()
    except Exception:
        return {**result, "reason": "ambiguous_markup"}
    if not markup.safe or markup.stack:
        return {**result, "reason": "unsafe_or_ambiguous_markup"}
    if markup.doctypes:
        candidate = re.sub(r"<!doctype\s+html\s*>", "", candidate, flags=re.I)
        operations["remove_html_doctype"] += markup.doctypes
    soup = BeautifulSoup(candidate, "html.parser", preserve_whitespace_tags=_NORMALIZABLE_TAGS)
    before = _signature(soup)
    for tag in list(soup.find_all(_ALIASES)):
        old_name = tag.name
        tag.name = _ALIASES[old_name]
        operations[f"semantic_alias_{old_name}"] += 1
    for tag in list(soup.find_all("span")):
        tag.unwrap()
        operations["unwrap_inline_container"] += 1
    # Work deepest-first so div/section boundaries remain separate paragraphs,
    # rather than concatenating their text when the wrappers are removed.
    for tag in reversed(list(soup.find_all(_BLOCK_WRAPPERS))):
        _wrap_inline_runs(tag, soup, operations)
        tag.unwrap()
        operations["unwrap_block_container"] += 1
    _wrap_inline_runs(soup, soup, operations)
    if before != _signature(soup):
        return {**result, "reason": "content_retention_failed"}
    normalized = soup.decode(formatter="minimal")
    validation = validate_semantic_html(normalized)
    if not validation["valid"]:
        return {**result, "reason": "normalization_incomplete", "candidate_validation": validation}
    # Compare after serialization too: escaping entities must not change text,
    # promote encoded HTML into executable markup or mutate a link destination.
    reparsed = BeautifulSoup(normalized, "html.parser", preserve_whitespace_tags=_NORMALIZABLE_TAGS)
    if before != _signature(reparsed):
        return {**result, "reason": "serialization_retention_failed"}
    return {**result, "html": normalized, "changed": normalized != html, "valid": True,
            "validation": validation,
            "operations": [{"operation": name, "count": count} for name, count in sorted(operations.items())]}
