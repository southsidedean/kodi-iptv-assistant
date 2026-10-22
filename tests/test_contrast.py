"""The Dashboard's colours stay readable: WCAG AA contrast in both themes."""

import re

import pytest

from kia import htmlpage

# (foreground, background, the least contrast it needs). 4.5 for text; 3 for the edges of
# controls and for the coverage bar's coloured parts, which also have their numbers as text.
PAIRS = [
    ("fg", "bg", 4.5), ("fg", "card", 4.5), ("fg", "note", 4.5), ("fg", "info-bg", 4.5), ("fg", "warn-bg", 4.5),
    ("muted", "bg", 4.5), ("muted", "card", 4.5), ("muted", "info-bg", 4.5), ("muted", "warn-bg", 4.5),
    ("good", "good-bg", 4.5), ("warn", "warn-bg", 4.5), ("bad", "bad-bg", 4.5), ("info", "info-bg", 4.5),
    ("good", "card", 4.5), ("warn", "card", 4.5), ("bad", "card", 4.5), ("info", "card", 4.5),
    ("control", "card", 3), ("control", "bg", 3),
    ("bar-full", "card", 3), ("bar-part", "card", 3), ("bar-dead", "card", 3),
]  # fmt: skip


def themes() -> dict[str, dict[str, str]]:
    blocks = re.findall(r":root \{(.*?)\}", htmlpage.STYLE, re.S)
    light = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", blocks[0]))
    return {"light": light, "dark": {**light, **dict(re.findall(r"--([\w-]+):\s*(#[0-9a-f]{6})", blocks[1]))}}


def luminance(colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(int(colour[i : i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colours_have_enough_contrast(theme):
    t = themes()[theme]
    low = [f"{fg} on {bg}: {contrast(t[fg], t[bg]):.2f} (needs {need})" for fg, bg, need in PAIRS
           if contrast(t[fg], t[bg]) < need]  # fmt: skip
    assert not low
