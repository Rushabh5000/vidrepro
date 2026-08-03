"""Confidence model.

step = clamp(0.5·action + 0.3·evidence + 0.2·agreement) × quality_prior
overall = 0.35·median(step) + 0.25·bug + 0.2·coverage + 0.2·expected_basis
Uncertainty propagates forward dampened: a step consuming state from an
uncertain predecessor inherits conf × (0.8 + 0.2·pred_conf).
"""
from statistics import median

QUALITY_PENALTY = {
    "blurry": 0.85,
    "low_fps": 0.9,
    "cursor_invisible": 0.9,
    "camera_capture": 0.7,
}

EXPECTED_BASIS_SCORE = {
    "visible_convention": 0.7,
    "ui_text": 0.8,
    "narration": 0.6,
    "assumed": 0.3,
}


def quality_prior(quality_flags: list[str]) -> float:
    prior = 1.0
    for flag in quality_flags:
        prior *= QUALITY_PENALTY.get(flag, 1.0)
    return max(0.6, prior)


def step_confidence(action_conf: float, evidence_confs: list[float],
                    agreement: float, prior: float) -> float:
    evidence = sum(evidence_confs) / len(evidence_confs) if evidence_confs else 0.4
    raw = 0.5 * action_conf + 0.3 * evidence + 0.2 * agreement
    return round(max(0.0, min(1.0, raw * prior)), 3)


def propagate(step_confs: list[float]) -> list[float]:
    out: list[float] = []
    for i, conf in enumerate(step_confs):
        if i == 0:
            out.append(round(conf, 3))
        else:
            out.append(round(conf * (0.8 + 0.2 * out[-1]), 3))
    return out


def overall_confidence(step_confs: list[float], bug_conf: float,
                       coverage: float, expected_basis: str | None) -> float:
    steps = median(step_confs) if step_confs else 0.0
    expected = EXPECTED_BASIS_SCORE.get(expected_basis, 0.0) if expected_basis else 0.5
    # a null expected result is honest, not wrong — score it neutrally (0.5)
    value = 0.35 * steps + 0.25 * bug_conf + 0.2 * coverage + 0.2 * expected
    return round(max(0.0, min(1.0, value)), 3)


def coverage(events_span_ms: int, duration_ms: int) -> float:
    if duration_ms <= 0:
        return 0.0
    return round(min(1.0, events_span_ms / duration_ms), 3)
