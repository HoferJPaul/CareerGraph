// Builds the two hand-off prompts for the human-in-the-loop presentation flow:
// job description -> Claude (extraction) -> requirements.json -> Neo4j match ->
// cv_context.json -> Claude (CV writing). Neither prompt is ever sent to an LLM
// API by this app -- the user copies it to Claude themselves.
import type { CVContext } from "./types/career";

export function buildExtractionPrompt(jobDescription: string): string {
  return `You are the requirement-extraction layer for CareerGraph.

Analyze the job description below and extract its meaningful hiring requirements into structured JSON.

Return ONLY valid JSON.

Do not include Markdown fences, commentary, explanations, or any text outside the JSON.

Use this exact structure:

{
  "requirements": [
    {
      "raw": "original requirement wording",
      "skillQuery": "normalized concise skill/capability",
      "importance": "required | preferred | inferred",
      "category": "technology | capability | soft_skill | domain",
      "relatedCapabilities": []
    }
  ]
}

Rules:

1. Extract concrete technologies explicitly mentioned.

Examples:
TypeScript
React
PostgreSQL
AWS
Docker

2. Extract meaningful capabilities explicitly supported by the JD.

Examples:
backend engineering
full-stack development
debugging
observability
API development

3. Do not create requirements from the job title alone.

4. Use "required" when the JD clearly expects the candidate to have it.

5. Use "preferred" for nice-to-have, bonus, advantageous, or optional requirements.

6. Use "inferred" sparingly and only when strongly supported by the job description.

7. Keep skillQuery concise and normalized.

Good:
"postgresql"
"backend engineering"

Bad:
"must have strong PostgreSQL experience"

8. Do not combine several independent requirements into one item.

Example:
"React, TypeScript and PostgreSQL"
should become three requirements.

9. Preserve the original wording in raw.

10. Leave relatedCapabilities empty.

CareerGraph will determine transferable capabilities later from the graph vocabulary.

11. Do not invent technologies or requirements not supported by the JD.

12. Deduplicate repeated requirements.

13. Return valid JSON matching the structure exactly.

JOB DESCRIPTION:

${jobDescription}`;
}

export function buildCvPrompt(cvContext: CVContext): string {
  return `You are an expert technical CV writer.

Create a highly targeted professional CV for the job represented by the CareerGraph context below.

The context was generated from a Neo4j knowledge graph containing verified professional experience, projects, achievements, skills, education, match results, transferable evidence, and provenance.

Use ONLY information supported by the supplied cv_context.json.

You are responsible for intelligently:

- deciding which experiences are most relevant
- grouping related project, role, achievement, and education evidence
- merging duplicate or overlapping evidence
- prioritizing professional experience when appropriate
- deciding what to omit
- writing natural recruiter-facing bullets
- creating a concise targeted profile
- organizing skills in a recruiter-friendly way

Do not mechanically dump every evidence item into the CV.

You MUST NOT:

- invent skills
- invent technologies
- invent responsibilities
- invent metrics
- invent dates
- invent companies or titles
- claim missing technologies
- turn transferable evidence into literal experience

Important:

A literal gap remains a gap.

Example:

If CloudWatch is a gap but the context contains transferable evidence for Observability:

You may emphasize observability experience.

You may NOT claim CloudWatch experience.

If Express is a gap but Fastify/FastAPI backend evidence exists:

You may emphasize backend/API experience.

You may NOT claim Express experience.

Treat match confidence, evidence strength, recommendation, and ranking values as internal guidance.

Do not mention them in the final CV.

Use normal CV conventions.

Prefer relevant professional experience before personal projects when appropriate.

Do not include an experience merely because one weak skill matched.

For example:

- do not create a standalone Student Tutor section just because it proves debugging
- merge it under 42 Prague if useful
- do not include Der Hutterer Weg solely to prove German if German can simply appear in Languages
- omit irrelevant teaching, NGO, farming, or technical work unless it materially strengthens this application

Write natural accomplishment-oriented bullets.

Prefer:

action + what was built/done + relevant technology + outcome/context

Do not output:

- "Skills used:"
- "Evidence-backed match"
- graph terminology
- provenance IDs
- gap analysis
- match confidence
- evidence strength
- internal recommendation labels

Suggested CV structure:

# Paul Hofer

## Professional Profile

A concise targeted profile.

## Professional Experience

Relevant employment first.

Use 2–4 strong bullets per role.

## Selected Projects

Only relevant substantial projects.

Use 2–4 strong bullets per project.

## Education

Include relevant education.

Group 42 Prague education, curriculum projects, and Student Tutor evidence coherently rather than as unrelated sections.

## Technical Skills

Group naturally, for example:

Languages
Frontend
Backend
Data
Infrastructure / Tools
AI

Do not expose CareerGraph ontology categories.

## Languages

Only state proficiency if explicitly supported.

Keep the final CV concise and targeted, approximately 1–2 pages of content.

Return ONLY the finished CV in Markdown.

CV_CONTEXT_JSON:

${JSON.stringify(cvContext, null, 2)}`;
}
