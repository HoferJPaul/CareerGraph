"""LLM integration for CareerGraph: everything provider-specific lives here, behind the
LLMProvider (llm_provider.py) and CVWriter (pipeline/cv_writer.py) abstractions.

    The LLM writes. The graph proves.

Groq may extract requirements and write CV language. It may never create career evidence:
every extracted value is validated into the existing domain models, and every generated
CV bullet must cite evidence that exists in the Neo4j-derived CVContext.
"""
