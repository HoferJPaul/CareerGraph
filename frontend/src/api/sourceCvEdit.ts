import type { FactEdit, ProfileEdit, SourceFactView, SourceProfileView } from "../types/career";

/** The editable form of a stored profile. Existing ids are echoed back so the server can tell an edited
 * entry from a new one (no id) and a removed one (left out). Excerpts, normalized dates and every other
 * server-derived field are never part of it. */
export function toEdit(profile: SourceProfileView): ProfileEdit {
  const facts = (items: SourceFactView[]): FactEdit[] => items.map((f) => ({ id: f.id, text: f.text }));
  return {
    expectedRevision: profile.revision,
    contact: { ...profile.contact },
    summary: profile.summary?.text ?? null,
    employment: profile.employment.map((e) => ({
      id: e.id,
      employer: e.employer,
      title: e.title,
      location: e.location,
      startText: e.startText,
      endText: e.endText,
      current: e.current,
      facts: facts(e.facts),
    })),
    education: profile.education.map((e) => ({
      id: e.id,
      institution: e.institution,
      qualification: e.qualification,
      field: e.field,
      location: e.location,
      startText: e.startText,
      endText: e.endText,
      current: e.current,
      facts: facts(e.facts),
    })),
    projects: profile.projects.map((p) => ({
      id: p.id,
      name: p.name,
      role: p.role,
      url: p.url,
      startText: p.startText,
      endText: p.endText,
      current: p.current,
      facts: facts(p.facts),
    })),
    certifications: profile.certifications.map((c) => ({ id: c.id, name: c.name, issuer: c.issuer, dateText: c.dateText })),
    languages: profile.languages.map((l) => ({ id: l.id, language: l.language, proficiency: l.proficiency })),
    otherSections: profile.otherSections.map((s) => ({ id: s.id, heading: s.heading, items: facts(s.items) })),
    conflictResolutions: {},
  };
}
