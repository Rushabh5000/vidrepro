"""Deterministic OCR text-quality heuristics.

Screen recordings produce a lot of OCR mush: status-bar clocks ("8:55"),
half-read tickers ("+1.50% [ 4138.58 B 4139.41"), and letter salad
("B ra) 00:52 ull"). Nothing here uses a dictionary or a model — every rule
is a cheap, explainable string heuristic, so results are stable across runs.

Used by event detection (click-target naming, typing text, navigation
targets) and by precondition building. The rule of thumb throughout: a bad
label is worse than no label, because a wrong target sends the reader
clicking the wrong thing.
"""
import re

# a time token plus fewer than this many word characters = status-bar clock.
# 10 covers real misreads like "11:57 ZS all all" (8 residue chars of antenna
# glyphs read as words) while keeping genuine titles ("Lunch at 12:30 Cafe").
CLOCK_RESIDUE_CHARS = 10

TIME_TOKEN = re.compile(r"^\W*\d{1,2}:\d{2}(:\d{2})?\W*$")
TIME_ANYWHERE = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\b")
DOMAIN_TOKEN = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/\S*)?$", re.IGNORECASE)
EMAIL_TOKEN = re.compile(r"^[\w.+-]+@[\w-]+\.[a-z]{2,}$", re.IGNORECASE)
NUMBER_TOKEN = re.compile(r"^[+\-]?\d[\d,.]*%?$")
WORD_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'&.-]*$")
ALNUM_WORD = re.compile(r"^[A-Za-z][A-Za-z]*\d{1,6}$")  # Password123, user2024
BATTERY = re.compile(r"^\d{1,3}%$")

# characters that carry no meaning in a UI label and are usually OCR artifacts
_STRIP_CHARS = "«»©®™•¤§|`~^*_=<>{}[]\\ ‘’“”"
_WS = re.compile(r"\s+")

# short English words that are legitimate 2-letter labels/prepositions
_SHORT_OK = frozenset(
    "ok on in to of at go no up or if my we he it is as an am do be by".split())

# recurring Tesseract misreads of status-bar glyphs (signal bars, wifi fan,
# battery) and carrier indicators — observed verbatim across real mobile
# recordings; they pass the vowel rule but are never real UI labels
_OCR_ARTIFACTS = frozenset(
    "ull ul ules ies wll atl lte volte 5g 4g 3g".split())


def clean(text: str) -> str:
    """Normalize an OCR string: drop artifact characters, collapse whitespace."""
    if not text:
        return ""
    out = []
    for ch in text:
        if ch in _STRIP_CHARS or ord(ch) < 32:
            out.append(" ")
        else:
            out.append(ch)
    return _WS.sub(" ", "".join(out)).strip(" .,;:-")


def _classify_token(tok: str) -> str:
    """One of: time, word, number, domain, junk."""
    bare = tok.strip(".,;:!?()\"'")
    if not bare:
        return "junk"
    if TIME_TOKEN.match(bare):
        return "time"
    if DOMAIN_TOKEN.match(bare) or EMAIL_TOKEN.match(bare):
        return "domain"
    if BATTERY.match(bare) or NUMBER_TOKEN.match(bare):
        return "number"
    if ALNUM_WORD.match(bare):
        letters = "".join(c for c in bare if c.isalpha()).lower()
        if len(letters) >= 3 and any(v in letters for v in "aeiouy"):
            return "word"
        return "junk"
    if WORD_TOKEN.match(bare):
        low = bare.lower()
        if low in _OCR_ARTIFACTS:
            return "junk"
        if len(bare) >= 3:
            # require a vowel (or y) — "wll", "brt" are OCR noise
            if any(v in low for v in "aeiouy"):
                return "word"
            if bare.isupper() and len(bare) <= 5:  # acronyms: SEP, DAX, GBP
                return "word"
            return "junk"
        if low in _SHORT_OK or (bare.isupper() and len(bare) == 2):
            return "word"
        return "junk"
    return "junk"


def label_quality(text: str) -> float:
    """Score 0..1: how usable is this string as a UI label / target name.

    0 means "do not show this to a human". Anything >= 0.5 reads as real text.
    """
    t = clean(text)
    if len(t) < 2:
        return 0.0
    tokens = t.split()
    chars = {"time": 0, "word": 0, "number": 0, "domain": 0, "junk": 0}
    counts = {"time": 0, "word": 0, "number": 0, "domain": 0, "junk": 0}
    for i, tok in enumerate(tokens):
        kind = _classify_token(tok)
        # a short token repeated back-to-back ("all all", "ull ull") is a
        # glyph row read twice, not language — real labels don't stutter
        if kind == "word" and len(tok) <= 3 and (
                (i > 0 and tokens[i - 1].lower() == tok.lower())
                or (i + 1 < len(tokens) and tokens[i + 1].lower() == tok.lower())):
            kind = "junk"
        chars[kind] += len(tok)
        counts[kind] += 1

    total = sum(chars.values()) or 1
    if counts["domain"]:
        return 0.9  # URLs are precise targets even with noise around them

    # clock/status junk: time tokens present but almost no real words
    if counts["time"] and chars["word"] < CLOCK_RESIDUE_CHARS:
        return 0.0
    # letter salad: junk outweighs words
    if chars["junk"] > chars["word"]:
        return 0.0
    # a lone short pseudo-word swimming in junk ("a ull B") is still salad —
    # real short labels ("Buy", "OK") come clean, without junk neighbors
    if counts["word"] == 1 and chars["word"] <= 3 and counts["junk"] >= 1:
        return 0.0
    # pure numbers are only a usable label when short and single ("+1.50%")
    if counts["word"] == 0:
        if counts["number"] == 1 and len(t) <= 10 and counts["junk"] == 0:
            return 0.5
        return 0.0

    good = chars["word"] + 0.5 * chars["number"]
    return round(min(1.0, good / total), 3)


def is_readable(text: str) -> bool:
    """True when the string is fit to appear in a report as a target name."""
    return label_quality(text) >= 0.5


def looks_like_clock(text: str) -> bool:
    """Status-bar detector: '8:55', '8:55 AM', '08:55 74%', '8:55 (c) 5G'."""
    t = clean(text)
    if not t:
        return False
    if not TIME_ANYWHERE.search(t):
        return False
    residue = TIME_ANYWHERE.sub(" ", t)
    residue_words = [tok for tok in residue.split() if _classify_token(tok) == "word"
                     and tok.lower() not in ("am", "pm")]
    return sum(len(w) for w in residue_words) < CLOCK_RESIDUE_CHARS


def _regular_caps(tok: str) -> bool:
    """lower / UPPER / Title are regular; OCR mush like 'iIFOREX', 'wLl' is not."""
    bare = "".join(c for c in tok if c.isalpha())
    if not bare:
        return True
    return bare.islower() or bare.isupper() or (
        bare[0].isupper() and bare[1:].islower())


def typed_text_ok(text: str) -> bool:
    """Gate for typing detection: the 'text the user typed' must be clean
    prose/identifier-like, never clock or ticker noise or re-OCR'd logos."""
    t = clean(text)
    if len(t) < 3 or TIME_ANYWHERE.search(t):
        return False
    letters = sum(1 for c in t if c.isalpha())
    digits = sum(1 for c in t if c.isdigit())
    if digits >= 3 and letters == 0 and len(t) <= 20:
        return True  # pure numbers: PINs, codes, amounts
    if letters < 3:
        return False
    # every word must be conventionally capitalized — logos re-OCR'd between
    # frames produce mixed-caps salad ("iIFOREX"), humans type regular words
    if not all(_regular_caps(tok) for tok in t.split()):
        return False
    return label_quality(t) >= 0.65


def sanitize_label(text: str, max_len: int = 60) -> str:
    """Cleaned, length-capped label — or empty string when unusable.

    A URL anywhere in the string wins outright: browser chrome OCR reads as
    'fm) 6 trader.example.com/path 3', and the URL alone is the usable part.
    Otherwise junk tokens are trimmed off both ends before the quality gate.
    """
    t = clean(text)
    if not t:
        return ""
    tokens = t.split()
    for tok in tokens:
        bare = tok.strip(".,;:!?()\"'")
        if bare and (DOMAIN_TOKEN.match(bare) or EMAIL_TOKEN.match(bare)):
            t = bare
            break
    else:
        # the whole string must already read as text — stripping junk off a
        # salad ("«209 ee wll B" -> "209") must not promote it into a label
        if not is_readable(t):
            return ""
        while tokens and _classify_token(tokens[0]) == "junk":
            tokens.pop(0)
        while tokens and _classify_token(tokens[-1]) == "junk":
            tokens.pop()
        t = " ".join(tokens)
        if not is_readable(t):
            return ""
    if len(t) > max_len:
        t = t[: max_len - 3].rstrip() + "..."
    return t
