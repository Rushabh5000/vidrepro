from vidrepro.worker.synthesis.steps import compress, synthesize_steps


def ev(id, t0, t1, type_, **detail):
    return {"id": id, "t_start_ms": t0, "t_end_ms": t1, "type": type_,
            "pos": detail.pop("pos", None), "detail": detail,
            "signals": [], "confidence": detail.pop("confidence", 0.8)}


FRAMES = [{"frame_id": "f1", "t_ms": 500, "segment_idx": 0, "storage_key": "k"},
          {"frame_id": "f2", "t_ms": 5000, "segment_idx": 1, "storage_key": "k"}]


def test_scroll_runs_merge():
    events = [
        ev("a", 0, 500, "scroll", direction="down", magnitude_px=100),
        ev("b", 900, 1400, "scroll", direction="down", magnitude_px=80),
        ev("c", 1800, 2200, "scroll", direction="up", magnitude_px=50),
    ]
    kept, suppressed = compress(events)
    assert len(kept) == 2  # two down-scrolls merged, up-scroll separate
    assert kept[0]["detail"]["magnitude_px"] == 180
    assert len(suppressed) == 1


def test_navigation_after_click_is_effect_not_step():
    events = [
        ev("a", 1000, 1200, "click", pos=[10, 10]),
        ev("b", 1500, 1800, "navigate", target="checkout"),
    ]
    kept, suppressed = compress(events)
    assert [e["type"] for e in kept] == ["click"]
    assert suppressed[0]["type"] == "navigate"


def test_standalone_navigation_is_a_step():
    events = [ev("b", 1500, 1800, "navigate", target="checkout")]
    kept, _ = compress(events)
    assert kept[0]["type"] == "navigate"


def test_events_after_bug_are_omitted():
    events = [
        ev("a", 1000, 1200, "click", pos=[10, 10]),
        ev("b", 9000, 9300, "click", pos=[20, 20]),
    ]
    steps, omitted = synthesize_steps(events, FRAMES, {}, bug_t_ms=2000)
    assert len(steps) == 1
    assert len(omitted) == 1
    assert omitted[0].t_start_ms == 9000


def test_steps_are_numbered_and_evidence_linked():
    events = [ev("a", 400, 600, "click", pos=[10, 10]),
              ev("b", 4800, 5100, "type", final_text="hello")]
    steps, _ = synthesize_steps(events, FRAMES, {"a": "Apply"}, bug_t_ms=None)
    assert [s.index for s in steps] == [1, 2]
    assert 'Apply' in steps[0].text
    assert steps[0].evidence[0].frame_id == "f1"
    assert steps[1].evidence[0].frame_id == "f2"
    assert 'hello' in steps[1].text
