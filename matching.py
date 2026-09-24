"""OCR text normalization and tolerant Pokémon-name matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - exercised only without dependencies
    fuzz = process = None  # type: ignore[assignment]

_PUNCTUATION = re.compile(r"[^\w\s-]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# These substitutions are intentionally applied only to OCR text, never to
# configured targets.  Keeping the original target in Match.target makes
# notifications and tracker keys stable and human-friendly.
_OCR_TYPO_REPLACEMENTS = (
    ("|", "i"),
    ("ı", "i"),
    ("0", "o"),
    ("1", "i"),
    ("5", "s"),
    ("8", "b"),
)


def normalize_text(value: str) -> str:
    """Normalize OCR noise while preserving Cyrillic and Latin letters."""
    value = value.casefold().replace("ё", "е")
    value = _PUNCTUATION.sub(" ", value)
    return _SPACES.sub(" ", value).strip()


def correct_ocr_typos(value: str) -> str:
    """Apply conservative, common OCR substitutions to recognized text."""
    for wrong, right in _OCR_TYPO_REPLACEMENTS:
        value = value.replace(wrong, right)
    return value


def _fallback_score(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio() * 100


@dataclass(frozen=True)
class Match:
    target: str
    score: float
    source: str
    area: str = ""


def find_matches(
    text: str,
    targets: Iterable[str | tuple[str, Iterable[str]]],
    threshold: int = 82,
) -> list[Match]:
    """Return distinct target matches found in OCR text, best score first."""
    # Score each OCR line independently.  A long unrelated line should not
    # dilute a short, correctly recognized Pokémon name.
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not raw_lines and text.strip():
        raw_lines = [text.strip()]
    normalized_lines = [normalize_text(correct_ocr_typos(line)) for line in raw_lines]
    haystack = normalize_text(correct_ocr_typos(text))
    results: list[Match] = []
    for item in targets:
        if isinstance(item, tuple):
            target, aliases = item
            options = [target, *aliases]
        else:
            target, options = item, [item]
        best_source = ""
        best_score = 0.0
        for option in options:
            needle = normalize_text(option)
            if not needle:
                continue
            line_scores: list[float] = []
            for line in normalized_lines or [haystack]:
                if needle in line:
                    line_scores.append(100.0)
                elif process is not None:
                    found = process.extractOne(needle, [line], scorer=fuzz.partial_ratio)
                    line_scores.append(float(found[1]) if found else 0.0)
                else:
                    line_scores.append(_fallback_score(needle, line))
            score = max(line_scores, default=0.0)
            if score > best_score:
                best_score, best_source = score, option
        if best_score >= threshold:
            results.append(Match(str(target), round(best_score, 2), best_source))
    return sorted(results, key=lambda match: match.score, reverse=True)
