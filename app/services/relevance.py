from __future__ import annotations

import re
from collections.abc import Iterable

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "did",
    "have",
    "i",
    "in",
    "is",
    "it",
    "last",
    "me",
    "my",
    "of",
    "on",
    "or",
    "search",
    "tell",
    "the",
    "to",
    "what",
    "when",
}


def tokenize_text(text: str | None) -> set[str]:
    if not text:
        return set()

    tokens = {
        match.group(0)
        for match in TOKEN_PATTERN.finditer(text.lower())
        if match.group(0) not in STOPWORDS
    }
    return expand_tokens(tokens)


def expand_tokens(tokens: Iterable[str]) -> set[str]:
    expanded = {token for token in tokens if token}

    for token in list(expanded):
        if token.endswith("ies") and len(token) > 4:
            expanded.add(f"{token[:-3]}y")
        elif token.endswith("s") and len(token) > 3:
            expanded.add(token[:-1])

    if "ev" in expanded:
        expanded.update({"electric", "vehicle"})

    if "electric" in expanded and "vehicle" in expanded:
        expanded.add("ev")

    return expanded


def overlap_score(
    query_tokens: set[str],
    metadata_tokens: set[str],
    *,
    weight: int,
) -> tuple[int, set[str]]:
    matched_tokens = query_tokens & metadata_tokens
    return len(matched_tokens) * weight, matched_tokens
