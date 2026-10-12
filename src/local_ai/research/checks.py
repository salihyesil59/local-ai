"""Checks that keep the model honest: numbers, citations and quotes of a draft against the tool output."""

from __future__ import annotations

import math
import re

NUMBER_RE = re.compile(r"(?<![\w.,])[-+]?\d[\d.,]*(?:[eE][-+]?\d+)?(?![\w])")
CITATION_RE = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")
REFERENCES_RE = re.compile(r"\n#{1,6}\s*(References|Kaynakça|Kaynaklar)\s*\n.*", re.DOTALL)
CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)


# -- numeric claim checking ---------------------------------------------------


def _candidate_values(token: str) -> set[float]:
    """Interpret a number token under both English (1,234.5) and Turkish (1.234,5) conventions."""
    token = token.strip("+").rstrip(".,")
    values = set()
    variants = {token}
    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            variants.add(token.replace(".", "").replace(",", "."))
        else:
            variants.add(token.replace(",", ""))
    elif "," in token:
        variants.add(token.replace(",", "."))
        variants.add(token.replace(",", ""))
    elif token.count(".") > 1:
        variants.add(token.replace(".", ""))
    elif re.fullmatch(r"-?\d{1,3}\.\d{3}", token):
        variants.add(token.replace(".", ""))  # Turkish thousands separator
    for v in variants:
        try:
            f = float(v)
        except ValueError:
            continue
        if math.isfinite(f):
            values.add(f)
    return values


def numbers_in(text: str) -> set[float]:
    values: set[float] = set()
    for token in NUMBER_RE.findall(text):
        values |= _candidate_values(token)
    return values


def _is_trivial(token: str) -> bool:
    """Small integers (counts, list items, section numbers) are not worth verifying."""
    t = token.strip("+-").rstrip(".,")
    return t.isdigit() and int(t) < 10


def unverified_numbers(draft: str, evidence: str, rel_tol: float = 5e-3) -> list[str]:
    """Numbers in the draft that do not appear (within rounding) in any tool output.

    Citation markers, the References section and code blocks are ignored.
    """
    body = REFERENCES_RE.sub("", draft)
    body = CODE_BLOCK_RE.sub("", body)
    body = CITATION_RE.sub("", body)
    known = numbers_in(evidence)
    missing: list[str] = []
    for token in NUMBER_RE.findall(body):
        if _is_trivial(token) or token in missing:
            continue
        candidates = _candidate_values(token)
        if not candidates:
            continue
        if any(
            math.isclose(c, k, rel_tol=rel_tol, abs_tol=1e-12) or math.isclose(abs(c), abs(k), rel_tol=rel_tol)
            for c in candidates
            for k in known
        ):
            continue
        missing.append(token.rstrip(".,"))
    return missing


def unknown_citations(draft: str, source_count: int) -> list[int]:
    body = REFERENCES_RE.sub("", draft)
    cited = set()
    for marker in CITATION_RE.findall(body):
        for n in re.findall(r"\d+", marker):
            cited.add(int(n))
    return sorted(n for n in cited if n < 1 or n > source_count)


# -- quote checking -------------------------------------------------------------


def _norm_words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def quote_supported(quote: str, evidence_norm: str, threshold: float = 0.8) -> bool:
    """True if most 4-word shingles of the quote occur in the (normalized) evidence."""
    words = _norm_words(quote)
    if not words:
        return True
    if len(words) < 4:
        return " ".join(words) in evidence_norm
    shingles = [" ".join(words[i : i + 4]) for i in range(len(words) - 3)]
    found = sum(1 for s in shingles if s in evidence_norm)
    return found / len(shingles) >= threshold


def unverified_quotes(sources: list[dict], evidence: str) -> list[str]:
    """Sources whose registered quotes do not appear in any tool output."""
    evidence_norm = " ".join(_norm_words(evidence))
    bad = []
    for src in sources:
        for quote in src.get("quotes", []):
            if not quote_supported(quote, evidence_norm):
                short = quote if len(quote) < 80 else quote[:77] + "..."
                bad.append(f'[{src["n"]}] "{short}"')
    return bad
