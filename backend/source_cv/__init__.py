"""The persistent "Source CV": the candidate's own base CV, parsed once into a strict structured
profile and reused for every generated CV.

Everything here is deterministic and provider-free (extraction, storage, grounding, reconciliation).
The only model call -- parsing the extracted text -- lives in backend/llm/source_profile.py behind
the existing structured-output client.
"""
