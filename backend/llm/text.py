"""Tiny text helpers shared by the extraction and CV-writing halves of the LLM layer."""
import re


def normalize(text: str) -> str:
    """Trim, lowercase and collapse whitespace -- the canonical form for comparing skill names."""
    return " ".join(text.strip().lower().split())


def mentions_term(text: str, term: str) -> bool:
    """Whole-phrase, case-insensitive match ('aws' matches 'AWS Lambda', never 'laws')."""
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text.lower()) is not None
