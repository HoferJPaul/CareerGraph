"""Graph/source reconciliation: deduplication, data authority, conflicts, chronology and gaps.
Pure and deterministic: no model, no Neo4j, no network."""
import copy

import pytest

from fixtures import build_cv_context
from llm.evidence_registry import build_registry
from source_cv.reconcile import (
    Span, find_chronology_gaps, format_span, norm_org, reconcile, spans_overlap,
)
from source_cv.schema import Conflict, derive
from source_helpers import TODAY_INDEX, make_profile


def _reconcile(profile=None, ctx=None):
    profile = profile or make_profile()
    ctx = ctx or build_cv_context()
    return reconcile(profile, build_registry(ctx), today_index=TODAY_INDEX)


def _by_employer(recon, employer):
    return [r for r in recon.roles if r.header.employer == employer]


def _edit_entry(profile, index, **fields):
    for key, value in fields.items():
        setattr(profile.employment[index], key, value)
    derive(profile)
    return profile


# ---- deduplication ----------------------------------------------------------------------------------------------


def test_a_role_present_in_both_appears_once_with_graph_evidence_and_source_fill_in() -> None:
    recon = _reconcile()
    assert len(recon.roles) == 3
    acme = _by_employer(recon, "Acme Analytics")
    assert len(acme) == 1
    role = acme[0]
    assert role.basis == "graph+source" and role.graph_story_ids == ("story:OrderSync",) and role.has_graph_evidence
    assert role.source_entry_id and role.header.title == "Backend Engineer"
    assert role.header.location == "Remote" and role.header.period == "Apr 2025 – Sep 2025"
    assert recon.conflicts == []


def test_roles_the_graph_does_not_know_are_kept_as_source_only_entries() -> None:
    recon = _reconcile()
    others = [r for r in recon.roles if r.header.employer != "Acme Analytics"]
    assert {r.header.employer for r in others} == {"Nordwind Logistics", "Kaffeehaus Ringstrasse"}
    assert all(r.basis == "source_only" and not r.has_graph_evidence and r.graph_story_ids == () for r in others)
    assert {r.header.title for r in others} == {"Warehouse Supervisor", "Retail Assistant"}


def test_graph_roles_missing_from_the_source_cv_are_kept_as_graph_only_entries() -> None:
    profile = make_profile(lambda p: p.employment.pop(0))  # the source CV no longer lists the Acme role
    recon = _reconcile(profile)
    acme = _by_employer(recon, "Acme Analytics")
    assert len(acme) == 1 and acme[0].basis == "graph_only" and acme[0].source_entry_id is None
    assert acme[0].header.title == "Backend Engineer" and acme[0].header.period == "Apr 2025 – Sep 2025"
    assert len(recon.roles) == 3


@pytest.mark.parametrize("spelling", ["ACME Analytics", "Acme Analytics GmbH", "Acme Analytics Ltd.", "  acme   analytics  "])
def test_employer_matching_ignores_case_punctuation_and_legal_suffixes(spelling) -> None:
    recon = _reconcile(make_profile(lambda p: setattr(p.employment[0], "employer", spelling)))
    assert len(recon.roles) == 3
    merged = [r for r in recon.roles if r.basis == "graph+source"]
    assert len(merged) == 1 and merged[0].header.employer == "Acme Analytics"  # the graph's spelling wins
    assert norm_org("Acme Analytics GmbH") == norm_org("ACME analytics")


def test_different_employers_are_never_merged_even_with_identical_titles_and_dates() -> None:
    recon = _reconcile(make_profile(lambda p: setattr(p.employment[0], "employer", "Acme Robotics")))
    assert len(recon.roles) == 4  # Acme Robotics (source only) + OrderSync at Acme Analytics (graph only) + 2 others
    assert {r.basis for r in recon.roles if r.header.employer in ("Acme Robotics", "Acme Analytics")} == {"source_only", "graph_only"}
    assert recon.conflicts == []


def test_the_same_employer_at_a_different_time_is_a_different_position() -> None:
    profile = make_profile(lambda p: p.employment[0].__setattr__("startText", "Jan 2019"))
    _edit_entry(profile, 0, startText="Jan 2019", endText="Dec 2019")
    recon = _reconcile(profile)
    acme = _by_employer(recon, "Acme Analytics")
    assert sorted(r.basis for r in acme) == ["graph_only", "source_only"]  # two stints, two entries, no conflict
    assert recon.conflicts == []


def test_several_graph_stories_of_one_role_merge_into_one_entry() -> None:
    ctx = build_cv_context()
    second = copy.deepcopy(ctx.evidenceStories[0])
    second.label = "OrderSync Dashboards"
    second.project = "OrderSync Dashboards"
    ctx.evidenceStories.append(second)
    recon = _reconcile(ctx=ctx)
    acme = _by_employer(recon, "Acme Analytics")
    assert len(acme) == 1 and set(acme[0].graph_story_ids) == {"story:OrderSync", "story:OrderSync Dashboards"}


def test_every_source_role_appears_exactly_once() -> None:
    profile = make_profile()
    recon = _reconcile(profile)
    ids = [r.source_entry_id for r in recon.roles if r.source_entry_id]
    assert sorted(ids) == sorted(e.id for e in profile.employment)
    assert len({r.role_id for r in recon.roles}) == len(recon.roles)


def test_a_title_mismatch_is_only_merged_when_it_is_unambiguously_the_same_position() -> None:
    ctx = build_cv_context()
    other = copy.deepcopy(ctx.evidenceStories[0])
    other.label, other.project, other.roleTitle = "PipelineWork", "PipelineWork", "Data Engineer"
    ctx.evidenceStories.append(other)  # two graph positions at Acme in the same period
    profile = make_profile(lambda p: setattr(p.employment[0], "title", "Software Engineer"))
    recon = _reconcile(profile, ctx)
    acme = _by_employer(recon, "Acme Analytics")
    assert sorted(r.basis for r in acme) == ["graph_only", "graph_only", "source_only"]  # ambiguous: nothing guessed
    assert recon.conflicts == []


# ---- conflicts: never silently chosen ---------------------------------------------------------------------------------


def _title_conflict_recon(source_title="Software Engineer", resolutions=None):
    profile = make_profile(lambda p: setattr(p.employment[0], "title", source_title))
    if resolutions:
        profile.conflicts = resolutions
    return profile, _reconcile(profile)


def test_a_disputed_title_is_recorded_and_left_out_of_the_cv_until_resolved() -> None:
    profile, recon = _title_conflict_recon()
    (conflict,) = recon.conflicts
    assert (conflict.origin, conflict.kind, conflict.field, conflict.resolution) == ("graph", "title", "title", None)
    assert conflict.sourceValue == "Software Engineer" and conflict.graphValue == "Backend Engineer"
    assert "Software Engineer" in conflict.description and "Backend Engineer" in conflict.description
    role = _by_employer(recon, "Acme Analytics")[0]
    assert role.header.title is None and role.header.omitted == ("title",)  # neither value is used
    assert role.basis == "graph+source" and role.has_graph_evidence  # the role itself, and its evidence, remain


def test_the_user_can_resolve_a_conflict_explicitly_either_way() -> None:
    _, first = _title_conflict_recon()
    conflict = first.conflicts[0]
    for choice, expected in (("use_source", "Software Engineer"), ("use_graph", "Backend Engineer")):
        resolved = conflict.model_copy(update={"resolution": choice})
        _, recon = _title_conflict_recon(resolutions=[resolved])
        role = _by_employer(recon, "Acme Analytics")[0]
        assert role.header.title == expected and role.header.omitted == ()
        assert recon.conflicts[0].resolution == choice and recon.conflicts[0].id == conflict.id


def test_a_resolution_stops_applying_the_moment_either_value_changes() -> None:
    _, first = _title_conflict_recon()
    resolved = first.conflicts[0].model_copy(update={"resolution": "use_source"})
    _, changed_source = _title_conflict_recon(source_title="Systems Engineer", resolutions=[resolved])
    role = _by_employer(changed_source, "Acme Analytics")[0]
    assert changed_source.conflicts[0].id != resolved.id and changed_source.conflicts[0].resolution is None
    assert role.header.title is None  # a stale decision is never applied to different values

    ctx = build_cv_context()
    ctx.evidenceStories[0].roleTitle = "Platform Engineer"
    profile = make_profile(lambda p: setattr(p.employment[0], "title", "Software Engineer"))
    profile.conflicts = [resolved]
    changed_graph = _reconcile(profile, ctx)
    assert changed_graph.conflicts[0].id != resolved.id and changed_graph.conflicts[0].resolution is None


def test_conflict_ids_are_deterministic_across_runs() -> None:
    assert _title_conflict_recon()[1].conflicts[0].id == _title_conflict_recon()[1].conflicts[0].id


def test_equivalent_titles_are_not_conflicts() -> None:
    for title in ("Backend Engineer", "backend engineer", "Senior Backend Engineer", "Engineer, Backend"):
        assert _title_conflict_recon(title)[1].conflicts == [], title


def test_document_conflicts_recorded_in_the_profile_are_not_touched_by_reconciliation() -> None:
    profile = make_profile(lambda p: (setattr(p.employment[1], "startText", "Sep 2021"), setattr(p.employment[1], "endText", "Mar 2018")))
    assert [c.origin for c in profile.conflicts] == ["document"] and profile.conflicts[0].kind == "date_order"
    assert _reconcile(profile).conflicts == []  # reconcile() reports graph-vs-source disagreements only


def _ctx_story(**updates):
    ctx = build_cv_context()
    for key, value in updates.items():
        setattr(ctx.evidenceStories[0], key, value)
    return ctx


def test_project_dates_narrower_than_the_source_role_are_not_a_conflict() -> None:
    profile = make_profile()
    _edit_entry(profile, 0, startText="Mar 2025", endText="Oct 2025")  # the role is wider than the graph project
    recon = _reconcile(profile)
    role = _by_employer(recon, "Acme Analytics")[0]
    assert recon.conflicts == [] and role.header.period == "Mar 2025 – Oct 2025"  # the source is authoritative for the role's span


def test_graph_dates_reaching_outside_the_source_role_are_a_conflict_and_the_dates_are_left_out() -> None:
    profile = make_profile()
    _edit_entry(profile, 0, startText="Apr 2025", endText="Aug 2025")  # the graph says the work ran to Sep 2025
    recon = _reconcile(profile)
    role = _by_employer(recon, "Acme Analytics")[0]
    (conflict,) = recon.conflicts
    assert conflict.kind == "dates" and "Apr 2025 – Aug 2025" in conflict.description and "Apr 2025 – Sep 2025" in conflict.description
    assert role.header.omitted == ("dates",) and role.header.period is None and not role.header.span.known


def test_a_graph_role_node_is_exact_so_any_date_difference_is_a_conflict() -> None:
    ctx = _ctx_story(sourceType="Role")
    profile = make_profile()
    _edit_entry(profile, 0, endText="Oct 2025")
    assert [c.kind for c in _reconcile(profile, ctx).conflicts] == ["dates"]
    assert _reconcile(make_profile(), ctx).conflicts == []


def test_vague_dates_never_contradict_more_precise_ones() -> None:
    ctx = _ctx_story(sourceType="Role")
    profile = make_profile()
    _edit_entry(profile, 0, startText="2025", endText="2025")  # a bare year fits any month of 2025
    assert _reconcile(profile, ctx).conflicts == []


def test_missing_values_are_filled_from_the_other_authority_without_conflict() -> None:
    profile = make_profile()
    _edit_entry(profile, 0, startText=None, endText=None, location="Vienna")
    ctx = _ctx_story(location=None, workMode=None)
    recon = _reconcile(profile, ctx)
    role = _by_employer(recon, "Acme Analytics")[0]
    assert recon.conflicts == []
    assert role.header.period == "Apr 2025 – Sep 2025"  # dates the source lacks come from the graph
    assert role.header.location == "Vienna"  # a location the graph lacks comes from the source


def test_a_disputed_location_is_a_conflict_too() -> None:
    profile = make_profile()
    _edit_entry(profile, 0, location="Vienna")
    ctx = _ctx_story(location="Berlin")
    recon = _reconcile(profile, ctx)
    assert [c.kind for c in recon.conflicts] == ["location"] and _by_employer(recon, "Acme Analytics")[0].header.location is None


# ---- education ---------------------------------------------------------------------------------------------------------


def test_education_present_in_both_is_merged_and_source_only_education_is_kept() -> None:
    recon = _reconcile()
    by_name = {e.institution: e for e in recon.education}
    assert set(by_name) == {"Riverbank University", "Handelsakademie Graz"}
    merged = by_name["Riverbank University"]
    assert merged.basis == "graph+source" and merged.program == "BSc Computer Science"
    assert merged.graph_story_ids[0] == "story:Riverbank University" and set(merged.graph_story_ids[1:]) == {"story:Peer Tutor", "story:Shell Project"}
    assert by_name["Handelsakademie Graz"].basis == "source_only" and format_span(by_name["Handelsakademie Graz"].span) == "2010 – 2015"


def test_a_disputed_qualification_is_a_conflict_and_is_left_out() -> None:
    profile = make_profile()
    profile.education[0].qualification = "MSc Data Science"
    recon = _reconcile(profile)
    (conflict,) = recon.conflicts
    assert (conflict.kind, conflict.field) == ("education", "qualification")
    merged = next(e for e in recon.education if e.institution == "Riverbank University")
    assert merged.program is None and merged.omitted == ("qualification",)


def test_graph_only_education_is_kept() -> None:
    recon = _reconcile(make_profile(lambda p: p.education.pop(0)))
    kept = next(e for e in recon.education if e.institution == "Riverbank University")
    assert kept.basis == "graph_only" and kept.program == "BSc Computer Science"


# ---- projects ------------------------------------------------------------------------------------------------------------


def test_projects_merge_by_name_and_keep_graph_only_and_source_only_ones() -> None:
    profile = make_profile()
    profile.projects.append(profile.projects[0].model_copy(update={"id": "prj_extra01", "name": "Bike Route Planner"}))
    recon = _reconcile(profile)
    by_name = {p.name: p for p in recon.projects}
    assert by_name["TrailMap"].basis == "graph+source" and by_name["TrailMap"].graph_story_id == "story:TrailMap"
    assert by_name["Bike Route Planner"].basis == "source_only"
    assert by_name["Village Chronicle"].basis == "graph_only"
    assert len(recon.projects) == 3


# ---- ordering and gaps ------------------------------------------------------------------------------------------------------


def test_sorting_is_deterministic_newest_first_current_roles_first_undated_last() -> None:
    profile = make_profile()
    _edit_entry(profile, 2, endText="Present")  # the retail job is (hypothetically) ongoing
    _edit_entry(profile, 1, startText=None, endText=None)  # the warehouse job has no dates
    first = _reconcile(profile)
    order = [r.header.employer for r in first.roles]
    assert order == ["Kaffeehaus Ringstrasse", "Acme Analytics", "Nordwind Logistics"]  # current, dated, undated
    assert first.undated_roles == [first.roles[-1].role_id]

    shuffled = copy.deepcopy(profile)
    shuffled.employment.reverse()
    again = _reconcile(shuffled)
    assert [r.role_id for r in again.roles] == [r.role_id for r in first.roles]  # input order does not matter


def test_ties_break_by_document_order() -> None:
    profile = make_profile()
    for index in (1, 2):
        _edit_entry(profile, index, startText="Mar 2018", endText="Aug 2021")
    recon = _reconcile(profile)
    tied = [r.header.employer for r in recon.roles if r.header.employer != "Acme Analytics"]
    assert tied == ["Nordwind Logistics", "Kaffeehaus Ringstrasse"]


def test_a_genuine_gap_is_reported_with_its_months_and_nothing_is_added_to_cover_it() -> None:
    recon = _reconcile()
    (gap,) = recon.chronology_gaps
    assert gap.months == 9 and gap.message == (
        "No employment or education is listed between Jul 2024 and Mar 2025 (9 months). Nothing was added to cover it."
    )
    assert len(recon.roles) == 3 and all(r.basis != "graph_only" for r in recon.roles)  # no role was invented


def test_education_periods_count_as_covered_time() -> None:
    assert all(g.start_index > 2024 * 12 for g in _reconcile().chronology_gaps)  # 2021-2024 study covers the 2021 handover


@pytest.mark.parametrize(
    "spans,expected",
    [
        ([Span("2019", "2019"), Span("2021", "2021")], 12),  # a full year is missing however the vague dates are read
        ([Span("2019", "2020"), Span("2021", "2021")], None),  # 2020 could end in December and 2021 start in January
        ([Span("2019-01", "2019-12"), Span("2020-03", "2020-12")], 2),  # below the threshold: not reported
        ([Span("2019-01", "2019-06"), Span("2020-01", "2020-06")], 6),
        ([Span("2019-01", "2021-12"), Span("2020-01", "2020-06"), Span("2022-01", "2022-06")], None),  # parallel roles overlap
        ([Span("2020-01", None, True), Span("2015-01", "2019-06")], 6),  # an ongoing role reaches the present
        ([Span(None, None)], None),  # undated entries cannot create or hide a gap
    ],
)
def test_gap_detection_uses_the_most_generous_reading_of_vague_dates(spans, expected) -> None:
    gaps = find_chronology_gaps(spans, TODAY_INDEX)
    months = [g.months for g in gaps]
    if expected is None or expected < 6:
        assert months == []
    else:
        assert months == [expected]


def test_span_overlap_treats_unknown_dates_as_not_contradicting() -> None:
    assert spans_overlap(Span("2020-01", "2020-12"), Span("2020-06", "2021-06")) is True
    assert spans_overlap(Span("2020-01", "2020-12"), Span("2021-01", "2021-12")) is False
    assert spans_overlap(Span(None, None), Span("2021-01", "2021-12")) is None


# ---- contact and purity ---------------------------------------------------------------------------------------------------


def test_contact_details_pass_through_unchanged() -> None:
    profile = make_profile()
    recon = _reconcile(profile)
    assert recon.contact == profile.contact and recon.contact is not profile.contact


def test_reconciliation_changes_neither_the_snapshot_nor_the_graph_context() -> None:
    profile, ctx = make_profile(), build_cv_context()
    before = (profile.model_dump(), ctx.model_dump())
    _reconcile(profile, ctx)
    assert (profile.model_dump(), ctx.model_dump()) == before


def test_the_reconciliation_summary_counts_what_happened() -> None:
    summary = _title_conflict_recon()[1].summary()
    assert summary == {
        "roles": 3, "rolesWithGraphEvidence": 1, "sourceOnlyRoles": 2, "graphOnlyRoles": 0, "education": 2,
        "conflicts": 1, "unresolvedConflicts": 1, "chronologyGaps": 1,
    }


def test_conflict_model_only_accepts_known_kinds_and_resolutions() -> None:
    with pytest.raises(Exception):
        Conflict(id="x", origin="graph", kind="salary", field="f", description="d")
    with pytest.raises(Exception):
        Conflict(id="x", origin="graph", kind="title", field="f", description="d", resolution="pick_random")
