# CareerGraph

*The LLM writes. The graph proves.*

A personal professional-evidence graph in Neo4j Aura, matched against job descriptions and
turned into a clean, provenance-preserving evidence package — with every claim traceable back
to real graph evidence.

## Layout

```
CareerGraph/
├── pipeline/           The pipeline itself (see Architecture below), plus requirements.txt
│                       (shared pipeline dependencies -- neo4j, pydantic -- single source of truth)
├── data/               Source/seed data: career_seed.json, jobs.txt, requirements.json
│                       (gitignored -- see data/README.md; bring your own)
├── output/             Generated pipeline artifacts (gitignored, regenerated on each run)
├── tests/              Pipeline regression tests (Neo4j-backed, no mocking)
├── backend/            FastAPI app -- orchestrates the pipeline modules, reimplements nothing
└── frontend/           React + TypeScript (Vite, React Router, React Flow)
```

## Architecture

Extraction is a mandatory, first-class stage -- a raw job description is never handed to
the matcher directly:

```
job description (raw text)
        |
        v
llm_provider.LLMProvider.extract_requirements()  -- ALWAYS runs first
        |
        v
RequirementList (validated against requirement_schema.py)
        |
        +--> written to output/requirements.json (transparency/debugging)
        |
        v
capability expansion (capability_suggest.suggest_from_context)
        |
        v
matching.match_requirements()  -- only ever called with a RequirementList
```

```
data/career_seed.json --(ingest_career.py, one-time/idempotent)--> Neo4j Aura
                                                                        |
                                                                        v
requirement_schema.py / matching.py / capability_suggest.py / metrics.py
        (matching -- read-only against Neo4j, no LLM API calls)
                                                                        |
                                                                        v
                                                       tailor_cv.py --> output/cv_context.json
                                                       (evidence retrieval: dedup, rank,
                                                       group into evidenceStories)
                                                                        |
                        +-----------------------------------------------+
                        |                                               |
                        v                                               v
        cv_evidence.py --> output/cv_evidence.json          cv_writer.py --> structured_cv.py
        (LLM-ready evidence package: grouped by            --> backend/cv_markdown.py
        real-world experience, ranked, include/optional/    (deterministic CV writer +
        omit guidance -- for manual/Claude CV writing)       renderer, used live by the web app)
                                                                        |
                                                                        v
                                                       backend/ (FastAPI, orchestrates the
                                                       pipeline modules above)
                                                                        |
                                                                        v
                                                       frontend/ (React + TypeScript)
```

Both `cv_evidence.py` and `cv_writer.py` consume the same `tailor_cv.CVContext` but serve
different purposes: `cv_writer.py` is the deterministic path the web app renders live;
`cv_evidence.py` produces a richer, grouped/ranked evidence package meant for a human or
Claude to write a higher-quality CV from manually. Neither reimplements matching or scoring.

## Run it

**Backend** (FastAPI, port 8000):

```
cd backend
python -m pip install -r requirements.txt   # pulls in ../pipeline/requirements.txt too
python -m uvicorn main:app --reload --port 8000
```

Requires a `.env` file in the repo root with `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`,
`NEO4J_DATABASE` for your own Neo4j Aura instance -- copy `.env.example` and fill in your values.

**Frontend** (Vite dev server, port 5173):

```
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

**Pipeline CLI** (no server needed):

```
python -m pip install -r pipeline/requirements.txt
python pipeline/extract_requirements.py data/jobs.txt   # checkpoint: validates data/requirements.json
                                                          # against this JD; see "What's manual right now"
python pipeline/tailor_cv.py data/requirements.json      # -> output/cv_context.json
python pipeline/cv_evidence.py output/cv_context.json    # -> output/cv_evidence.json
python pipeline/write_cv.py output/cv_context.json       # -> output/structured_cv.json (deterministic)
```

## Tests

```
# Pipeline (Neo4j-backed, no mocking)
python pipeline/metrics.py
python pipeline/matching.py
python tests/test_tailor_cv.py
python tests/test_capability_suggest.py
python tests/test_cv_writer.py
python tests/test_cv_evidence.py
python tests/test_llm_provider.py

# API layer
cd backend
python test_api.py
```

## What's manual right now

Requirement extraction (job description → structured requirements) was designed for an
interactive LLM (Claude Code reading a JD and authoring `data/requirements.json` by hand — see
`extract_requirements.py` and `match_job.py`'s docstring). There is no external LLM API
configured for this project, so `backend/llm_provider.py` does not fake one. Every job
description still goes through `LLMProvider.extract_requirements()` before anything else sees
it — never straight into `matching.py` — via one of two honest, non-LLM implementations:
pasting the exact demo job description (`data/jobs.txt`) reuses the already-curated
`data/requirements.json` (`ManualFileLLMProvider`); anything else falls back to a fully
automatic, but purely keyword-based, extraction against the real CareerGraph skill vocabulary
(`HeuristicKeywordLLMProvider`). Both always return a validated `RequirementList`. Run
`python pipeline/extract_requirements.py <job_description.txt>` as an explicit checkpoint — it validates
`data/requirements.json` against the schema and confirms it corresponds to that JD, or prints
the exact manual-authoring steps if it doesn't. See `backend/llm_provider.py` for the full
explanation and the `LLMProvider` interface a real LLM-backed implementation would plug into.

CV prose (headline, profile paragraph, polished bullets) has the same shape of gap: the web app's
`cv_writer.DeterministicCVWriter` is rule-based (selects, merges, suppresses, and buckets real
evidence but doesn't rewrite it into tighter prose). `write_cv.py`'s "Claude-assisted" mode, or
handing `output/cv_evidence.json` to Claude directly, is the higher-quality path until a real LLM
provider implements the same `CVWriter` interface.
