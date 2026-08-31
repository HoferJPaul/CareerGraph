"""Read-only Neo4j evidence retrieval for extracted job requirements.

Given a RequirementList (produced by extraction.py, or hand-built for testing),
resolves each requirement's skillQuery against the skill_search fulltext index
and returns whatever supporting evidence already exists in CareerGraph.

Never writes to Neo4j. Never creates a Skill node. Never substitutes a
different skill to make a weak match look stronger -- a requirement with zero
accepted candidates, or a resolved Skill with zero evidence edges, is returned
with hasEvidence=False rather than dropped or guessed at.

Matching is two-stage:
  1. Candidate retrieval (Neo4j) -- the skill_search fulltext index returns up
     to 5 ranked candidate Skills per requirement. This is retrieval only, not
     acceptance: fulltext scores reward any shared token, including generic
     ones ("security", "infrastructure", ...), so a raw top hit is not treated
     as a match.
  2. Lexical validation (Python, _resolve_candidate) -- exact canonical-name or
     alias matches are accepted as high confidence unconditionally. Everything
     else must show at least one non-generic token overlap with the candidate's
     name or aliases; candidates whose only overlap is a generic token (see
     _GENERIC_TOKENS) are rejected outright rather than downgraded, since a
     shared filler word is not evidence of a real relationship. Among surviving
     candidates, multi-token overlap can reach high confidence (if it also
     clearly beats the runner-up); single-token overlap is capped at low.
"""
import re
from pathlib import Path
from typing import Optional

from neo4j import GraphDatabase

from metrics import has_quantified_metric
from setup_schema import load_env
from requirement_schema import (
    Evidence,
    MatchResult,
    RelatedCapability,
    Requirement,
    RequirementList,
    TransferableEvidence,
)

# Lucene classic-parser reserved characters (queryparser.classic.QueryParser.escape).
# Unescaped, e.g. "/" opens a regex-term literal and "ci/cd" crashes the parser
# with a lexical error rather than matching anything. Spaces are deliberately
# NOT escaped -- they must stay as term separators so multi-word queries like
# "backend engineering" keep matching as an OR of tokens.
_LUCENE_SPECIAL = set('+-&|!(){}[]^"~*?:\\/')

# Tokens too generic to count as evidence of a real match on their own. Shared
# only via one of these, a candidate is rejected, not just downgraded. Kept
# short and data-driven (see matching.py commit history / stress-test notes)
# rather than an exhaustive stopword list -- each entry either caused a
# confirmed false positive ("professional", "user") or is a suffix-noun that
# recurs across many semantically unrelated skill names in CareerGraph
# ("data", "communication"), the same category as the original four.
_GENERIC_TOKENS = {
    "security", "engineering", "development", "management",
    "system", "systems", "architecture", "infrastructure",
    "professional", "user", "data", "communication",
}

_CANDIDATE_LIMIT = 5


def _escape_lucene_query(text: str) -> str:
    return "".join(f"\\{ch}" if ch in _LUCENE_SPECIAL else ch for ch in text)


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _normalize(text: str) -> str:
    return text.strip().lower()


def _exact_match_type(query: str, candidate: dict) -> Optional[str]:
    query_norm = _normalize(query)
    if query_norm == _normalize(candidate["name"]):
        return "canonical_exact"
    if any(query_norm == _normalize(a) for a in (candidate.get("aliases") or [])):
        return "alias_exact"
    return None


def _meaningful_overlap(query: str, candidate: dict) -> set[str]:
    query_tokens = _tokenize(query)
    name_tokens = _tokenize(candidate["name"])
    alias_tokens: set[str] = set()
    for alias in candidate.get("aliases") or []:
        alias_tokens |= _tokenize(alias)
    return (query_tokens & (name_tokens | alias_tokens)) - _GENERIC_TOKENS


def _resolve_candidate(
    query: str, candidates: list[dict]
) -> tuple[Optional[dict], Optional[float], str, str]:
    """Pick the accepted candidate (if any), its confidence, and matchType.

    candidates must already be sorted by Lucene score, descending.
    Returns (candidate_or_None, matchScore_or_None, matchConfidence, matchType).
    matchType classifies *how* the match was made; matchConfidence classifies
    how strong/unambiguous it is. A "lexical_multi_token" match can still end
    up "low" confidence if a close runner-up makes it ambiguous.
    """
    # Pass 1: an exact canonical-name or alias match wins unconditionally,
    # regardless of its rank in the fulltext results.
    for c in candidates:
        exact_type = _exact_match_type(query, c)
        if exact_type:
            return c, c["score"], "high", exact_type

    # Pass 2: lexical-validated fuzzy candidates, still in score order.
    validated: list[tuple[dict, int]] = []
    for c in candidates:
        overlap = _meaningful_overlap(query, c)
        if overlap:
            validated.append((c, len(overlap)))
        # else: rejected -- the only overlap (if any) was a generic token,
        # e.g. "infrastructure security" vs "security boundary design"
        # sharing just "security"/"infrastructure". Not treated as a match
        # at any confidence level, not even "low".

    if not validated:
        return None, None, "no_match", "no_match"

    top, top_overlap_count = validated[0]

    if top_overlap_count < 2:
        # Single non-generic token overlap is real but weak -- never "high".
        return top, top["score"], "low", "lexical_single_token"

    if len(validated) == 1:
        return top, top["score"], "high", "lexical_multi_token"

    runner_up = validated[1][0]
    if top["score"] > 0 and runner_up["score"] / top["score"] < 0.7:
        return top, top["score"], "high", "lexical_multi_token"
    return top, top["score"], "low", "lexical_multi_token"


def _recommendation_for(match_type: str, confidence: str) -> str:
    """Automatic-CV-inclusion signal, kept separate from matchConfidence/matchType.

    Single-token lexical overlaps are excluded outright -- a shared connector word is
    "real but weak" evidence (see _resolve_candidate), not something a CV-writer should
    have to rediscover as a false positive on every run. Exact and decisive multi-token
    matches are safe to include automatically; an ambiguous multi-token match (a close
    runner-up made it "low" confidence) is merely "optional".
    """
    if match_type in ("canonical_exact", "alias_exact"):
        return "include"
    if match_type == "lexical_multi_token":
        return "include" if confidence == "high" else "optional"
    return "exclude"  # lexical_single_token, no_match


_SOURCE_TYPE_BASE_SCORE = {"Achievement": 2, "Project": 1, "Role": 1, "Education": 0}


def _apply_evidence_strength(
    evidence_list: list[dict], professional_projects: set[str], professional_roles: set[str]
) -> None:
    """Mutate each evidence dict in place, adding professionalContext/evidenceStrength.

    This is a provenance signal, independent of matchConfidence/matchType (which measure
    lexical certainty of the skill match, not how strong the underlying career evidence
    actually is for a given requirement -- e.g. an exact "debugging" match sourced only
    from tutoring/coursework should read as high confidence but weak/moderate strength).

    Scoring is intentionally simple and structural, not an absolute professional-over-
    personal rule: Achievement/Project/Role all start ahead of bare Education (coursework);
    reaching the evidence via Project-[:DURING]->Role-[:AT]->Company (real employment
    provenance) adds a boost; a genuine quantified outcome adds a further boost. Because
    this score is only ONE of several ranking signals downstream (see tailor_cv.py's
    SOURCE_TYPE_RANK), a highly specific personal-project Achievement can still outrank
    weaker professional evidence overall.
    """
    for ev in evidence_list:
        professional = (
            ev.get("project") in professional_projects
            or ev.get("role") in professional_roles
        )
        score = _SOURCE_TYPE_BASE_SCORE.get(ev["sourceType"], 0)
        if professional:
            score += 2
        if has_quantified_metric(ev["source"]):
            score += 1
        if ev["sourceType"] == "Education":
            score -= 1
        score = max(score, 0)
        ev["professionalContext"] = professional
        ev["evidenceStrength"] = "strong" if score >= 4 else "moderate" if score >= 2 else "weak"


PROFESSIONAL_CONTEXT_QUERY = """
MATCH (p:Project)-[:DURING]->(r:Role)-[:AT]->(:Company)
RETURN collect(DISTINCT p.name) AS projects, collect(DISTINCT r.title) AS roles
""".strip()


CANDIDATE_QUERY = """
UNWIND $requirements AS requirement
CALL (requirement) {
  CALL db.index.fulltext.queryNodes('skill_search', requirement.searchQuery) YIELD node, score
  WITH node, score ORDER BY score DESC LIMIT $candidateLimit
  RETURN collect({name: node.name, aliases: node.aliases, score: score}) AS candidates
}
RETURN requirement.raw AS raw,
       requirement.skillQuery AS skillQuery,
       requirement.importance AS importance,
       requirement.category AS category,
       candidates
ORDER BY requirement.skillQuery
""".strip()

EVIDENCE_QUERY = """
UNWIND $accepted AS acc
OPTIONAL MATCH (skill:Skill {name: acc.canonicalSkill})
OPTIONAL MATCH (evidence)-[r:USED|DEMONSTRATES|LEARNED]->(skill)
OPTIONAL MATCH (achievementProject:Project)-[:ACHIEVED]->(evidence)
OPTIONAL MATCH (achievementRole:Role)-[:ACHIEVED]->(evidence)
WITH acc, evidence, r, achievementProject, achievementRole,
     CASE labels(evidence)[0]
       WHEN 'Project' THEN evidence
       WHEN 'Achievement' THEN achievementProject
       ELSE null
     END AS provenanceProject
OPTIONAL MATCH (provenanceProject)-[:PART_OF]->(edu:Education)
WITH acc,
     collect(DISTINCT CASE WHEN evidence IS NULL THEN null ELSE {
       source: CASE labels(evidence)[0]
                 WHEN 'Achievement' THEN evidence.description
                 WHEN 'Project' THEN evidence.name
                 WHEN 'Role' THEN evidence.title
                 WHEN 'Education' THEN evidence.institution
                 ELSE coalesce(evidence.name, evidence.title, evidence.institution, evidence.description)
               END,
       sourceType: labels(evidence)[0],
       relationship: type(r),
       project: CASE labels(evidence)[0]
                  WHEN 'Achievement' THEN achievementProject.name
                  WHEN 'Project' THEN evidence.name
                  ELSE null
                END,
       role: CASE labels(evidence)[0]
               WHEN 'Achievement' THEN achievementRole.title
               WHEN 'Role' THEN evidence.title
               ELSE null
             END,
       education: edu.institution
     } END) AS evidence
RETURN acc.raw AS raw,
       acc.skillQuery AS skillQuery,
       acc.importance AS importance,
       acc.category AS category,
       acc.canonicalSkill AS canonicalSkill,
       acc.matchScore AS matchScore,
       acc.matchConfidence AS matchConfidence,
       acc.matchType AS matchType,
       evidence,
       size(evidence) > 0 AS hasEvidence
ORDER BY acc.skillQuery
""".strip()


def match_requirements(
    requirements: RequirementList, project_root: Optional[Path] = None
) -> list[MatchResult]:
    root = project_root or Path(__file__).resolve().parent.parent
    env = load_env(root / ".env")

    driver = GraphDatabase.driver(
        env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])
    )
    try:
        driver.verify_connectivity()
        with driver.session(
            database=env["NEO4J_DATABASE"], default_access_mode="READ"
        ) as session:
            # Stage 1: candidate retrieval only (Neo4j fulltext), not acceptance.
            payload = []
            for r in requirements.requirements:
                item = r.model_dump()
                item["searchQuery"] = _escape_lucene_query(r.skillQuery)
                payload.append(item)
            candidate_rows = session.run(
                CANDIDATE_QUERY, requirements=payload, candidateLimit=_CANDIDATE_LIMIT
            ).data()

            # Stage 2: lexical validation / acceptance, in Python.
            accepted = []
            for row in candidate_rows:
                skill, score, confidence, match_type = _resolve_candidate(
                    row["skillQuery"], row["candidates"]
                )
                accepted.append(
                    {
                        "raw": row["raw"],
                        "skillQuery": row["skillQuery"],
                        "importance": row["importance"],
                        "category": row["category"],
                        "canonicalSkill": skill["name"] if skill else None,
                        "matchScore": score,
                        "matchConfidence": confidence,
                        "matchType": match_type,
                    }
                )

            # Stage 2b: candidate retrieval + resolution for related-capability
            # queries (transferable evidence). Reuses the exact same fulltext +
            # lexical-validation pipeline as the literal requirements -- a
            # capability query is just another skillQuery, resolved completely
            # independently of whether its parent literal requirement matched.
            capability_queries = sorted(
                {cap.skillQuery for r in requirements.requirements for cap in r.relatedCapabilities}
            )
            capability_accepted = []
            if capability_queries:
                capability_payload = [
                    {
                        "raw": cap,
                        "skillQuery": cap,
                        "importance": "preferred",
                        "category": "capability",
                        "searchQuery": _escape_lucene_query(cap),
                    }
                    for cap in capability_queries
                ]
                capability_candidate_rows = session.run(
                    CANDIDATE_QUERY, requirements=capability_payload, candidateLimit=_CANDIDATE_LIMIT
                ).data()
                for row in capability_candidate_rows:
                    skill, score, confidence, match_type = _resolve_candidate(
                        row["skillQuery"], row["candidates"]
                    )
                    capability_accepted.append(
                        {
                            "raw": row["raw"],
                            "skillQuery": row["skillQuery"],
                            "importance": row["importance"],
                            "category": row["category"],
                            "canonicalSkill": skill["name"] if skill else None,
                            "matchScore": score,
                            "matchConfidence": confidence,
                            "matchType": match_type,
                        }
                    )

            # Stage 3: fetch evidence for whatever was actually accepted (both
            # literal requirements and related-capability queries), plus the
            # small structural signal needed for evidence-strength scoring.
            records = session.run(EVIDENCE_QUERY, accepted=accepted).data()
            capability_records = (
                session.run(EVIDENCE_QUERY, accepted=capability_accepted).data()
                if capability_accepted
                else []
            )
            prof_row = session.run(PROFESSIONAL_CONTEXT_QUERY).single()
    finally:
        driver.close()

    professional_projects = set(prof_row["projects"]) if prof_row else set()
    professional_roles = set(prof_row["roles"]) if prof_row else set()

    for rec in records:
        _apply_evidence_strength(rec["evidence"], professional_projects, professional_roles)
        rec["recommendation"] = _recommendation_for(rec["matchType"], rec["matchConfidence"])
    for rec in capability_records:
        _apply_evidence_strength(rec["evidence"], professional_projects, professional_roles)

    capability_by_query = {rec["skillQuery"]: rec for rec in capability_records}
    related_capabilities_by_query = {
        r.skillQuery: r.relatedCapabilities for r in requirements.requirements
    }

    for rec in records:
        transferable = []
        for cap in related_capabilities_by_query.get(rec["skillQuery"], []):
            cap_rec = capability_by_query.get(cap.skillQuery)
            if not cap_rec:
                continue
            transferable.append(
                {
                    "capabilityQuery": cap.skillQuery,
                    "canonicalSkill": cap_rec["canonicalSkill"],
                    "matchType": cap_rec["matchType"],
                    "evidence": cap_rec["evidence"],
                    "reason": cap.reason,
                    "source": cap.source,
                }
            )
        rec["transferableEvidence"] = transferable

    return [MatchResult(**rec) for rec in records]


if __name__ == "__main__":
    # Smoke test: hand-built requirement list, no LLM call involved.
    sample = RequirementList(
        requirements=[
            Requirement(
                raw="Experience with PostgreSQL or similar relational databases",
                skillQuery="postgres",
                importance="required",
                category="technology",
            ),
            Requirement(
                raw="Experience building backend APIs",
                skillQuery="fastapi",
                importance="required",
                category="technology",
            ),
            Requirement(
                raw="Strong backend engineering fundamentals",
                skillQuery="backend engineering",
                importance="required",
                category="capability",
            ),
            Requirement(
                raw="Kubernetes experience",
                skillQuery="kubernetes",
                importance="preferred",
                category="technology",
            ),
            # Regression test: "jwt verification" has no Project-level USED/DEMONSTRATES
            # edge and no Education LEARNED edge -- its only evidence path is
            # Project -[:ACHIEVED]-> Achievement -[:DEMONSTRATES]-> Skill. Achievement
            # nodes have no name/title/institution property, only `description`; before
            # the label-aware fix this produced Evidence(source=None), which failed
            # Pydantic validation (source: str) and crashed the matcher.
            Requirement(
                raw="Experience implementing JWT verification",
                skillQuery="jwt verification",
                importance="required",
                category="capability",
            ),
            # Regression test: "infrastructure security" previously high-confidence
            # matched "security boundary design" purely because both strings contain
            # the generic tokens "infrastructure"/"security". Both tokens are on the
            # generic stoplist, so the meaningful (non-generic) overlap is empty and
            # the candidate must be rejected outright, not just downgraded.
            Requirement(
                raw="Improve infrastructure reliability, security, and performance.",
                skillQuery="infrastructure security",
                importance="required",
                category="capability",
            ),
            # Regression test: "professional" is a generic connector token (like
            # "engineering"/"management"), not evidence of a real relationship on its
            # own. Previously false-positive-matched "professional communication" via
            # the English Teacher role purely on that shared word.
            Requirement(
                raw="1-3 years as a software engineer, or fresh out of university with strong projects",
                skillQuery="professional software engineering experience",
                importance="required",
                category="domain",
            ),
            # Regression test: "user" is likewise generic. Previously false-positive-
            # matched "user and permission management" (Linux sudo/group config on
            # Born2beroot) purely on that shared word -- semantically unrelated to a
            # product/UX "user-first mindset".
            Requirement(
                raw="A user-first mindset -- you want what you build to work for real people",
                skillQuery="user-first mindset",
                importance="inferred",
                category="soft_skill",
            ),
            # Regression test: CloudWatch is a genuine gap (no Skill node exists for
            # it), but DataBridge has real observability/logging work. That must
            # surface as transferable evidence WITHOUT turning the literal gap into
            # a match -- CloudWatch itself was never used.
            Requirement(
                raw="Trace and fix bugs in production (Sentry, CloudWatch, Postgres)",
                skillQuery="cloudwatch",
                importance="required",
                category="technology",
                relatedCapabilities=[
                    RelatedCapability(
                        skillQuery="observability",
                        reason="CloudWatch is used for application/infrastructure monitoring",
                        source="llm_semantic",
                    ),
                    RelatedCapability(
                        skillQuery="application logging",
                        reason="CloudWatch centralizes application logs",
                        source="llm_semantic",
                    ),
                ],
            ),
            # Regression test: Express is a genuine gap (El Compas used Fastify, a
            # different library), but the Fastify backend work is real transferable
            # backend-engineering evidence. Must not be claimed as Express experience.
            Requirement(
                raw="Node/Express on the backend",
                skillQuery="express",
                importance="preferred",
                category="technology",
                relatedCapabilities=[
                    RelatedCapability(
                        skillQuery="backend engineering",
                        reason="Express is a Node.js backend web framework",
                        source="llm_semantic",
                    ),
                ],
            ),
            # Regression test: AWS must NOT auto-resolve to a generic capability like
            # "backend engineering" -- AWS is a broad cloud platform with no specific,
            # conservative capability link in this graph. relatedCapabilities stays
            # empty by default (nothing here proposes one), and transferableEvidence
            # must stay empty too -- there is no automatic AWS -> anything inference.
            Requirement(
                raw="Postgres, AWS",
                skillQuery="aws",
                importance="preferred",
                category="technology",
            ),
            # Regression test: Cursor must NOT automatically equal Claude Code. Both are
            # AI coding tools, but knowing one is not meaningful transferable evidence for
            # the other -- relatedCapabilities stays empty by default here too.
            Requirement(
                raw="AI-native: tools like Claude Code or Cursor are a natural part of how you work",
                skillQuery="cursor",
                importance="required",
                category="technology",
            ),
            # Regression test: "debugging" is a canonical exact match (high
            # matchConfidence) but its only evidence is 42 Prague peer-tutoring and
            # curriculum coursework -- evidenceStrength must reflect that context
            # independently of the lexical-certainty confidence score.
            Requirement(
                raw="Trace and fix bugs in production -- debugging is the fastest way to understand a system",
                skillQuery="debugging",
                importance="required",
                category="capability",
            ),
            # Regression test: DataBridge's LLM extraction achievement is reached via
            # Project-[:DURING]->Role-[:AT]->Company (real paid employment) -- it should
            # score higher evidenceStrength than a personal-project achievement of the
            # same sourceType.
            Requirement(
                raw="First experience with AI tools and LLM APIs",
                skillQuery="llm api integration",
                importance="preferred",
                category="technology",
            ),
        ]
    )
    results = match_requirements(sample)
    for result in results:
        print(result.model_dump_json(indent=2))
        if result.skillQuery == "jwt verification":
            assert result.hasEvidence, "regression: jwt verification should have evidence"
            assert all(
                e.sourceType == "Achievement" for e in result.evidence
            ), "regression: jwt verification's only evidence source should be an Achievement"
            assert all(
                e.source and not e.source.isspace() for e in result.evidence
            ), "regression: Achievement evidence source must not be null/blank"
            assert all(
                e.project for e in result.evidence
            ), "regression: Achievement evidence must resolve back to its owning Project"
            assert result.matchType == "canonical_exact", "regression: 'jwt verification' is itself a canonical skill name"
        if result.skillQuery == "postgres":
            assert result.canonicalSkill == "postgresql", "regression: postgres alias must resolve to postgresql"
            assert result.matchConfidence == "high", "regression: exact alias match must be high confidence"
            assert result.matchType == "alias_exact", "regression: 'postgres' matches via alias, not canonical name"
        if result.skillQuery == "backend engineering":
            assert result.canonicalSkill == "backend engineering", "regression: exact canonical name must resolve to itself"
            assert result.matchConfidence == "high", "regression: exact canonical-name match must be high confidence"
            assert result.matchType == "canonical_exact", "regression: 'backend engineering' matches its own canonical name"
        if result.skillQuery == "infrastructure security":
            assert not (result.canonicalSkill == "security boundary design" and result.matchConfidence == "high"), (
                "regression: 'infrastructure security' must not high-confidence-match "
                "'security boundary design' via generic token overlap alone"
            )
            assert result.matchConfidence != "high", (
                "regression: 'infrastructure security' has only generic-token overlap "
                "with any candidate and must not reach high confidence on anything"
            )
            assert result.matchType == "no_match", "regression: purely generic-token overlap must be classified no_match"
        if result.skillQuery == "professional software engineering experience":
            assert result.canonicalSkill != "professional communication", (
                "regression: 'professional' is a generic token and must not alone justify "
                "matching 'professional communication'"
            )
            assert result.recommendation != "include" or result.matchType in (
                "canonical_exact", "alias_exact", "lexical_multi_token"
            ), "regression: must not auto-include a single-generic-token false positive"
        if result.skillQuery == "user-first mindset":
            assert result.canonicalSkill != "user and permission management", (
                "regression: 'user' is a generic token and must not alone justify matching "
                "'user and permission management'"
            )
        if result.skillQuery == "cloudwatch":
            assert not result.hasEvidence, "regression: CloudWatch has no Skill node and must remain a gap"
            assert result.matchType == "no_match", "regression: CloudWatch must not fuzzy-match anything"
            transferable_skills = {t.canonicalSkill for t in result.transferableEvidence if t.canonicalSkill}
            assert "observability" in transferable_skills, (
                "regression: CloudWatch's related-capability 'observability' should surface "
                "transferable evidence from DataBridge even though CloudWatch itself is a gap"
            )
            for t in result.transferableEvidence:
                assert t.evidence, f"regression: transferable capability {t.capabilityQuery!r} should have evidence"
        if result.skillQuery == "express":
            assert not result.hasEvidence, "regression: Express (Fastify != Express) must remain a gap"
            assert result.matchType == "no_match", "regression: Express must not fuzzy-match Fastify"
            transferable_skills = {t.canonicalSkill for t in result.transferableEvidence if t.canonicalSkill}
            assert "backend engineering" in transferable_skills, (
                "regression: Express's related-capability 'backend engineering' should surface "
                "the El Compas Fastify backend work without claiming Express itself"
            )
        if result.skillQuery == "debugging":
            assert result.matchType == "canonical_exact", "regression: 'debugging' is itself a canonical skill name"
            assert result.matchConfidence == "high", "regression: exact canonical match must be high confidence"
            assert result.hasEvidence and result.evidence, "regression: debugging should have evidence (tutoring/coursework)"
            assert all(
                e.evidenceStrength in ("weak", "moderate") for e in result.evidence
            ), (
                "regression: debugging's only evidence (42 Prague tutoring + curriculum) is not "
                "professional/production context, so evidenceStrength must stay weak/moderate "
                "even though matchConfidence is high"
            )
        if result.skillQuery == "aws":
            assert not result.hasEvidence, "regression: AWS has no Skill node and must remain a gap"
            assert result.transferableEvidence == [], (
                "regression: AWS must not auto-resolve to a generic capability like 'backend "
                "engineering' -- with no relatedCapabilities proposed, transferableEvidence must "
                "stay empty rather than inventing a broad, unjustified link"
            )
        if result.skillQuery == "cursor":
            assert not result.hasEvidence, "regression: Cursor has no Skill node and must remain a gap"
            assert result.transferableEvidence == [], (
                "regression: Cursor must not automatically equal Claude Code -- two AI coding "
                "tools are not meaningful transferable evidence for each other"
            )
        if result.skillQuery == "llm api integration":
            assert result.matchType == "canonical_exact"
            professional_evidence = [e for e in result.evidence if e.professionalContext]
            assert professional_evidence, (
                "regression: DataBridge's LLM-extraction achievement is reached via "
                "Project-[:DURING]->Role-[:AT]->Company and must be flagged professionalContext=True"
            )
            assert all(e.evidenceStrength == "strong" for e in professional_evidence), (
                "regression: professional-context Achievement evidence should score 'strong'"
            )
    print("All regression checks passed.")
