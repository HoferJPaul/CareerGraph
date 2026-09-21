"""A FICTIONAL candidate and CV for the Source CV tests. No real person, employer or contact detail.

The CV is built to line up with tests/offline/fixtures.py's synthetic career graph, so the same test data
exercises both directions of the merge:

  Backend Engineer, Acme Analytics (Apr 2025 - Sep 2025)   also in the graph as OrderSync  -> merged
  Riverbank University, BSc Computer Science (2021-2024)   also in the graph                -> merged
  TrailMap (Oct 2025 - Jan 2026)                            also in the graph as a project   -> merged
  Warehouse Supervisor, Nordwind Logistics                  source only, irrelevant to software jobs
  Retail Assistant, Kaffeehaus Ringstrasse                  source only, irrelevant
  Handelsakademie Graz                                      source-only education
"""
import copy

from llm.source_schemas import SourceProfileDraft

NAME = "Mira Tannenbaum"
EMAIL = "mira.tannenbaum@example.org"
PHONE = "+43 660 555 0142"
LINKEDIN = "linkedin.com/in/mira-tannenbaum"
GITHUB = "github.com/mira-tannenbaum"
PORTFOLIO = "https://miratannenbaum.example.org"

ACME_FACT_1 = "Built a FastAPI service that extracts structured order data from PDFs, cutting manual data entry time by 60%."
ACME_FACT_2 = "Wrote onboarding documentation for new engineers."
NORDWIND_FACT_1 = "Supervised a team of 12 warehouse operators across two shifts."
NORDWIND_FACT_2 = "Introduced a barcode check that reduced picking errors."
KAFFEE_FACT = "Managed opening and closing procedures."
TRAILMAP_FACT = "Built a hiking route planner with a React front end."
SUMMARY = "Software engineer with a background in logistics operations."

HEAD_ACME = "Backend Engineer - Acme Analytics, Remote"
HEAD_NORDWIND = "Warehouse Supervisor - Nordwind Logistics, Vienna"
HEAD_KAFFEE = "Retail Assistant - Kaffeehaus Ringstrasse, Graz"
HEAD_RIVERBANK = "Riverbank University - BSc Computer Science"
HEAD_HANDELS = "Handelsakademie Graz - Matura"
CERT_LINE = "Certified Scrum Master, Scrum Alliance, 2023"
LANG_LINE = "German (Native), English (C1)"


def cv_lines() -> list[str]:
    return [
        NAME,
        f"Graz, Austria | {EMAIL} | {PHONE}",
        f"{LINKEDIN} | {GITHUB} | {PORTFOLIO}",
        "",
        "Summary",
        SUMMARY,
        "",
        "Experience",
        HEAD_ACME,
        "Apr 2025 - Sep 2025",
        f"- {ACME_FACT_1}",
        f"- {ACME_FACT_2}",
        "",
        HEAD_NORDWIND,
        "Mar 2018 - Aug 2021",
        f"- {NORDWIND_FACT_1}",
        f"- {NORDWIND_FACT_2}",
        "",
        HEAD_KAFFEE,
        "Jan 2015 - Dec 2017",
        f"- {KAFFEE_FACT}",
        "",
        "Education",
        HEAD_RIVERBANK,
        "Sep 2021 - Jun 2024",
        HEAD_HANDELS,
        "2010 - 2015",
        "",
        "Projects",
        "TrailMap",
        "Oct 2025 - Jan 2026",
        f"- {TRAILMAP_FACT}",
        "",
        "Certifications",
        CERT_LINE,
        "",
        "Languages",
        LANG_LINE,
    ]


def cv_text() -> str:
    return "\n".join(cv_lines())


def _fact(text: str) -> dict:
    return {"text": text, "excerpt": text}


def valid_source_draft() -> dict:
    """What a well-behaved model returns for cv_text(): every value copied from the document."""
    return {
        "contact": {
            "fullName": NAME, "email": EMAIL, "telephone": PHONE, "city": "Graz", "country": "Austria",
            "linkedinUrl": f"https://{LINKEDIN}", "githubUrl": f"https://{GITHUB}", "portfolioUrl": PORTFOLIO,
            "otherUrls": [],
        },
        "summary": _fact(SUMMARY),
        "employment": [
            {
                "employer": "Acme Analytics", "title": "Backend Engineer", "location": "Remote",
                "startText": "Apr 2025", "endText": "Sep 2025", "current": False, "excerpt": HEAD_ACME,
                "facts": [_fact(ACME_FACT_1), _fact(ACME_FACT_2)],
            },
            {
                "employer": "Nordwind Logistics", "title": "Warehouse Supervisor", "location": "Vienna",
                "startText": "Mar 2018", "endText": "Aug 2021", "current": False, "excerpt": HEAD_NORDWIND,
                "facts": [_fact(NORDWIND_FACT_1), _fact(NORDWIND_FACT_2)],
            },
            {
                "employer": "Kaffeehaus Ringstrasse", "title": "Retail Assistant", "location": "Graz",
                "startText": "Jan 2015", "endText": "Dec 2017", "current": False, "excerpt": HEAD_KAFFEE,
                "facts": [_fact(KAFFEE_FACT)],
            },
        ],
        "education": [
            {
                "institution": "Riverbank University", "qualification": "BSc Computer Science", "fieldOfStudy": "",
                "location": "", "startText": "Sep 2021", "endText": "Jun 2024", "current": False,
                "excerpt": HEAD_RIVERBANK, "details": [],
            },
            {
                "institution": "Handelsakademie Graz", "qualification": "Matura", "fieldOfStudy": "", "location": "",
                "startText": "2010", "endText": "2015", "current": False, "excerpt": HEAD_HANDELS, "details": [],
            },
        ],
        "projects": [
            {
                "name": "TrailMap", "role": "", "url": "", "startText": "Oct 2025", "endText": "Jan 2026",
                "current": False, "excerpt": "TrailMap", "facts": [_fact(TRAILMAP_FACT)],
            }
        ],
        "certifications": [
            {"name": "Certified Scrum Master", "issuer": "Scrum Alliance", "dateText": "2023", "excerpt": CERT_LINE}
        ],
        "languages": [
            {"language": "German", "proficiency": "Native", "excerpt": "German (Native)"},
            {"language": "English", "proficiency": "C1", "excerpt": "English (C1)"},
        ],
        "otherSections": [],
        "warnings": [],
    }


def valid_draft() -> SourceProfileDraft:
    return SourceProfileDraft.model_validate(copy.deepcopy(valid_source_draft()))
