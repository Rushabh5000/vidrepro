"""Regression tests for OCR text-quality heuristics.

The REJECT fixtures are real strings that appeared in reports generated from
real screen recordings (mobile status bars, trading tickers, half-read
logos). If any of them starts passing again, reports regress to mush.
"""
import pytest

from vidrepro.worker.textquality import (
    clean,
    is_readable,
    label_quality,
    looks_like_clock,
    sanitize_label,
    typed_text_ok,
)

# ---------------------------------------------------------------- clean()

@pytest.mark.parametrize("raw,expected", [
    ("iFOREX«", "iFOREX"),
    ("= IFOREX« ©", "IFOREX"),
    ("«209»", "209"),
    ("  hello   world  ", "hello world"),
    ("your saved passwords.", "your saved passwords"),
    ("trailing colons::", "trailing colons"),
    ("", ""),
    ("   ", ""),
    ("™®© only", "only"),
    ("tab\there", "tab here"),
    ("a\x00b\x1fc", "a b c"),
    ("keep (parens) and 40%", "keep (parens) and 40%"),
    ("keep /slash/and-dash", "keep /slash/and-dash"),
])
def test_clean(raw, expected):
    assert clean(raw) == expected


# ------------------------------------------------- garbage must be rejected

GARBAGE = [
    "8:55",                                  # status-bar clock
    "8:55 @",
    "8:55 B ra) 00:52 ull",                  # clock + signal-bar mush (real)
    "8:54 a @ 00:00 58,0 ira al ull a",      # real report title regression
    "8:55 B 01:24 «209 ee wll B",            # real typing false positive
    "8:58 : 09 SS at atl",                   # real
    "8:58 ® 04:31 «WA a all Be",             # real
    "08:55 74%",                             # clock + battery
    "+1.50% [ 4138.58 B 4139.41",            # ticker fragment (real)
    "wll brt kkj",                           # vowel-less OCR salad
    "b Q w",                                 # single letters
    "= §",
    "( |",
    "",
    " ",
    "a",
    "4138.58 4139.41 4140.02",               # multiple bare numbers
    "ies ull ull",                           # signal-bar glyph misreads (real)
    "LTE ull 4G",                            # carrier + antenna artifacts
    "11:57 ZS all all",                      # clock + antenna-as-words (real)
    "Mee all all",                           # stuttered glyph row (real title regression)
    "all all",
]


@pytest.mark.parametrize("text", GARBAGE)
def test_garbage_rejected(text):
    assert not is_readable(text), f"should reject: {text!r}"


@pytest.mark.parametrize("text", GARBAGE)
def test_garbage_never_becomes_a_label(text):
    assert sanitize_label(text) == "", f"should sanitize to empty: {text!r}"


# ---------------------------------------------------- real labels must pass

GOOD_LABELS = [
    "Open Deal",
    "Create a password",
    "Germany 40 (SEP)",
    "Sign In",
    "OK",
    "Submit",
    "Driver's License",
    "Available Margin",
    "Gold",
    "Settings",
    "Add to cart",
    "iforex.in",
    "trader.iforex.com/webpl4/trading",
    "user@example.com",
    "Password123",
    "Continue with Google",
    "your saved passwords",
    "Retry",
]


@pytest.mark.parametrize("text", GOOD_LABELS)
def test_real_labels_accepted(text):
    assert is_readable(text), f"should accept: {text!r}"
    assert sanitize_label(text) != ""


def test_compact_price_is_borderline_usable():
    # a single short number ("+1.50%") can be a legitimate button label
    assert label_quality("+1.50%") == 0.5


def test_words_beat_numbers_in_quality():
    assert label_quality("Open Deal") > label_quality("+1.50%")


# ------------------------------------------------------- looks_like_clock()

@pytest.mark.parametrize("text,expected", [
    ("8:55", True),
    ("8:55 AM", True),
    ("08:55 74%", True),
    ("8:54 a @ 00:00 58,0 ira al ull a", True),
    ("8:55 B ra) 00:52 ull", True),
    ("12:30", True),
    ("11:57 ZS all all", True),
    ("Lunch at 12:30 Cafe", False),                   # real title with a time
    ("Meeting at 8:55 with the whole team", False),  # enough real words
    ("Germany 40", False),                            # no time at all
    ("Open Deal", False),
    ("", False),
])
def test_looks_like_clock(text, expected):
    assert looks_like_clock(text) is expected


# --------------------------------------------------------- typed_text_ok()

@pytest.mark.parametrize("text,expected", [
    ("hello world", True),
    ("john.doe@example.com", True),
    ("Password123", True),
    ("search query terms", True),
    ("1234", True),                      # PIN
    ("123456", True),                    # OTP
    ("12", False),                       # too short
    ("8:54 @ ® 00:11 «28 Se ll @", False),   # clock noise (real regression)
    ("8:55 B 01:24 «209 ee wll B", False),   # (real regression)
    ("iIFOREX 2]", False),               # re-OCR'd logo, irregular caps
    ("= iIFOREX« 2]", False),            # (real regression)
    ("hELLO", False),                    # irregular caps
    ("ab", False),
    ("Hello", True),
    ("HELLO", True),                     # regular all-caps
])
def test_typed_text_ok(text, expected):
    assert typed_text_ok(text) is expected, f"typed_text_ok({text!r})"


# -------------------------------------------------------- sanitize_label()

@pytest.mark.parametrize("raw,expected", [
    # URL anywhere in browser-chrome mush wins outright (real regressions)
    ("fm) 6 trader.iforex.com/webpl4/accour 3", "trader.iforex.com/webpl4/accour"),
    ("25 trader.iforex.com/webpl4/trading (©)", "trader.iforex.com/webpl4/trading"),
    ("26 iforex.in :", "iforex.in"),
    ("@ trader.iforex.com/webpl4/trading", "trader.iforex.com/webpl4/trading"),
    # junk trimmed off the ends, words kept
    ("= IFOREX« ©", "IFOREX"),
    ("Open Deal", "Open Deal"),
    ("Germany 40 (SEP)", "Germany 40 (SEP)"),
])
def test_sanitize_label(raw, expected):
    assert sanitize_label(raw) == expected


def test_sanitize_label_caps_length_with_ascii_ellipsis():
    long = "This is a very long label " * 5
    out = sanitize_label(long, max_len=60)
    assert len(out) <= 60
    assert out.endswith("...")
    assert "…" not in out


def test_sanitize_label_no_special_chars_survive():
    out = sanitize_label("«Open» ™Deal© _now_")
    for ch in "«»™©_":
        assert ch not in out
