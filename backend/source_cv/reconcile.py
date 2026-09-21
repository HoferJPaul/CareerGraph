"""Graph/source reconciliation: one deterministic career timeline from two authorities.

    Neo4j (via the CVContext's evidence stories) is authoritative for achievements, skills, quantified
    claims and matches.  The Source CV is authoritative for the complete chronology, contact details,
    education and anything the graph does not know.  This module decides -- with no model involved --
    which source role IS which graph role, and how their facts combine.

Matching (roles): same employer after normalisation (case, punctuation, legal suffixes such as GmbH),
periods that overlap (unknown dates never contradict), and -- to avoid merging genuinely separate
positions -- either a similar title or the only candidate on both sides. A source role matches at most
one graph role and vice versa, so no role can appear twice.

Combining a matched pair:
    * graph value present, source absent  -> graph.        source present, graph absent -> source fills in.
    * both present and equivalent         -> the graph's spelling.
    * both present and DIFFERENT          -> a Conflict. Neither value is chosen silently: unless the user has
      explicitly resolved it (use_source / use_graph) the field is left OUT of the CV, and the entry still
      appears with everything that is not in dispute.
    * A graph Project story's dates describe the project, not necessarily the whole role, so a source
      role with a wider period is not a conflict; a graph period reaching outside the source's is.

Everything is sorted by one deterministic key (most recent first; undated last; then document order), and
possible gaps in the timeline are REPORTED, never filled.
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from llm.evidence_registry import EvidenceRegistry
from source_cv.dates import format_month_index, format_partial, month_index, parse_date_text
from source_cv.schema import (
    ContactInfo,
    Conflict,
    EducationEntry,
    EmploymentEntry,
    ProjectEntry,
    SourceProfile,
    graph_conflict_id,
)
from tailor_cv import EvidenceStory

Basis = Literal["graph+source", "source_only", "graph_only"]

GAP_THRESHOLD_MONTHS = 6
_LEGAL_SUFFIXES = {
    "gmbh", "ag", "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "sa", "bv", "oy", "ab", "plc",
    "srl", "kg", "ug", "se", "nv", "pty", "llp", "company",
}
_FAR_FUTURE = 10**9


# ---- text normalisation ---------------------------------------------------------------------------------


def _words(text: Optional[str]) -> list[str]:
    return re.findall(r"[^\W_]+", (text or "").replace("&", " and ").casefold())


def norm_org(text: Optional[str]) -> str:
    """Employer/institution key: lowercase words, no punctuation, no legal-form suffix."""
    words = _words(text)
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def norm_title(text: Optional[str]) -> str:
    return " ".join(_words(text))


def _similar(a: Optional[str], b: Optional[str], threshold: float = 0.6) -> bool:
    x, y = set(_words(a)), set(_words(b))
    if not x or not y:
        return False
    return x <= y or y <= x or len(x & y) / len(x | y) >= threshold


def _title_score(source_title: Optional[str], graph_title: Optional[str]) -> int:
    """2 = same, 1 = similar or not comparable, 0 = different."""
    if not source_title or not graph_title:
        return 1
    if norm_title(source_title) == norm_title(graph_title):
        return 2
    return 1 if _similar(source_title, graph_title) else 0


# ---- periods --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    start: Optional[str] = None  # "YYYY" | "YYYY-MM"
    end: Optional[str] = None
    current: bool = False
    start_text: Optional[str] = None  # original wording, used only when the value could not be normalized
    end_text: Optional[str] = None

    @property
    def known(self) -> bool:
        return bool(self.start)


def _start_bounds(value: str) -> tuple[int, int]:
    return month_index(value, end=False), month_index(value, end=True)


def spans_overlap(a: Span, b: Span) -> Optional[bool]:
    """True/False when both periods are known, None when either has no start (cannot contradict)."""
    if not (a.start and b.start):
        return None
    a_lo = _start_bounds(a.start)[0]
    b_lo = _start_bounds(b.start)[0]
    a_hi = _FAR_FUTURE if a.current or not a.end else month_index(a.end, end=True)
    b_hi = _FAR_FUTURE if b.current or not b.end else month_index(b.end, end=True)
    return a_lo <= b_hi and b_lo <= a_hi


def _points_differ(x: Optional[str], y: Optional[str]) -> bool:
    """Two dates disagree only if NO reading of either (a bare year spans its whole year) can make them equal."""
    if not (x and y):
        return False
    x_lo, x_hi = _start_bounds(x)
    y_lo, y_hi = _start_bounds(y)
    return x_hi < y_lo or y_hi < x_lo


def spans_disagree_exactly(source: Span, graph: Span) -> bool:
    if _points_differ(source.start, graph.start) or _points_differ(source.end, graph.end):
        return True
    return bool(source.current != graph.current and (source.end or graph.end))


def graph_span_outside(source: Span, graph: Span) -> bool:
    """True when the graph's period reaches outside the source role's (impossible if the source is right)."""
    if source.start and graph.start and _start_bounds(graph.start)[1] < _start_bounds(source.start)[0]:
        return True
    if graph.end and source.end and not source.current and month_index(graph.end, end=False) > month_index(source.end, end=True):
        return True
    if graph.current and source.end and not source.current:
        return True
    return False


def format_span(span: Span) -> Optional[str]:
    start = format_partial(span.start) or span.start_text
    end = "Present" if span.current else (format_partial(span.end) or span.end_text)
    if start and end:
        return f"{start} – {end}"
    return start or end or None


def _chron_key(span: Span, order: int) -> tuple:
    """Most recent first; undated last; then original order. Total and deterministic."""
    if not span.start:
        return (1, 0, 0, order)
    end = _FAR_FUTURE if span.current else (month_index(span.end, end=True) if span.end else month_index(span.start, end=True))
    return (0, -end, -month_index(span.start, end=False), order)


def _span_of_entry(entry) -> Span:
    return Span(start=entry.start, end=entry.end, current=entry.current, start_text=entry.startText, end_text=entry.endText)


def _graph_span(stories: list[EvidenceStory]) -> Span:
    """Union of the stories' periods. As in the graph-only CV, a start with no end is an ongoing role."""
    starts = [s for s in (parse_date_text(st.startDate) for st in stories) if s]
    if not starts:
        return Span()
    ends = [parse_date_text(st.endDate) for st in stories]
    ongoing = any(st.startDate and not st.endDate for st in stories)
    end_values = [e for e in ends if e]
    return Span(
        start=min(starts, key=lambda v: month_index(v, end=False)),
        end=None if ongoing or not end_values else max(end_values, key=lambda v: month_index(v, end=True)),
        current=ongoing,
    )


# ---- results --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Header:
    """The deterministic, renderable facts of one entry. `omitted` names fields left out because the two
    authorities disagree and the user has not resolved it."""

    title: Optional[str] = None
    employer: Optional[str] = None
    location: Optional[str] = None
    span: Span = field(default_factory=Span)
    omitted: tuple[str, ...] = ()

    @property
    def period(self) -> Optional[str]:
        return format_span(self.span)


@dataclass
class ReconciledRole:
    role_id: str
    basis: Basis
    header: Header
    source_entry_id: Optional[str] = None
    graph_story_ids: tuple[str, ...] = ()
    order: int = 0

    @property
    def has_graph_evidence(self) -> bool:
        return bool(self.graph_story_ids)

    @property
    def label(self) -> str:
        return " — ".join(x for x in (self.header.title, self.header.employer) if x) or "Position"


@dataclass
class ReconciledEducation:
    entry_id: str
    basis: Basis
    institution: str
    program: Optional[str]
    span: Span
    source_entry_id: Optional[str] = None
    graph_story_ids: tuple[str, ...] = ()  # the anchor story first, then the stories folded into it
    omitted: tuple[str, ...] = ()
    order: int = 0


@dataclass
class ReconciledProject:
    entry_id: str
    basis: Basis
    name: str
    role: Optional[str]
    context: Optional[str]
    span: Span
    source_entry_id: Optional[str] = None
    graph_story_id: Optional[str] = None
    omitted: tuple[str, ...] = ()
    order: int = 0


@dataclass(frozen=True)
class ChronologyGap:
    start_index: int  # first uncovered month
    end_index: int  # last uncovered month
    months: int

    @property
    def message(self) -> str:
        return (
            f"No employment or education is listed between {format_month_index(self.start_index)} and "
            f"{format_month_index(self.end_index)} ({self.months} months). Nothing was added to cover it."
        )


@dataclass
class Reconciliation:
    roles: list[ReconciledRole]
    education: list[ReconciledEducation]
    projects: list[ReconciledProject]
    contact: ContactInfo
    conflicts: list[Conflict]
    chronology_gaps: list[ChronologyGap]
    undated_roles: list[str]  # role ids
    revision: int

    def role(self, role_id: str) -> Optional[ReconciledRole]:
        return next((r for r in self.roles if r.role_id == role_id), None)

    def summary(self) -> dict:
        return {
            "roles": len(self.roles),
            "rolesWithGraphEvidence": sum(1 for r in self.roles if r.has_graph_evidence),
            "sourceOnlyRoles": sum(1 for r in self.roles if r.basis == "source_only"),
            "graphOnlyRoles": sum(1 for r in self.roles if r.basis == "graph_only"),
            "education": len(self.education),
            "conflicts": len(self.conflicts),
            "unresolvedConflicts": len([c for c in self.conflicts if c.resolution is None]),
            "chronologyGaps": len(self.chronology_gaps),
        }


# ---- conflict handling ----------------------------------------------------------------------------------


class _Resolver:
    """Creates conflicts and applies the user's earlier, explicit resolutions to them."""

    def __init__(self, previous: list[Conflict]):
        self.previous = {c.id: c.resolution for c in previous if c.origin == "graph"}
        self.conflicts: list[Conflict] = []

    def pick(
        self,
        *,
        kind: str,
        entry_id: str,
        field: str,
        source_value: Optional[str],
        graph_value: Optional[str],
        equivalent: Callable[[str, str], bool],
        description: str,
    ) -> tuple[Optional[str], bool]:
        """(value to use, left_out). See the module docstring for the rules."""
        if graph_value and source_value:
            if equivalent(source_value, graph_value):
                return graph_value, False
            return self._conflict(kind, entry_id, field, source_value, graph_value, description, source_value, graph_value)
        return graph_value or source_value, False

    def pick_span(
        self, *, kind: str, entry_id: str, source: Span, graph: Span, graph_is_exact: bool, subject: str
    ) -> tuple[Span, bool]:
        if source.known and graph.known:
            disagree = spans_disagree_exactly(source, graph) if graph_is_exact else graph_span_outside(source, graph)
            if not disagree:
                return (graph if graph_is_exact else source), False
            value, left_out = self._conflict(
                kind, entry_id, "dates", format_span(source), format_span(graph),
                f"Your CV dates this {subject} {format_span(source)}; your career graph dates it {format_span(graph)}.",
                source, graph,
            )
            return (value if isinstance(value, Span) else Span()), left_out
        chosen = graph if graph.known else source
        return chosen, False

    def _conflict(self, kind, entry_id, field, source_text, graph_text, description, source_value, graph_value):
        conflict_id = graph_conflict_id(kind, entry_id, field, source_text, graph_text)
        conflict = Conflict(
            id=conflict_id, origin="graph", kind=kind, entryId=entry_id, field=field,
            description=description if description.endswith(".") else description + ".",
            sourceValue=source_text, graphValue=graph_text, resolution=self.previous.get(conflict_id),
        )
        self.conflicts.append(conflict)
        if conflict.resolution == "use_source":
            return source_value, False
        if conflict.resolution == "use_graph":
            return graph_value, False
        return None, True


# ---- graph-side structures --------------------------------------------------------------------------------


@dataclass
class _GraphRole:
    company: Optional[str]
    title: Optional[str]
    location: Optional[str]
    span: Span
    exact: bool  # True when a Role node (not just a Project built during it) supplied the period
    story_ids: list[str] = field(default_factory=list)


def _graph_roles(registry: EvidenceRegistry) -> list[_GraphRole]:
    groups: dict[tuple, _GraphRole] = {}
    members: dict[tuple, list[EvidenceStory]] = {}
    for story_id, placement in registry.placements.items():
        if placement.section != "experience":
            continue
        story = registry.stories[story_id]
        title = story.roleTitle or story.label
        key = (norm_org(story.company), norm_title(title)) if story.company else ("", story_id)
        group = groups.setdefault(key, _GraphRole(company=story.company, title=title, location=None, span=Span(), exact=False))
        group.story_ids.append(story_id)
        members.setdefault(key, []).append(story)
        group.location = group.location or story.location
    for key, group in groups.items():
        stories = members[key]
        group.span = _graph_span(stories)
        group.exact = any(s.sourceType == "Role" for s in stories)
        group.location = group.location or next((s.workMode for s in stories if s.workMode), None)
    return list(groups.values())


def _match_roles(sources: list[EmploymentEntry], graphs: list[_GraphRole]) -> dict[int, int]:
    """source index -> graph index. Greedy by score, one-to-one."""
    candidates: list[tuple[int, int, int]] = []
    for si, source in enumerate(sources):
        if not source.employer:
            continue
        for gi, graph in enumerate(graphs):
            if not graph.company or norm_org(source.employer) != norm_org(graph.company):
                continue
            overlap = spans_overlap(_span_of_entry(source), graph.span)
            if overlap is False:
                continue  # the same employer at a different time is a different position
            score = _title_score(source.title, graph.title) * 10 + (1 if overlap else 0)
            candidates.append((score, si, gi))
    per_source: dict[int, int] = {}
    per_graph: dict[int, int] = {}
    for _, si, gi in candidates:
        per_source[si] = per_source.get(si, 0) + 1
        per_graph[gi] = per_graph.get(gi, 0) + 1
    assigned: dict[int, int] = {}
    taken: set[int] = set()
    for score, si, gi in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        if si in assigned or gi in taken:
            continue
        if score < 10 and not (per_source[si] == 1 and per_graph[gi] == 1):
            continue  # different titles: only merge when it is unambiguously the same position
        assigned[si] = gi
        taken.add(gi)
    return assigned


def _short(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8]


# ---- the reconciliation ------------------------------------------------------------------------------------


def _reconcile_roles(profile: SourceProfile, registry: EvidenceRegistry, resolver: _Resolver) -> list[ReconciledRole]:
    sources = profile.employment
    graphs = _graph_roles(registry)
    matches = _match_roles(sources, graphs)
    matched_graphs = set(matches.values())
    roles: list[ReconciledRole] = []

    for index, source in enumerate(sources):
        source_span = _span_of_entry(source)
        gi = matches.get(index)
        if gi is None:
            roles.append(
                ReconciledRole(
                    role_id=f"role:{source.id}", basis="source_only", source_entry_id=source.id,
                    header=Header(title=source.title, employer=source.employer, location=source.location, span=source_span),
                    order=index,
                )
            )
            continue
        graph = graphs[gi]
        omitted: list[str] = []
        who = f"{source.title or 'position'} at {source.employer}" if source.employer else (source.title or "position")
        title, left_out = resolver.pick(
            kind="title", entry_id=source.id, field="title", source_value=source.title, graph_value=graph.title,
            equivalent=lambda s, g: _title_score(s, g) >= 1,
            description=f"Your CV titles this position \"{source.title}\"; your career graph titles it \"{graph.title}\"",
        )
        if left_out:
            omitted.append("title")
        location, left_out = resolver.pick(
            kind="location", entry_id=source.id, field="location", source_value=source.location,
            graph_value=graph.location if graph.location and graph.location.casefold() != "remote" else None,
            equivalent=lambda s, g: _similar(s, g, 0.5),
            description=f"Your CV places {who} in \"{source.location}\"; your career graph places it in \"{graph.location}\"",
        )
        if left_out:
            omitted.append("location")
        elif not location:
            location = source.location or graph.location  # only fills a gap; never overrides a disputed value
        span, left_out = resolver.pick_span(
            kind="dates", entry_id=source.id, source=source_span, graph=graph.span, graph_is_exact=graph.exact,
            subject=f"position ({who})",
        )
        if left_out:
            omitted.append("dates")
            span = Span()
        roles.append(
            ReconciledRole(
                role_id=f"role:{source.id}", basis="graph+source", source_entry_id=source.id,
                graph_story_ids=tuple(graph.story_ids),
                header=Header(title=title, employer=graph.company or source.employer, location=location, span=span, omitted=tuple(omitted)),
                order=index,
            )
        )

    for offset, (gi, graph) in enumerate((i, g) for i, g in enumerate(graphs) if i not in matched_graphs):
        roles.append(
            ReconciledRole(
                role_id=f"role:graph:{_short(graph.company or '', graph.title or '', *graph.story_ids)}", basis="graph_only",
                graph_story_ids=tuple(graph.story_ids),
                header=Header(title=graph.title, employer=graph.company, location=graph.location, span=graph.span),
                order=len(sources) + offset,
            )
        )

    roles.sort(key=lambda r: _chron_key(r.header.span, r.order))
    return roles


def _program_text(qualification: Optional[str], field_of_study: Optional[str]) -> Optional[str]:
    if qualification and field_of_study and norm_title(field_of_study) not in norm_title(qualification):
        return f"{qualification}, {field_of_study}"
    return qualification or field_of_study


def _reconcile_education(profile: SourceProfile, registry: EvidenceRegistry, resolver: _Resolver) -> list[ReconciledEducation]:
    anchors = [
        (story_id, registry.stories[story_id])
        for story_id, placement in registry.placements.items()
        if placement.section == "education" and placement.folds_into is None
    ]
    by_institution = {norm_org(story.label): (story_id, story) for story_id, story in anchors}
    used: set[str] = set()
    out: list[ReconciledEducation] = []

    for index, source in enumerate(profile.education):
        source_span = _span_of_entry(source)
        source_program = _program_text(source.qualification, source.field)
        hit = by_institution.get(norm_org(source.institution))
        if hit is None:
            out.append(ReconciledEducation(
                entry_id=f"edu:{source.id}", basis="source_only", institution=source.institution, program=source_program,
                span=source_span, source_entry_id=source.id, order=index,
            ))
            continue
        story_id, story = hit
        used.add(story_id)
        folded = tuple(sid for sid in registry.placements if registry.placements[sid].folds_into == story_id)
        omitted: list[str] = []
        program, left_out = resolver.pick(
            kind="education", entry_id=source.id, field="qualification", source_value=source_program, graph_value=story.program,
            equivalent=lambda s, g: _similar(s, g, 0.5),
            description=f"Your CV lists \"{source_program}\" at {source.institution}; your career graph lists \"{story.program}\"",
        )
        if left_out:
            omitted.append("qualification")
        graph_span = _graph_span([story])
        span, left_out = resolver.pick_span(
            kind="education", entry_id=source.id, source=source_span, graph=graph_span, graph_is_exact=True,
            subject=f"education at {source.institution}",
        )
        if left_out:
            omitted.append("dates")
            span = Span()
        out.append(ReconciledEducation(
            entry_id=f"edu:{source.id}", basis="graph+source", institution=story.label, program=program, span=span,
            source_entry_id=source.id, graph_story_ids=(story_id, *folded), omitted=tuple(omitted), order=index,
        ))

    for offset, (story_id, story) in enumerate(a for a in anchors if a[0] not in used):
        folded = tuple(sid for sid in registry.placements if registry.placements[sid].folds_into == story_id)
        out.append(ReconciledEducation(
            entry_id=f"edu:graph:{_short(story.label)}", basis="graph_only", institution=story.label, program=story.program,
            span=_graph_span([story]), graph_story_ids=(story_id, *folded), order=len(profile.education) + offset,
        ))
    out.sort(key=lambda e: _chron_key(e.span, e.order))
    return out


def _reconcile_projects(profile: SourceProfile, registry: EvidenceRegistry, resolver: _Resolver) -> list[ReconciledProject]:
    graph = {
        norm_title(registry.stories[sid].label): (sid, registry.stories[sid])
        for sid, placement in registry.placements.items() if placement.section == "project"
    }
    used: set[str] = set()
    out: list[ReconciledProject] = []
    for index, source in enumerate(profile.projects):
        source_span = _span_of_entry(source)
        hit = graph.get(norm_title(source.name))
        if hit is None:
            out.append(ReconciledProject(
                entry_id=f"prj:{source.id}", basis="source_only", name=source.name, role=source.role, context=None,
                span=source_span, source_entry_id=source.id, order=index,
            ))
            continue
        story_id, story = hit
        used.add(story_id)
        span, left_out = resolver.pick_span(
            kind="dates", entry_id=source.id, source=source_span, graph=_graph_span([story]), graph_is_exact=True,
            subject=f"project ({source.name})",
        )
        out.append(ReconciledProject(
            entry_id=f"prj:{source.id}", basis="graph+source", name=story.label, role=story.personRole or source.role,
            context=story.context, span=Span() if left_out else span, source_entry_id=source.id, graph_story_id=story_id,
            omitted=("dates",) if left_out else (), order=index,
        ))
    for offset, (key, (story_id, story)) in enumerate((k, v) for k, v in graph.items() if v[0] not in used):
        out.append(ReconciledProject(
            entry_id=f"prj:graph:{_short(story.label)}", basis="graph_only", name=story.label, role=story.personRole,
            context=story.context, span=_graph_span([story]), graph_story_id=story_id, order=len(profile.projects) + offset,
        ))
    out.sort(key=lambda p: _chron_key(p.span, p.order))
    return out


def find_chronology_gaps(spans: list[Span], today_index: int, threshold: int = GAP_THRESHOLD_MONTHS) -> list[ChronologyGap]:
    """Months not covered by any role or education period, using the MOST GENEROUS reading of every vague
    date, so a gap is only reported if it exists however the fuzzy dates are read. Reported, never filled."""
    intervals = []
    for span in spans:
        if not span.start:
            continue
        low = month_index(span.start, end=False)
        high = today_index if span.current or not span.end else month_index(span.end, end=True)
        intervals.append((low, max(low, high)))
    intervals.sort()
    gaps: list[ChronologyGap] = []
    covered_until: Optional[int] = None
    for low, high in intervals:
        if covered_until is not None and low - covered_until - 1 >= threshold:
            gaps.append(ChronologyGap(start_index=covered_until + 1, end_index=low - 1, months=low - covered_until - 1))
        covered_until = high if covered_until is None else max(covered_until, high)
    return gaps


def reconcile(profile: SourceProfile, registry: EvidenceRegistry, *, today_index: int) -> Reconciliation:
    resolver = _Resolver(profile.conflicts)
    roles = _reconcile_roles(profile, registry, resolver)
    education = _reconcile_education(profile, registry, resolver)
    projects = _reconcile_projects(profile, registry, resolver)
    gaps = find_chronology_gaps([r.header.span for r in roles] + [e.span for e in education], today_index)
    return Reconciliation(
        roles=roles, education=education, projects=projects, contact=profile.contact.model_copy(deep=True),
        conflicts=resolver.conflicts, chronology_gaps=gaps,
        undated_roles=[r.role_id for r in roles if not r.header.span.known and "dates" not in r.header.omitted],
        revision=profile.revision,
    )
