"""Portable content and CTA handling remain entirely local and explicit."""
import socket

from bs4 import BeautifulSoup
import pytest
import requests

from src.deployment.content import (
    CTA_MARKERS, MAX_HTML_CHARACTERS, prepare_wordpress_content, validate_semantic_html,
)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local content preparation attempted a network request")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)


def test_semantic_article_stays_unchanged_and_theme_controls_presentation():
    html = '<h2>Moving to Spain</h2>\n<p>A <strong>short paragraph</strong> with <em>emphasis</em>.</p>\n<ul><li>First step</li></ul>'
    checked = validate_semantic_html(html)
    assert checked == {"valid": True, "errors": [], "warnings": [], "cta_markers": []}
    result = prepare_wordpress_content(html, {})
    assert result["ready"] and result["content"] == html


@pytest.mark.parametrize("html", [
    '<h1>Duplicate theme title</h1><p>Source text.</p>',
    '<p style="color:red">Text</p>', '<p class="elementor-heading-title">Text</p>',
    '<p id="fixed-layout">Text</p>', '<div><p>Layout wrapper.</p></div>',
    '<p onclick="alert(1)">Text</p>', '<script>alert(1)</script><p>Text</p>',
    '<style>p{color:red}</style><p>Text</p>', '<p><iframe src="https://example.test"></iframe>Text</p>',
    '<p><img src="https://example.test/photo.jpg">Text</p>',
    '<!-- wp:custom-theme --><p>Text</p>', '<!DOCTYPE html><p>Text</p>',
    '<p>[gallery ids="1,2"]</p>', '<p>[elementor-template id="1"]</p>',
    '<p>**Markdown strong**</p>', '<p>[Markdown link](https://example.test)</p>',
    '```html\n<p>Text</p>\n```', '<p># Markdown heading</p>',
    '<p><strong>Wrong nesting</p></strong>', '<p>Missing close', '<p/>',
    '<ul><p>Must be a list item</p></ul>', '<li>Missing list</li>',
    '<table>Text directly in table</table>', '<p><h2>Nested heading</h2></p>',
    '<p><a href="javascript:alert(1)">Unsafe</a></p>',
    '<p><a href="&#106;avascript:alert(1)">Unsafe</a></p>',
    '<p><a href="data:text/html,anything">Unsafe</a></p>',
    '<p><a href="//example.test">Protocol relative</a></p>',
    '<p><a href="https://user:secret@example.test/">Credentials</a></p>',
    '<p><a href="https://example.test" href="https://other.test">Duplicate</a></p>',
    '<p><a>Missing destination</a></p>',
    '<p><a href="https://example.test"><a href="https://example.test">Nested links</a></a></p>',
    '<p><a href="https://example.test/?q=[gallery]">Shortcode attribute</a></p>',
    '<p>[CTA_UNKNOWN_EN]</p>', '<p><a href="https://example.test/?x=[CTA_TAXES_EN]">Tax</a></p>',
    '<p><a href="https://example.test">[CTA_TAXES_EN]</a></p>',
    '<p>Null\x00character</p>', 'Unstructured plain text', '<p></p>', '<p>[CTA_TAXES_EN]</p>',
])
def test_incompatible_html_blocks_payload_without_silent_repair(html):
    validation = validate_semantic_html(html)
    assert not validation["valid"] and validation["errors"]
    result = prepare_wordpress_content(html, {})
    assert result["ready"] is False and result["content"] is None


def test_all_six_cta_markers_are_replaced_only_as_text_with_spaces_preserved():
    html = '<h2>Guidance</h2><p>Before  ' + ' \n '.join(f'[{marker}]' for marker in CTA_MARKERS) + '  after.</p>'
    links = {marker: {"url": f"https://example.test/{index}/", "text": f"Service {index}"} for index, marker in enumerate(CTA_MARKERS)}
    result = prepare_wordpress_content(html, links)
    assert result["ready"]
    soup = BeautifulSoup(result["content"], "html.parser", preserve_whitespace_tags={"p"})
    assert len(soup.find_all("a")) == 6
    assert soup.p.get_text() == 'Before  ' + ' \n '.join(f'Service {i}' for i in range(6)) + '  after.'
    assert result["replaced_ctas"] == list(CTA_MARKERS)
    assert not result["validation"]["cta_markers"]


def test_repeated_markers_remain_explicit_and_do_not_join_words():
    html = '<h2>Guidance</h2><p>Read [CTA_TAXES_EN], then [CTA_TAXES_EN] today.</p>'
    result = prepare_wordpress_content(html, {"CTA_TAXES_EN": {"url": "https://example.test/taxes/", "text": "tax guidance"}})
    assert result["ready"]
    assert BeautifulSoup(result["content"], "html.parser").p.get_text() == 'Read tax guidance, then tax guidance today.'
    assert result["replaced_ctas"] == ["CTA_TAXES_EN"]


def test_cta_resolution_preserves_existing_spaces_between_inline_elements():
    html = '<h2>Guide</h2><p>Read <strong>this</strong>  <em>carefully</em> [CTA_TAXES_EN].</p>'
    result = prepare_wordpress_content(html, {"CTA_TAXES_EN": {"url": "https://example.test", "text": "Tax guide"}})
    assert result["ready"]
    assert '</strong>  <em>' in result["content"]


@pytest.mark.parametrize("links", [
    {}, {"CTA_TAXES_EN": {}}, {"CTA_TAXES_EN": {"url": "https://example.test"}},
    {"CTA_TAXES_EN": {"url": "http://example.test", "text": "Tax"}},
    {"CTA_TAXES_EN": {"url": "javascript:alert(1)", "text": "Tax"}},
    {"CTA_TAXES_EN": {"url": "https://example.test", "text": ""}},
    {"CTA_TAXES_EN": {"url": "https://example.test", "text": "Tax", "style": "red"}},
    {"CTA_TAXES_EN": {"url": "https://example.test", "text": "[CTA_TAXES_FR]"}},
    {"[CTA_TAXES_EN]": {"url": "https://example.test", "text": "Tax"}},
    None,
])
def test_missing_or_invalid_cta_configuration_never_invents_a_link(links):
    html = '<h2>Taxes</h2><p>Learn more: [CTA_TAXES_EN].</p>'
    result = prepare_wordpress_content(html, links)
    assert not result["ready"] and result["content"] is None
    assert result["validation"]["errors"]


def test_entities_and_configured_anchor_text_cannot_inject_html():
    html = '<h2>Guide</h2><p>Literal &lt;script&gt; text &amp; details. [CTA_TAXES_EN]</p>'
    text = '<img src=x onerror="alert(1)"> & guidance'
    result = prepare_wordpress_content(html, {"CTA_TAXES_EN": {"url": "https://example.test/?a=1&b=2", "text": text}})
    assert result["ready"]
    soup = BeautifulSoup(result["content"], "html.parser")
    assert not soup.find_all(["img", "script"])
    assert soup.a.get_text() == text
    assert soup.a["href"] == 'https://example.test/?a=1&b=2'
    assert 'Literal <script> text & details.' in soup.p.get_text()


def test_semantic_table_uses_only_structural_attributes():
    html = '<h2>Comparison</h2><table><caption>Fees</caption><thead><tr><th scope="col">Type</th></tr></thead><tbody><tr><td colspan="1">General</td></tr></tbody></table>'
    assert validate_semantic_html(html)["valid"]
    assert not validate_semantic_html(html.replace('colspan="1"', 'width="500"'))["valid"]


def test_input_type_and_size_are_checked_before_parsing():
    assert not validate_semantic_html(None)["valid"]
    assert not validate_semantic_html(12)["valid"]
    assert not validate_semantic_html('x' * (MAX_HTML_CHARACTERS + 1))["valid"]
