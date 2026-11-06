"""Shared serialization instructions, independent of editable editorial prompts.

This contract reduces formatting mistakes. Validation and deterministic local
normalization remain necessary: natural-language instructions cannot guarantee
that a model will always return compliant markup.
"""
from src.deployment.content import ALLOWED_TAGS


OUTPUT_CONTRACT_VERSION = "semantic-body-v1"


def output_instructions(locale):
    """Return technical output rules for every initial article generation.

    The locale selects examples only; it does not translate or alter editorial
    content. Importing the shared CTA mapping here avoids a second marker list.
    """
    from src.deployment.repository import CTA_NAMES

    markers = CTA_NAMES.get(locale, ())
    marker_rules = (
        "The only CTA markers permitted for this destination are "
        + ", ".join(f"[{marker}]" for marker in markers)
        + ". Include a marker only when the editorial instructions and the topic call for it. "
        "A standalone CTA MUST be its own HTML paragraph, exactly like these valid examples: "
        + " ".join(f"<p>[{marker}]</p>" for marker in markers)
        + ". A marker may also be literal text inside an existing paragraph. "
        "NEVER put a marker on a bare line outside HTML, inside an <a>, or in an attribute. "
        "Do not invent CTA URLs or emit markers for another destination."
        if markers else
        "Do not invent CTA markers or CTA URLs. If no supported marker is configured for this destination, omit CTAs."
    )
    return "\n\n" + f"""APPLICATION OUTPUT CONTRACT ({OUTPUT_CONTRACT_VERSION})
These serialization rules apply to every editorial template. They constrain HTML only; preserve the approved scope, meaning, facts, language and editorial instructions.
Return ONLY a raw HTML fragment for the WordPress article body. No introduction about the response, JSON, Markdown, code fences, XML, document declarations, comments, <html>, <head>, <body>, title field or <h1>. WordPress stores the title and featured image separately.
Allowed tags: {', '.join('<' + tag + '>' for tag in sorted(ALLOWED_TAGS))}.
At the fragment root use only <p>, <h2>, <h3>, <ul>, <ol>, <blockquote> or <table>. ALL visible text must belong to a suitable semantic block. Never leave ordinary text, inline formatting or a CTA directly between root-level blocks.
Use <p> for each paragraph, <h2> for sections and <h3> for subsections. Keep blocks outside paragraphs and headings; these may contain only text and inline <strong>, <em>, <a> or <br>. Open and close every tag in the correct order; only <br> is void.
Lists must contain <li> children; each <li> must be inside <ul> or <ol>. Tables must use valid table structure: <tr> inside <table>, <thead>, <tbody> or <tfoot>, and <th>/<td> inside <tr>. No bare text directly inside list containers, table containers, rows or table sections.
Do not include styles, CSS, classes, IDs, event handlers, images, media, forms, scripts, shortcodes, page-builder elements or other attributes. An <a> may have only href and optional title; href must be an absolute HTTPS URL without credentials, and links cannot nest. Table cells may use only positive colspan/rowspan from 1 to 100; <th> may also use scope="row", "col", "rowgroup" or "colgroup".
{marker_rules}
Before returning the fragment, check that every paragraph and CTA is enclosed in valid HTML, that nesting is balanced, and that there is no text outside semantic blocks. Do not output this checklist or any editorial verification notes.
"""
