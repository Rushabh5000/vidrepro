"""Regression tests for OCR role classification. The top strip of a mobile
recording holds the status bar (clock, battery, signal) — it must classify
as status_bar, never as title_bar/url_bar, or clocks leak into report
titles, preconditions, and navigation targets."""
import pytest

from vidrepro.worker.vision.ocr import classify_role

H, W = 1920, 1080  # portrait mobile frame


def top(text):
    return classify_role((300, 20, 200, 40), H, W, text)


@pytest.mark.parametrize("text", [
    "8:55",
    "8:54 a @ 00:00 58,0 ira al ull a",
    "08:55 74%",
    "8:55 B ra) 00:52 ull",
    "a ull B",          # unreadable top-strip mush
    "= §",
])
def test_top_strip_clock_and_mush_is_status_bar(text):
    assert top(text) == "status_bar"


@pytest.mark.parametrize("text,role", [
    ("trader.iforex.com/webpl4/trading", "url_bar"),
    ("demo.example.com/app", "url_bar"),
    ("Settings", "title_bar"),
    ("Create your account", "title_bar"),
])
def test_top_strip_real_chrome(text, role):
    assert top(text) == role


def test_url_below_status_bar_is_url_bar():
    # full-phone recordings: browser URL bar sits below the status bar
    # (~6-16% height); URL-shaped text there is chrome, not page content
    assert classify_role((100, int(H * 0.09), 500, 40), H, W,
                         "25 trader.iforex.com/webpl4/trading") == "url_bar"


def test_plain_text_below_status_bar_is_not_url_bar():
    role = classify_role((100, int(H * 0.09), 500, 40), H, W,
                         "Create your account")
    assert role != "url_bar"


def test_bottom_strip_is_status_bar():
    assert classify_role((300, H - 40, 200, 30), H, W, "Home Search Profile") == "status_bar"


def test_small_mid_screen_text_is_button_like():
    assert classify_role((400, 900, 180, 60), H, W, "Open Deal") == "button_like"


def test_long_mid_screen_text_is_body():
    assert classify_role(
        (100, 900, 800, 60), H, W,
        "By submitting your information, you agree to our terms") == "body"
