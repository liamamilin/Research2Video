"""Keep commands, flags, code and version numbers exactly as written on screen.

The renderer draws "-" edge to edge in every bundled macOS font, so "--models" reads as
"–models"; it also keeps each ASCII run on one line. These helpers mirror that behaviour
for the Python text-fit checks and reject on-screen wording that alters technical tokens.
"""
from __future__ import annotations

import re

# Must match HYPHEN_GAP_EM and TECH_RUN in g1/renderer/src/Root.tsx.
HYPHEN_GAP_EM = 0.12
UNIT = re.compile(r"`[^`\n]{1,40}`|[!-~]{2,40}|[\s\S]")
TOKEN = re.compile(r"`([^`\n]+)`|[!-~]+")
# "cost/time" is shorthand, not code; paths still carry "." or "_" (eval_crew.py, g2/run_g2.py).
TECHNICAL = re.compile(r"(?:^|[A-Za-z0-9])[-_.][A-Za-z0-9]|^--?[A-Za-z0-9]|\[['\"]")
DASH_BEFORE_LATIN = re.compile(r"[\u2010-\u2015\u2212]+(?=[A-Za-z0-9])")


def wrap_units(text: str) -> list[str]:
    """Characters, with short ASCII runs and backticked code kept as one unbreakable unit."""
    return UNIT.findall(text)


def display_width(font, text: str) -> float:
    gaps = sum(1 for a, b in zip(text, text[1:]) if a == b == "-")
    return font.getlength(text) + gaps * HYPHEN_GAP_EM * font.size


def technical_tokens(text: str) -> list[str]:
    tokens = []
    for match in TOKEN.finditer(text):
        token = match.group(1) or match.group(0).lstrip("(\"'").rstrip(".,;:!?)+*")
        if token and (match.group(1) or TECHNICAL.search(token)):
            tokens.append(token)
    return tokens


def _occurs(token: str, reference: str) -> bool:
    # Whole-token match: "v1.15.27" is not "1.15.27", and "-models" is not "--models".
    return re.search(r"(?<![A-Za-z0-9_.-])" + re.escape(token) + r"(?![A-Za-z0-9_-])", reference) is not None


def altered_technical_text(text: str, reference: str) -> str | None:
    """Return the first technical token in on-screen text that the reference does not contain verbatim."""
    for dash in DASH_BEFORE_LATIN.finditer(text):
        word = re.match(r"[\u2010-\u2015\u2212]+[A-Za-z0-9_.-]*", text[dash.start():]).group(0)
        if word not in reference:
            return word
    for token in technical_tokens(text):
        if not _occurs(token, reference):
            return token
    return None
