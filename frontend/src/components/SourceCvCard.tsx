import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, describeApiError, type DescribedError } from "../api/client";
import { toEdit } from "../api/sourceCvEdit";
import { useAnalysis } from "../context/AnalysisContext";
import type { IngestStage, LlmStatus, SourceConflict, SourceProfileView } from "../types/career";
import ErrorNotice from "./ErrorNotice";
import SourceCvEditor from "./SourceCvEditor";
import { SourceParseDetails } from "./TechnicalDetails";

const STAGES: { id: IngestStage; label: string }[] = [
  { id: "uploading", label: "Uploading the file" },
  { id: "extracting", label: "Reading the text from the file" },
  { id: "parsing", label: "Parsing it into structured data" },
  { id: "validating", label: "Checking every value against the document" },
  { id: "saving", label: "Saving it privately on this server" },
];

function formatWhen(iso: string | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatBytes(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(0)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function period(start: string | null, end: string | null, current: boolean): string {
  const to = current ? "Present" : end;
  return [start, to].filter(Boolean).join(" – ");
}

function StageList({ current, dev }: { current: IngestStage; dev: boolean }) {
  const index = STAGES.findIndex((s) => s.id === current);
  return (
    <ol className="stage-list" role="status" aria-live="polite" aria-busy="true">
      {STAGES.map((stage, i) => {
        const state = i < index ? "done" : i === index ? "active" : "pending";
        const label = stage.id === "parsing" && dev ? "Parsing it with the development parser" : stage.label;
        return (
          <li key={stage.id} className={`stage stage-${state}`}>
            <span className="stage-mark" aria-hidden="true">
              {state === "done" ? "✓" : state === "active" ? <span className="spinner" /> : "○"}
            </span>
            <span>
              {label}
              {state === "active" ? "…" : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function ConflictList({
  conflicts,
  busy,
  onResolve,
}: {
  conflicts: SourceConflict[];
  busy: boolean;
  onResolve: (id: string, choice: "use_source" | "use_graph") => void;
}) {
  if (conflicts.length === 0) return null;
  return (
    <div className="notice notice-conflict" role="note">
      <strong>{plural(conflicts.length, "disagreement needs", "disagreements need")} your review.</strong> Neither value
      is used in a generated CV until you decide: the disputed detail is left out instead.
      <ul className="conflict-list">
        {conflicts.map((c) => (
          <li key={c.id}>
            <div>{c.description}</div>
            {c.origin === "graph" ? (
              <div className="conflict-actions">
                <button className="btn" disabled={busy} onClick={() => onResolve(c.id, "use_source")}>
                  Use my CV: {c.sourceValue}
                </button>
                <button className="btn" disabled={busy} onClick={() => onResolve(c.id, "use_graph")}>
                  Use my career graph: {c.graphValue}
                </button>
                {c.resolution && <span className="prov-note">Chosen: {c.resolution === "use_source" ? "your CV" : "career graph"}</span>}
              </div>
            ) : (
              <div className="prov-note">This is inside your CV itself. Correct the entry under “Review and correct”.</div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProfileReview({ profile }: { profile: SourceProfileView }) {
  const c = profile.contact;
  const contactRows: [string, string | null][] = [
    ["Name", c.fullName],
    ["Email", c.email],
    ["Telephone", c.telephone],
    ["Location", [c.city, c.country].filter(Boolean).join(", ") || null],
    ["LinkedIn", c.linkedinUrl],
    ["GitHub", c.githubUrl],
    ["Portfolio", c.portfolioUrl],
    ...c.otherUrls.map((u): [string, string | null] => [u.label ?? "Link", u.url]),
  ];
  return (
    <div className="source-review">
      <h3>Contact and links</h3>
      <dl className="source-dl">
        {contactRows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value ?? <span className="faint">not found</span>}</dd>
          </div>
        ))}
      </dl>

      {profile.summary && (
        <>
          <h3>Summary</h3>
          <p>{profile.summary.text}</p>
        </>
      )}

      <h3>Employment ({profile.employment.length})</h3>
      {profile.employment.length === 0 && <p className="faint">No positions found.</p>}
      <ul className="source-list">
        {profile.employment.map((e) => (
          <li key={e.id}>
            <strong>{[e.title, e.employer].filter(Boolean).join(" — ")}</strong>
            <span className="faint">
              {" "}
              {[e.location, period(e.startText, e.endText, e.current)].filter(Boolean).join(" · ")}
            </span>
            {e.facts.length > 0 && (
              <ul>
                {e.facts.map((f) => (
                  <li key={f.id}>{f.text}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>

      <h3>Education ({profile.education.length})</h3>
      {profile.education.length === 0 && <p className="faint">No education found.</p>}
      <ul className="source-list">
        {profile.education.map((e) => (
          <li key={e.id}>
            <strong>{e.institution}</strong>
            <span className="faint">
              {" "}
              {[[e.qualification, e.field].filter(Boolean).join(", "), period(e.startText, e.endText, e.current)]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </li>
        ))}
      </ul>

      {profile.languages.length > 0 && (
        <>
          <h3>Languages</h3>
          <p>{profile.languages.map((l) => (l.proficiency ? `${l.language} (${l.proficiency})` : l.language)).join(", ")}</p>
        </>
      )}
      {profile.certifications.length > 0 && (
        <>
          <h3>Certifications</h3>
          <ul className="source-list">
            {profile.certifications.map((cert) => (
              <li key={cert.id}>
                {[cert.name, cert.issuer, cert.dateText].filter(Boolean).join(" — ")}
              </li>
            ))}
          </ul>
        </>
      )}
      {profile.projects.length > 0 && (
        <>
          <h3>Projects</h3>
          <ul className="source-list">
            {profile.projects.map((p) => (
              <li key={p.id}>
                <strong>{p.name}</strong>
                <span className="faint"> {period(p.startText, p.endText, p.current)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {profile.otherSections.map((s) => (
        <div key={s.id}>
          <h3>{s.heading}</h3>
          <ul className="source-list">
            {s.items.map((i) => (
              <li key={i.id}>{i.text}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export default function SourceCvCard({ llmStatus }: { llmStatus: LlmStatus | null }) {
  const { sourceCvStatus: status, sourceCvStatusFailed, refreshSourceCvStatus } = useAnalysis();
  const [loadedProfile, setProfile] = useState<SourceProfileView | null>(null);
  const [phase, setPhase] = useState<"idle" | "processing" | "failed">("idle");
  const [stage, setStage] = useState<IngestStage>("uploading");
  const [error, setError] = useState<DescribedError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  // Refs, not just state: two rapid clicks/drops can both run before a state update re-renders.
  const inFlight = useRef(false);
  const retryAction = useRef<(() => Promise<SourceProfileView>) | null>(null);
  const loadRequested = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const exists = status?.exists ?? false;
  // What the server says exists is authoritative: a deleted CV is never shown, even for a render.
  const profile = exists ? loadedProfile : null;
  const dev = llmStatus?.devFallback ?? false;
  const maxBytes = status?.limits.maxUploadBytes ?? 5 * 1024 * 1024;

  // Fetch the parsed profile whenever the server says one exists and this card has not shown it yet.
  useEffect(() => {
    if (!exists || loadedProfile || phase !== "idle" || loadRequested.current) return;
    loadRequested.current = true;
    api
      .getSourceCv()
      .then(setProfile)
      .catch((err) => {
        const described = describeApiError(err, "Couldn't load your stored source CV.");
        // "Not found" just means it was deleted meanwhile: the refreshed status will show the empty state.
        if (described.code !== "source_cv_not_found") setError(described);
      })
      .finally(() => {
        loadRequested.current = false;
      });
  }, [exists, loadedProfile, phase]);

  function reloadProfile() {
    setError(null);
    api
      .getSourceCv()
      .then(setProfile)
      .catch((err) => setError(describeApiError(err, "Couldn't load your stored source CV.")));
  }

  async function run(action: () => Promise<SourceProfileView>) {
    if (inFlight.current) return;
    inFlight.current = true;
    retryAction.current = action;
    setCanRetry(true);
    setPhase("processing");
    setStage("uploading");
    setError(null);
    setNotice(null);
    try {
      const result = await action();
      setProfile(result);
      setEditing(false);
      setExpanded(true);
      // Refresh the status BEFORE leaving the busy state, so the empty "drop a file" state never flashes.
      await refreshSourceCvStatus();
      setPhase("idle");
    } catch (err) {
      setError(describeApiError(err, "Couldn't process the CV."));
      setPhase("failed");
    } finally {
      inFlight.current = false;
    }
  }

  function handleFile(file: File | undefined) {
    if (!file || inFlight.current) return;
    if (file.size === 0) {
      setError({ message: "That file is empty.", code: "empty_document", retryable: false });
      setPhase("failed");
      retryAction.current = null;
      setCanRetry(false);
      return;
    }
    if (file.size > maxBytes) {
      setError({
        message: `That file is larger than the ${formatBytes(maxBytes)} limit.`,
        code: "file_too_large",
        retryable: false,
      });
      setPhase("failed");
      retryAction.current = null;
      setCanRetry(false);
      return;
    }
    void run(() => api.uploadSourceCv(file, setStage));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragOver(false);
    handleFile(e.dataTransfer.files[0]);
  }

  async function save(edit: Parameters<typeof api.saveSourceCv>[0]) {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      setProfile(await api.saveSourceCv(edit));
      setEditing(false);
      setNotice("Your corrections were saved. Analyses you already ran keep using the earlier version — run Analyze job again to use them.");
      await refreshSourceCvStatus();
    } catch (err) {
      const described = describeApiError(err, "Couldn't save your corrections.");
      setError(described);
      if (described.code === "profile_version_conflict") setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  function resolve(id: string, choice: "use_source" | "use_graph") {
    if (!profile) return;
    void save({ ...toEdit(profile), conflictResolutions: { [id]: choice } });
  }

  async function remove() {
    if (inFlight.current) return;
    inFlight.current = true;
    setError(null);
    try {
      await api.deleteSourceCv();
      // Refresh the status FIRST: until it says "gone", the card would try to reload the deleted profile.
      await refreshSourceCvStatus();
      setProfile(null);
      setExpanded(false);
      setEditing(false);
      setConfirmingDelete(false);
      setNotice("Your source CV and its parsed profile were deleted from this server.");
    } catch (err) {
      setError(describeApiError(err, "Couldn't delete the source CV."));
    } finally {
      inFlight.current = false;
    }
  }

  const fileControls = (
    <input
      ref={fileInput}
      type="file"
      hidden
      accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      onChange={(e) => {
        handleFile(e.target.files?.[0]);
        e.target.value = ""; // allow choosing the same file again
      }}
    />
  );

  const disclosure = dev
    ? "Development mode: a local rule-based parser reads your CV, so nothing is sent to an AI provider."
    : "Your CV is sent to the AI provider configured on this server (Groq) to be parsed into structured data, then stored privately on this server. Turn on Zero Data Retention in your Groq console. Demo only — there is no login.";

  const unresolved = profile ? profile.conflicts.filter((c) => c.resolution === null) : [];

  return (
    <div className="card source-cv-card" aria-labelledby="source-cv-title">
      <div className="source-cv-head">
        <div>
          <h2 id="source-cv-title" style={{ marginBottom: 2 }}>
            Source CV
          </h2>
          <span className="badge badge-neutral">{exists ? "Active" : "Optional"}</span>
        </div>
        {exists && phase === "idle" && profile && (
          <div className="source-cv-actions">
            <button className="btn" onClick={() => fileInput.current?.click()}>
              Replace CV
            </button>
            <button className="btn" onClick={() => setExpanded((v) => !v)} aria-expanded={expanded}>
              {expanded ? "Hide details" : "Review and correct"}
            </button>
          </div>
        )}
      </div>
      {fileControls}

      {sourceCvStatusFailed && !status && (
        <div className="error-box" role="alert">
          Couldn't check for a stored source CV. Check that the backend is running.
          <div>
            <button className="btn" style={{ marginTop: 8 }} onClick={() => void refreshSourceCvStatus()}>
              Try again
            </button>
          </div>
        </div>
      )}
      {!status && !sourceCvStatusFailed && <p className="loading-text">Checking for a stored source CV…</p>}

      {notice && (
        <div className="notice" role="status" style={{ marginTop: 12 }}>
          {notice}
        </div>
      )}

      {phase === "processing" && <StageList current={stage} dev={dev} />}

      {phase === "failed" && error && (
        <div>
          <ErrorNotice
            error={error}
            onRetry={canRetry ? () => void run(retryAction.current!) : undefined}
            retryLabel="Try again"
            busy={false}
          />
          {exists && <p className="prov-note">Your previously stored source CV is unchanged.</p>}
          <div style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => setPhase("idle")}>
              {exists ? "Keep the stored CV" : "Choose another file"}
            </button>
          </div>
        </div>
      )}

      {status && !exists && phase !== "processing" && (
        <div>
          <p>
            This is your <strong>complete base CV</strong>. It supplies your contact details, links, education and full
            employment history, so a generated CV is <strong>complete</strong> — not only the evidence-backed sections
            tailored from your career graph. Your career graph stays the authority for achievements, skills and numbers.
          </p>
          <div
            className={`dropzone${dragOver ? " dropzone-over" : ""}`}
            onDragEnter={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <div>Drag and drop your CV here, or</div>
            <button className="btn btn-primary" onClick={() => fileInput.current?.click()} disabled={phase !== "idle" && phase !== "failed"}>
              Choose a file
            </button>
            <div className="prov-note">
              PDF (with selectable text) or DOCX · up to {formatBytes(maxBytes)}
            </div>
          </div>
          <p className="prov-note" style={{ marginTop: 10 }}>
            {disclosure}
          </p>
        </div>
      )}

      {exists && phase === "idle" && !profile && !error && <p className="loading-text">Loading your source CV…</p>}

      {exists && phase !== "processing" && profile && (
        <div>
          <div className="source-summary">
            <div>
              <strong>{profile.upload.originalFilename}</strong>
              <span className="faint"> · {profile.upload.detectedType.toUpperCase()} · updated {formatWhen(status?.updatedAt ?? profile.updatedAt)}</span>
            </div>
            <div>
              {[profile.contact.fullName, profile.contact.email, profile.contact.telephone].filter(Boolean).join(" · ") || (
                <span className="faint">No contact details found</span>
              )}
            </div>
            <div className="faint">
              {plural(profile.employment.length, "position", "positions")} · {plural(profile.education.length, "education entry", "education entries")} ·{" "}
              {plural(profile.languages.length, "language", "languages")} · {plural(profile.certifications.length, "certification", "certifications")}
              {profile.projects.length > 0 ? ` · ${plural(profile.projects.length, "project", "projects")}` : ""}
            </div>
            {status?.completeness && status.completeness.missing.length > 0 && (
              <div className="prov-note">Not found in your CV: {status.completeness.missing.join(", ")}</div>
            )}
          </div>

          {profile.warnings.length > 0 && (
            <div className="notice" role="note" style={{ marginTop: 12 }}>
              <strong>{plural(profile.warnings.length, "thing", "things")} to check</strong>
              <ul className="warning-list">
                {profile.warnings.map((w, i) => (
                  <li key={`${w.code}-${i}`}>{w.message}</li>
                ))}
              </ul>
            </div>
          )}

          <ConflictList conflicts={unresolved} busy={saving} onResolve={resolve} />
          {error && phase === "idle" && (
            <div>
              <ErrorNotice error={error} busy={saving} />
              {error.code === "profile_version_conflict" && (
                <button className="btn" style={{ marginTop: 8 }} onClick={reloadProfile}>
                  Reload the stored CV
                </button>
              )}
            </div>
          )}

          {expanded &&
            (editing ? (
              <SourceCvEditor profile={profile} saving={saving} onSave={(edit) => void save(edit)} onCancel={() => setEditing(false)} />
            ) : (
              <div>
                <div style={{ margin: "14px 0 6px" }}>
                  <button className="btn" onClick={() => setEditing(true)}>
                    Edit details
                  </button>{" "}
                  <button className="btn btn-ghost" onClick={() => void run(() => api.reparseSourceCv(setStage))} title="Parse the stored original again. This replaces your corrections.">
                    Parse the original again
                  </button>
                </div>
                <ProfileReview profile={profile} />
                <SourceParseDetails profile={profile} />
              </div>
            ))}

          <div className="danger-zone">
            {confirmingDelete ? (
              <div role="alertdialog" aria-label="Confirm deleting the source CV">
                <strong>Delete the stored CV and its parsed profile?</strong>
                <p className="prov-note">
                  The original file and the parsed data are removed from this server for good. Generated CVs you already
                  made are not affected.
                </p>
                <button className="btn btn-danger" onClick={() => void remove()}>
                  Yes, delete source CV
                </button>{" "}
                <button className="btn" onClick={() => setConfirmingDelete(false)}>
                  Cancel
                </button>
              </div>
            ) : (
              <button className="btn btn-danger-outline" onClick={() => setConfirmingDelete(true)}>
                Delete source CV
              </button>
            )}
          </div>
          <p className="prov-note">{disclosure}</p>
        </div>
      )}
    </div>
  );
}
