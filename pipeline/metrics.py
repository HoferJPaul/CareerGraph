"""Conservative quantified-outcome detection, shared by matching.py (evidence
strength scoring) and tailor_cv.py (hasMetric on selected evidence).

Deliberately positive-pattern-only: a string counts as having a metric only if
it contains a number anchored to a recognized outcome unit (percentage,
currency, distance, area, a count of processed items, or a count of people).
A bare number or model/version string ("Llama 3.3 70B", "GPT-4") matches none
of these units and is correctly NOT a metric -- no separate blocklist is
needed because the detector never fires on digits alone.
"""
import re

_UNIT_PATTERNS = [
    r"\d[\d,]*\.?\d*\s?%",  # percentages: 20%, 1,000%, 3.5%
    r"[$€£]\s?\d[\d,]*\.?\d*",  # currency prefix: $5,000  €1.5
    r"\d[\d,]*\.?\d*\s?(?:eur|usd|gbp|euros?|dollars?)\b",  # currency suffix
    r"\d[\d,]*\+?[\s-]?(?:km|kilomet(?:er|re)s?|miles?|mi)\b",  # distance
    r"\d[\d,]*\+?[\s-]?acres?\b",  # area
    r"\d[\d,]*[\s-]?(?:rows?|records?|items?|batches?|files?|documents?|"
    r"programmes?|programs?)\b",  # volume of things processed
    r"\d[\d,]*\+?[\s-]?(?:participants?|customers?|clients?|users?|people|"
    r"learners?|students?|employees?|experts?)\b",  # counts of people
]

_METRIC_RE = re.compile("|".join(f"(?:{p})" for p in _UNIT_PATTERNS), re.IGNORECASE)


def has_quantified_metric(text: str) -> bool:
    """True only if `text` contains a number anchored to a real outcome unit."""
    if not text:
        return False
    return bool(_METRIC_RE.search(text))


if __name__ == "__main__":
    positive_cases = [
        "Walked and paddled more than 3,000 km as part of an ongoing pilgrimage.",
        "Raised approximately ~€5,000 in fundraising.",
        "Processed an 800-row QC dataset covering 20 batches and 4 customers.",
        "Managed 15,000 acres/year across multiple growing seasons.",
        "Symposium drew more than 100 participants and 20 experts over 3 days.",
        "Reduced manual review time by 40%.",
    ]
    negative_cases = [
        "Integrated Groq and Llama 3.3 70B to convert unstructured documents.",
        "Took El Compas from concept to a functioning MVP architecture.",
        "Developed an AI-assisted engineering workflow using GPT-4 and Claude 3.7.",
        "",
    ]

    for text in positive_cases:
        assert has_quantified_metric(text), f"expected metric in: {text!r}"
    for text in negative_cases:
        assert not has_quantified_metric(text), f"unexpected metric in: {text!r}"

    print("All metrics.py regression checks passed.")
