"""s11: structural validation + confidence scoring of the draft ReportBody.

Mechanical guarantees enforced here:
- body validates against the ReportBody contract
- chronology is monotonic (violating steps get flagged, not dropped)
- steps without evidence are demoted to grounding=assumed with a note
- per-step confidence recomputed with quality prior + forward propagation
- overall confidence assembled from the documented formula
"""
from vidrepro.contracts.report import ConfidenceBreakdown, ReportBody
from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.synthesis import confidence as conf


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id
        quality_flags = list(video.quality_flags or [])
        duration_ms = int((video.duration_s or 0) * 1000)

    draft = artifacts.load(org_id, job_id, "s10_synthesis")
    body = ReportBody.model_validate(draft)  # raises → retry/fail path in runner
    ocr_art = artifacts.load(org_id, job_id, "s04_frames_ocr")

    # evidence integrity: every referenced frame must exist
    known_frames = {f["frame_id"] for f in ocr_art["frames"]}
    for step in body.steps:
        step.evidence = [e for e in step.evidence
                         if not e.frame_id or e.frame_id in known_frames]
        if not step.evidence:
            step.grounding = "assumed"
            if not step.uncertainty_note:
                step.uncertainty_note = "no direct frame evidence for this step"

    for violation in body.validate_chronology():
        body.ambiguity_notes.append(f"chronology check: {violation}")

    prior = conf.quality_prior(quality_flags)
    ocr_confs = [b["confidence"] for b in ocr_art["ocr"]] or [0.0]
    ocr_mean = sum(ocr_confs) / len(ocr_confs)

    raw_confs = []
    for step in body.steps:
        agreement = 0.75  # single deterministic layer in no-AI mode
        evidence_confs = [ocr_mean] if step.evidence else []
        raw_confs.append(conf.step_confidence(step.confidence, evidence_confs,
                                              agreement, prior))
    propagated = conf.propagate(raw_confs)
    for step, c in zip(body.steps, propagated):
        step.confidence = c
        if c < 0.6 and not step.uncertainty_note:
            step.uncertainty_note = "low-confidence inference; verify during review"

    bug_conf = body.bug_manifestation.confidence if body.bug_manifestation else 0.0
    span = 0
    if body.steps and body.steps[0].evidence and body.steps[-1].evidence:
        span = body.steps[-1].evidence[0].t_end_ms - body.steps[0].evidence[0].t_start_ms
    coverage = conf.coverage(span, duration_ms)

    body.confidence_breakdown = ConfidenceBreakdown(
        actions=round(sum(propagated) / len(propagated), 3) if propagated else 0.0,
        ocr=round(ocr_mean, 3),
        bug_localization=round(bug_conf, 3),
        expected_result=(body.expected_result.confidence if body.expected_result else 0.0),
        coverage=coverage,
    )
    body.overall_confidence = conf.overall_confidence(
        propagated, bug_conf, coverage,
        body.expected_result.basis if body.expected_result else None,
    )

    artifacts.save(org_id, job_id, "s11_validate_score", body.model_dump())
