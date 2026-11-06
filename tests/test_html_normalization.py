"""Formatting recovery never rewrites prose or weakens publication validation."""
from itertools import product
import socket

from bs4 import BeautifulSoup
import pytest
import requests

from src.deployment.content import CTA_MARKERS, MAX_HTML_CHARACTERS, validate_semantic_html
from src.generation.html_normalization import NORMALIZATION_POLICY, normalize_generated_html


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Formatting normalization must remain local")
    monkeypatch.setattr(socket, "create_connection", fail)
    monkeypatch.setattr(requests.Session, "request", fail)


def wording(html):
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text().split())


@pytest.mark.parametrize("html", [
    '<h2>Guide</h2>\r\n<p>Exact  spaces &amp; Unicode: Québec, Bogotá.</p>',
    '<h2>Guide</h2><p><strong>Read</strong> <em>carefully</em>.</p>',
    '<p>Read <a title="Guide" href="https://example.test/?a=1&amp;b=2">this guide</a>.</p>',
    '<h2>Guide</h2><p>[CTA_IMMIGRATION_EN]</p><p>Editorial wording.</p>',
    '<table><tr><th scope="row">Cost</th><td colspan="2">€1,234</td></tr></table>',
])
def test_valid_output_remains_byte_for_byte(html):
    result = normalize_generated_html(html)
    assert result["valid"]
    assert result["html"] == html and not result["changed"]
    assert result["operations"] == []
    assert result["policy"] == NORMALIZATION_POLICY


@pytest.mark.parametrize("marker,position", list(product(CTA_MARKERS, ("before", "between", "after"))))
def test_bare_ctas_are_normalized_for_every_destination_and_position(marker, position):
    sections = ["<h2>Guía · Québec</h2>", "<p>El texto editorial permanece sin cambios.</p>"]
    value = f"\n\n[{marker}]\n\n"
    sections.insert({"before": 0, "between": 1, "after": 2}[position], value)
    html = "".join(sections)
    assert not validate_semantic_html(html)["valid"]
    result = normalize_generated_html(html)
    assert result["valid"] and result["changed"]
    assert wording(result["html"]) == wording(html)
    assert result["validation"]["cta_markers"] == [marker]
    soup = BeautifulSoup(result["html"], "html.parser")
    marker_node = soup.find(string=lambda s: s and f"[{marker}]" in s)
    assert marker_node.parent.name == "p"
    assert any(op["operation"] == "wrap_orphan_text_or_inline" for op in result["operations"])
    again = normalize_generated_html(result["html"])
    assert again["html"] == result["html"] and again["valid"] and not again["changed"]


def test_retirement_regression_two_ctas_between_realistic_sections():
    html = ('<h2>Residencia no lucrativa para colombianos</h2>\n'
            '<p>No cambia el contenido original de la API.</p>\n\n[CTA_INMIGRACION_ES]\n\n'
            '<h2>Residencia fiscal en España</h2>\n'
            '<p>Consulte su situación individual.</p>\n\n[CTA_IMPUESTOS_ES]\n\n'
            '<h2>Elegir dónde vivir</h2><p>Compare ciudades.</p>')
    checked = validate_semantic_html(html)
    assert checked["errors"] == ["El texto debe estar dentro de un bloque semántico apropiado."]
    result = normalize_generated_html(html)
    assert result["valid"] and result["original_validation"] == checked
    assert wording(result["html"]) == wording(html)
    assert result["validation"]["cta_markers"] == ["CTA_INMIGRACION_ES", "CTA_IMPUESTOS_ES"]


@pytest.mark.parametrize("fragment", [
    "Texto suelto con €, CAD y COP.",
    '<strong>Énfasis</strong> y <em>detalle</em>.',
    'Antes <a href="https://example.test/?x=1&amp;y=2" title="Ayuda">enlace</a> después.',
    '[CTA_TAXES_EN] y [CTA_TAXES_EN].',
    'Literal &lt;script&gt; y &amp; información.',
])
def test_orphan_text_and_inline_runs_keep_words_links_entities_and_marker_order(fragment):
    html = "<h2>Guía</h2>\n" + fragment + "\n<p>Final del texto.</p>"
    result = normalize_generated_html(html)
    assert result["valid"] and result["changed"]
    assert wording(result["html"]) == wording(html)
    before = BeautifulSoup(html, "html.parser")
    after = BeautifulSoup(result["html"], "html.parser")
    assert [(a.get("href"), a.get("title"), a.get_text()) for a in before.find_all("a")] == [
        (a.get("href"), a.get("title"), a.get_text()) for a in after.find_all("a")]
    assert not after.find("script")


@pytest.mark.parametrize("opening,closing", [("```html", "```"), ("```HTML", "```"), ("```", "```"), ("~~~html", "~~~")])
def test_single_transport_fence_is_removed_without_rewriting_inner_html(opening, closing):
    body = "<h2>Guide</h2><p>Original body.</p>\n[CTA_TAXES_EN]"
    result = normalize_generated_html(f"  {opening}\n{body}\n{closing}\n")
    assert result["valid"] and result["changed"]
    assert wording(result["html"]) == wording(body)
    assert {x["operation"] for x in result["operations"]} >= {"remove_outer_html_fence", "wrap_orphan_text_or_inline"}


@pytest.mark.parametrize("html,paragraphs", [
    ("<div>One</div><div>Two</div>", ["One", "Two"]),
    ("<section>First<div>Nested</div>Last</section>", ["First", "Nested", "Last"]),
    ("<article><h2>Guide</h2><div>First <span>inline</span> sentence.</div></article>", ["First inline sentence."]),
    ("<!DOCTYPE html><html lang=\"es-CO\"><body><main><h2>Guía</h2><p>Texto.</p>[CTA_IMPUESTOS_ES]</main></body></html>", ["Texto.", "[CTA_IMPUESTOS_ES]"]),
])
def test_presentation_containers_are_unwrapped_without_joining_block_paragraphs(html, paragraphs):
    result = normalize_generated_html(html)
    assert result["valid"] and result["changed"]
    soup = BeautifulSoup(result["html"], "html.parser")
    assert [p.get_text().strip() for p in soup.find_all("p")] == paragraphs
    assert not soup.find(["html", "body", "main", "article", "section", "div", "span"])


def test_equivalent_inline_aliases_use_semantic_elements_only():
    html = '<h2>Guide</h2><p><b>Important</b> <i>detail</i> <span>here</span>.</p>'
    result = normalize_generated_html(html)
    assert result["valid"]
    assert result["html"] == '<h2>Guide</h2><p><strong>Important</strong> <em>detail</em> here.</p>'
    assert wording(result["html"]) == wording(html)


@pytest.mark.parametrize("html", [
    '<p style="color:red">Keep style error explicit.</p>',
    '<div class="hidden"><p>Potentially hidden content.</p></div>',
    '<span style="display:none">Hidden</span><p>Visible.</p>',
    '<div id="anchor">Target</div>',
    '<div data-plugin="x"><p>Plugin content</p></div>',
    '<span title="[CTA_TAXES_EN]">Lost marker</span>',
    '<script>alert(1)</script><p>Article.</p>',
    '<style>p{display:none}</style><p>Article.</p>',
    '<p><iframe src="https://example.test"></iframe>Article.</p>',
    '<p><img src="https://example.test/photo.jpg">Article.</p>',
    '<svg><text>Picture</text></svg><p>Article.</p>',
    '<p onclick="alert(1)">Article.</p>',
    '<b onclick="alert(1)">Article.</b>',
    '<p><a href="javascript:alert(1)">Link</a></p>[CTA_TAXES_EN]',
    '<p><a href="&#106;avascript:alert(1)">Link</a></p>',
    '<p><a href="https://user:secret@example.test/">Link</a></p>',
    '<p><a href="https://example.test" href="https://other.test">Link</a></p>',
    '<p><a href="https://example.test/?q=[CTA_TAXES_EN]">Link</a></p>',
    '<p><a href="https://example.test">[CTA_TAXES_EN]</a></p>',
    '<p><a>Missing URL.</a></p>',
    '<p>[CTA_UNKNOWN_EN]</p>',
    '<p>[gallery ids="1,2"]</p>',
    '<!-- wp:custom-plugin --><p>Article.</p>',
    '<!DOCTYPE html PUBLIC "https://example.test/dtd"><p>Article.</p>',
    '<?dangerous processing?><p>Article.</p>',
    '<p><strong>Wrong nesting</p></strong>',
    '<p>Missing closing tag',
    '<p><h2>Nested heading</h2></p>',
    '<p/>',
    '<li>Ambiguous list</li>',
    '<ul>Text outside items<li>Item</li></ul>',
    '<ul><p>Not a list item</p></ul>',
    '<table>Ambiguous text</table>',
    '<p>**Markdown emphasis**</p>',
    '# Markdown heading\n\nArticle text',
    '```json\n{"article": "not HTML"}\n```',
    'Here is the article:\n```html\n<p>Article</p>\n```',
    '<p>Empty next</p><html><p>Invalid nested document</p></html></p>',
    '<html><head><title>Discarded title</title></head><body><p>Article</p></body></html>',
])
def test_unsafe_or_ambiguous_output_stays_rejected_and_original_is_never_discarded(html):
    result = normalize_generated_html(html)
    assert not result["valid"]
    assert result["html"] == html and result["changed"] is False
    assert not result["validation"]["valid"] and result["reason"]
    assert result["operations"] == []


@pytest.mark.parametrize("html", [None, 12, "", "  ", "<p>Null\x00character.</p>", "x" * (MAX_HTML_CHARACTERS + 1)])
def test_invalid_inputs_do_not_enter_repair(html):
    result = normalize_generated_html(html)
    assert not result["valid"] and not result["changed"]
    assert result["html"] == html and result["reason"] == "invalid_input"


def test_cta_only_content_does_not_become_an_editorial_article():
    html = "[CTA_TAXES_EN]"
    result = normalize_generated_html(html)
    assert not result["valid"] and result["html"] == html


def test_table_attributes_remain_unchanged_when_surrounding_text_needs_wrapping():
    html = '<h2>Guide</h2><table><tr><th scope="row">Cost</th><td colspan="2">€1,234</td></tr></table>\n[CTA_TAXES_EN]'
    result = normalize_generated_html(html)
    assert result["valid"]
    before, after = (BeautifulSoup(value, "html.parser") for value in (html, result["html"]))
    assert str(before.table) == str(after.table)


def test_existing_paragraph_whitespace_is_preserved_while_root_cta_is_wrapped():
    paragraph = '<p>Two  spaces\ninside text. <strong>  Bold\tcontent  </strong>    End\u00a0here.</p>'
    html = paragraph + '\n\n[CTA_IMMIGRATION_EN]\n\n' + paragraph
    result = normalize_generated_html(html)
    assert result['valid'] and result['changed']
    assert result['html'].count(paragraph) == 2
    assert wording(result['html']) == wording(html)


@pytest.mark.parametrize('literal', [
    '&lt;script&gt;alert(1)&lt;/script&gt;',
    '&amp;lt;script&amp;gt;alert(1)&amp;lt;/script&amp;gt;',
    '&lt;img src=x onerror=alert(1)&gt;',
    '&lt;div&gt;Text that looks like markup&lt;/div&gt;',
    '&#60;iframe src=javascript:alert(1)&#62;',
])
def test_encoded_markup_cannot_be_promoted_into_elements_during_serialization(literal):
    html = '<h2>Quoted text</h2><p>' + literal + '</p>\n[CTA_TAXES_EN]'
    result = normalize_generated_html(html)
    assert result['valid']
    soup = BeautifulSoup(result['html'], 'html.parser')
    assert not soup.find(['script', 'iframe', 'img', 'div'])
    assert wording(result['html']) == wording(html)


def test_nested_inline_wrappers_preserve_word_order_and_block_separation():
    html = '<div><div>First <span><span>nested</span> inline</span>.</div><div>Second <b>bold <i>inside</i></b>.</div></div>'
    result = normalize_generated_html(html)
    assert result['valid']
    soup = BeautifulSoup(result['html'], 'html.parser')
    assert [p.get_text() for p in soup.find_all('p')] == ['First nested inline.', 'Second bold inside.']
    assert wording(result['html']) == wording(html)


@pytest.mark.parametrize('html', [
    '<p><span><div>Invalid nested block</div></span></p>',
    '<p><div>Invalid nested block</div></p>',
    '<strong><div>Invalid nested block</div></strong>',
])
def test_wrappers_do_not_hide_invalid_block_inside_inline_structure(html):
    result = normalize_generated_html(html)
    assert not result['valid'] and result['html'] == html


def test_deep_nested_transport_markup_fails_safely():
    html = "<div>" * 130 + "Article" + "</div>" * 130
    result = normalize_generated_html(html)
    assert not result["valid"] and result["html"] == html
