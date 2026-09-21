"""Source-aware CV generation: what is deterministic, what the model may write, the provenance namespaces,
the claim rules, the semantic verifier and the bounded automatic repair loop. The model is scripted; the
deterministic layers under test are real."""
import copy
import json

import pytest

from cv_generation import MAX_REPAIR_ATTEMPTS, build_complete_cv_document
from llm.complete_writers import DeterministicCompleteWriter, VerbatimSupportVerifier
from llm.errors import ProvenanceValidationError
from source_cv.schema import SourceFact
from source_helpers import ScriptedClient, groq_complete_stack, make_ci, make_profile, valid_complete_draft


def generate(draft=None, *, ci=None, client=None, max_repairs=MAX_REPAIR_ATTEMPTS):
    ci = ci or make_ci()
    draft = draft if draft is not None else valid_complete_draft(ci.profile)
    client = client or ScriptedClient(write_complete_cv=draft)
    writer, verifier = groq_complete_stack(client)
    return build_complete_cv_document(writer, verifier, ci, max_repairs=max_repairs), client, ci


def codes(exc: ProvenanceValidationError) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in exc.violations:
        out[v.code] = out.get(v.code, 0) + 1
    return out


def reject(draft=None, *, ci=None, client=None, max_repairs=0) -> ProvenanceValidationError:
    with pytest.raises(ProvenanceValidationError) as info:
        generate(draft, ci=ci, client=client, max_repairs=max_repairs)
    return info.value


def acme_bullets(draft: dict) -> list[dict]:
    return draft["roles"][0]["bullets"]


# ---- what is deterministic, what is written ---------------------------------------------------------------------------


def test_a_complete_cv_combines_source_details_with_graph_backed_wording() -> None:
    document, client, ci = generate()
    md = document.markdown
    assert md.startswith("# Mira Tannenbaum\n")
    assert "Graz, Austria | <mira.tannenbaum@example.org> | +43 660 555 0142" in md
    assert "LinkedIn: <https://linkedin.com/in/mira-tannenbaum> | GitHub: <https://github.com/mira-tannenbaum> | Portfolio: <https://miratannenbaum.example.org>" in md
    assert "### Backend Engineer — Acme Analytics\n*Remote | Apr 2025 – Sep 2025*" in md
    assert "cutting manual data entry time by 60%" in md and "Wrote onboarding documentation for new engineers." in md
    assert "## Additional Experience" in md and "**Warehouse Supervisor**, Nordwind Logistics | Vienna | Mar 2018 – Aug 2021" in md
    assert "**Retail Assistant**, Kaffeehaus Ringstrasse | Graz | Jan 2015 – Dec 2017" in md
    assert "### Riverbank University\n*BSc Computer Science | Sep 2021 – Jun 2024*" in md
    assert "### Handelsakademie Graz\n*Matura | 2010 – 2015*" in md
    assert "## Certifications\n- Certified Scrum Master — Scrum Alliance, 2023" in md
    assert "## Languages\nGerman (Native), English (C1)" in md
    assert document.repair_attempts == 0 and document.verification == "semantic"
    assert client.operations() == ["write_complete_cv", "verify_claims"]


def test_the_model_has_no_field_for_contact_details_employers_titles_or_dates() -> None:
    from llm.source_schemas import CompleteCvDraft

    fields = json.dumps(CompleteCvDraft.model_json_schema()).lower()
    for absent in ("email", "phone", "telephone", "employer", "organization", "startdate", "enddate", "linkedin", "fullname", "institution"):
        assert absent not in fields, absent
    document, _, ci = generate()
    cv = document.structured_cv
    acme = cv.experience[0]
    assert (acme.title, acme.organization, acme.location, acme.dateRange) == ("Backend Engineer", "Acme Analytics", "Remote", "Apr 2025 – Sep 2025")
    assert cv.name == "Mira Tannenbaum" and cv.contact.email == "mira.tannenbaum@example.org"


def test_every_role_appears_exactly_once_even_when_the_model_omits_or_repeats_them() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    only_acme = copy.deepcopy(draft)
    only_acme["roles"] = only_acme["roles"][:1]  # the model "forgot" two roles
    document, _, _ = generate(only_acme, ci=ci)
    entries = [*document.structured_cv.experience, *document.structured_cv.additionalExperience]
    assert sorted(e.entryId for e in entries) == sorted(r.role_id for r in ci.recon.roles)
    assert len({e.entryId for e in entries}) == len(entries) == 3
    omitted = {e.organization for e in document.structured_cv.additionalExperience}
    assert omitted == {"Nordwind Logistics", "Kaffeehaus Ringstrasse"}  # kept, compactly, with no bullets
    assert all(e.bullets == [] for e in document.structured_cv.additionalExperience)


def test_repeated_and_unknown_roles_are_flagged_and_dropped_without_a_model_call() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    messy = copy.deepcopy(draft)
    messy["roles"].append(copy.deepcopy(draft["roles"][0]))  # Acme twice
    messy["roles"].append({"roleId": "role:emp_invented", "emphasis": "featured", "bullets": []})
    document, client, _ = generate(messy, ci=ci)
    assert document.repair_attempts == 1  # one deterministic clean-up round...
    assert "repair_cv" not in client.operations()  # ...that needed no model
    assert len(document.structured_cv.experience) == 1


def test_irrelevant_roles_are_compact_never_deleted_and_never_given_invented_relevance() -> None:
    document, _, _ = generate()
    cv = document.structured_cv
    assert [e.title for e in cv.experience] == ["Backend Engineer"]
    assert [e.title for e in cv.additionalExperience] == ["Warehouse Supervisor", "Retail Assistant"]  # newest first
    assert all(not e.bullets for e in cv.additionalExperience)
    assert "Warehouse Supervisor" in document.markdown  # present in the finished CV, one line each


def test_a_compact_role_may_carry_exactly_one_short_supported_bullet() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    kaffee = ci.profile.employment[2]
    draft["roles"][2]["bullets"] = [{"text": "Managed opening and closing procedures.", "evidenceIds": [f"source:{kaffee.facts[0].id}"]}]
    document, _, _ = generate(draft, ci=ci)
    assert [b.text for b in document.structured_cv.additionalExperience[1].bullets] == ["Managed opening and closing procedures."]
    assert "  - Managed opening and closing procedures." in document.markdown

    nordwind = ci.profile.employment[1]
    draft["roles"][1]["bullets"] = [
        {"text": "Introduced a barcode check that reduced picking errors.", "evidenceIds": [f"source:{nordwind.facts[1].id}"]},
        {"text": "Supervised warehouse operators.", "evidenceIds": [f"source:{nordwind.facts[0].id}"]},
    ]
    assert codes(reject(draft, ci=ci)) == {"compact_too_long": 1}


def test_a_relevant_source_only_role_can_be_featured_with_source_backed_bullets() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    nordwind = ci.profile.employment[1]
    draft["roles"][1] = {
        "roleId": f"role:{nordwind.id}", "emphasis": "featured",
        "bullets": [{"text": "Supervised a team of 12 warehouse operators across two shifts.", "evidenceIds": [f"source:{nordwind.facts[0].id}"]}],
    }
    document, _, _ = generate(draft, ci=ci)
    cv = document.structured_cv
    assert [e.title for e in cv.experience] == ["Backend Engineer", "Warehouse Supervisor"]  # featured, newest first
    assert [e.title for e in cv.additionalExperience] == ["Retail Assistant"]
    trace = next(t for t in document.traces if t.entry.startswith("Warehouse"))
    assert [e.origin for e in trace.evidence] == ["source"]


def test_graph_backed_roles_are_featured_whatever_emphasis_the_model_chose() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    draft["roles"][0]["emphasis"] = "compact"
    document, _, _ = generate(draft, ci=ci)
    assert [e.title for e in document.structured_cv.experience] == ["Backend Engineer"]


def test_a_featured_role_with_nothing_to_show_is_demoted_not_dropped() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    draft["roles"][1]["emphasis"] = "featured"  # asked to feature, wrote no bullets
    document, _, _ = generate(draft, ci=ci)
    assert "Warehouse Supervisor" in [e.title for e in document.structured_cv.additionalExperience]


def test_source_text_is_markdown_escaped_but_graph_text_is_rendered_as_before() -> None:
    profile = make_profile(lambda p: setattr(p.employment[1], "employer", "Nordwind_Logistics*"))
    document, _, _ = generate(ci=make_ci(profile))
    assert "**Warehouse Supervisor**, Nordwind\\_Logistics\\*" in document.markdown


def test_education_is_complete_and_uses_graph_evidence_only_through_its_own_bullets() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    riverbank = next(e for e in ci.recon.education if e.institution == "Riverbank University")
    draft["education"] = [{"entryId": riverbank.entry_id, "bullets": [
        {"text": "Tutored 30 students in debugging and C programming.", "evidenceIds": ["graph:achievement:Peer Tutor#1"]}]}]
    document, _, _ = generate(draft, ci=ci)
    assert [e.institution for e in document.structured_cv.education] == ["Riverbank University", "Handelsakademie Graz"]
    assert [b.text for b in document.structured_cv.education[0].bullets] == ["Tutored 30 students in debugging and C programming."]
    assert document.structured_cv.education[1].bullets == []


def test_chronological_sorting_in_the_finished_cv_is_deterministic() -> None:
    first, _, _ = generate()
    shuffled = make_profile(lambda p: p.employment.reverse())
    second, _, _ = generate(ci=make_ci(shuffled), draft=valid_complete_draft(shuffled))
    assert first.markdown == second.markdown


# ---- provenance namespaces ---------------------------------------------------------------------------------------------


def _with_bullet(bullet: dict, ci=None):
    ci = ci or make_ci()
    draft = valid_complete_draft(ci.profile)
    acme_bullets(draft)[0] = bullet
    return draft, ci


def test_evidence_ids_must_carry_their_namespace() -> None:
    draft, ci = _with_bullet({"text": "Built a FastAPI service.", "evidenceIds": ["achievement:OrderSync#1"]})
    assert codes(reject(draft, ci=ci)) == {"missing_namespace": 1}  # the legacy un-prefixed id is not a citation here


def test_the_graph_and_source_namespaces_cannot_be_swapped() -> None:
    ci = make_ci()
    fact = ci.profile.employment[0].facts[0].id
    for bad in (f"graph:{fact}", "source:achievement:OrderSync#1", "graph:", "source:", "graph:story:Nowhere"):
        draft, _ = _with_bullet({"text": "Built a FastAPI service.", "evidenceIds": [bad]}, ci)
        assert codes(reject(draft, ci=ci)) == {"unknown_evidence_id": 1}, bad


def test_unknown_source_ids_are_rejected_and_never_guessed() -> None:
    draft, ci = _with_bullet({"text": "Wrote onboarding documentation for new engineers.", "evidenceIds": ["source:f_00000000"]})
    assert codes(reject(draft, ci=ci)) == {"unknown_evidence_id": 1}


def test_source_ids_resolve_against_the_analysis_snapshot_only() -> None:
    ci = make_ci()  # snapshot at revision 1
    newer = make_profile()
    newer.employment[0].facts.append(SourceFact(id="f_newerfact", text="Added after the analysis ran."))
    draft, _ = _with_bullet({"text": "Added after the analysis ran.", "evidenceIds": ["source:f_newerfact"]}, ci)
    assert codes(reject(draft, ci=ci)) == {"unknown_evidence_id": 1}  # a later edit is invisible to this analysis


def test_a_bullet_may_only_cite_evidence_belonging_to_its_own_entry() -> None:
    ci = make_ci()
    nordwind_fact = ci.profile.employment[1].facts[1].id
    for foreign in (f"source:{nordwind_fact}", "graph:achievement:TrailMap#1"):
        draft, _ = _with_bullet({"text": "Wrote things.", "evidenceIds": [foreign]}, ci)
        assert codes(reject(draft, ci=ci)) == {"foreign_evidence": 1}, foreign


def test_a_bullet_without_evidence_or_text_is_rejected() -> None:
    draft, ci = _with_bullet({"text": "Built a FastAPI service.", "evidenceIds": []})
    assert codes(reject(draft, ci=ci)) == {"empty_evidence": 1}
    draft, ci = _with_bullet({"text": "  ", "evidenceIds": ["graph:achievement:OrderSync#1"]})
    assert "empty_text" in codes(reject(draft, ci=ci))


def test_provenance_traces_record_the_origin_of_every_cited_item() -> None:
    document, _, _ = generate()
    origins = {t.text: [e.origin for e in t.evidence] for t in document.traces}
    assert origins["Wrote onboarding documentation for new engineers."] == ["source"]
    fastapi = next(text for text in origins if text.startswith("Built a FastAPI service that uses an LLM"))
    assert origins[fastapi] == ["graph"]
    assert document.traces[0].section == "profile"


# ---- claim rules ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,evidence,expected",
    [
        ("Built a FastAPI service that extracts order data from PDFs, cutting entry time by 90%.", "graph:achievement:OrderSync#1", {"unsupported_number": 1}),
        ("Built a FastAPI service and deployed it on AWS.", "graph:achievement:OrderSync#1", {"gap_claimed": 1, "unsupported_term": 0}),
        ("Monitored production with CloudWatch dashboards.", "graph:transferable:observability#1", {"gap_claimed": 1}),
        ("Built a FastAPI service, described in evidence id achievement:OrderSync#1.", "graph:achievement:OrderSync#1", {"internal_language": 2}),  # an id AND the words "evidence id"
        ("Built a FastAPI service; contact me at jane.doe@example.com.", "graph:achievement:OrderSync#1", {"contact_in_prose": 1}),
        ("Led a Senior team building a FastAPI service.", "graph:achievement:OrderSync#1", {"unsupported_title": 1}),
        ("Built a FastAPI service during an MSc programme.", "graph:achievement:OrderSync#1", {"unsupported_qualification": 1}),
    ],
)
def test_unsupported_claims_are_rejected_with_their_own_code(text, evidence, expected) -> None:
    draft, ci = _with_bullet({"text": text, "evidenceIds": [evidence]})
    found = codes(reject(draft, ci=ci))
    for code, count in expected.items():
        if count:
            assert found.get(code) == count, found
        else:
            assert code not in found, found


def test_literal_gaps_are_never_claimed_even_when_the_source_cv_mentions_them() -> None:
    profile = make_profile()
    profile.employment[1].facts.append(SourceFact(id="f_awsfact001", text="Deployed the ordering tool on AWS."))
    ci = make_ci(profile)
    draft = valid_complete_draft(profile)
    draft["roles"][1] = {"roleId": f"role:{profile.employment[1].id}", "emphasis": "featured",
                         "bullets": [{"text": "Deployed the ordering tool on AWS.", "evidenceIds": ["source:f_awsfact001"]}]}
    assert "gap_claimed" in codes(reject(draft, ci=ci))  # the graph decides what is a gap


def test_a_term_the_cited_evidence_does_not_contain_is_unsupported() -> None:
    ci = make_ci()
    acme_fact = ci.profile.employment[0].facts[1].id
    draft, _ = _with_bullet({"text": "Wrote Python onboarding documentation for new engineers.", "evidenceIds": [f"source:{acme_fact}"]}, ci)
    assert codes(reject(draft, ci=ci)) == {"unsupported_term": 1}


def test_numbers_must_come_from_the_cited_evidence() -> None:
    ci = make_ci()
    nordwind = ci.profile.employment[1]
    draft = valid_complete_draft(ci.profile)
    draft["roles"][1] = {"roleId": f"role:{nordwind.id}", "emphasis": "featured",
                         "bullets": [{"text": "Supervised a team of 15 warehouse operators.", "evidenceIds": [f"source:{nordwind.facts[0].id}"]}]}
    assert codes(reject(draft, ci=ci)) == {"unsupported_number": 1}


def test_the_graph_stays_authoritative_for_numbers_on_a_role_it_has_evidence_for() -> None:
    profile = make_profile()
    profile.employment[0].facts.append(SourceFact(id="f_speedup0001", text="Cut onboarding time from 10 days to 3 days."))
    ci = make_ci(profile)
    draft = valid_complete_draft(profile)
    acme_bullets(draft).append({"text": "Cut onboarding time from 10 days to 3 days.", "evidenceIds": ["source:f_speedup0001"]})
    assert codes(reject(draft, ci=ci)) == {"quantified_claim_needs_graph": 2}  # both numbers: the graph has none for this role

    # the same statement is fine where the graph has no evidence (the source is the only authority there)
    profile2 = make_profile()
    profile2.employment[1].facts.append(SourceFact(id="f_speedup0002", text="Cut onboarding time from 10 days to 3 days."))
    draft2 = valid_complete_draft(profile2)
    draft2["roles"][1] = {"roleId": f"role:{profile2.employment[1].id}", "emphasis": "featured",
                          "bullets": [{"text": "Cut onboarding time from 10 days to 3 days.", "evidenceIds": ["source:f_speedup0002"]}]}
    generate(draft2, ci=make_ci(profile2))


def test_the_profile_must_cite_valid_evidence_and_headline_and_profile_are_checked() -> None:
    ci = make_ci()
    base = valid_complete_draft(ci.profile)
    for mutate, expected in (
        (lambda d: d.update(profileEvidenceIds=[]), {"empty_evidence": 1}),
        (lambda d: d.update(profileEvidenceIds=["graph:achievement:Nowhere#1"]), {"unknown_evidence_id": 1}),
        (lambda d: d.update(profile="Backend engineer with 12 years of experience with Python."), {"unsupported_number": 1}),
        (lambda d: d.update(profile="Engineer who also knows AWS and Python."), {"gap_claimed": 1}),
        (lambda d: d.update(headline="Senior Backend Engineer"), {"unsupported_title": 1}),
        (lambda d: d.update(profile=f"Reach me at {ci.profile.contact.email}."), {"contact_in_prose": 1}),
    ):
        draft = copy.deepcopy(base)
        mutate(draft)
        found = codes(reject(draft, ci=ci))
        for code, count in expected.items():
            assert found.get(code) == count, (found, code)


def test_skills_are_limited_to_what_the_career_graph_supports() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    draft["skills"]["tools"] = ["Unix", "Kubernetes"]  # not in the graph, even if a CV might mention it
    error = reject(draft, ci=ci)
    assert codes(error) == {"unsupported_skill": 1} and error.violations[0].target == "skill:tools:Kubernetes"


# ---- semantic verification ------------------------------------------------------------------------------------------------


def _verdicts(verdict, ref_filter=None):
    def respond(kwargs):
        claims = json.loads(kwargs["user_content"])["claims"]
        return {"verdicts": [{"ref": c["ref"], "verdict": verdict} for c in claims if ref_filter is None or ref_filter(c)]}
    return respond


@pytest.mark.parametrize("verdict", ["adds_facts", "overstates", "contradicts", "unrelated"])
def test_a_source_backed_claim_the_verifier_does_not_accept_is_rejected(verdict) -> None:
    client = ScriptedClient(write_complete_cv=valid_complete_draft(), verify_claims=_verdicts(verdict))
    error = reject(client=client, max_repairs=0)
    assert codes(error) == {"unsupported_source_claim": 1}
    assert error.violations[0].target.startswith("bullet:role:emp_")


def test_a_missing_verdict_fails_closed() -> None:
    client = ScriptedClient(write_complete_cv=valid_complete_draft(), verify_claims=_verdicts("supported", lambda c: False))
    assert codes(reject(client=client, max_repairs=0)) == {"unsupported_source_claim": 1}


def test_only_claims_citing_the_source_cv_are_sent_to_the_verifier_and_no_contact_details_are() -> None:
    document, client, ci = generate()
    verify_calls = [c for c in client.calls if c["operation"] == "verify_claims"]
    assert len(verify_calls) == 1
    payload = json.loads(verify_calls[0]["user_content"])
    assert [c["text"] for c in payload["claims"]] == ["Wrote onboarding documentation for new engineers."]  # graph-only bullets are not
    text = verify_calls[0]["user_content"]
    for private in (ci.profile.contact.email, ci.profile.contact.telephone, "Mira", "Tannenbaum", "linkedin.com"):
        assert private not in text


def test_a_cv_with_no_source_backed_claims_needs_no_verification_call() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    acme_bullets(draft).pop()  # drop the one source-backed bullet
    document, client, _ = generate(draft, ci=ci)
    assert client.operations() == ["write_complete_cv"] and document.verification == "not_needed"


# ---- the bounded automatic repair loop ------------------------------------------------------------------------------------------


def _fix(target, text, ids=(), action="rewrite"):
    return {"target": target, "action": action, "text": text, "evidenceIds": list(ids)}


def _bad_number_draft(ci):
    draft = valid_complete_draft(ci.profile)
    acme_bullets(draft)[0]["text"] = "Built a FastAPI service that extracts order data from PDFs, cutting entry time by 90%."
    return draft


def test_a_failed_first_draft_is_repaired_automatically_and_only_where_it_failed() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#1"
    fixed = "Built a FastAPI service that extracts order data from PDFs, cutting entry time by 60%."
    client = ScriptedClient(
        write_complete_cv=_bad_number_draft(ci),
        repair_cv={"fixes": [
            _fix(target, fixed, ["graph:achievement:OrderSync#1"]),
            _fix(f"bullet:{ci.recon.roles[0].role_id}#2", "Rewrote a bullet that was never flagged."),  # not invited: ignored
        ]},
    )
    document, _, _ = generate(ci=ci, client=client)
    bullets = [b.text for b in document.structured_cv.experience[0].bullets]
    assert bullets[0] == fixed and bullets[1] == "Designed the PostgreSQL schema and migrations for multi-tenant order data."
    assert document.repair_attempts == 1
    assert client.operations() == ["write_complete_cv", "repair_cv", "verify_claims"]

    request = json.loads(next(c for c in client.calls if c["operation"] == "repair_cv")["user_content"])
    assert [i["target"] for i in request["invalid"]] == [target]  # only the invalid claim goes back
    assert request["invalid"][0]["codes"] == ["unsupported_number"]
    assert {e["evidenceId"] for e in request["invalid"][0]["allowedEvidence"]} >= {"graph:achievement:OrderSync#1"}
    assert "Designed the PostgreSQL" not in client.calls[1]["user_content"]  # valid claims are not resent
    assert "Tannenbaum" not in client.calls[1]["user_content"] and "@example.org" not in client.calls[1]["user_content"]


def test_a_claim_can_be_repaired_by_removing_it() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#1"
    client = ScriptedClient(write_complete_cv=_bad_number_draft(ci), repair_cv={"fixes": [_fix(target, "", action="remove")]})
    document, _, _ = generate(ci=ci, client=client)
    assert [b.text[:20] for b in document.structured_cv.experience[0].bullets] == ["Designed the Postgre", "Wrote onboarding doc"]


def test_the_profile_headline_and_skills_can_be_repaired_too() -> None:
    ci = make_ci()
    draft = valid_complete_draft(ci.profile)
    draft["headline"] = "Senior Backend Engineer"
    draft["profile"] = "Backend engineer with 12 years of experience."
    draft["skills"]["tools"] = ["Unix", "Kubernetes"]
    client = ScriptedClient(write_complete_cv=draft, repair_cv={"fixes": [
        _fix("headline", "Backend Engineer"),
        _fix("profile", "Backend engineer building Python APIs backed by PostgreSQL.", ["graph:achievement:OrderSync#1"]),
        _fix("skill:tools:Kubernetes", "", action="remove"),
    ]})
    document, _, _ = generate(ci=ci, client=client)
    cv = document.structured_cv
    assert (cv.headline, cv.skills.tools) == ("Backend Engineer", ["Unix"]) and "12 years" not in cv.profile
    assert cv.profileEvidenceIds == ["graph:achievement:OrderSync#1"]


def test_repair_is_bounded_at_two_attempts_and_then_the_cv_is_rejected_with_a_report() -> None:
    ci = make_ci()
    client = ScriptedClient(write_complete_cv=_bad_number_draft(ci), repair_cv={"fixes": []})  # the model never fixes anything
    with pytest.raises(ProvenanceValidationError) as info:
        generate(ci=ci, client=client)
    error = info.value
    assert MAX_REPAIR_ATTEMPTS == 2 and client.operations() == ["write_complete_cv", "repair_cv", "repair_cv"]
    assert error.repair_attempts == 2 and codes(error) == {"unsupported_number": 1}
    detail = error.to_detail()
    assert detail["violations"] == {"unsupported_number": 1} and detail["repairAttempts"] == 2 and detail["retryable"] is True
    assert "90%" not in json.dumps(detail)  # codes and counts only


def test_the_whole_result_is_revalidated_after_every_repair() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#1"
    # the "repair" fixes the number but introduces a literal gap: the second validation catches it
    client = ScriptedClient(
        write_complete_cv=_bad_number_draft(ci),
        repair_cv={"fixes": [_fix(target, "Built a FastAPI service on AWS, cutting entry time by 60%.", ["graph:achievement:OrderSync#1"])]},
    )
    with pytest.raises(ProvenanceValidationError) as info:
        generate(ci=ci, client=client)
    assert "gap_claimed" in codes(info.value) and info.value.repair_attempts == 2


def test_a_repair_that_stays_within_the_rules_on_the_second_attempt_succeeds() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#1"
    client = ScriptedClient(
        write_complete_cv=_bad_number_draft(ci),
        repair_cv=[{"fixes": []}, {"fixes": [_fix(target, "Built a FastAPI service that extracts order data from PDFs.", ["graph:achievement:OrderSync#1"])]}],
    )
    document, _, _ = generate(ci=ci, client=client)
    assert document.repair_attempts == 2 and client.operations().count("repair_cv") == 2


def test_repair_fixes_for_targets_that_were_not_flagged_are_discarded() -> None:
    ci = make_ci()
    client = ScriptedClient(
        write_complete_cv=_bad_number_draft(ci),
        repair_cv={"fixes": [_fix("headline", "Chief Executive Officer"), _fix("profile", "Ignored profile rewrite.")]},
    )
    with pytest.raises(ProvenanceValidationError):
        generate(ci=ci, client=client)  # nothing valid was fixed; the unrequested rewrites changed nothing
    # ...and had they been applied, the headline would have failed validation instead of the number
    assert client.calls[-1]["operation"] == "repair_cv"


def test_no_repair_happens_when_the_first_draft_is_valid() -> None:
    document, client, _ = generate()
    assert document.repair_attempts == 0 and "repair_cv" not in client.operations()


def test_a_semantic_failure_is_repaired_and_then_reverified() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#3"
    calls = {"verify": 0}

    def verify(kwargs):
        calls["verify"] += 1
        return _verdicts("adds_facts" if calls["verify"] == 1 else "supported")(kwargs)

    client = ScriptedClient(
        write_complete_cv=valid_complete_draft(ci.profile),
        verify_claims=verify,
        repair_cv={"fixes": [_fix(target, "Wrote onboarding documentation.", [f"source:{ci.profile.employment[0].facts[1].id}"])]},
    )
    document, _, _ = generate(ci=ci, client=client)
    assert client.operations() == ["write_complete_cv", "verify_claims", "repair_cv", "verify_claims"]
    assert document.structured_cv.experience[0].bullets[2].text == "Wrote onboarding documentation."


def test_usage_and_attempts_add_up_across_write_repair_and_verification() -> None:
    ci = make_ci()
    target = f"bullet:{ci.recon.roles[0].role_id}#1"
    client = ScriptedClient(write_complete_cv=_bad_number_draft(ci), repair_cv={"fixes": [_fix(target, "", action="remove")]})
    document, _, _ = generate(ci=ci, client=client)
    assert document.attempts == 3 and document.usage.total_tokens == 450 and document.usage.prompt_tokens == 300


# ---- dev fallback ----------------------------------------------------------------------------------------------------------


def test_the_development_writer_builds_a_complete_validated_cv_and_never_asks_for_repair() -> None:
    ci = make_ci()
    document = build_complete_cv_document(DeterministicCompleteWriter(), VerbatimSupportVerifier(), ci)
    assert (document.provider, document.model, document.mode) == ("dev", None, "deterministic_fallback")
    entries = [*document.structured_cv.experience, *document.structured_cv.additionalExperience]
    assert len(entries) == 3 and document.repair_attempts == 0
    assert "cutting manual data entry time by 60%" in document.markdown  # verbatim graph evidence
    assert "Village Chronicle" not in document.markdown  # the same substantial-project rule as the legacy writer
    assert document.markdown.startswith("# Mira Tannenbaum")


def test_the_development_fallback_is_held_to_the_same_validation() -> None:
    class Sloppy(DeterministicCompleteWriter):
        def write(self, ci):
            result = super().write(ci)
            result.draft.roles[0].bullets[0].evidenceIds = ["achievement:OrderSync#1"]  # legacy id, no namespace
            return result

    with pytest.raises(ProvenanceValidationError) as info:
        build_complete_cv_document(Sloppy(), VerbatimSupportVerifier(), make_ci())
    assert codes(info.value) == {"missing_namespace": 1}


# ---- layout warnings ------------------------------------------------------------------------------------------------------------


def test_a_genuine_chronology_gap_is_reported_and_nothing_is_fabricated_to_cover_it() -> None:
    document, _, ci = generate()
    gap = [w for w in document.layout_warnings if w.code == "chronology_gap"]
    assert len(gap) == 1 and "Jul 2024 and Mar 2025" in gap[0].message and "Nothing was added" in gap[0].message
    entries = [*document.structured_cv.experience, *document.structured_cv.additionalExperience]
    assert {e.entryId for e in entries} == {r.role_id for r in ci.recon.roles}  # no role beyond the record


def test_the_complete_cv_over_the_page_budget_warns_and_keeps_every_position() -> None:
    def many_roles(profile):
        template = profile.employment[2]
        for i in range(30):
            profile.employment.append(template.model_copy(deep=True, update={
                "id": f"emp_extra{i:04d}", "employer": f"Employer Number {i}", "title": "Assistant",
                "startText": "Jan 2000", "endText": "Dec 2000",
            }))

    profile = make_profile(many_roles)
    ci = make_ci(profile, page_budget=1)
    document, _, _ = generate(valid_complete_draft(profile), ci=ci)
    over = [w for w in document.layout_warnings if w.code == "over_page_budget"]
    assert len(over) == 1 and "Nothing was removed" in over[0].message and "1-page target" in over[0].message
    assert len(document.structured_cv.additionalExperience) == 32  # every one of the 33 positions survives (1 featured)


def test_unresolved_conflicts_a_missing_name_and_compact_roles_are_all_surfaced() -> None:
    profile = make_profile(lambda p: (setattr(p.employment[0], "title", "Software Engineer"), setattr(p.contact, "fullName", None)))
    document, _, _ = generate(valid_complete_draft(profile), ci=make_ci(profile))
    by_code = {w.code: w for w in document.layout_warnings}
    assert "title" in by_code["unresolved_conflict"].message and "left out of the CV" in by_code["unresolved_conflict"].message
    assert by_code["no_name"].severity == "warning" and by_code["additional_experience"].severity == "info"
    assert document.structured_cv.experience[0].title == "Acme Analytics"  # the disputed title is not asserted
    assert document.structured_cv.name == "Paul Hofer"  # the pre-existing default, and the warning says so


def test_undated_positions_are_reported() -> None:
    profile = make_profile(lambda p: (setattr(p.employment[2], "startText", None), setattr(p.employment[2], "endText", None)))
    document, _, _ = generate(valid_complete_draft(profile), ci=make_ci(profile))
    assert any(w.code == "undated_role" for w in document.layout_warnings)
    assert document.structured_cv.additionalExperience[-1].title == "Retail Assistant"  # listed last


# ---- what the writer is (not) shown -----------------------------------------------------------------------------------------------


def test_the_writer_payload_excludes_contact_details_and_the_candidates_name_but_includes_the_career() -> None:
    from llm.complete_registry import build_complete_payload

    profile = make_profile()
    profile.employment[1].facts.append(SourceFact(id="f_leaky00001", text="Call jane.doe@example.com or +43 660 555 0142 about it."))
    ci = make_ci(profile)
    message = json.dumps(build_complete_payload(ci))
    for private in ("mira.tannenbaum@example.org", "+43 660 555 0142", "Tannenbaum", "linkedin.com", "github.com", "miratannenbaum", "jane.doe@example.com", "660 555"):
        assert private not in message, private
    assert "[redacted]" in message
    for career in ("Acme Analytics", "Nordwind Logistics", "Warehouse Supervisor", "Apr 2025", "graph:achievement:OrderSync#1"):
        assert career in message
    assert set(json.loads(message)) >= {"roles", "projects", "education", "literalGaps", "availableSkills", "candidateSummary"}


def test_every_role_is_offered_to_the_model_with_evidence_ids_in_the_right_namespace() -> None:
    from llm.complete_registry import build_complete_payload

    ci = make_ci()
    payload = build_complete_payload(ci)
    assert [r["roleId"] for r in payload["roles"]] == [r.role_id for r in ci.recon.roles]
    acme = payload["roles"][0]
    assert acme["hasGraphEvidence"] is True
    assert all(e["evidenceId"].startswith("graph:") for e in acme["graphEvidence"] + acme.get("transferable", []))
    assert all(e["evidenceId"].startswith("source:") for e in acme["sourceFacts"])
    assert payload["roles"][1]["hasGraphEvidence"] is False and "graphEvidence" not in payload["roles"][1]


def test_an_oversized_payload_is_refused_before_any_model_call() -> None:
    from llm.errors import InputTooLargeError
    from source_helpers import settings
    from llm.complete_writers import GroqCompleteWriter

    client = ScriptedClient(write_complete_cv=valid_complete_draft())
    with pytest.raises(InputTooLargeError):
        GroqCompleteWriter(settings(max_cv_context_chars=1000), client).write(make_ci())
    assert client.calls == []
