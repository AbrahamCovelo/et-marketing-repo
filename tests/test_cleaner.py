"""Regression tests for failure modes found in the fifteen reviewed articles."""
import json
from pathlib import Path

import pytest

from src.extraction.cleaner import ExtractionError, extract_html


BASE = "https://movingtospain.com/example/"
FILLER = "The application requires several documents. Check the published requirements carefully and retain copies of every submitted document for future administrative procedures."


def page(body, extra=""):
    return f'''<html lang="en-GB"><head><link rel="canonical" href="{BASE}">
    <meta property="article:published_time" content="2026-01-01T00:00:00+00:00"></head>
    <body><h1>Example article</h1>{extra}<article id="post-12" class="single-entry category-visas">
    <div class="entry-content single-content"><p>{FILLER}</p>{body}</div></article>
    <article><div class="entry-content">UNRELATED CARD CONTENT</div></article></body></html>'''


def extract(body, extra=""):
    return extract_html(page(body, extra), BASE)


def test_explicit_article_root_and_metadata_fail_closed():
    r = extract('<nav>Table of contents duplicated heading</nav><h2>Required documents</h2><p>A valid passport is essential.</p>')
    assert "UNRELATED" not in r.clean_text
    assert "Table of contents" not in r.clean_text
    assert r.language == "en-GB"
    assert r.published_at == "2026-01-01T00:00:00+00:00"
    assert r.metadata["categories"] == ["visas"]
    with pytest.raises(ExtractionError):
        extract_html('<h1>Verify you are human</h1><main>Challenge text</main>', BASE)
    with pytest.raises(ExtractionError):
        extract_html(page('<div class="entry-content single-content">ambiguous</div>'), BASE)


def test_inline_word_boundaries_entities_and_line_breaks():
    r = extract('<p>Visit <strong>Spain </strong>today. I<em>f</em> eligible, apply. A&nbsp;valid &amp; current passport is required.<br>Keep a copy.</p>')
    assert "Visit Spain today." in r.clean_text
    assert "If eligible" in r.clean_text
    assert "A valid & current passport" in r.clean_text
    assert "Spaintoday" not in r.clean_text


def test_promotional_faq_questions_do_not_keep_sales_answers():
    r = extract('''<div id="rank-math-faq">
    <div class="rank-math-list-item"><h3 class="rank-math-question">Does Moving to Spain arrange custom scouting trips?</h3>
    <div class="rank-math-answer">Yes. The service includes a tailored itinerary and a planning session.</div></div>
    <div class="rank-math-list-item"><h3 class="rank-math-question">What does a Moving to Spain custom scouting trip include?</h3>
    <div class="rank-math-answer">A personalized plan and access to local partners.</div></div>
    <div class="rank-math-list-item"><h3 class="rank-math-question">Could I visit several cities during my trip?</h3>
    <div class="rank-math-answer">Yes. Allow enough time in each city to compare transport and housing.</div></div>
    <div class="rank-math-list-item"><h3 class="rank-math-question">What is the difference between a group and a custom scouting trip?</h3>
    <div class="rank-math-answer">Group trips follow a fixed itinerary; custom trips follow the traveler's priorities.</div></div>
    </div>''')
    assert "Moving to Spain" not in r.clean_text
    assert "tailored itinerary" not in r.clean_text
    assert "personalized plan" not in r.clean_text
    assert "Could I visit several cities" in r.clean_text
    assert "Allow enough time" in r.clean_text
    assert "What is the difference" in r.clean_text
    assert "Group trips follow a fixed itinerary" in r.clean_text
    assert len([e for e in r.events if e['reason'] == 'commercial_cta']) == 2


def test_mixed_paragraph_removes_referral_not_useful_internal_anchor_or_local_table_reference():
    r = extract('''<p>Applicants need a <a href="/nie/">NIE number</a> for this procedure.
    Read our <a href="/other-guide/">complete guide</a> for more details.
    See the table below for the required documents.</p>''')
    assert "Applicants need a NIE number" in r.clean_text
    assert "Read our" not in r.clean_text
    assert "See the table below" in r.clean_text
    assert next(l for l in r.links if l["url"].endswith("/nie/"))["disposition"] == "retained"
    assert next(l for l in r.links if l["url"].endswith("/other-guide/"))["disposition"] == "removed"


def test_authored_quote_and_personal_sentences_quarantined_without_rewriting():
    r = extract('''<p>Mike Bailey gives an example:</p><blockquote><p>My renewal began last year.</p><cite>Mike Bailey</cite></blockquote>
    <p>The permit lasts three years. I renewed mine in May. Documents must remain valid.</p>''',
    '<a href="/writers/mike-bailey/">Mike Bailey</a>')
    assert "Mike" not in r.clean_text
    assert "I renewed" not in r.clean_text
    assert "The permit lasts three years. Documents must remain valid." in r.clean_text
    assert any(e["reason"] == "editorial_quote" and e["requires_review"] for e in r.events)
    assert any(e["text"] == "I renewed mine in May." for e in r.events)
    assert r.metadata["authors"] == ["Mike Bailey"]


def test_official_quote_with_first_person_and_technical_quote_survive():
    r = extract('''<blockquote><p>We are preparing a new call for applications.</p>
    <cite><a href="https://www.juntaex.es/program">Regional administration</a></cite></blockquote>
    <blockquote><p>EU new vehicle rule. Vehicles under six months old require special treatment.</p>
    <ul><li>Check the age.</li><li>Check the mileage.</li></ul></blockquote>''')
    quotes = [b for b in r.blocks if b["kind"] == "quote"]
    assert len(quotes) == 2
    assert "institutional_quote" in quotes[0]["flags"]
    assert "We are preparing" in quotes[0]["text"]
    assert any(p["kind"] == "list" for p in quotes[1]["data"]["content_blocks"])
    assert next(l for l in r.links if "juntaex" in l["url"])["kind"] == "external_reference"


def test_kadence_and_rankmath_faq_keep_questions_hidden_answers_nested_tables_once():
    r = extract('''<div class="kt-accordion-pane"><div><button><span class="kt-blocks-accordion-title">Can I apply?</span></button></div>
    <div class="kt-accordion-panel-hidden"><div class="kt-accordion-panel-inner"><p>Yes, with valid documents.</p>
    <table><tr><th>Item</th><th>Requirement</th></tr><tr><td>Passport</td><td>Valid</td></tr></table></div></div></div>
    <div id="rank-math-faq"><div class="rank-math-list-item"><h3 class="rank-math-question">When does it expire?</h3>
    <div class="rank-math-answer"><p>The date is shown on the permit. Read our <a href="/renewal/">renewal guide</a>.</p></div></div></div>''')
    faqs = [b for b in r.blocks if b["kind"] == "faq"]
    assert len(faqs) == 2
    assert r.clean_text.count("Can I apply?") == 1
    assert any(b["kind"] == "table" for b in faqs[0]["data"]["answer_blocks"])
    assert "renewal guide" not in r.clean_text
    assert "The date is shown" in faqs[1]["text"]


def test_nested_lists_and_span_tables_keep_relations_fragment_is_not_a_header():
    r = extract('''<ol start="3"><li>Documents<ul><li>Passport</li><li>Certificate</li></ul></li><li>Appointment</li></ol>
    <figure class="wp-block-table"><table><tr><th>Document</th><th>Type</th></tr>
    <tr><td rowspan="2">Passport</td><td>Original</td></tr><tr><td>Copy</td></tr>
    <tr><td colspan="2">Bring all documents</td></tr></table><figcaption>Documents to carry.</figcaption></figure>
    <table><tr><td>Madrid</td><td>900 km</td></tr></table>''')
    list_block = next(b for b in r.blocks if b["kind"] == "list")
    assert list_block["text"].startswith("3. Documents\n  - Passport")
    assert list_block["data"]["items"][0]["children"][0]["items"][1]["text"] == "Certificate"
    tables = [b for b in r.blocks if b["kind"] == "table"]
    assert tables[0]["data"]["rows"][2] == ["Passport", "Copy"]
    assert tables[0]["data"]["rows"][3] == ["Bring all documents", "Bring all documents"]
    assert tables[1]["data"]["header_rows"] == []
    assert "table_fragment_no_header" in tables[1]["flags"]


def test_personal_table_cell_is_not_silently_replaced_with_empty_text():
    r = extract('''<table><tr><th>Fee</th><th>Cost</th></tr><tr><td>Registration</td><td>I paid 300 euros.</td></tr></table>''')
    assert not any(b["kind"] == "table" for b in r.blocks)
    assert any(e["reason"] == "table_context_loss" and "300 euros" in e["text"] for e in r.events)
    assert "I paid" not in json.dumps(r.blocks)


def test_promotional_sections_widget_empty_heading_and_media_gap_are_audited():
    r = extract('''<h2>Useful section</h2><p>Documents need to be valid.</p>
    <div class="wp-block-kadence-rowlayout"><h3>Call now</h3><a href="/services/tax/">Book a consultation</a></div>
    <h2>Spain Tax Residency Check Tool</h2><div id="mts-residency-check">Start the check<input></div>
    <h2>Ranking of 38 towns</h2><figure><img alt="Ranking of 38 towns" src="ranking.png"></figure>
    <h2>Run your own move, or hand it over?</h2><p>Custom services and promotional copy.</p>
    <h2>After arrival</h2><p>Register your address and keep the certificate.</p>''')
    assert "Call now" not in r.clean_text
    assert "Start the check" not in r.clean_text
    assert "Ranking of 38 towns" not in r.clean_text
    assert "promotional copy" not in r.clean_text
    assert "After arrival" in r.clean_text
    assert any(e["reason"] == "empty_heading" for e in r.events)
    gap = next(e for e in r.events if e["reason"] == "media_dependency")
    assert gap["prev_block_key"] and gap["next_block_key"]
    assert gap["requires_review"]


def test_disclaimer_keeps_general_warning_and_removes_service_sentence():
    r = extract('''<blockquote><p>Important: this is general information, not legal advice.
    Confirm your case through our <a href="/services/lawyer/">expert partner</a>.
    Regulations can change before the next application.</p></blockquote>''')
    assert "not legal advice" in r.clean_text
    assert "Regulations can change" in r.clean_text
    assert "expert partner" not in r.clean_text


def test_roman_numerals_and_vet_appointments_are_not_authorship_or_sales():
    r = extract('''<blockquote><p>Program II Requirements:</p><ul><li>Have been a beneficiary of Program I.</li></ul></blockquote>
    <table><tr><th>Grant</th><th>Amount</th></tr><tr><td>Programme I</td><td>€8,000–€10,000 initial (Programme I) + €4,000–€5,000 for a third year (Programme II).</td></tr></table>
    <table><tr><td>12–8 weeks before travel</td><td>Book your vet appointments.</td></tr></table>
    <p>Applicants need the Annex I form for type I and class I categories.</p>''')
    assert "€8,000–€10,000" in r.clean_text
    assert "€4,000–€5,000" in r.clean_text
    assert "Book your vet appointments" in r.clean_text
    assert "Annex I" in r.clean_text
    assert any(b["kind"] == "quote" and "Program I" in b["text"] for b in r.blocks)
    more = extract('<p>Annex form (I–VI) is required. Group I &amp; II beneficiaries qualify.</p><table><tr><td>Ask: Could I afford to live here? Would I feel at home?</td></tr></table>')
    assert "Annex form (I–VI)" in more.clean_text
    assert "Group I & II" in more.clean_text
    assert "Could I afford" in more.clean_text
    assert "Would I feel at home?" in more.clean_text


def test_authorial_faq_quarantined_and_personal_case_outcome_not_generalized():
    r = extract('''<div class="rank-math-list-item"><h3 class="rank-math-question">How did we choose our ranking?</h3>
    <div class="rank-math-answer"><p>Review the full methodology at the end of the article.</p></div></div>
    <div class="rank-math-list-item"><h3 class="rank-math-question">Will late registration cause refusal?</h3>
    <div class="rank-math-answer"><p>Not automatically. In my case the authority asked for documents. The renewal was then approved. The safer approach is to correct the gap early.</p></div></div>''')
    assert "How did we" not in r.clean_text
    assert "The renewal was then approved" not in r.clean_text
    assert "The safer approach" in r.clean_text
    assert any(e["reason"] == "editorial_faq" for e in r.events)


def test_rankmath_bare_text_answer_is_content_not_an_unanswered_question():
    r = extract('''<div class="rank-math-list-item"><h3 class="rank-math-question">How long should a scouting trip be?</h3>
    <div class="rank-math-answer">Five days per shortlisted region is a useful starting point. Allow time to experience daily routines.</div></div>''')
    faq = next(b for b in r.blocks if b["kind"] == "faq")
    assert "Five days per shortlisted region" in faq["text"]
    assert not any(e["reason"] == "unanswered_question" for e in r.events)


def test_referral_list_removed_without_removing_following_external_sources():
    r = extract('''<h2>Where to go next</h2><p>Read next:</p><ul><li><a href="/one/">Guide one</a></li><li><a href="/two/">Guide two</a></li></ul>
    <h2>Sources and further reading</h2><ul><li><a href="https://www.boe.es/legal">Official regulation</a></li></ul>''')
    assert "Guide one" not in r.clean_text
    assert "Read next" not in r.clean_text
    assert "Official regulation" in r.clean_text
    assert "Sources and further reading" in r.clean_text


def test_affiliate_name_and_reviewer_quote_introductions_excluded():
    r = extract('''<p>Use a filing tool. MyExpatTaxes can help.</p><p><a rel="sponsored" href="/go/myexpattaxes/">MyExpatTaxes</a></p>
    <blockquote><p>A statement of opinion.</p><cite>Alex Ibañez, Tax partner</cite></blockquote>
    <p>Ibañez gives an example:</p><blockquote><p>A personal example with no name in this sentence.</p></blockquote>''')
    assert "Use a filing tool" in r.clean_text
    assert "MyExpatTaxes" not in r.clean_text
    assert "Ibañez" not in r.clean_text
    assert "personal example" not in r.clean_text


def test_removed_heading_keeps_section_boundary_without_leaking_its_text():
    r = extract('''<h2>Documents</h2><p>Bring a valid passport.</p>
    <h2>What I Learned During My Renewal</h2><p>Applications require supporting evidence.</p>
    <h3>Timing</h3><p>Apply before the permit expires.</p>
    <h2>Fees</h2><p>A processing fee is payable.</p>''')
    evidence = next(b for b in r.blocks if b["text"] == "Applications require supporting evidence.")
    assert evidence["section_path"] == []
    assert "removed_section_heading" in evidence["flags"]
    assert evidence["data"]["source_section_ids"]
    assert "What I Learned" not in json.dumps(r.blocks)
    timing = next(b for b in r.blocks if b["text"] == "Apply before the permit expires.")
    assert timing["section_path"] == ["Timing"]


@pytest.mark.parametrize("quote", [
    'A beautiful place, observes Matthew.',
    'The rent is affordable, says Sean.',
    'Leigh enthuses about the experience.',
    'Insider Tip: From David, living in Madrid. Arrive early.',
    'Mine took about three weeks to process.',
    '“A pleasant lifestyle.”',
])
def test_interview_quotes_do_not_need_a_formal_cite_tag(quote):
    r = extract(f'<blockquote><p>{quote}</p></blockquote>')
    assert quote not in r.clean_text
    assert any(e["reason"] in {"editorial_quote", "ambiguous_quote"} for e in r.events)


@pytest.mark.skipif(not Path("data/research/extremadura-digital-nomad-grant.html").exists(), reason="Optional locally downloaded inspection corpus")
def test_real_inspection_corpus_regressions():
    def cached(slug):
        return extract_html(Path(f"data/research/{slug}.html").read_text(encoding="utf-8"), f"https://movingtospain.com/{slug}/")
    grant = cached("extremadura-digital-nomad-grant")
    assert "€8,000–€10,000 initial (Programme I)" in grant.clean_text
    assert "Program II Requirements" in grant.clean_text
    assert any("institutional_quote" in b["flags"] for b in grant.blocks)
    pets = cached("dogs-pets-spain")
    assert "Book your vet appointments" in pets.clean_text
    assert len([b for b in pets.blocks if b["kind"] == "table"]) == 17
    tax = cached("becoming-a-tax-resident-in-spain")
    assert "Ibañez gives" not in tax.clean_text
    assert "mts-residency-check" not in tax.clean_text
    car = cached("ship-a-car-to-spain")
    assert "EU “new vehicle” trap" in car.clean_text
    assert "Read next:" not in car.clean_text
    assert "Sources and further reading" in car.clean_text
    assert "payIGICinstead" not in car.clean_text
    assert "shipping aleft-hand-drive" not in car.clean_text
    retirement = cached("best-places-to-retire-in-spain")
    assert "How did we choose" not in retirement.clean_text
    assert "Why did we exclude" not in retirement.clean_text
    methodology = cached("methodology-spain-wealth-tax-calculator")
    assert "Moving to Spain Expert Tax Partner" not in methodology.clean_text
    us = cached("us-expat-taxes-in-spain")
    assert "MyExpatTaxes" not in us.clean_text
