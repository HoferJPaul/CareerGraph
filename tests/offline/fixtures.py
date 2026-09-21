"""Synthetic (fictional) career fixtures for the offline test suite.

Nothing here comes from a real person, employer or job posting -- it exists only so the LLM
layer can be exercised without Neo4j, Groq or any personal data. The shapes mirror what
backend/pipeline.build_cv_context() produces from real graph evidence:

  - OrderSync: a professional project (Role/Company provenance) with a quantified achievement,
    plus an *unselected* observability achievement that only surfaces as transferable evidence
  - TrailMap: a personal full-stack project (Fastify backend)
  - Riverbank University: an Education anchor, with a folded Role (Peer Tutor) and a folded
    curriculum Project (Shell Project)
  - Literal gaps: cloudwatch (has transferable observability evidence), express (transferable
    backend engineering), aws (nothing at all)
"""
from tailor_cv import (
    CV_GUIDANCE,
    AchievementRef,
    CVContext,
    EvidenceStory,
    RequirementMatch,
    SkillSummary,
)
from requirement_schema import Evidence, TransferableEvidence

ACH_ORDER_LLM = (
    "Built a FastAPI service that extracts structured order data from PDFs with an LLM, "
    "cutting manual data entry time by 60%."
)
ACH_ORDER_DB = "Designed a PostgreSQL schema and migrations for multi-tenant order data."
ACH_ORDER_OBSERVABILITY = "Added structured logging and dashboards to monitor API latency and error rates."
ACH_TRAIL_BACKEND = "Implemented a Fastify REST backend with JWT authentication and Zod request validation."
ACH_TUTOR = "Tutored 30 students in debugging and C programming."


def _evidence(source, source_type, relationship, project=None, role=None, education=None, professional=False):
    return Evidence(
        source=source,
        sourceType=source_type,
        relationship=relationship,
        project=project,
        role=role,
        education=education,
        professionalContext=professional,
        evidenceStrength="strong" if professional else "moderate",
    )


def _match(requirement, skill_query, canonical, importance, category, evidence, transferable=None, confidence="high"):
    return RequirementMatch(
        requirement=requirement,
        skillQuery=skill_query,
        importance=importance,
        category=category,
        matchType="canonical_exact" if evidence else "no_match",
        canonicalSkill=canonical,
        confidence=confidence if evidence else "no_match",
        recommendation="include" if evidence else "exclude",
        evidence=evidence,
        transferableEvidence=transferable or [],
    )


def build_cv_context() -> CVContext:
    order_sync = EvidenceStory(
        label="OrderSync",
        sourceType="Project",
        project="OrderSync",
        roleTitle="Backend Engineer",
        company="Acme Analytics",
        type="professional",
        domain="logistics",
        context="Internal order-processing platform",
        description="Order-processing platform used by warehouse operators.",
        personRole="Backend engineer",
        startDate="2025-04",
        endDate="2025-09",
        location="Remote",
        professionalContext=True,
        evidenceStrength="strong",
        directSkills=["fastapi", "postgresql", "python"],
        strongestAchievements=[
            AchievementRef(
                description=ACH_ORDER_LLM,
                matchedSkills=["fastapi", "llm api integration", "python"],
                evidenceStrength="strong",
                hasMetric=True,
            ),
            AchievementRef(
                description=ACH_ORDER_DB,
                matchedSkills=["postgresql"],
                evidenceStrength="strong",
                hasMetric=False,
            ),
        ],
        supportsRequirements=["Python backend experience", "PostgreSQL", "LLM API integration"],
        requiredCount=2,
    )
    trail_map = EvidenceStory(
        label="TrailMap",
        sourceType="Project",
        project="TrailMap",
        type="personal",
        context="Personal full-stack project",
        description="Full-stack hiking route planner.",
        personRole="Solo developer",
        startDate="2025-10",
        endDate="2026-01",
        professionalContext=False,
        evidenceStrength="moderate",
        directSkills=["backend engineering", "fastify", "react", "typescript"],
        strongestAchievements=[
            AchievementRef(
                description=ACH_TRAIL_BACKEND,
                matchedSkills=["fastify", "backend engineering"],
                evidenceStrength="moderate",
                hasMetric=False,
            )
        ],
        supportsRequirements=["TypeScript and React", "Backend engineering"],
        requiredCount=1,
    )
    university = EvidenceStory(
        label="Riverbank University",
        sourceType="Education",
        description="Software engineering curriculum covering systems programming and databases.",
        program="BSc Computer Science",
        startDate="2021-09",
        endDate="2024-06",
        status="completed",
        professionalContext=False,
        evidenceStrength="weak",
        directSkills=["c"],
        strongestAchievements=[],
        supportsRequirements=["Fundamentals of computer science"],
        requiredCount=0,
    )
    tutor = EvidenceStory(
        label="Peer Tutor",
        sourceType="Role",
        roleTitle="Peer Tutor",
        company="Riverbank University",
        startDate="2023-01",
        endDate="2023-12",
        professionalContext=False,
        evidenceStrength="weak",
        directSkills=["debugging"],
        strongestAchievements=[
            AchievementRef(description=ACH_TUTOR, matchedSkills=["debugging"], evidenceStrength="weak", hasMetric=True)
        ],
        supportsRequirements=["Debugging in production"],
        requiredCount=1,
    )
    shell = EvidenceStory(
        label="Shell Project",
        sourceType="Project",
        project="Shell Project",
        education="Riverbank University",
        description="Implemented a Unix shell in C.",
        professionalContext=False,
        evidenceStrength="weak",
        directSkills=["c", "unix"],
        strongestAchievements=[],
        supportsRequirements=["Systems fundamentals"],
        requiredCount=0,
    )
    language_only = EvidenceStory(
        label="Village Chronicle",
        sourceType="Project",
        project="Village Chronicle",
        description="Community archive project.",
        professionalContext=False,
        evidenceStrength="weak",
        directSkills=["german"],
        strongestAchievements=[],
        supportsRequirements=["German language"],
        requiredCount=0,
    )

    skills = [
        SkillSummary(name="backend engineering", displayName="Backend Engineering", category="capability"),
        SkillSummary(name="c", displayName="C", category="technical"),
        SkillSummary(name="debugging", displayName="Debugging", category="capability"),
        SkillSummary(name="fastapi", displayName="FastAPI", category="tool"),
        SkillSummary(name="fastify", displayName="Fastify", category="tool"),
        SkillSummary(name="german", displayName="German", category="language"),
        SkillSummary(name="postgresql", displayName="PostgreSQL", category="tool"),
        SkillSummary(name="python", displayName="Python", category="technical"),
        SkillSummary(name="react", displayName="React", category="tool"),
        SkillSummary(name="typescript", displayName="TypeScript", category="technical"),
        SkillSummary(name="unix", displayName="Unix", category="tool"),
    ]

    matched = [
        _match(
            "Python backend experience", "python", "python", "required", "technology",
            [_evidence(ACH_ORDER_LLM, "Achievement", "DEMONSTRATES", project="OrderSync", professional=True)],
        ),
        _match(
            "Experience with PostgreSQL", "postgres", "postgresql", "required", "technology",
            [_evidence(ACH_ORDER_DB, "Achievement", "DEMONSTRATES", project="OrderSync", professional=True)],
        ),
    ]
    gaps = [
        _match(
            "Trace and fix production bugs (Sentry, CloudWatch)", "cloudwatch", None, "required", "technology",
            [],
            transferable=[
                TransferableEvidence(
                    capabilityQuery="observability",
                    canonicalSkill="observability",
                    matchType="canonical_exact",
                    evidence=[
                        _evidence(
                            ACH_ORDER_OBSERVABILITY, "Achievement", "DEMONSTRATES",
                            project="OrderSync", professional=True,
                        )
                    ],
                    reason="CloudWatch is used for application monitoring",
                    source="llm_semantic",
                )
            ],
        ),
        _match(
            "Node/Express on the backend", "express", None, "preferred", "technology",
            [],
            transferable=[
                TransferableEvidence(
                    capabilityQuery="backend engineering",
                    canonicalSkill="backend engineering",
                    matchType="canonical_exact",
                    evidence=[_evidence(ACH_TRAIL_BACKEND, "Achievement", "DEMONSTRATES", project="TrailMap")],
                    reason="Express is a Node.js backend framework",
                    source="llm_semantic",
                )
            ],
        ),
        _match("Postgres, AWS", "aws", None, "preferred", "technology", []),
    ]

    requirements = [
        {"raw": m.requirement, "skillQuery": m.skillQuery, "importance": m.importance,
         "category": m.category, "relatedCapabilities": []}
        for m in matched + gaps
    ]

    return CVContext(
        requirements=requirements,
        matchedRequirements=matched,
        partialRequirements=[],
        gaps=gaps,
        evidenceStories=[order_sync, trail_map, university, tutor, shell, language_only],
        skills=skills,
        cvGuidance=CV_GUIDANCE,
    )


def valid_cv_draft() -> dict:
    """A well-behaved model response for build_cv_context(): every bullet cites real
    evidence ids, transferable evidence stays under its owning story, gaps never claimed."""
    return {
        "headline": "Backend Engineer",
        "profile": (
            "Backend engineer with hands-on experience building Python APIs and data-backed "
            "services, plus full-stack project work in TypeScript."
        ),
        "experience": [
            {
                "storyId": "story:OrderSync",
                "bullets": [
                    {
                        "text": (
                            "Built a FastAPI service that uses an LLM to extract structured order data "
                            "from PDFs, cutting manual data entry time by 60%."
                        ),
                        "evidenceIds": ["achievement:OrderSync#1"],
                    },
                    {
                        "text": "Designed the PostgreSQL schema and migrations for multi-tenant order data.",
                        "evidenceIds": ["achievement:OrderSync#2"],
                    },
                    {
                        "text": "Added structured logging and dashboards to monitor API latency and error rates.",
                        "evidenceIds": ["transferable:observability#1"],
                    },
                ],
            }
        ],
        "projects": [
            {
                "storyId": "story:TrailMap",
                "bullets": [
                    {
                        "text": "Implemented a Fastify REST backend with JWT authentication and Zod request validation.",
                        "evidenceIds": ["achievement:TrailMap#1"],
                    }
                ],
            }
        ],
        "education": [
            {
                "storyId": "story:Riverbank University",
                "bullets": [
                    {
                        "text": "Completed a software engineering curriculum covering systems programming and databases.",
                        "evidenceIds": ["story:Riverbank University"],
                    },
                    {
                        "text": "Tutored 30 students in debugging and C programming as a peer tutor.",
                        "evidenceIds": ["achievement:Peer Tutor#1"],
                    },
                ],
            }
        ],
        "skills": {
            "programming": ["Python", "TypeScript", "C"],
            "frameworks": ["FastAPI", "Fastify", "React"],
            "databases": ["PostgreSQL"],
            "tools": ["Unix"],
            "capabilities": ["Backend Engineering", "Debugging"],
        },
        "languages": ["German"],
    }
