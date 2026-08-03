"""Regression tests for step synthesis: run merging, repeat collapsing,
sentence cleanliness, and preconditions. The baselines these protect: a real
16s recording produced four consecutive 'Tap the control that opens the next
screen' filler steps and a duplicated 'Tap "Account"' pair."""
import pytest

from vidrepro.worker.synthesis.steps import (
    _sentence,
    build_preconditions,
    compress,
    merge_repeated_targets,
    merge_unlabeled_taps,
    synthesize_steps,
)

FORBIDDEN_CHARS = set("«»©®™•¤§—⚠_*`")


def ev(id, t0, t1, type_, pos=None, confidence=0.7, **detail):
    return {"id": id, "t_start_ms": t0, "t_end_ms": t1, "type": type_,
            "pos": pos, "detail": detail, "signals": [], "confidence": confidence}


FRAMES = [{"frame_id": "f1", "t_ms": 500, "segment_idx": 0, "storage_key": "k"},
          {"frame_id": "f2", "t_ms": 5000, "segment_idx": 1, "storage_key": "k"}]


class TestMergeUnlabeledTaps:
    def test_run_of_fillers_collapses(self):
        events = [ev("a", 9000, 9400, "tap"), ev("b", 10000, 10400, "tap"),
                  ev("c", 10600, 11000, "tap")]
        out = merge_unlabeled_taps(events, {})
        assert len(out) == 1
        assert out[0]["detail"]["merged_count"] == 3
        assert out[0]["t_end_ms"] == 11000

    def test_labeled_tap_breaks_the_run(self):
        events = [ev("a", 9000, 9400, "tap"), ev("b", 10000, 10400, "tap"),
                  ev("c", 10600, 11000, "tap")]
        out = merge_unlabeled_taps(events, {"b": "Account"})
        assert len(out) == 3

    def test_positioned_tap_not_merged(self):
        events = [ev("a", 9000, 9400, "tap"), ev("b", 10000, 10400, "tap", pos=[5, 5])]
        assert len(merge_unlabeled_taps(events, {})) == 2

    def test_large_gap_not_merged(self):
        events = [ev("a", 9000, 9400, "tap"), ev("b", 20000, 20400, "tap")]
        assert len(merge_unlabeled_taps(events, {})) == 2

    def test_merged_sentence_counts_screens(self):
        events = [ev("a", 9000, 9400, "tap"), ev("b", 10000, 10400, "tap"),
                  ev("c", 10600, 11000, "tap")]
        out = merge_unlabeled_taps(events, {})
        text, note = _sentence(out[0], "")
        assert "3 screens" in text
        assert note != ""


class TestMergeRepeatedTargets:
    def test_double_activation_collapses(self):
        # real regression: 'Tap "Create a password"' twice, 2s apart
        events = [ev("a", 22000, 22400, "tap", pos=[5, 5]),
                  ev("b", 24000, 24400, "tap", pos=[5, 5])]
        targets = {"a": "Create a password", "b": "Create a password"}
        out = merge_repeated_targets(events, targets)
        assert len(out) == 1
        text, _ = _sentence(out[0], "Create a password")
        assert text == 'Tap "Create a password" (2 times).'

    def test_different_targets_not_merged(self):
        events = [ev("a", 22000, 22400, "tap", pos=[5, 5]),
                  ev("b", 24000, 24400, "tap", pos=[5, 5])]
        targets = {"a": "Save", "b": "Cancel"}
        assert len(merge_repeated_targets(events, targets)) == 2

    def test_distant_repeats_not_merged(self):
        events = [ev("a", 22000, 22400, "tap", pos=[5, 5]),
                  ev("b", 60000, 60400, "tap", pos=[5, 5])]
        targets = {"a": "Save", "b": "Save"}
        assert len(merge_repeated_targets(events, targets)) == 2

    def test_unlabeled_pairs_not_merged_here(self):
        events = [ev("a", 22000, 22400, "tap"), ev("b", 24000, 24400, "tap")]
        assert len(merge_repeated_targets(events, {})) == 2


class TestSentences:
    CASES = [
        (ev("a", 0, 100, "tap", pos=[10, 20]), "Open Deal"),
        (ev("a", 0, 100, "tap", pos=[10, 20]), ""),
        (ev("a", 0, 100, "tap"), ""),
        (ev("a", 0, 100, "click", pos=[10, 20]), "Submit"),
        (ev("a", 0, 100, "form_submit", pos=[10, 20]), "Save"),
        (ev("a", 0, 100, "type", final_text="hello"), ""),
        (ev("a", 0, 100, "scroll", direction="down"), ""),
        (ev("a", 0, 100, "navigate", target="app.example.com"), ""),
        (ev("a", 0, 100, "navigate", target=""), ""),
        (ev("a", 0, 100, "dialog_open"), ""),
        (ev("a", 0, 100, "unknown_kind"), ""),
    ]

    @pytest.mark.parametrize("event,target", CASES)
    def test_no_special_chars_in_text_or_note(self, event, target):
        text, note = _sentence(event, target)
        bad = (set(text) | set(note)) & FORBIDDEN_CHARS
        assert not bad, f"special chars {bad} in {text!r} / {note!r}"

    @pytest.mark.parametrize("event,target", CASES)
    def test_text_is_a_sentence(self, event, target):
        text, _ = _sentence(event, target)
        assert text and text[0].isupper() and text.endswith(".")

    def test_named_tap(self):
        text, note = _sentence(ev("a", 0, 100, "tap", pos=[1, 2]), "Open Deal")
        assert text == 'Tap "Open Deal".'
        assert note == ""

    def test_navigate_with_target(self):
        text, _ = _sentence(ev("a", 0, 100, "navigate", target="app.example.com"), "")
        assert text == 'Go to "app.example.com".'

    def test_typing(self):
        text, _ = _sentence(ev("a", 0, 100, "type", final_text="hello"), "")
        assert text == 'Type "hello" into the field.'


class TestSynthesizePipeline:
    def test_full_flow_merges_and_numbers(self):
        events = [
            ev("s1", 0, 400, "scroll", direction="down", magnitude_px=50),
            ev("a", 5000, 5400, "tap", pos=[5, 5], confidence=0.75),
            ev("b", 6000, 6400, "tap", pos=[5, 5], confidence=0.75),
            ev("c", 9000, 9400, "tap"), ev("d", 10000, 10400, "tap"),
        ]
        targets = {"a": "Account", "b": "Account"}
        steps, omitted = synthesize_steps(events, FRAMES, targets, bug_t_ms=None)
        texts = [s.text for s in steps]
        assert len(steps) == 3, texts
        assert steps[0].action_type == "scroll"
        assert "(2 times)" in steps[1].text
        assert "2 screens" in steps[2].text
        assert [s.index for s in steps] == [1, 2, 3]

    def test_events_after_bug_are_omitted(self):
        events = [ev("a", 1000, 1200, "tap", pos=[5, 5]),
                  ev("b", 9000, 9300, "tap", pos=[5, 5])]
        steps, omitted = synthesize_steps(events, FRAMES, {}, bug_t_ms=2000)
        assert len(steps) == 1
        assert len(omitted) == 1

    def test_every_step_has_evidence(self):
        events = [ev("a", 400, 600, "tap", pos=[5, 5]),
                  ev("b", 4800, 5100, "scroll", direction="up")]
        steps, _ = synthesize_steps(events, FRAMES, {}, bug_t_ms=None)
        assert all(s.evidence for s in steps)
        assert steps[0].evidence[0].frame_id == "f1"
        assert steps[1].evidence[0].frame_id == "f2"


class TestPreconditions:
    FRAMES2 = [{"frame_id": "f1", "t_ms": 300, "segment_idx": 0, "storage_key": "k"}]

    def test_readable_title_becomes_precondition(self):
        blocks = [{"frame_id": "f1", "t_ms": 300, "text": "demo.example.com/app",
                   "bbox": [0, 0, 200, 20], "confidence": 0.8, "role": "url_bar"}]
        pre = build_preconditions(blocks, self.FRAMES2)
        assert pre[0].grounding == "observed"
        assert "demo.example.com/app" in pre[0].text

    def test_garbage_title_falls_back_to_assumed(self):
        # real regression: precondition read 'Start on "8:54 a @ 00:00 58,0..."'
        blocks = [{"frame_id": "f1", "t_ms": 300,
                   "text": "8:54 a @ 00:00 58,0 ira al ull a",
                   "bbox": [0, 0, 200, 20], "confidence": 0.8, "role": "title_bar"}]
        pre = build_preconditions(blocks, self.FRAMES2)
        assert pre[0].grounding == "assumed"
        assert "8:54" not in pre[0].text

    def test_no_blocks_falls_back(self):
        pre = build_preconditions([], self.FRAMES2)
        assert pre[0].grounding == "assumed"


class TestTitlePick:
    @staticmethod
    def _block(text, role="title_bar", conf=0.8):
        return {"frame_id": "f1", "t_ms": 100, "text": text,
                "bbox": [0, 0, 100, 20], "confidence": conf, "role": role}

    def test_best_quality_wins_over_first(self):
        # real regression: title read 'Functional in 409 ZS teh ul ull'
        # because the first passing block won instead of the best one
        from vidrepro.worker.stages.s10_synthesis import pick_title_source
        blocks = [self._block("409 ZS teh ul ull"),
                  self._block("trader.iforex.com/webpl4/trading", role="url_bar")]
        assert pick_title_source(blocks) == "trader.iforex.com/webpl4/trading"

    def test_garbage_only_yields_empty(self):
        from vidrepro.worker.stages.s10_synthesis import pick_title_source
        assert pick_title_source([self._block("8:55 B ra) 00:52 ull")]) == ""

    def test_low_confidence_ignored(self):
        from vidrepro.worker.stages.s10_synthesis import pick_title_source
        assert pick_title_source([self._block("Settings", conf=0.4)]) == ""

    def test_signal_bar_misreads_never_title(self):
        # real regression: title read 'Functional in ies ull ull' — signal
        # glyph misreads pass the vowel rule without the artifact stoplist
        from vidrepro.worker.stages.s10_synthesis import pick_title_source
        blocks = [self._block("ies ull ull"),
                  self._block("trader.iforex.com/webpl4/trading", role="url_bar")]
        assert pick_title_source(blocks) == "trader.iforex.com/webpl4/trading"

    def test_truncated_logo_loses_to_full_name(self):
        # real regression: title read 'Functional in iF' — a 2-letter logo
        # fragment scored perfect quality and beat the URL
        from vidrepro.worker.stages.s10_synthesis import pick_title_source
        blocks = [self._block("iF"),
                  self._block("trader.iforex.com/webpl4/trading", role="url_bar")]
        assert pick_title_source(blocks) == "trader.iforex.com/webpl4/trading"


class TestCompress:
    def test_scroll_runs_merge(self):
        events = [ev("a", 0, 500, "scroll", direction="down", magnitude_px=100),
                  ev("b", 900, 1400, "scroll", direction="down", magnitude_px=80),
                  ev("c", 1800, 2200, "scroll", direction="up", magnitude_px=50)]
        kept, suppressed = compress(events)
        assert len(kept) == 2
        assert kept[0]["detail"]["magnitude_px"] == 180

    def test_navigation_right_after_click_is_effect(self):
        events = [ev("a", 1000, 1200, "click", pos=[1, 1]),
                  ev("b", 1500, 1800, "navigate", target="checkout")]
        kept, suppressed = compress(events)
        assert [e["type"] for e in kept] == ["click"]

    def test_compress_does_not_mutate_input(self):
        events = [ev("a", 0, 500, "scroll", direction="down", magnitude_px=100),
                  ev("b", 900, 1400, "scroll", direction="down", magnitude_px=80)]
        compress(events)
        assert events[0]["detail"]["magnitude_px"] == 100

    def test_navigation_from_same_boundary_as_later_tap_suppressed(self):
        # real regression: cursor-dwell timestamps sort the tap AFTER the
        # navigation born from the same screen transition, producing
        # 'Go to the next screen' + 'Tap X' step pairs
        events = [ev("n", 5000, 5300, "navigate", target=""),
                  ev("t", 5100, 5200, "tap", pos=[5, 5])]
        kept, suppressed = compress(events)
        assert [e["type"] for e in kept] == ["tap"]
        assert suppressed[0]["type"] == "navigate"

    def test_dialog_then_answering_click_keeps_both(self):
        # a dialog opening and the user clicking OK inside it are two facts
        events = [ev("d", 5000, 5300, "dialog_open"),
                  ev("t", 5100, 5200, "tap", pos=[5, 5])]
        kept, _ = compress(events)
        assert [e["type"] for e in kept] == ["dialog_open", "tap"]

    def test_navigation_within_effect_window_after_tap_suppressed(self):
        events = [ev("t", 5000, 5100, "tap", pos=[5, 5]),
                  ev("n", 6800, 7100, "navigate", target="")]  # 1.7s later
        kept, _ = compress(events)
        assert [e["type"] for e in kept] == ["tap"]
