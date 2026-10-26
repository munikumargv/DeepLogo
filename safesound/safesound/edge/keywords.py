"""On-device keyword spotting over a fixed, school-configured term list.

The recogniser on the device (see ``audio.py``) is constrained to the
configured phrases, so its output is a stream of short candidate strings.
This module normalises them (English + Arabic), matches them against the
list and returns term ids. Nothing else is kept.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ..schemas import KeywordCategory

_ARABIC_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_ARABIC_MAP = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "گ": "ك", "چ": "ج", "پ": "ب", "ڤ": "ف",
})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _ARABIC_DIACRITICS.sub("", text).translate(_ARABIC_MAP)
    text = _NON_WORD.sub(" ", text)
    return " ".join(text.split())


@dataclass(frozen=True)
class Term:
    id: int
    text: str
    category: KeywordCategory
    lang: str = "en"

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(normalize(self.text).split())


def _token_match(a: str, b: str, fuzzy: bool) -> bool:
    if a == b:
        return True
    # Arabic clitics: "والحشيش" -> "حشيش"
    for prefix in ("وال", "بال", "فال", "لل", "ال", "و"):
        if a.startswith(prefix) and a[len(prefix):] == b:
            return True
    if fuzzy and min(len(a), len(b)) >= 5:
        return SequenceMatcher(None, a, b).ratio() >= 0.85
    return False


class KeywordSpotter:
    def __init__(self, terms: list[Term], version: int = 0, fuzzy: bool = True):
        self.version = version
        self.fuzzy = fuzzy
        # longest phrases first so "hide it" wins over "hide"
        self.terms = sorted(terms, key=lambda t: -len(t.tokens))

    def spot(self, utterance: str) -> list[Term]:
        tokens = normalize(utterance).split()
        used = [False] * len(tokens)
        hits: list[Term] = []
        for term in self.terms:
            tt = term.tokens
            if not tt:
                continue
            for i in range(len(tokens) - len(tt) + 1):
                if any(used[i:i + len(tt)]):
                    continue
                if all(_token_match(tokens[i + j], tt[j], self.fuzzy) for j in range(len(tt))):
                    hits.append(term)
                    for j in range(len(tt)):
                        used[i + j] = True
        return hits
