"""The canonical ReportBody contract. Everything the pipeline produces and the
review studio edits validates against this model (stage s11 enforces it)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Grounding = Literal["observed", "inferred", "assumed"]
BugType = Literal[
    "ui", "functional", "crash", "validation", "navigation",
    "state_inconsistency", "rendering", "performance", "workflow", "unknown",
]


class EvidenceLink(BaseModel):
    frame_id: str = ""
    t_start_ms: int
    t_end_ms: int
    note: str = ""


class ReproStep(BaseModel):
    key: str
    index: int
    text: str
    action_type: str = "unknown"
    grounding: Grounding = "inferred"
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    uncertainty_note: str = ""
    evidence: list[EvidenceLink] = []
    screen_state: str = ""
    compressed_from: Optional[dict] = None

    @field_validator("text")
    @classmethod
    def non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("step text must not be empty")
        return v


class ResultBlock(BaseModel):
    text: str
    evidence: list[EvidenceLink] = []
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class ExpectedResult(BaseModel):
    text: str
    basis: Literal["visible_convention", "ui_text", "narration", "assumed"] = "assumed"
    confidence: float = Field(ge=0.0, le=1.0, default=0.3)


class Precondition(BaseModel):
    text: str
    grounding: Grounding = "assumed"
    confidence: float = 0.5


class Environment(BaseModel):
    os: str = ""
    browser: str = ""
    app: str = ""
    device_class: str = "unknown"
    resolution: str = ""
    locale: str = ""
    theme: str = ""
    field_confidence: dict[str, float] = {}


class BugManifestation(BaseModel):
    t_ms: int
    evidence: list[EvidenceLink] = []
    confidence: float = 0.5


class OmittedSpan(BaseModel):
    t_start_ms: int
    t_end_ms: int
    reason: str


class ConfidenceBreakdown(BaseModel):
    actions: float = 0.5
    ocr: float = 0.5
    bug_localization: float = 0.5
    expected_result: float = 0.0
    coverage: float = 0.5


class ReportBody(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    title: str
    summary: str = ""
    bug_type: BugType = "unknown"
    preconditions: list[Precondition] = []
    environment: Environment = Environment()
    steps: list[ReproStep] = []
    observed_result: ResultBlock
    expected_result: Optional[ExpectedResult] = None  # null is allowed and encouraged
    bug_manifestation: Optional[BugManifestation] = None
    alternate_interpretations: list[str] = []
    ambiguity_notes: list[str] = []
    omitted_activity: list[OmittedSpan] = []
    overall_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    confidence_breakdown: ConfidenceBreakdown = ConfidenceBreakdown()

    def validate_chronology(self) -> list[str]:
        """Return list of chronology violations (step evidence must be monotonic)."""
        errors: list[str] = []
        last_t = -1
        for step in self.steps:
            if step.evidence:
                t = min(e.t_start_ms for e in step.evidence)
                if t < last_t - 1500:  # tolerate small overlap
                    errors.append(f"step {step.key} evidence at {t}ms precedes prior step")
                last_t = max(last_t, t)
        return errors
