"""s10: draft ReportBody synthesis — deterministic templates over the event
log. If (and only if) an LLM provider is configured, step *wording* may be
refined afterward; the step structure, evidence, and confidences are never
touched by the model."""
from vidrepro.contracts.report import (
    BugManifestation,
    Environment,
    EvidenceLink,
    ExpectedResult,
    ReportBody,
    ResultBlock,
)
from vidrepro.db.models import ProcessingJob, Video
from vidrepro.db.session import db_session
from vidrepro.worker import artifacts
from vidrepro.worker.synthesis.steps import (
    build_entry_step,
    build_preconditions,
    synthesize_steps,
)
from vidrepro.worker.textquality import label_quality, sanitize_label


def normalized_viewport(width: int, height: int) -> tuple[int, int]:
    """Dimensions of the s02-normalized video (longest side capped at 1080,
    even dims) — the space cursor positions and OCR bboxes live in. Must
    mirror the ffmpeg scale expression in s02."""
    if not width or not height:
        return (0, 0)
    if width >= height:
        nw = min(1080, width)
        nh = int(round(height * nw / width / 2) * 2)
    else:
        nh = min(1080, height)
        nw = int(round(width * nh / height / 2) * 2)
    return nw, nh


def pick_title_source(blocks: list[dict]) -> str:
    """Best chrome text to name the report after. Quality weighted by length
    (up to 8 chars): a clean URL beats top-strip OCR mush that limps past the
    gate, and a full app/page name beats a truncated 2-letter logo ("iF").
    Ties go to the earliest block."""
    best, best_q = "", 0.0
    for b in blocks:
        if b["role"] not in ("title_bar", "url_bar") or b["confidence"] <= 0.5:
            continue
        label = sanitize_label(b["text"], max_len=60)
        if not label:
            continue
        q = label_quality(label) * (min(len(label), 8) / 8)
        if q > best_q:
            best, best_q = label, q
    return best


def build_environment(video_info: dict, ocr_blocks: list[dict], samples: list[dict]) -> Environment:
    has_url_bar = any(b["role"] == "url_bar" for b in ocr_blocks)
    lums = [s.get("lum", 128) for s in samples]
    median_lum = sorted(lums)[len(lums) // 2] if lums else 128
    return Environment(
        device_class=video_info.get("device_class", "unknown"),
        browser="browser (URL bar visible)" if has_url_bar else "",
        resolution=f"{video_info.get('width', 0)}x{video_info.get('height', 0)}",
        theme="dark" if median_lum < 90 else "light",
        field_confidence={
            "device_class": 0.8, "resolution": 0.95,
            "browser": 0.7 if has_url_bar else 0.0, "theme": 0.75,
        },
    )


def run(job_id: str) -> None:
    with db_session() as db:
        job = db.get(ProcessingJob, job_id)
        video = db.get(Video, job.video_id)
        org_id = video.org_id
        filename = video.original_filename
        quality_flags = list(video.quality_flags or [])

    info = artifacts.load(org_id, job_id, "s01_validate")
    seg_art = artifacts.load(org_id, job_id, "s03_segment")
    ocr_art = artifacts.load(org_id, job_id, "s04_frames_ocr")
    events_art = artifacts.load(org_id, job_id, "s06_events")
    targets = artifacts.load(org_id, job_id, "s07_targets")["targets"]
    bug = artifacts.load(org_id, job_id, "s09_bug")

    events = events_art["events"]
    frames = ocr_art["frames"]
    blocks = ocr_art["ocr"]

    bug_t = bug["t_ms"] if bug["found"] else None
    viewport = normalized_viewport(info.get("width", 0), info.get("height", 0))
    steps, omitted = synthesize_steps(events, frames, targets, bug_t, viewport)

    # step 1 is always the entry point: which app/site this flow starts in
    entry = build_entry_step(blocks, frames, info["device_class"])
    steps = [entry] + steps
    for i, step in enumerate(steps, start=1):
        step.index, step.key = i, f"s{i}"

    ambiguity: list[str] = []
    if not events:
        ambiguity.append("No user actions could be detected; the recording may be "
                         "static, heavily degraded, or show only the failure state.")
    if "cursor_invisible" in quality_flags:
        ambiguity.append("No cursor or touch indicator was visible; click positions "
                         "are inferred from screen transitions.")
    if "blurry" in quality_flags:
        ambiguity.append("The recording is blurry; extracted text may be incomplete.")
    if not bug["found"]:
        ambiguity.append("The bug manifestation point was not detected automatically "
                         "and must be marked during review.")

    manifestation = None
    observed_evidence: list[EvidenceLink] = []
    if bug["found"]:
        nearest = min(frames, key=lambda f: abs(f["t_ms"] - bug["t_ms"])) if frames else None
        observed_evidence = [EvidenceLink(
            frame_id=nearest["frame_id"] if nearest else "",
            t_start_ms=bug["t_ms"], t_end_ms=bug["t_ms"] + 500,
        )]
        manifestation = BugManifestation(
            t_ms=bug["t_ms"], evidence=observed_evidence, confidence=bug["confidence"],
        )

    # Deterministic mode never invents an expected result. The only case with
    # enough evidence is validation-style error text, where the convention
    # "input should be accepted or produce a clear message" is visible on-screen.
    expected = None
    if bug["found"] and bug.get("anomaly", {}).get("type") == "error_text":
        expected = ExpectedResult(
            text="The action completes without the error shown, or the UI explains "
                 "clearly which input must change.",
            basis="visible_convention", confidence=0.5,
        )

    title_source = pick_title_source(blocks)
    bug_word = bug["bug_type"] if bug["found"] else "issue"
    title = f"{bug_word.capitalize()} in {title_source}" if title_source \
        else f"{bug_word.capitalize()} reproduced from recording {filename or job_id[:8]}"

    body = ReportBody(
        title=title[:200],
        summary=(f"Reproduction extracted from a {info['duration_s']:.0f}s "
                 f"{info['device_class']} recording. "
                 + ("Bug manifestation detected automatically."
                    if bug["found"] else "No bug manifestation auto-detected.")),
        bug_type=bug["bug_type"] if bug["found"] else "unknown",
        preconditions=build_preconditions(blocks, frames),
        environment=build_environment(info, blocks, seg_art["samples"]),
        steps=steps,
        observed_result=ResultBlock(text=bug["observed"], evidence=observed_evidence,
                                    confidence=bug["confidence"] if bug["found"] else 0.2),
        expected_result=expected,
        bug_manifestation=manifestation,
        alternate_interpretations=[
            f"Alternative failure point at {c['t_ms'] / 1000:.1f}s ({c['type']})"
            for c in bug.get("candidates", [])[:3]
        ],
        ambiguity_notes=ambiguity,
        omitted_activity=omitted,
    )

    body = _maybe_refine_wording(body)
    artifacts.save(org_id, job_id, "s10_synthesis", body.model_dump())


def _maybe_refine_wording(body: ReportBody) -> ReportBody:
    """Optional LLM polish of step wording only. OFF unless configured."""
    from vidrepro.config import get_settings
    if get_settings().llm_provider != "anthropic":
        return body
    try:
        from vidrepro.worker.llm.client import refine_step_texts
        return refine_step_texts(body)
    except Exception:
        return body  # refinement is cosmetic — never fail the job for it
