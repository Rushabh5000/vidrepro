"""Regression tests for all export renderers. The core contract, born from a
real complaint: exports must carry NO markup noise. No _(likely)_ italic
wrappers, no bare underscores, no warning glyphs — and the text export is
pure ASCII, safe for any ticket field."""
import re

import pytest

from vidrepro.contracts.report import (
    BugManifestation,
    EvidenceLink,
    ExpectedResult,
    Precondition,
    ReportBody,
    ReproStep,
    ResultBlock,
)
from vidrepro.exports.ado import render_repro_html, severity_for
from vidrepro.exports.github import github_issue_payload
from vidrepro.exports.markdown import render_markdown
from vidrepro.exports.text import render_text


def make_body(overall=0.7, expected=None, bug_type="functional") -> ReportBody:
    return ReportBody(
        title="Functional bug in trader.example.com",
        summary="Reproduction extracted from a 120s mobile recording.",
        bug_type=bug_type,
        preconditions=[Precondition(text='Start on "trader.example.com".',
                                    grounding="observed", confidence=0.8)],
        steps=[
            ReproStep(key="s1", index=1, text='Tap "Open Deal".',
                      action_type="tap", confidence=0.75,
                      evidence=[EvidenceLink(frame_id="f1", t_start_ms=52_000,
                                             t_end_ms=52_400)]),
            ReproStep(key="s2", index=2, text="Scroll down the page.",
                      action_type="scroll", confidence=0.92,
                      evidence=[EvidenceLink(frame_id="f2", t_start_ms=61_000,
                                             t_end_ms=62_000)]),
            ReproStep(key="s3", index=3, text="Tap the control that opens the next screen.",
                      action_type="tap", confidence=0.55,
                      uncertainty_note="activation inferred from the screen "
                                       "transition only",
                      evidence=[EvidenceLink(frame_id="f3", t_start_ms=70_000,
                                             t_end_ms=70_400)]),
        ],
        observed_result=ResultBlock(
            text="The UI displays the error text: 'Something went wrong'",
            evidence=[EvidenceLink(frame_id="f3", t_start_ms=71_000, t_end_ms=71_500)],
            confidence=0.85),
        expected_result=expected,
        bug_manifestation=BugManifestation(t_ms=71_000, confidence=0.85),
        ambiguity_notes=["The cursor was not visible in parts of the recording."],
        overall_confidence=overall,
    )


NOISE_PATTERNS = [
    re.compile(r"_\("),        # _(likely)_ / _(uncertain)_
    re.compile(r"_\["),        # _[0:42]_
    re.compile(r"[⚠«»·—]"),    # glyph noise
]


class TestMarkdown:
    def test_no_markup_noise(self):
        md = render_markdown(make_body())
        for pat in NOISE_PATTERNS:
            assert not pat.search(md), f"noise {pat.pattern!r} in markdown"

    def test_no_stray_underscores_at_all(self):
        assert "_" not in render_markdown(make_body())

    def test_steps_numbered_with_plain_timestamps(self):
        md = render_markdown(make_body())
        assert "1. [0:52] Tap \"Open Deal\"." in md
        assert "2. [1:01] Scroll down the page." in md

    def test_low_confidence_flagged_in_words(self):
        md = render_markdown(make_body())
        assert md.count("(low confidence)") == 1  # only the 0.55 step

    def test_uncertainty_note_rendered_as_note_line(self):
        md = render_markdown(make_body())
        assert "   - Note: activation inferred" in md

    def test_sections_present(self):
        md = render_markdown(make_body())
        for section in ("## Preconditions", "## Steps to Reproduce",
                        "## Observed Result", "## Expected Result",
                        "## Bug Manifestation", "## Ambiguities"):
            assert section in md

    def test_null_expected_result_stated_honestly(self):
        md = render_markdown(make_body(expected=None))
        assert "Not stated" in md

    def test_explicit_expected_result_rendered(self):
        body = make_body(expected=ExpectedResult(
            text="The deal opens without an error.", basis="visible_convention",
            confidence=0.5))
        md = render_markdown(body)
        assert "The deal opens without an error." in md
        assert "visible_convention" in md


class TestText:
    def test_pure_ascii(self):
        txt = render_text(make_body())
        non_ascii = {c for c in txt if ord(c) > 127}
        assert not non_ascii, f"non-ASCII in text export: {non_ascii}"

    def test_no_markdown_syntax(self):
        txt = render_text(make_body())
        assert "##" not in txt
        assert "**" not in txt
        assert "_(" not in txt

    def test_header_and_sections(self):
        txt = render_text(make_body())
        assert txt.startswith("BUG REPORT")
        for section in ("STEPS TO REPRODUCE", "OBSERVED RESULT",
                        "EXPECTED RESULT", "PRECONDITIONS", "AMBIGUITIES"):
            assert section in txt

    def test_steps_numbered_with_timestamps(self):
        txt = render_text(make_body())
        assert '1. Tap "Open Deal". [0:52]' in txt
        assert "Note: activation inferred" in txt

    def test_title_and_confidence_stated(self):
        txt = render_text(make_body())
        assert "Title:      Functional bug in trader.example.com" in txt
        assert "Confidence: 0.70 (overall)" in txt

    def test_null_expected_result_stated(self):
        assert "Not stated" in render_text(make_body(expected=None))


class TestAdoHtml:
    def test_steps_are_an_ordered_list(self):
        html_out = render_repro_html(make_body())
        assert "<ol>" in html_out and "</ol>" in html_out
        steps_list = html_out.split("<ol>")[1].split("</ol>")[0]
        assert steps_list.count("<li>") == 3

    def test_no_warning_glyphs(self):
        html_out = render_repro_html(make_body())
        assert "⚠" not in html_out
        assert "Note: activation inferred" in html_out

    def test_step_text_html_escaped(self):
        body = make_body()
        body.steps[0].text = 'Tap "<script>alert(1)</script>".'
        html_out = render_repro_html(body)
        assert "<script>" not in html_out
        assert "&lt;script&gt;" in html_out

    def test_null_expected_result_flagged_for_reviewer(self):
        html_out = render_repro_html(make_body(expected=None))
        assert "reviewer should confirm" in html_out

    @pytest.mark.parametrize("bug_type,severity", [
        ("crash", "1 - Critical"),
        ("functional", "2 - High"),
        ("ui", "3 - Medium"),
        ("unknown", "3 - Medium"),
    ])
    def test_severity_mapping(self, bug_type, severity):
        assert severity_for(bug_type) == severity


class TestGithub:
    def test_payload_shape(self):
        payload = github_issue_payload(make_body())
        assert payload["title"] == "Functional bug in trader.example.com"
        assert "## Steps to Reproduce" in payload["body"]
        assert "bug" in payload["labels"]
        assert "vidrepro:functional" in payload["labels"]

    def test_low_confidence_adds_verification_label(self):
        payload = github_issue_payload(make_body(overall=0.5))
        assert "needs-verification" in payload["labels"]

    def test_confident_report_has_no_verification_label(self):
        payload = github_issue_payload(make_body(overall=0.8))
        assert "needs-verification" not in payload["labels"]
