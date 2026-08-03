"""Regression tests for the ReportBody contract: chronology validation,
field constraints, and JSON round-tripping (the review studio and every
export renderer rely on these invariants)."""
import pytest
from pydantic import ValidationError

from vidrepro.contracts.report import (
    EvidenceLink,
    ReportBody,
    ReproStep,
    ResultBlock,
)


def step(key, index, t_ms, text="Do the thing."):
    return ReproStep(key=key, index=index, text=text,
                     evidence=[EvidenceLink(frame_id=f"f{index}",
                                            t_start_ms=t_ms, t_end_ms=t_ms + 400)])


def body_with(steps):
    return ReportBody(title="t", steps=steps,
                      observed_result=ResultBlock(text="observed"))


class TestChronology:
    def test_monotonic_steps_pass(self):
        body = body_with([step("s1", 1, 1000), step("s2", 2, 5000)])
        assert body.validate_chronology() == []

    def test_backwards_evidence_flagged(self):
        body = body_with([step("s1", 1, 10_000), step("s2", 2, 1000)])
        violations = body.validate_chronology()
        assert len(violations) == 1
        assert "s2" in violations[0]

    def test_small_overlap_tolerated(self):
        body = body_with([step("s1", 1, 5000), step("s2", 2, 4000)])
        assert body.validate_chronology() == []  # within 1500ms tolerance

    def test_steps_without_evidence_skip_check(self):
        s = ReproStep(key="s1", index=1, text="No evidence step.")
        assert body_with([s]).validate_chronology() == []


class TestValidation:
    def test_empty_step_text_rejected(self):
        with pytest.raises(ValidationError):
            ReproStep(key="s1", index=1, text="   ")

    def test_confidence_bounds_enforced(self):
        with pytest.raises(ValidationError):
            ReproStep(key="s1", index=1, text="x", confidence=1.5)

    def test_bug_type_restricted(self):
        with pytest.raises(ValidationError):
            ReportBody(title="t", bug_type="not_a_type",
                       observed_result=ResultBlock(text="o"))

    def test_null_expected_result_is_valid(self):
        body = body_with([])
        assert body.expected_result is None

    def test_grounding_restricted(self):
        with pytest.raises(ValidationError):
            ReproStep(key="s1", index=1, text="x", grounding="guessed")


class TestRoundTrip:
    def test_json_roundtrip_preserves_body(self):
        body = body_with([step("s1", 1, 1000)])
        again = ReportBody.model_validate(body.model_dump())
        assert again == body

    def test_schema_version_pinned(self):
        assert body_with([]).schema_version == "1.0"
