"""Regression tests for s06 event detection: scrolls, clicks, typing,
navigation, anomalies, and target naming. Fixtures mirror the artifact
shapes produced by s03/s04/s05."""
import pytest

from vidrepro.worker.stages.s06_events import (
    dedupe_clicks,
    detect_anomalies,
    detect_clicks,
    detect_navigation,
    detect_scrolls,
    detect_typing,
    nearest_text,
)


def motion(t, dy):
    return {"t_ms": t, "dx": 0.0, "dy": dy}


def seg(idx, start, end, kind):
    return {"idx": idx, "start_ms": start, "end_ms": end, "transition_type": kind}


def block(frame_id, t, text, bbox=(100, 400, 200, 40), conf=0.9, role="body"):
    return {"frame_id": frame_id, "t_ms": t, "text": text,
            "bbox": list(bbox), "confidence": conf, "role": role}


def frame(fid, t, seg_idx):
    return {"frame_id": fid, "t_ms": t, "segment_idx": seg_idx, "storage_key": "k"}


# ------------------------------------------------------------------ scrolls

class TestScrolls:
    def test_below_threshold_ignored(self):
        assert detect_scrolls([motion(0, 1.0), motion(200, -2.0)]) == []

    def test_single_run_grouped(self):
        events = detect_scrolls([motion(0, -10), motion(200, -12), motion(400, -8)])
        assert len(events) == 1
        assert events[0]["detail"]["direction"] == "down"
        assert events[0]["detail"]["magnitude_px"] == 30.0

    def test_direction_change_splits(self):
        events = detect_scrolls([motion(0, -10), motion(200, 12)])
        assert [e["detail"]["direction"] for e in events] == ["down", "up"]

    def test_time_gap_splits(self):
        events = detect_scrolls([motion(0, -10), motion(2000, -10)])
        assert len(events) == 2

    def test_scroll_confidence_is_high(self):
        events = detect_scrolls([motion(0, -10)])
        assert events[0]["confidence"] == 0.92


# ------------------------------------------------------------------- clicks

CURSOR = [{"t_ms": 900, "x": 50.0, "y": 100.0}]


class TestClicks:
    def test_cursor_anchored_click(self):
        events = detect_clicks([seg(0, 0, 1000, "start"), seg(1, 1000, 3000, "nav")],
                               CURSOR, 2.0, "desktop")
        assert len(events) == 1
        assert events[0]["type"] == "click"
        assert events[0]["pos"] == [100, 200]  # scaled by coord_scale
        assert events[0]["confidence"] == 0.75

    def test_mobile_gets_tap(self):
        events = detect_clicks([seg(1, 1000, 3000, "nav")], CURSOR, 1.0, "mobile")
        assert events[0]["type"] == "tap"

    def test_nav_without_cursor_is_low_confidence_inference(self):
        events = detect_clicks([seg(1, 5000, 8000, "nav")], [], 1.0, "desktop")
        assert len(events) == 1
        assert events[0]["pos"] is None
        assert events[0]["confidence"] == 0.55

    def test_dialog_without_cursor_is_not_a_click(self):
        # dialogs open on their own (toasts, prompts) — no invented taps
        events = detect_clicks([seg(1, 5000, 8000, "dialog")], [], 1.0, "desktop")
        assert events == []

    def test_dialog_with_cursor_is_a_click(self):
        events = detect_clicks([seg(1, 1000, 3000, "dialog")], CURSOR, 1.0, "desktop")
        assert len(events) == 1

    def test_minor_transition_never_clicks(self):
        events = detect_clicks([seg(1, 1000, 3000, "minor")], CURSOR, 1.0, "desktop")
        assert events == []

    def test_scroll_explains_transition(self):
        # transition at 1000ms right after scrolling = content moved, not a tap
        moving = [motion(700, -20), motion(900, -25)]
        events = detect_clicks([seg(1, 1000, 3000, "nav")], CURSOR, 1.0,
                               "desktop", motions=moving)
        assert events == []

    def test_old_scroll_does_not_explain(self):
        moving = [motion(200, -20)]  # 800ms before the transition
        events = detect_clicks([seg(1, 1000, 3000, "nav")], CURSOR, 1.0,
                               "desktop", motions=moving)
        assert len(events) == 1

    def test_burst_dedupe_keeps_best(self):
        a = {"id": "a", "type": "tap", "t_start_ms": 1000, "t_end_ms": 1100,
             "pos": None, "detail": {}, "signals": [], "confidence": 0.55}
        b = {"id": "b", "type": "tap", "t_start_ms": 1400, "t_end_ms": 1500,
             "pos": [5, 5], "detail": {}, "signals": [], "confidence": 0.75}
        out = dedupe_clicks([a, b])
        assert len(out) == 1
        assert out[0]["id"] == "b"

    def test_separated_clicks_survive_dedupe(self):
        a = {"id": "a", "type": "tap", "t_start_ms": 1000, "t_end_ms": 1100,
             "pos": None, "detail": {}, "signals": [], "confidence": 0.55}
        b = {"id": "b", "type": "tap", "t_start_ms": 3000, "t_end_ms": 3100,
             "pos": None, "detail": {}, "signals": [], "confidence": 0.55}
        assert len(dedupe_clicks([a, b])) == 2


# ------------------------------------------------------------------- typing

class TestTyping:
    FRAMES = [frame("f1", 1000, 1), frame("f2", 3000, 1)]

    def test_legit_text_growth(self):
        blocks = [block("f1", 1000, "he", (100, 100, 80, 30)),
                  block("f2", 3000, "hello world", (100, 100, 160, 30))]
        events = detect_typing(blocks, self.FRAMES)
        assert len(events) == 1
        assert events[0]["detail"]["final_text"] == "hello world"

    def test_empty_field_growth(self):
        blocks = [block("f1", 1000, "", (100, 100, 80, 30)),
                  block("f2", 3000, "hello", (100, 100, 120, 30))]
        # empty prev text has no bbox overlap in practice, but same-bbox works
        events = detect_typing(blocks, self.FRAMES)
        assert len(events) == 1

    def test_clock_growth_rejected(self):
        blocks = [block("f1", 1000, "8:54 @ 00:11", (0, 0, 200, 20)),
                  block("f2", 3000, "8:54 @ 00:11 «28 Se ll", (0, 0, 240, 20))]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_logo_reocr_rejected(self):
        # real regression: hamburger icon "=" then "= iIFOREX« 2]"
        blocks = [block("f1", 1000, "=", (100, 100, 30, 30)),
                  block("f2", 3000, "= iIFOREX« 2]", (100, 100, 160, 30))]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_cross_segment_rejected(self):
        frames = [frame("f1", 1000, 1), frame("f2", 3000, 2)]
        blocks = [block("f1", 1000, "he"), block("f2", 3000, "hello world")]
        assert detect_typing(blocks, frames) == []

    def test_moved_bbox_rejected(self):
        blocks = [block("f1", 1000, "he", (100, 100, 80, 30)),
                  block("f2", 3000, "hello world", (600, 600, 160, 30))]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_low_ocr_confidence_rejected(self):
        blocks = [block("f1", 1000, "he", (100, 100, 80, 30)),
                  block("f2", 3000, "hello world", (100, 100, 160, 30), conf=0.4)]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_status_bar_role_rejected(self):
        blocks = [block("f1", 1000, "he", (100, 100, 80, 30)),
                  block("f2", 3000, "hello world", (100, 100, 160, 30),
                        role="status_bar")]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_shrinking_text_rejected(self):
        blocks = [block("f1", 1000, "hello world"), block("f2", 3000, "he")]
        assert detect_typing(blocks, self.FRAMES) == []

    def test_tiny_growth_rejected(self):
        blocks = [block("f1", 1000, "hello"), block("f2", 3000, "hellos")]
        assert detect_typing(blocks, self.FRAMES) == []


# --------------------------------------------------------------- navigation

class TestNavigation:
    def test_nav_with_readable_url(self):
        segments = [seg(0, 0, 1000, "start"), seg(1, 1000, 3000, "nav")]
        frames = [frame("f1", 1350, 1)]
        blocks = [block("f1", 1350, "25 trader.iforex.com/webpl4/trading (©)",
                        (10, 5, 300, 20), role="url_bar")]
        events = detect_navigation(segments, blocks, frames)
        assert len(events) == 1
        assert events[0]["detail"]["target"] == "trader.iforex.com/webpl4/trading"

    def test_nav_with_garbage_title_has_no_target(self):
        segments = [seg(0, 0, 1000, "start"), seg(1, 1000, 3000, "nav")]
        frames = [frame("f1", 1350, 1)]
        blocks = [block("f1", 1350, "8:55 B ra) 00:52 ull",
                        (10, 5, 300, 20), role="title_bar")]
        events = detect_navigation(segments, blocks, frames)
        assert events[0]["detail"]["target"] == ""

    def test_dialog_becomes_dialog_open(self):
        segments = [seg(0, 0, 1000, "start"), seg(1, 1000, 3000, "dialog")]
        events = detect_navigation(segments, [], [])
        assert events[0]["type"] == "dialog_open"

    def test_first_segment_never_navigates(self):
        assert detect_navigation([seg(0, 0, 1000, "nav")], [], []) == []

    def test_minor_segment_never_navigates(self):
        segments = [seg(0, 0, 1000, "start"), seg(1, 1000, 3000, "minor")]
        assert detect_navigation(segments, [], []) == []


# ------------------------------------------------------------- nearest_text

class TestNearestText:
    def test_returns_closest_readable(self):
        blocks = [block("f1", 1000, "Open Deal", (90, 390, 100, 30),
                        role="button_like")]
        assert nearest_text(blocks, [150, 410], 1000) == "Open Deal"

    def test_garbage_under_cursor_skipped_for_readable_neighbor(self):
        blocks = [
            block("f1", 1000, "«209 ee wll B", (140, 400, 60, 20)),
            block("f1", 1000, "Buy", (150, 470, 60, 24), role="button_like"),
        ]
        assert nearest_text(blocks, [160, 410], 1000) == "Buy"

    def test_button_label_beats_nearby_text_wall(self):
        blocks = [
            block("f1", 1000, "By submitting your information, you agree to our",
                  (100, 400, 400, 30), role="body"),
            block("f1", 1000, "Submit", (150, 460, 80, 28), role="button_like"),
        ]
        assert nearest_text(blocks, [180, 415], 1000) == "Submit"

    def test_nothing_readable_returns_none(self):
        blocks = [block("f1", 1000, "8:55 B ra)", (140, 400, 60, 20))]
        assert nearest_text(blocks, [150, 410], 1000) is None

    def test_far_text_ignored(self):
        blocks = [block("f1", 1000, "Open Deal", (900, 900, 100, 30))]
        assert nearest_text(blocks, [50, 50], 1000) is None

    def test_temporally_distant_text_ignored(self):
        blocks = [block("f1", 20000, "Open Deal", (90, 390, 100, 30))]
        assert nearest_text(blocks, [150, 410], 1000) is None

    def test_low_ocr_confidence_ignored(self):
        blocks = [block("f1", 1000, "Open Deal", (90, 390, 100, 30), conf=0.3)]
        assert nearest_text(blocks, [150, 410], 1000) is None


# ---------------------------------------------------------------- anomalies

class TestAnomalies:
    def test_error_text_detected(self):
        blocks = [block("f1", 5000, "Error: Something went wrong")]
        found = detect_anomalies(blocks, [], [])
        assert len(found) == 1
        assert found[0]["type"] == "error_text"

    def test_error_word_in_garbage_ignored(self):
        # contains the word "error" but the line is OCR mush — readability
        # gate must veto the regex match
        blocks = [block("f1", 5000, "error B ra) 00:52 ull")]
        assert detect_anomalies(blocks, [], []) == []

    def test_low_conf_error_ignored(self):
        blocks = [block("f1", 5000, "Error: failed", conf=0.3)]
        assert detect_anomalies(blocks, [], []) == []

    def test_blank_screen_and_dedupe(self):
        samples = [{"t_ms": t, "score": 0.0, "change_frac": 0.0, "lum": 3.0}
                   for t in (1000, 1500, 2000, 8000)]
        found = detect_anomalies([], samples, [])
        blanks = [a for a in found if a["type"] == "blank_screen"]
        assert len(blanks) == 2  # 1000ms run deduped; 8000ms separate

    def test_stall_after_last_action(self):
        events = [{"id": "e", "type": "click", "t_start_ms": 900,
                   "t_end_ms": 1000, "pos": None, "detail": {},
                   "signals": [], "confidence": 0.8}]
        samples = [{"t_ms": t, "score": 0.001, "change_frac": 0.0, "lum": 100}
                   for t in range(2000, 12001, 1000)]
        found = detect_anomalies([], samples, events)
        assert any(a["type"] == "stall" for a in found)

    def test_no_stall_when_screen_changes(self):
        events = [{"id": "e", "type": "click", "t_start_ms": 900,
                   "t_end_ms": 1000, "pos": None, "detail": {},
                   "signals": [], "confidence": 0.8}]
        samples = [{"t_ms": t, "score": 0.5, "change_frac": 0.4, "lum": 100}
                   for t in range(2000, 12001, 1000)]
        found = detect_anomalies([], samples, events)
        assert not any(a["type"] == "stall" for a in found)
