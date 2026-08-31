# data/

This directory holds the pipeline's input data. Three files are gitignored because they contain
one person's real career history and a real third-party job posting -- they're not meant to be
redistributed:

- `career_seed.json` -- the source-of-truth career graph (person, roles, companies, projects,
  education, skills, achievements, relationships) ingested into Neo4j by `ingest_career.py`. See
  `ingest_career.py` and `requirement_schema.py` for the expected shape.
- `jobs.txt` -- a raw job description used as the demo input to `extract_requirements.py`.
- `requirements.json` -- the structured `RequirementList` (validated against
  `requirement_schema.py`) extracted from `jobs.txt`.

To run the pipeline yourself, supply your own versions of these three files with the same shape,
then follow the "Run it" steps in the root `README.md`.
