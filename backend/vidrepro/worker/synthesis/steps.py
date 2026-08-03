"""Rule-based step synthesis: typed events → numbered human-readable steps.

Purely deterministic. Compression rules: consecutive same-direction scrolls
merge; navigations within 1.2s of a click/tap are treated as that click's
effect (not separate steps); runs of unlabeled tap inferences collapse into
one step; events after the bug manifestation are dropped into
omitted_activity. Every kept step carries frame-span evidence.
"""
from vidrepro.contracts.report import (
    EvidenceLink,
    OmittedSpan,
    Precondition,
    ReproStep,
)
from vidrepro.worker.textquality import (
    DOMAIN_TOKEN,
    label_quality,
    sanitize_label,
)

EFFECT_WINDOW_MS = 2000
SAME_BOUNDARY_MS = 400
UNLABELED_RUN_GAP_MS = 3000


def _nearest_frame(frames: list[dict], t_ms: int) -> dict | None:
    if not frames:
        return None
    return min(frames, key=lambda f: abs(f["t_ms"] - t_ms))


def _evidence(frames: list[dict], event: dict) -> list[EvidenceLink]:
    frame = _nearest_frame(frames, event["t_start_ms"])
    return [EvidenceLink(
        frame_id=frame["frame_id"] if frame else "",
        t_start_ms=event["t_start_ms"], t_end_ms=event["t_end_ms"],
    )]


def screen_region(pos: list[int], width: int, height: int) -> str:
    """Human name for a screen position: 'top right of the screen'. Never
    raw coordinates — a reader can find 'the bottom left' on their own
    device; pixel numbers from someone else's recording help no one."""
    if not width or not height:
        return "screen"
    fx, fy = pos[0] / width, pos[1] / height
    row = "top" if fy < 0.25 else "bottom" if fy > 0.75 else "middle"
    col = "left" if fx < 0.33 else "right" if fx > 0.66 else "center"
    if row == "middle" and col == "center":
        return "center of the screen"
    if row == "middle":
        return f"{col} side of the screen"
    if col == "center":
        return f"{row} of the screen"
    return f"{row} {col} of the screen"


def _sentence(event: dict, target: str,
              viewport: tuple[int, int] = (0, 0)) -> tuple[str, str]:
    """Return (text, uncertainty_note). Text is plain prose: no markup, no
    pixel coordinates ever, no OCR mush (targets arrive pre-sanitized;
    empty means unusable)."""
    etype = event["type"]
    verb = {"click": "Click", "tap": "Tap", "form_submit": "Click"}.get(etype, "Click")
    if etype in ("click", "tap", "form_submit"):
        if target:
            repeats = event["detail"].get("repeat_count", 1)
            if repeats > 1:
                return f'{verb} "{target}" ({repeats} times).', ""
            return f'{verb} "{target}".', ""
        merged = event["detail"].get("merged_count", 0)
        if merged > 1:
            return (f"{verb} through the next {merged} screens "
                    f"(the exact controls could not be read from the video).",
                    "several screen changes in a row; individual targets were "
                    "not readable")
        if event.get("pos"):
            region = screen_region(event["pos"], *viewport)
            return (f"{verb} the control at the {region}.",
                    "target text could not be read; location is from the "
                    "cursor track")
        return (f"{verb} the control that opens the next screen.",
                "activation inferred from the screen transition only; the "
                "cursor/touch indicator was not visible")
    if etype == "type":
        text = event["detail"].get("final_text", "")
        return f'Type "{text}" into the field.', ""
    if etype == "scroll":
        d = event["detail"]
        return f"Scroll {d.get('direction', 'down')} the page.", ""
    if etype == "navigate":
        target_text = event["detail"].get("target", "")
        if target_text:
            return f'Go to "{target_text}".', ""
        return ("Go to the next screen.",
                "destination title/URL was not readable")
    if etype == "dialog_open":
        return "A dialog or overlay opens.", ""
    return "Perform the next visible interaction.", "action type could not be classified"


def compress(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge scroll runs; suppress navigations that are click effects.
    Returns (kept_events, suppressed_events).

    Activation lookup runs over ALL events, not just already-kept ones: a
    click's cursor-dwell timestamp can sort just after the navigation event
    born from the same screen transition, and that navigation is duplication,
    not a step. dialog_open is only suppressed by a preceding click (a click
    following a dialog is the user answering it — keep both)."""
    activations = [e for e in events
                   if e["type"] in ("click", "tap", "form_submit")]
    kept: list[dict] = []
    suppressed: list[dict] = []
    for event in sorted(events, key=lambda e: e["t_start_ms"]):
        if event["type"] == "scroll" and kept and kept[-1]["type"] == "scroll" \
                and kept[-1]["detail"].get("direction") == event["detail"].get("direction") \
                and event["t_start_ms"] - kept[-1]["t_end_ms"] < 2500:
            kept[-1]["t_end_ms"] = event["t_end_ms"]
            kept[-1]["detail"]["magnitude_px"] = round(
                kept[-1]["detail"].get("magnitude_px", 0)
                + event["detail"].get("magnitude_px", 0), 1)
            suppressed.append(event)
            continue
        if event["type"] in ("navigate", "dialog_open"):
            after_click = any(
                0 <= event["t_start_ms"] - a["t_end_ms"] <= EFFECT_WINDOW_MS
                for a in activations)
            same_boundary = event["type"] == "navigate" and any(
                abs(event["t_start_ms"] - a["t_start_ms"]) <= SAME_BOUNDARY_MS
                or abs(event["t_start_ms"] - a["t_end_ms"]) <= SAME_BOUNDARY_MS
                for a in activations)
            if after_click or same_boundary:
                suppressed.append(event)  # it's the effect of the click, not a step
                continue
        kept.append({**event, "detail": dict(event["detail"])})
    return kept, suppressed


def merge_repeated_targets(events: list[dict], targets: dict[str, str],
                           gap_ms: int = UNLABELED_RUN_GAP_MS) -> list[dict]:
    """Collapse back-to-back activations of the same named control ("Tap X" /
    "Tap X") into one step with a repeat count. Rapid double-activations are
    almost always one user intent seen through two frame transitions."""
    out: list[dict] = []
    for event in events:
        if out:
            prev = out[-1]
            t = targets.get(event["id"], "")
            if (event["type"] == prev["type"]
                    and event["type"] in ("click", "tap", "form_submit")
                    and t and t == targets.get(prev["id"], "")
                    and event["t_start_ms"] - prev["t_end_ms"] <= gap_ms):
                prev["t_end_ms"] = event["t_end_ms"]
                prev["detail"]["repeat_count"] = prev["detail"].get("repeat_count", 1) + 1
                prev["confidence"] = max(prev["confidence"], event["confidence"])
                continue
        out.append({**event, "detail": dict(event["detail"])})
    return out


def merge_unlabeled_taps(events: list[dict], targets: dict[str, str],
                         gap_ms: int = UNLABELED_RUN_GAP_MS) -> list[dict]:
    """Collapse runs of click/tap events that have neither a position nor a
    readable target. Five 'tap something, screen changes' inferences in a row
    are one fact ('the user tapped through five screens'), not five steps."""
    def unlabeled(e: dict) -> bool:
        return (e["type"] in ("click", "tap")
                and not e.get("pos") and not targets.get(e["id"], ""))

    out: list[dict] = []
    for event in events:
        if unlabeled(event) and out:
            prev = out[-1]
            if (unlabeled(prev) or prev["detail"].get("merged_count", 0) > 0) \
                    and event["t_start_ms"] - prev["t_end_ms"] <= gap_ms:
                prev["t_end_ms"] = event["t_end_ms"]
                prev["detail"]["merged_count"] = prev["detail"].get("merged_count", 1) + 1
                prev["confidence"] = min(prev["confidence"], event["confidence"])
                continue
        out.append({**event, "detail": dict(event["detail"])})
    return out


def build_entry_step(ocr_blocks: list[dict], frames: list[dict],
                     device_class: str) -> ReproStep:
    """Step 1 is always the entry point: 'Open <url> in the browser' or
    'Open the <name> app'. A reproduction that starts mid-flight ('Tap X')
    strands the reader — they need to know where the flow begins."""
    first_frames = sorted(frames, key=lambda f: f["t_ms"])[:2]
    ids = {f["frame_id"] for f in first_frames}
    best_domain, best_domain_q = "", 0.0
    best_title, best_title_q = "", 0.0
    for b in ocr_blocks:
        if b["frame_id"] not in ids or b["confidence"] <= 0.45:
            continue
        label = sanitize_label(b["text"], max_len=80)
        if not label:
            continue
        q = label_quality(label) * (min(len(label), 8) / 8)
        if DOMAIN_TOKEN.match(label.split("/")[0]):
            if q > best_domain_q:
                best_domain, best_domain_q = label, q
        elif b["role"] in ("title_bar",) and q > best_title_q:
            best_title, best_title_q = label, q

    evidence = [EvidenceLink(
        frame_id=first_frames[0]["frame_id"] if first_frames else "",
        t_start_ms=0,
        t_end_ms=first_frames[0]["t_ms"] if first_frames else 0,
    )]
    if best_domain:
        return ReproStep(
            key="s0", index=1, text=f'Open "{best_domain}" in the browser.',
            action_type="navigate", grounding="observed", confidence=0.85,
            evidence=evidence)
    if best_title:
        return ReproStep(
            key="s0", index=1, text=f'Open the "{best_title}" app.',
            action_type="navigate", grounding="observed", confidence=0.7,
            evidence=evidence)
    kind = "app" if device_class == "mobile" else "application"
    return ReproStep(
        key="s0", index=1,
        text=f"Open the {kind} shown at the start of the recording.",
        action_type="navigate", grounding="assumed", confidence=0.5,
        uncertainty_note="the app name and URL were not readable on the "
                         "first screen",
        evidence=evidence)


def synthesize_steps(events: list[dict], frames: list[dict], targets: dict[str, str],
                     bug_t_ms: int | None, viewport: tuple[int, int] = (0, 0),
                     ) -> tuple[list[ReproStep], list[OmittedSpan]]:
    omitted: list[OmittedSpan] = []
    usable = events
    if bug_t_ms is not None:
        usable = [e for e in events if e["t_start_ms"] <= bug_t_ms + 1500]
        after = [e for e in events if e["t_start_ms"] > bug_t_ms + 1500]
        if after:
            omitted.append(OmittedSpan(
                t_start_ms=after[0]["t_start_ms"], t_end_ms=after[-1]["t_end_ms"],
                reason=f"{len(after)} action(s) after the bug manifestation "
                       f"are not needed to reproduce",
            ))

    kept, _suppressed = compress(usable)
    kept = merge_unlabeled_taps(kept, targets)
    kept = merge_repeated_targets(kept, targets)

    steps: list[ReproStep] = []
    for i, event in enumerate(kept, start=1):
        text, note = _sentence(event, targets.get(event["id"], ""), viewport)
        grounding = "observed" if event["confidence"] >= 0.8 else "inferred"
        steps.append(ReproStep(
            key=f"s{i}", index=i, text=text, action_type=event["type"],
            grounding=grounding, confidence=event["confidence"],
            uncertainty_note=note, evidence=_evidence(frames, event),
        ))
    return steps, omitted


def build_preconditions(ocr_blocks: list[dict], frames: list[dict]) -> list[Precondition]:
    """First screen's URL/title becomes the starting precondition."""
    first_frames = sorted(frames, key=lambda f: f["t_ms"])[:2]
    ids = {f["frame_id"] for f in first_frames}
    for block in ocr_blocks:
        if block["frame_id"] in ids and block["role"] in ("url_bar", "title_bar") \
                and block["confidence"] > 0.4:
            label = sanitize_label(block["text"], max_len=150)
            if not label:
                continue
            return [Precondition(
                text=f'Start on "{label}" (visible when the recording begins; '
                     f"any prior setup is unknown).",
                grounding="observed", confidence=round(block["confidence"], 2),
            )]
    return [Precondition(
        text="Start on the screen visible at the beginning of the recording; "
             "prior setup steps are unknown.",
        grounding="assumed", confidence=0.4,
    )]
