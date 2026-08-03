from vidrepro.worker.synthesis import confidence as conf


def test_quality_prior_penalizes_flags():
    assert conf.quality_prior([]) == 1.0
    assert conf.quality_prior(["blurry"]) == 0.85
    assert conf.quality_prior(["blurry", "low_fps"]) < 0.85
    assert conf.quality_prior(["blurry", "low_fps", "camera_capture",
                              "cursor_invisible"]) >= 0.6  # floor


def test_step_confidence_bounds():
    c = conf.step_confidence(0.95, [0.9], 1.0, 1.0)
    assert 0.9 <= c <= 1.0
    c = conf.step_confidence(0.3, [], 0.4, 0.7)
    assert c < 0.5


def test_propagation_dampens_forward():
    out = conf.propagate([0.9, 0.9, 0.9])
    assert out[0] == 0.9
    assert out[1] < 0.9
    assert out[2] <= out[1] + 1e-9


def test_overall_neutral_when_expected_null():
    with_null = conf.overall_confidence([0.8], 0.8, 0.9, None)
    with_assumed = conf.overall_confidence([0.8], 0.8, 0.9, "assumed")
    assert with_null > with_assumed  # honesty (null) scores above weak guessing


def test_coverage():
    assert conf.coverage(5000, 10000) == 0.5
    assert conf.coverage(20000, 10000) == 1.0
    assert conf.coverage(1000, 0) == 0.0
