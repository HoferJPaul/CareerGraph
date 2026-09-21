import { useState } from "react";
import { toEdit } from "../api/sourceCvEdit";
import type {
  ContactInfo,
  EducationEdit,
  EmploymentEdit,
  FactEdit,
  ProfileEdit,
  ProjectEdit,
  SourceProfileView,
} from "../types/career";

function replaceAt<T>(list: T[], index: number, patch: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...patch } : item));
}

function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  wide,
}: {
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  placeholder?: string;
  wide?: boolean;
}) {
  return (
    <label className={`field${wide ? " field-wide" : ""}`}>
      <span>{label}</span>
      <input type="text" value={value ?? ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

function FactsEditor({ facts, onChange, noun }: { facts: FactEdit[]; onChange: (facts: FactEdit[]) => void; noun: string }) {
  return (
    <div className="facts-editor">
      {facts.map((fact, i) => (
        <div className="fact-row" key={fact.id ?? `new-${i}`}>
          <textarea
            rows={2}
            value={fact.text}
            aria-label={`${noun} ${i + 1}`}
            onChange={(e) => onChange(replaceAt(facts, i, { text: e.target.value }))}
          />
          <button type="button" className="btn btn-ghost" onClick={() => onChange(removeAt(facts, i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" className="btn btn-ghost" onClick={() => onChange([...facts, { id: null, text: "" }])}>
        + Add a statement
      </button>
    </div>
  );
}

function DateFields({
  startText,
  endText,
  current,
  onChange,
}: {
  startText: string | null;
  endText: string | null;
  current: boolean;
  onChange: (patch: { startText?: string; endText?: string; current?: boolean }) => void;
}) {
  return (
    <>
      <Field label="Start (as written)" value={startText} onChange={(v) => onChange({ startText: v })} placeholder="e.g. Mar 2018" />
      <Field label="End (as written)" value={endText} onChange={(v) => onChange({ endText: v })} placeholder="e.g. Aug 2021, or Present" />
      <label className="field field-check">
        <input type="checkbox" checked={current} onChange={(e) => onChange({ current: e.target.checked })} />
        <span>Ongoing</span>
      </label>
    </>
  );
}

const CONTACT_FIELDS: { key: keyof Omit<ContactInfo, "otherUrls">; label: string; placeholder?: string }[] = [
  { key: "fullName", label: "Full name" },
  { key: "email", label: "Email" },
  { key: "telephone", label: "Telephone" },
  { key: "city", label: "City" },
  { key: "country", label: "Country" },
  { key: "linkedinUrl", label: "LinkedIn", placeholder: "https://linkedin.com/in/…" },
  { key: "githubUrl", label: "GitHub", placeholder: "https://github.com/…" },
  { key: "portfolioUrl", label: "Portfolio or website", placeholder: "https://…" },
];

interface EditorProps {
  profile: SourceProfileView;
  saving: boolean;
  onSave: (edit: ProfileEdit) => void;
  onCancel: () => void;
}

export default function SourceCvEditor({ profile, saving, onSave, onCancel }: EditorProps) {
  const [draft, setDraft] = useState<ProfileEdit>(() => toEdit(profile));
  const patch = (changes: Partial<ProfileEdit>) => setDraft((d) => ({ ...d, ...changes }));

  return (
    <form
      className="editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft);
      }}
    >
      <p className="editor-hint">
        Correct anything the parse got wrong. Dates are kept exactly as you write them. Removing an entry removes it
        from every CV generated from now on.
      </p>

      <fieldset disabled={saving}>
        <legend>Contact and links</legend>
        <div className="editor-grid">
          {CONTACT_FIELDS.map((f) => (
            <Field
              key={f.key}
              label={f.label}
              placeholder={f.placeholder}
              value={draft.contact[f.key]}
              onChange={(v) => patch({ contact: { ...draft.contact, [f.key]: v } })}
            />
          ))}
        </div>
        {draft.contact.otherUrls.length > 0 && (
          <p className="editor-hint">Other links kept as they are: {draft.contact.otherUrls.map((u) => u.url).join(", ")}</p>
        )}
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Profile summary</legend>
        <textarea
          rows={3}
          aria-label="Profile summary"
          value={draft.summary ?? ""}
          onChange={(e) => patch({ summary: e.target.value })}
        />
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Employment ({draft.employment.length})</legend>
        {draft.employment.map((entry: EmploymentEdit, i) => (
          <div className="editor-entry" key={entry.id ?? `new-${i}`}>
            <div className="editor-grid">
              <Field label="Job title" value={entry.title} onChange={(v) => patch({ employment: replaceAt(draft.employment, i, { title: v }) })} />
              <Field label="Employer" value={entry.employer} onChange={(v) => patch({ employment: replaceAt(draft.employment, i, { employer: v }) })} />
              <Field label="Location" value={entry.location} onChange={(v) => patch({ employment: replaceAt(draft.employment, i, { location: v }) })} />
              <DateFields
                startText={entry.startText}
                endText={entry.endText}
                current={entry.current}
                onChange={(c) => patch({ employment: replaceAt(draft.employment, i, c) })}
              />
            </div>
            <FactsEditor
              noun="Statement"
              facts={entry.facts}
              onChange={(facts) => patch({ employment: replaceAt(draft.employment, i, { facts }) })}
            />
            <button type="button" className="btn btn-ghost btn-remove" onClick={() => patch({ employment: removeAt(draft.employment, i) })}>
              Remove this position
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn"
          onClick={() =>
            patch({
              employment: [
                ...draft.employment,
                { id: null, employer: "", title: "", location: "", startText: "", endText: "", current: false, facts: [] },
              ],
            })
          }
        >
          + Add a position
        </button>
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Education ({draft.education.length})</legend>
        {draft.education.map((entry: EducationEdit, i) => (
          <div className="editor-entry" key={entry.id ?? `new-${i}`}>
            <div className="editor-grid">
              <Field label="Institution" value={entry.institution} onChange={(v) => patch({ education: replaceAt(draft.education, i, { institution: v }) })} />
              <Field label="Qualification" value={entry.qualification} onChange={(v) => patch({ education: replaceAt(draft.education, i, { qualification: v }) })} />
              <Field label="Field of study" value={entry.field} onChange={(v) => patch({ education: replaceAt(draft.education, i, { field: v }) })} />
              <Field label="Location" value={entry.location} onChange={(v) => patch({ education: replaceAt(draft.education, i, { location: v }) })} />
              <DateFields
                startText={entry.startText}
                endText={entry.endText}
                current={entry.current}
                onChange={(c) => patch({ education: replaceAt(draft.education, i, c) })}
              />
            </div>
            <button type="button" className="btn btn-ghost btn-remove" onClick={() => patch({ education: removeAt(draft.education, i) })}>
              Remove this entry
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn"
          onClick={() =>
            patch({
              education: [
                ...draft.education,
                { id: null, institution: "", qualification: "", field: "", location: "", startText: "", endText: "", current: false, facts: [] },
              ],
            })
          }
        >
          + Add education
        </button>
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Languages ({draft.languages.length})</legend>
        {draft.languages.map((entry, i) => (
          <div className="editor-grid editor-inline" key={entry.id ?? `new-${i}`}>
            <Field label="Language" value={entry.language} onChange={(v) => patch({ languages: replaceAt(draft.languages, i, { language: v }) })} />
            <Field label="Level" value={entry.proficiency} onChange={(v) => patch({ languages: replaceAt(draft.languages, i, { proficiency: v }) })} />
            <button type="button" className="btn btn-ghost" onClick={() => patch({ languages: removeAt(draft.languages, i) })}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" className="btn" onClick={() => patch({ languages: [...draft.languages, { id: null, language: "", proficiency: "" }] })}>
          + Add a language
        </button>
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Certifications ({draft.certifications.length})</legend>
        {draft.certifications.map((entry, i) => (
          <div className="editor-grid editor-inline" key={entry.id ?? `new-${i}`}>
            <Field label="Certification" value={entry.name} onChange={(v) => patch({ certifications: replaceAt(draft.certifications, i, { name: v }) })} />
            <Field label="Issuer" value={entry.issuer} onChange={(v) => patch({ certifications: replaceAt(draft.certifications, i, { issuer: v }) })} />
            <Field label="Date" value={entry.dateText} onChange={(v) => patch({ certifications: replaceAt(draft.certifications, i, { dateText: v }) })} />
            <button type="button" className="btn btn-ghost" onClick={() => patch({ certifications: removeAt(draft.certifications, i) })}>
              Remove
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn"
          onClick={() => patch({ certifications: [...draft.certifications, { id: null, name: "", issuer: "", dateText: "" }] })}
        >
          + Add a certification
        </button>
      </fieldset>

      <fieldset disabled={saving}>
        <legend>Projects ({draft.projects.length})</legend>
        {draft.projects.map((entry: ProjectEdit, i) => (
          <div className="editor-entry" key={entry.id ?? `new-${i}`}>
            <div className="editor-grid">
              <Field label="Project" value={entry.name} onChange={(v) => patch({ projects: replaceAt(draft.projects, i, { name: v }) })} />
              <Field label="Your role" value={entry.role} onChange={(v) => patch({ projects: replaceAt(draft.projects, i, { role: v }) })} />
              <Field label="Link" value={entry.url} onChange={(v) => patch({ projects: replaceAt(draft.projects, i, { url: v }) })} />
              <DateFields
                startText={entry.startText}
                endText={entry.endText}
                current={entry.current}
                onChange={(c) => patch({ projects: replaceAt(draft.projects, i, c) })}
              />
            </div>
            <FactsEditor noun="Statement" facts={entry.facts} onChange={(facts) => patch({ projects: replaceAt(draft.projects, i, { facts }) })} />
            <button type="button" className="btn btn-ghost btn-remove" onClick={() => patch({ projects: removeAt(draft.projects, i) })}>
              Remove this project
            </button>
          </div>
        ))}
      </fieldset>

      <div className="editor-actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Saving…" : "Save corrections"}
        </button>
        <button type="button" className="btn" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </form>
  );
}
