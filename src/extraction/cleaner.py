"""Deterministic, auditable text extraction for the inspected WordPress templates.

This module does not paraphrase, translate, validate facts, or invent replacements.
Unusable source material remains in the audit trail, outside the reusable body.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from .models import ExtractionResult


class ExtractionError(ValueError):
    """The page cannot safely be recognized as an article."""


_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote"}
_SKIP_TEXT = {"script", "style", "noscript", "svg", "img", "video", "audio", "iframe", "template"}
_PERSONAL = re.compile(r"\b(?:I|I[’'](?:m|ve|d|ll)|my|My|mine|Mine|myself|We|we|We[’'](?:ve|re|d|ll)|we[’'](?:ve|re|d|ll)|our|Our|ours|us|me|Me)\b")
_PROMO = re.compile(
    r"\b(?:book (?:a|your|my) (?:consultation|call|session)|sign up|subscribe|join (?:our|the|thrive)|get our|"
    r"our (?:vetted|trusted|expert|specialist|immigration|tax|rental|school|currency|partner)|"
    r"we (?:recommend|can introduce|offer|help)|(?:mts|moving to spain) membership|"
    r"self-led relocation|guided custom relocation|decide package|"
    r"find your (?:corner|place)|custom scouting trips?|relocate planning session|"
    r"free (?:call|consultation)|contact myexpattaxes|affiliate|commission at no extra|"
    r"moving to spain expert tax partner|moving to spain housing hub|coming soon:|learn more at)\b", re.I,
)
_REFERRAL = re.compile(
    r"(?:\b(?:also read|read also|read more|read next|jump to)\b|^\s*(?:related (?:articles|guides)|further reading)\b|"
    r"\b(?:see|read|browse|check out|visit|refer to|covered in|set out in|found in|learn more|find out more)\b.*"
    r"\b(?:our|guide|article|report|calculator|checklist|series|dedicated)\b|"
    r"\bour\b.{0,90}\b(?:guide|article|calculator|checklist|planner|report|series)\b.{0,90}"
    r"\b(?:covers|compares|explains|breaks down|works through|has the detail|can help|tracks|sequences|gives you|sets out)\b|"
    r"\b(?:full|further|more) (?:details|information|requirements)\b.{0,80}\b(?:guide|article)\b)", re.I,
)
_EDITORIAL = re.compile(
    r"\b(?:written by|reviewed by|co[- ]founder|writer at|owner moving to spain|"
    r"(?:mts|moving to spain) (?:client|immigration|tax|partner|webinar)|"
    r"expert note|in collaboration with|our research|our experience|our clients|"
    r"reviewer of this article|reviewed and (?:verified|audited)|audited and reviewed)\b", re.I,
)
_PROMO_SECTIONS = re.compile(
    r"^(?:Know you can move\.|Run your own move, or hand it over\?|"
    r"Staying supported once you are here|Why (?:choose|work with) us|"
    r"Get (?:our|the) .*package|Related (?:articles|guides)|Further reading)", re.I,
)
_MEDIA_DEPENDENCY = re.compile(r"\b(?:ranking|ranked|index|infographic|chart|statistics|results|table|38 towns|38 places)\b", re.I)


def _has_personal(text: str) -> bool:
    # The inspected grants article uses Program I and Programme II: these are
    # identifiers, not a first-person author pronoun.
    probe = re.sub(r"\b(?:program(?:me)?|part|phase|stage|type|class|group|form|schedule|annex|appendix|chapter|title|section)(?:\s+form)?\s*[:(]?\s*[IVXLCDM]+(?:\s*[-–—&,]\s*[IVXLCDM]+)*\b", "identifier", text, flags=re.I)
    probe = re.sub(r"\([IVXLCDM]+(?:\s*[-–—&,]\s*[IVXLCDM]+)*\)", "identifier", probe)
    if re.match(r"^(?:Ask|Ask yourself|Consider these questions)\s*:", probe, re.I):
        probe = re.sub(r"\b(?:Could|Would|Can|Will|Do|Should|Am) I\b", "reader question", probe)
    return bool(_PERSONAL.search(probe))


def _named_testimonial(text: str) -> bool:
    if re.search(r"\b(?:government|minister|director|authority|commission|UGE|Junta|tribunal|court)\b", text, re.I):
        return False
    name = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ’'\-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ’'\-]+){0,2}"
    verbs = r"(?i:says|said|observes|observed|notes|noted|enthuses|advises|recalls|remembers|shares|adds)"
    return bool(re.search(verbs + r"\s+" + name + r"\b", text)
                or re.search(name + r"\s+(?:of MovingtoSpain\.com\s+)?" + verbs + r"\b", text)
                or re.search(r"(?i:insider tip|personal tip):?\s*(?i:from|by)\s+" + name, text))


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\xa0", " ").replace("\u200b", "").replace("\ufeff", "")
    return "\n".join(re.sub(r"[\t \r\f\v]+", " ", line).strip() for line in value.split("\n") if line.strip()).strip()


def _raw_text(node: Tag | NavigableString) -> str:
    """Collect first, normalize once: whitespace inside inline tags is meaningful."""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return str(node)
    if node.name in _SKIP_TEXT:
        return ""
    if node.name == "br":
        return "\n"
    parts: list[str] = []
    for child in node.children:
        value = _raw_text(child)
        if isinstance(child, Tag) and child.name in _BLOCK_TAGS:
            parts.append("\n" + value + "\n")
        else:
            parts.append(value)
    return "".join(parts)


def _text(node: Tag | NavigableString) -> str:
    return _normalize(_raw_text(node))


def _fold(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)).casefold()


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _official(url: str) -> bool:
    host = _hostname(url)
    return host.endswith((".gob.es", ".gov", ".gov.uk", ".gc.ca", ".gouv.fr", ".europa.eu")) or host in {
        "boe.es", "juntaex.es", "seg-social.es", "ine.es", "europa.eu", "agenciatributaria.es", "sede.agenciatributaria.gob.es",
    }


class _Cleaner:
    def __init__(self, html: str, url: str):
        self.soup = BeautifulSoup(html, "html.parser")
        # No generic body fallback: error pages must never replace a stored article.
        candidates = self.soup.select("article .entry-content.single-content")
        if len(candidates) != 1:
            raise ExtractionError(f"Expected one article body, found {len(candidates)}")
        self.root = candidates[0]
        self.url = url
        self.orders = {id(node): i for i, node in enumerate(self.root.find_all(True), start=1)}
        self.sections: list[tuple[int, str]] = []
        self.heading_boundaries: list[dict] = []
        self.recorded_links: set[int] = set()
        self.author_terms: set[str] = set()
        self.affiliate_terms: set[str] = set()
        h1 = self.soup.find("h1")
        if h1 is None or not _text(h1):
            raise ExtractionError("Article has no title")
        canonical = self.soup.select_one('link[rel="canonical"]')
        language = (self.soup.html.get("lang", "en") if self.soup.html else "en")
        self.result = ExtractionResult(
            url=url,
            canonical_url=urljoin(url, canonical.get("href", url)) if canonical else url,
            title=_text(h1),
            language=language,
            original_text=_text(self.root),
            cleaner_version="1.2.0",
        )
        for field, name in [("published_at", "article:published_time"), ("modified_at", "article:modified_time")]:
            tag = self.soup.find("meta", attrs={"property": name})
            if tag:
                setattr(self.result, field, tag.get("content"))
        article = self.root.find_parent("article")
        metadata: dict[str, Any] = {
            "content_selector": "article .entry-content.single-content",
            "categories": [c[9:] for c in article.get("class", []) if c.startswith("category-")],
            "tags": [c[4:] for c in article.get("class", []) if c.startswith("tag-")],
            "source_post_id": article.get("id"),
            "authors": [],
            "reviewers": [],
        }
        for anchor in self.soup.select('a[href*="/writers/"], a[href*="/reviewers/"]'):
            role = "authors" if "/writers/" in anchor["href"] else "reviewers"
            name = re.split(r"[,–—]|\s+\(", _text(anchor))[0].strip()
            name = re.sub(r"\s+(?:LLB|LLM|PhD)$", "", name)
            if name and name not in metadata[role]:
                metadata[role].append(name)
                self._register_name(name)
        for quote in self.root.select("blockquote"):
            # Testimonial names are often in an ordinary final paragraph, not cite.
            paragraphs = quote.find_all("p", recursive=False)
            if len(paragraphs) > 1:
                last = _text(paragraphs[-1])
                match = re.match(r"^([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){0,3})\s*[,–—]", last)
                if match and len(last) < 240 and not any(_official(a.get("href", "")) for a in quote.select("a[href]")):
                    self._register_name(match.group(1))
            if not any(_official(a.get("href", "")) for a in quote.select("a[href]")):
                for line in _text(quote).splitlines()[1:]:
                    match = re.match(r"^([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){0,3})\s*[,–—]", line)
                    if match and len(line) < 240:
                        self._register_name(match.group(1))
                for cite in quote.select("cite"):
                    name = re.split(r"[,–—]", _text(cite))[0].strip()
                    if re.fullmatch(r"[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ]+){1,3}", name):
                        self._register_name(name)
        for anchor in self.root.select('a[rel~="sponsored"], a[href*="/go/"]'):
            path = urlsplit(anchor.get("href", "")).path
            if "/go/" in path:
                slug = path.split("/go/", 1)[1].strip("/")
                if len(slug) >= 5:
                    self.affiliate_terms.add(slug.replace("-", ""))
        self.result.metadata = metadata

    def _register_name(self, name: str) -> None:
        self.author_terms.add(_fold(name))
        for part in name.split():
            if len(part) >= 4 and part not in {"Moving", "Spain", "Owner", "Writer", "Partner"}:
                self.author_terms.add(_fold(part))

    def _has_author(self, text: str) -> bool:
        folded = _fold(text)
        return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", folded) for term in self.author_terms)

    def _selector(self, node: Tag) -> str:
        if node.get("id"):
            return f'{node.name}#{node["id"]}'
        return node.name + "".join("." + c for c in node.get("class", [])[:3])

    def _event(self, node: Tag, reason: str, text: str | None = None, review: bool = False, **details: Any) -> None:
        self.result.events.append({
            "source_order": self.orders.get(id(node), 0), "reason": reason,
            "text": _text(node) if text is None else text, "selector": self._selector(node),
            "requires_review": review, "prev_block_key": None, "next_block_key": None,
            "details": {"section_path": [name for _, name in self.sections], "action": "quarantined" if review else "removed", **details},
        })
        if review and "needs_editorial_review" not in self.result.quality_flags:
            self.result.quality_flags.append("needs_editorial_review")

    def _link_kind(self, anchor: Tag, url: str) -> str:
        if any(s in url for s in ["/writers/", "/reviewers/"]):
            return "author"
        if self._commercial_link(anchor):
            return "commercial"
        if anchor.get("href", "").startswith("#"):
            return "section_reference"
        return "internal_reference" if _hostname(url) == _hostname(self.url) else "external_reference"

    def _commercial_link(self, anchor: Tag) -> bool:
        url = urljoin(self.url, anchor.get("href", ""))
        path = urlsplit(url).path.lower()
        return "sponsored" in anchor.get("rel", []) or (
            _hostname(url) == _hostname(self.url) and any(part in path for part in (
                "/services/", "/go/", "/decide/", "/relocate/", "/thrive/", "/membership", "/email-preferences", "/newsletter", "/packages/",
            ))
        )

    def _links(self, node: Tag, key: str | None, kept_text: str = "") -> None:
        anchors = ([node] if node.name == "a" and node.has_attr("href") else []) + node.select("a[href]")
        for anchor in anchors:
            if id(anchor) in self.recorded_links:
                continue
            self.recorded_links.add(id(anchor))
            href = anchor.get("href", "")
            if not href or urlsplit(href).scheme not in {"", "http", "https"}:
                continue
            url = urljoin(self.url, href)
            label = _text(anchor)
            retained = bool(key and label and _fold(label) in _fold(kept_text))
            self.result.links.append({
                "block_key": key if retained else None, "source_order": self.orders.get(id(anchor), 0),
                "url": url, "anchor": label, "kind": self._link_kind(anchor, url),
                "disposition": "retained" if retained else "removed",
            })

    def _discard(self, node: Tag, reason: str, review: bool = False, **details: Any) -> None:
        self._event(node, reason, review=review, **details)
        self._links(node, None)

    def _classify(self, text: str, *, heading: bool = False) -> str | None:
        if _REFERRAL.search(text):
            return "article_referral"
        if _PROMO.search(text):
            return "commercial_cta"
        if _EDITORIAL.search(text) or self._has_author(text) or _named_testimonial(text):
            return "editorial_attribution"
        if any(term in _fold(text).replace("-", "") for term in self.affiliate_terms):
            return "commercial_cta"
        if _has_personal(text):
            return "personal_experience"
        if re.search(r"\b(?:MTS|MtS)\b", text):
            return "editorial_attribution"
        return None

    def _clean_text(self, node: Tag, raw: str | None = None, *, official: bool = False) -> str:
        value = _text(node) if raw is None else _normalize(raw)
        if not value:
            return ""
        if official:
            return value
        kept = []
        removed_personal = False
        reader_questions = bool(re.match(r"^(?:Ask|Ask yourself|Consider these questions)\s*:", value, re.I))
        # Split only at clear sentence/line boundaries, never rewrite a clause.
        # A nav tail can share a <p> with genuine prose.
        value = re.sub(r"(?i)(?<!^)(?=\bJump to:)", "\n", value)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý“\"‘])|\n+", value):
            sentence = sentence.strip()
            if not sentence:
                continue
            reason = self._classify(sentence)
            if reader_questions and reason == "personal_experience" and re.match(r"^(?:Could|Would|Can|Will|Do|Should|Am) I\b.*\?$", sentence):
                reason = None
            if not reason and any(self._commercial_link(a) and _text(a) and _text(a) in sentence for a in node.select("a[href]")):
                reason = "commercial_cta"
            if not reason and re.search(r"\b(?:read|see|browse|review|check out)\b", sentence, re.I):
                referenced = [a for a in node.select("a[href]") if _text(a) and _text(a) in sentence
                              and self._link_kind(a, urljoin(self.url, a["href"])) == "internal_reference"]
                if referenced and re.match(r"^(?:For\b.*?[,;]\s*)?(?:read|see|browse|review|check out)\b", sentence, re.I):
                    reason = "article_referral"
            if reason:
                self._event(node, reason, sentence, reason in {"personal_experience", "editorial_attribution"}, scope="sentence")
                removed_personal |= reason in {"personal_experience", "editorial_attribution"}
            else:
                kept.append(sentence)
        if removed_personal:
            independent = []
            for sentence in kept:
                if re.match(r"^(?:This|That|These|Those|It|They|He|She|The renewal|The approval|The request|The application)\b", sentence):
                    self._event(node, "unsupported_context", sentence, True, detail="Sentence depends on removed personal case")
                else:
                    independent.append(sentence)
            kept = independent
        return " ".join(kept)

    def _add(self, node: Tag, kind: str, text: str, target: list[dict], *, data: dict | None = None, flags: list[str] | None = None, level: int | None = None) -> dict | None:
        if not text:
            self._links(node, None)
            return None
        order = self.orders.get(id(node), 0)
        block = {
            "key": f"b{order:06d}", "position": len(target), "source_order": order,
            "kind": kind, "text": text, "heading_level": level,
            "section_path": [name for _, name in self.sections], "data": data or {}, "flags": flags or [],
        }
        target.append(block)
        self._links(node, block["key"], text)
        return block

    def _list_data(self, node: Tag, *, official: bool = False) -> dict:
        items = []
        for li in node.find_all("li", recursive=False):
            chunks = []
            nested = []
            for child in li.children:
                if isinstance(child, Tag) and child.name in {"ul", "ol"}:
                    nested.append(self._list_data(child, official=official))
                else:
                    chunks.append(_raw_text(child))
            raw = _normalize("".join(chunks))
            clean = self._clean_text(li, raw, official=official)
            if not clean:
                if nested:
                    self._event(li, "unsupported_context", review=True, detail="Nested list lost its parent item")
                continue
            items.append({"text": clean, "children": [n for n in nested if n["items"]]})
        try:
            start = int(node.get("start", 1))
        except ValueError:
            start = 1
        return {"ordered": node.name == "ol", "start": start, "items": items}

    def _list_text(self, data: dict, depth: int = 0) -> str:
        lines = []
        for index, item in enumerate(data["items"], data.get("start", 1)):
            bullet = f"{index}." if data["ordered"] else "-"
            lines.append("  " * depth + bullet + " " + item["text"])
            for child in item["children"]:
                lines.append(self._list_text(child, depth + 1))
        return "\n".join(lines)

    def _table(self, node: Tag, target: list[dict]) -> None:
        table = node if node.name == "table" else node.find("table")
        rows: list[list[str]] = []
        cells: list[dict] = []
        header_rows = []
        carried: dict[int, tuple[int, str]] = {}
        unsafe = False
        edited = False
        for r, tr in enumerate(table.find_all("tr")):
            if tr.find_parent("table") is not table:
                continue
            row: dict[int, str] = {}
            future: dict[int, tuple[int, str]] = {}
            for column, (remaining, text) in carried.items():
                row[column] = text
                if remaining > 1:
                    future[column] = (remaining - 1, text)
            column = 0
            source_cells = tr.find_all(["th", "td"], recursive=False)
            if source_cells and all(c.name == "th" for c in source_cells):
                header_rows.append(r)
            for cell in source_cells:
                while column in row:
                    column += 1
                raw = _text(cell)
                text = self._clean_text(cell)
                if raw and not text:
                    unsafe = True
                if raw != text:
                    edited = True
                try:
                    rowspan = min(1000, max(1, int(cell.get("rowspan", 1))))
                    colspan = min(1000, max(1, int(cell.get("colspan", 1))))
                except ValueError:
                    rowspan, colspan = 1, 1
                    self._event(cell, "malformed_table_span", review=True)
                cells.append({"row": r, "column": column, "rowspan": rowspan, "colspan": colspan, "header": cell.name == "th", "text": text})
                for offset in range(colspan):
                    row[column + offset] = text
                    if rowspan > 1:
                        future[column + offset] = (rowspan - 1, text)
                column += colspan
            rows.append([row.get(c, "") for c in range(max(row, default=-1) + 1)])
            carried = future
        if unsafe:
            self._discard(node, "table_context_loss", True, detail="A nonempty cell would disappear after cleaning; entire table quarantined")
            return
        if not rows:
            self._discard(node, "empty_table")
            return
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        caption = node.find("figcaption") if node.name == "figure" else None
        caption_text = self._clean_text(caption) if caption else ""
        text = "\n".join(" | ".join(cell.replace("\n", " / ") for cell in row) for row in rows)
        if caption_text:
            text += "\n" + caption_text
        flags = ["table_text_removed"] if edited else []
        if len(rows) == 1 and not header_rows:
            flags.append("table_fragment_no_header")
        self._add(node, "table", text, target, data={"rows": rows, "header_rows": header_rows, "cells": cells, "caption": caption_text}, flags=flags)

    def _faq(self, node: Tag, target: list[dict]) -> None:
        question = node.select_one(".kt-blocks-accordion-title, .rank-math-question")
        answer = node.select_one(".kt-accordion-panel-inner, .rank-math-answer")
        if not question or not answer:
            self._discard(node, "malformed_faq", True)
            return
        question_text = _text(question)
        question_reason = self._classify(question_text)
        if question_reason == "commercial_cta" and not (
            re.search(r"\b(?:Moving to Spain|MTS)\b", question_text)
            or re.search(r"\b(?:our|subscribe|newsletter|consultation|package|membership)\b", question_text, re.I)
            or any(self._commercial_link(a) for a in node.select("a[href]"))
        ):
            # A generic comparison or appointment question is not an advert
            # merely because it names a service; answers are still cleaned.
            question_reason = None
        if question_reason in {"commercial_cta", "article_referral", "editorial_attribution"}:
            self._discard(node, question_reason, question_reason == "editorial_attribution")
            return
        if re.search(r"\b(?:we|our|ours)\b", question_text, re.I) or self._has_author(question_text):
            self._discard(node, "editorial_faq", True, question=question_text)
            return
        old_sections = list(self.sections)
        old_boundaries = len(self.heading_boundaries)
        parts: list[dict] = []
        self._walk(answer, parts)
        self.sections = old_sections
        del self.heading_boundaries[old_boundaries:]
        if not parts:
            self._discard(node, "unanswered_question", True, question=question_text)
            return
        text = question_text + "\n" + "\n\n".join(p["text"] for p in parts)
        faq = self._add(node, "faq", text, target, data={
            "question": question_text,
            "answer_blocks": [{k: p[k] for k in ("kind", "text", "data", "flags", "heading_level")} for p in parts],
        })
        nested_keys = {p["key"] for p in parts}
        for link in self.result.links:
            if link["block_key"] in nested_keys:
                link["block_key"] = faq["key"]

    def _quote(self, node: Tag, target: list[dict]) -> None:
        text = _text(node)
        official = any(_official(urljoin(self.url, a.get("href", ""))) for a in node.select("a[href]"))
        preceding = node.find_previous_sibling()
        previous_text = _text(preceding) if preceding else ""
        introduced = bool(preceding and preceding.name == "p" and (
            (self._has_author(previous_text) and re.search(r"\b(?:gives|says|said|puts|describes|explains|example|observation|comparison)\b", previous_text, re.I))
            or re.search(r"\b(?:puts it|said it|summed up|describes? it|told us)\b", previous_text, re.I)
        ))
        attributed = bool(node.select_one('a[href*="/writers/"], a[href*="/reviewers/"]')) or self._has_author(text) or bool(_EDITORIAL.search(text)) or (not official and _named_testimonial(text))
        # A general disclaimer with one commercial sentence is not a testimonial.
        if not attributed and text.lower().startswith(("important:", "note:", "warning:")):
            clean = self._clean_text(node)
            self._add(node, "quote", clean, target, flags=["technical_notice"])
            return
        if attributed or (not official and (introduced or _has_personal(text) or node.find("cite"))):
            self._discard(node, "editorial_quote", True)
            return
        if not official and re.match(r'^[“"‘]', text):
            self._discard(node, "ambiguous_quote", True, detail="Quotation has no identified institutional source")
            return
        if not official and (_PROMO.search(text) or any(self._commercial_link(a) for a in node.select("a[href]"))):
            # Technical notices can contain a promotional sentence; retain the notice.
            if text.lower().startswith(("important:", "note:", "warning:")):
                clean = self._clean_text(node)
                self._add(node, "quote", clean, target, flags=["quote_promotion_removed"])
            else:
                self._discard(node, "commercial_cta")
            return
        if official:
            self._add(node, "quote", text, target, flags=["institutional_quote", "verify_before_adaptation"])
        else:
            # Lists inside a technical blockquote remain structured.
            parts: list[dict] = []
            for child in node.children:
                self._walk(child, parts)
            clean = "\n\n".join(p["text"] for p in parts)
            quote = self._add(node, "quote", clean, target, data={"content_blocks": [{k: p[k] for k in ("kind", "text", "data", "flags")} for p in parts]}, flags=["unattributed_quote"])
            if quote:
                keys = {p["key"] for p in parts}
                for link in self.result.links:
                    if link["block_key"] in keys:
                        link["block_key"] = quote["key"]

    def _walk(self, node: Tag | NavigableString, target: list[dict]) -> None:
        if isinstance(node, (Comment, NavigableString)):
            return
        classes = set(node.get("class", []))
        if node.name in {"script", "style", "noscript", "template", "title", "hr"}:
            return
        if node.name == "nav" or "kb-table-of-content-nav" in classes:
            self._discard(node, "navigation")
            return
        if node.get("id") == "mts-residency-check" or "mts-spain-map" in classes:
            self._discard(node, "interactive_widget", True)
            return
        if "kt-accordion-pane" in classes or "rank-math-list-item" in classes:
            self._faq(node, target)
            return
        if node.name in {"img", "svg", "picture", "video", "audio", "iframe"} or (node.name == "figure" and not node.find("table")):
            media_text = _text(node) + " " + node.get("alt", "") + " ".join(a.get("alt", "") for a in node.select("img"))
            previous = node.find_previous_sibling()
            context = _text(previous) if previous else ""
            dependent = bool(_MEDIA_DEPENDENCY.search(media_text + " " + context))
            self._discard(node, "media_dependency" if dependent else "media", dependent, alt_text=media_text.strip())
            return
        if node.name in {"form", "input", "select", "textarea", "button"}:
            self._discard(node, "controls")
            return
        if "wp-block-kadence-rowlayout" in classes and (
            any(self._commercial_link(a) for a in node.select("a[href]")) or _PROMO.search(_text(node))
        ):
            self._discard(node, "commercial_cta")
            return
        if node.name == "blockquote":
            self._quote(node, target)
            return
        if node.name == "table" or (node.name == "figure" and node.find("table")):
            self._table(node, target)
            return
        if node.name in {"ul", "ol"}:
            previous = node.find_previous_sibling()
            if previous and re.search(r"\b(?:read next|read also|also read|related (?:articles|guides))\b", _text(previous), re.I):
                self._discard(node, "article_referral")
                return
            data = self._list_data(node)
            self._add(node, "list", self._list_text(data), target, data=data)
            return
        if re.fullmatch(r"h[1-6]", node.name):
            level = int(node.name[1])
            text = _text(node)
            self.heading_boundaries.append({"source_order": self.orders[id(node)], "level": level, "text": text, "key": f'b{self.orders[id(node)]:06d}'})
            while self.sections and self.sections[-1][0] >= level:
                self.sections.pop()
            self.sections.append((level, text))
            reason = self._classify(text, heading=True)
            # Reader questions are not autobiographical author claims.
            if reason == "personal_experience" and re.match(r"^(?:Can|Do|How|What|Where|When|Should|Will|Am|Could)\b.*\b(?:I|my)\b.*\?$", text) and not re.search(r"\b(?:Would Do|Our|We|we|our)\b", text):
                reason = None
            if reason:
                self._discard(node, reason, reason in {"personal_experience", "editorial_attribution"})
            else:
                self._add(node, "heading", text, target, level=level)
            return
        if node.name in {"p", "figcaption", "dt", "dd", "pre"}:
            clean = self._clean_text(node)
            self._add(node, "paragraph", clean, target)
            return
        if node.name == "a":
            # Standalone buttons/cards are references, unlike inline conceptual links.
            self._discard(node, "commercial_cta" if self._commercial_link(node) else "article_referral")
            return
        if not node.find(list(_BLOCK_TAGS | {"table", "figure", "button", "form"})):
            self._add(node, "paragraph", self._clean_text(node), target)
            return
        # Structural wrappers must not be flattened: that would duplicate children.
        for child in node.children:
            self._walk(child, target)

    def run(self) -> ExtractionResult:
        promo_level: int | None = None
        for node in self.root.children:
            if isinstance(node, Tag) and re.fullmatch(r"h[1-6]", node.name):
                level = int(node.name[1])
                if promo_level is not None and level <= promo_level:
                    promo_level = None
                if _PROMO_SECTIONS.search(_text(node)):
                    promo_level = level
            if promo_level is not None and isinstance(node, Tag):
                if re.fullmatch(r"h[1-6]", node.name):
                    self.heading_boundaries.append({"source_order": self.orders[id(node)], "level": int(node.name[1]), "text": _text(node), "key": f'b{self.orders[id(node)]:06d}'})
                self._discard(node, "commercial_section")
            else:
                self._walk(node, self.result.blocks)
        blocks = self.result.blocks
        removed_keys: set[str] = set()
        # A heading owns content only until the next heading of equal/higher level.
        for index, block in enumerate(blocks):
            if block["kind"] != "heading":
                continue
            next_boundary = min((h["source_order"] for h in self.heading_boundaries
                                 if h["source_order"] > block["source_order"] and h["level"] <= block["heading_level"]), default=float("inf"))
            useful = any(b["kind"] != "heading" and block["source_order"] < b["source_order"] < next_boundary for b in blocks[index + 1:])
            if not useful:
                removed_keys.add(block["key"])
                self.result.events.append({
                    "source_order": block["source_order"], "reason": "empty_heading", "text": block["text"],
                    "selector": f'h{block["heading_level"]}', "requires_review": True,
                    "prev_block_key": None, "next_block_key": None,
                    "details": {"section_path": block["section_path"], "action": "removed"},
                })
        self.result.blocks = [b for b in blocks if b["key"] not in removed_keys]
        sections: list[dict] = []
        boundaries = iter(sorted(self.heading_boundaries, key=lambda h: h["source_order"]))
        boundary = next(boundaries, None)
        retained_keys = {b["key"] for b in self.result.blocks}
        for index, block in enumerate(self.result.blocks):
            block["position"] = index
            while boundary and boundary["source_order"] <= block["source_order"]:
                while sections and sections[-1]["level"] >= boundary["level"]:
                    sections.pop()
                sections.append(boundary)
                boundary = next(boundaries, None)
            block["section_path"] = [h["text"] for h in sections if h["key"] in retained_keys]
            block["data"]["source_section_ids"] = ["s" + str(h["source_order"]) for h in sections]
            if any(h["key"] not in retained_keys for h in sections):
                block["flags"].append("removed_section_heading")
        for link in self.result.links:
            if link["block_key"] in removed_keys:
                link["block_key"], link["disposition"] = None, "removed"
        for event in self.result.events:
            prior = [b for b in self.result.blocks if b["source_order"] < event["source_order"]]
            after = [b for b in self.result.blocks if b["source_order"] > event["source_order"]]
            event["prev_block_key"] = prior[-1]["key"] if prior else None
            event["next_block_key"] = after[0]["key"] if after else None
        if not self.result.blocks or len(self.result.clean_text.split()) < 20:
            raise ExtractionError("Article has insufficient reusable text after cleaning")
        if any(e["requires_review"] for e in self.result.events):
            self.result.quality_flags = sorted(set(self.result.quality_flags + ["needs_editorial_review"]))
        self.result.metadata["source_word_count"] = len(self.result.original_text.split())
        self.result.metadata["clean_word_count"] = len(self.result.clean_text.split())
        self.result.metadata["removed_ratio"] = round(max(0, 1 - len(self.result.clean_text.split()) / max(1, len(self.result.original_text.split()))), 4)
        if self.result.metadata["removed_ratio"] > 0.55:
            self.result.quality_flags.append("high_removal_ratio")
        return self.result


def extract_html(html: str, url: str) -> ExtractionResult:
    """Extract one article; fail closed when its content template is unrecognized."""
    return _Cleaner(html, url).run()
