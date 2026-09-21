"""Careful, deterministic date handling for CV entries.

The model only ever reports the date text AS WRITTEN. This module derives the normalized form, and it
never invents precision: "2020" stays "2020" (never "2020-01"), "Summer 2020" is only a year, and text
it cannot read stays unparsed (the original text is always kept next to the normalized value).

Normalized form: "YYYY" or "YYYY-MM". Both English and German month names are understood, since CVs
are often written in either.
"""
import re
from datetime import date
from typing import Optional

_MONTHS = {
    "jan": 1, "january": 1, "januar": 1, "jänner": 1, "jaenner": 1,
    "feb": 2, "february": 2, "februar": 2,
    "mar": 3, "march": 3, "mär": 3, "maer": 3, "märz": 3, "maerz": 3,
    "apr": 4, "april": 4,
    "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12, "dez": 12, "dezember": 12,
}
_MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_PRESENT_WORDS = {
    "present", "current", "currently", "now", "today", "ongoing", "to date", "till date", "date",
    "heute", "aktuell", "derzeit", "laufend", "gegenwart", "bis heute",
}
_SEASONS_AND_QUARTERS = re.compile(r"^(?:spring|summer|autumn|fall|winter|q[1-4]|h[12]|early|mid|late)\s+(\d{4})$", re.I)

_YEAR = re.compile(r"^(\d{4})$")
_ISO_MONTH = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
_MONTH_YEAR_NUM = re.compile(r"^(\d{1,2})[-/.](\d{4})$")
_FULL_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_FULL_DMY = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
_MONTH_NAME_YEAR = re.compile(r"^([A-Za-zäöüÄÖÜ]+)\.?,?\s+(\d{4})$")
_NORMALIZED = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2]))?$")


def is_normalized(value: str) -> bool:
    return bool(_NORMALIZED.match(value))


def is_present_word(text: str) -> bool:
    return " ".join(text.lower().replace(".", " ").split()) in _PRESENT_WORDS


def _month(value: int) -> Optional[int]:
    return value if 1 <= value <= 12 else None


def parse_date_text(text: Optional[str]) -> Optional[str]:
    """Original date text -> "YYYY" / "YYYY-MM", or None when it cannot be read with certainty.
    A day of the month is dropped (kept in the original text); a year is never given a month."""
    if not text:
        return None
    raw = " ".join(text.strip().split())
    if not raw:
        return None

    if match := _YEAR.match(raw):
        return match.group(1)
    if match := _SEASONS_AND_QUARTERS.match(raw):
        return match.group(1)  # a season/quarter is only a year: no month is inferred
    if match := _ISO_MONTH.match(raw):
        month = _month(int(match.group(2)))
        return f"{match.group(1)}-{month:02d}" if month else None
    if match := _FULL_ISO.match(raw):
        month = _month(int(match.group(2)))
        return f"{match.group(1)}-{month:02d}" if month else None
    if match := _FULL_DMY.match(raw):
        month = _month(int(match.group(2)))
        return f"{match.group(3)}-{month:02d}" if month else None
    if match := _MONTH_YEAR_NUM.match(raw):
        month = _month(int(match.group(1)))
        return f"{match.group(2)}-{month:02d}" if month else None
    if match := _MONTH_NAME_YEAR.match(raw):
        month = _MONTHS.get(match.group(1).lower())
        return f"{match.group(2)}-{month:02d}" if month else None
    return None


def format_partial(value: Optional[str]) -> Optional[str]:
    """'2025-04' -> 'Apr 2025', '2025' -> '2025'. Same look as the graph-derived dates in cv_writer."""
    if not value:
        return None
    parts = value.split("-")
    if len(parts) == 2 and parts[1].isdigit() and 1 <= int(parts[1]) <= 12:
        return f"{_MONTH_NAMES[int(parts[1]) - 1]} {parts[0]}"
    return parts[0]


def month_index(value: str, *, end: bool) -> int:
    """A comparable month number. Year-only values span the whole year: an END bound resolves to
    December and a START bound to January, so a comparison only reports a difference that holds under
    every possible reading of the vague date."""
    parts = value.split("-")
    year = int(parts[0])
    if len(parts) == 2:
        return year * 12 + int(parts[1]) - 1
    return year * 12 + (11 if end else 0)


def today_index(today: Optional[date] = None) -> int:
    today = today or date.today()
    return today.year * 12 + today.month - 1


def format_month_index(index: int) -> str:
    return f"{_MONTH_NAMES[index % 12]} {index // 12}"


def has_more_precision(a: str, b: str) -> bool:
    return len(a) != len(b)
