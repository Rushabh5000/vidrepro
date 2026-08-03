"""Regression tests for s03 segmentation helpers — especially ambient-churn
suppression, which keeps live tickers / price feeds / playing videos from
being read as user navigation (the iforex trading-app regression)."""
import numpy as np

from vidrepro.worker.stages.s03_segment import (
    ambient_rate_per_min,
    build_segments,
    classify_transition,
    compute_threshold,
    find_boundaries,
)


def sample(t, score, change=0.0, lum=100.0):
    return {"t_ms": t, "score": score, "change_frac": change, "lum": lum}


def quiet_video(duration_ms=60_000, step=200, noise=0.001):
    return [sample(t, noise) for t in range(0, duration_ms, step)]


class TestThreshold:
    def test_floor_applies_on_static_video(self):
        assert compute_threshold(np.zeros(100)) == 0.08

    def test_adapts_upward_on_noisy_video(self):
        scores = np.array([0.2] * 50 + [0.9] * 5)
        assert compute_threshold(scores) > 0.2


class TestBoundaries:
    def test_first_sample_is_always_a_boundary(self):
        assert find_boundaries(quiet_video(), 0.08)[0] == 0

    def test_spike_creates_boundary(self):
        samples = quiet_video()
        samples[50]["score"] = 0.9
        assert find_boundaries(samples, 0.08) == [0, 50]

    def test_min_segment_spacing_enforced(self):
        samples = quiet_video(step=200)
        samples[50]["score"] = 0.9
        samples[51]["score"] = 0.9  # 200ms later — below MIN_SEGMENT_MS
        assert find_boundaries(samples, 0.08) == [0, 50]

    def test_spaced_spikes_both_kept(self):
        samples = quiet_video(step=200)
        samples[50]["score"] = 0.9
        samples[100]["score"] = 0.9  # 10s later
        assert find_boundaries(samples, 0.08) == [0, 50, 100]


class TestAmbientRate:
    def test_rare_change_has_low_rate(self):
        samples = quiet_video()
        samples[50]["score"] = 0.9
        assert ambient_rate_per_min(samples, 0.9, 60_000) == 1.0

    def test_ticker_churn_has_high_rate(self):
        samples = quiet_video()
        for i in range(0, len(samples), 5):  # a similar change every second
            samples[i]["score"] = 0.3
        assert ambient_rate_per_min(samples, 0.3, 60_000) >= 60

    def test_zero_score_is_zero_rate(self):
        assert ambient_rate_per_min(quiet_video(), 0.0, 60_000) == 0.0


class TestClassifyTransition:
    def test_first_is_start(self):
        assert classify_transition(0, 0.9, 0.9, quiet_video(), 60_000) == "start"

    def test_rare_full_screen_change_is_nav(self):
        samples = quiet_video()
        samples[50]["score"] = 0.9
        assert classify_transition(1, 0.8, 0.9, samples, 60_000) == "nav"

    def test_rare_partial_change_is_dialog(self):
        samples = quiet_video()
        samples[50]["score"] = 0.3
        assert classify_transition(1, 0.2, 0.3, samples, 60_000) == "dialog"

    def test_ticker_churn_is_minor(self):
        # the iforex regression: partial-screen changes every second must
        # not become dialog transitions
        samples = quiet_video()
        for i in range(0, len(samples), 5):
            samples[i]["score"] = 0.3
        assert classify_transition(1, 0.2, 0.3, samples, 60_000) == "minor"

    def test_video_playback_is_minor(self):
        # constant full-frame churn = embedded video, not navigation
        samples = [sample(t, 0.7, change=0.8) for t in range(0, 60_000, 200)]
        assert classify_transition(1, 0.8, 0.7, samples, 60_000) == "minor"

    def test_tiny_change_is_minor(self):
        assert classify_transition(1, 0.05, 0.1, quiet_video(), 60_000) == "minor"

    def test_short_clip_taps_are_not_ambient(self):
        # real regression: a 16s clip with 4 similar-magnitude taps computed
        # an inflated per-minute rate and suppressed every real transition
        samples = [sample(t, 0.001) for t in range(0, 16_000, 200)]
        for i in (20, 30, 45, 60):
            samples[i]["score"] = 0.35
        assert classify_transition(1, 0.3, 0.35, samples, 16_000) == "dialog"

    def test_short_clip_with_dense_churn_is_still_ambient(self):
        # a 30s clip fully dominated by a ticker must still suppress
        samples = [sample(t, 0.001) for t in range(0, 30_000, 200)]
        for i in range(0, len(samples), 5):
            samples[i]["score"] = 0.35
        assert classify_transition(1, 0.3, 0.35, samples, 30_000) == "minor"

    def test_tap_flurry_sees_its_siblings_but_stays_dialog(self):
        # numbers taken from a real 16s recording: 7 tap transitions at
        # score 0.65-0.84 clustered in 5.4-10.8s, plus mid-score scroll
        # samples in the same magnitude band. The transitions must not
        # suppress each other as "churn".
        samples = [sample(t, 0.001) for t in range(0, 16_400, 200)]
        for i, sc in [(27, 0.651), (32, 0.838), (34, 0.822), (49, 0.826),
                      (50, 0.739), (52, 0.762), (54, 0.715)]:
            samples[i]["score"] = sc
        for i in (10, 12, 14, 16, 20, 22, 24, 26):  # scroll activity
            samples[i]["score"] = 0.45
        assert classify_transition(1, 0.39, 0.651, samples, 16_400) == "dialog"

    def test_clustered_churn_without_spread_is_not_ambient(self):
        # 40 similar-magnitude changes packed into the first quarter of a
        # 2min video (e.g. an animation burst) — no full-timeline spread,
        # so real transitions elsewhere must not be suppressed
        samples = [sample(t, 0.001) for t in range(0, 120_000, 200)]
        for i in range(0, 40):
            samples[i]["score"] = 0.35
        assert classify_transition(1, 0.3, 0.35, samples, 120_000) == "dialog"


class TestBuildSegments:
    def test_static_video_is_one_segment(self):
        segments = build_segments(quiet_video(), 0.08)
        assert len(segments) == 1
        assert segments[0]["transition_type"] == "start"

    def test_two_navigations_make_three_segments(self):
        samples = quiet_video()
        samples[50] = sample(10_000, 0.9, change=0.8)
        samples[150] = sample(30_000, 0.9, change=0.8)
        segments = build_segments(samples, 0.08)
        assert [s["transition_type"] for s in segments] == ["start", "nav", "nav"]
        assert segments[1]["start_ms"] == 10_000
        assert segments[1]["end_ms"] == 30_000

    def test_segment_spans_cover_video(self):
        samples = quiet_video()
        samples[50] = sample(10_000, 0.9, change=0.8)
        segments = build_segments(samples, 0.08)
        assert segments[0]["start_ms"] == 0
        assert segments[-1]["end_ms"] > samples[-1]["t_ms"]
