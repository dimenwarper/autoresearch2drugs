from __future__ import annotations

from typing import Any

from policy_helpers import (
    check_common_blocker_patterns,
    has_action,
    normalize_available_actions,
    rank_candidates_within_program,
    rank_visible_opportunities,
    recommended_budget_top_up,
    score_stage_urgency,
)


POLICY_NOTES = """
Editable routing policy surface.
Keep choose_next_action deterministic and return a single legal simulator action plan.
"""


PHASE1_DESIGN = {
    "population_definition": "dose_escalation_then_expansion",
    "endpoint": "objective_biomarker",
    "comparator": "placebo",
    "dose_strategy": "moderate_escalation",
    "duration": 6,
    "sample_size": 48,
    "enrichment_strategy": None,
}
PHASE2_DESIGN = {
    "population_definition": "biomarker_enriched_population",
    "endpoint": "binary_response",
    "comparator": "standard_of_care",
    "dose_strategy": "adaptive_mid_dose",
    "duration": 12,
    "sample_size": 140,
    "enrichment_strategy": "biomarker_positive",
}
PHASE3_DESIGN = {
    "population_definition": "broad_intent_to_treat",
    "endpoint": "survival_or_event",
    "comparator": "standard_of_care",
    "dose_strategy": "commercial_dose",
    "duration": 24,
    "sample_size": 420,
    "enrichment_strategy": None,
}
OPTIMIZE_OBJECTIVE = {
    "potency": 0.30,
    "selectivity": 0.20,
    "pk": 0.20,
    "safety_margin": 0.20,
    "developability": 0.10,
}


def _plan(action: str, *, program_id: str | None = None, kwargs: dict[str, Any] | None = None, reason: str) -> dict[str, Any]:
    return {
        "action": action,
        "program_id": program_id,
        "kwargs": kwargs or {},
        "reason": reason,
    }


def _best_candidate_id(program_state: dict[str, Any]) -> str | None:
    ranked = rank_candidates_within_program(program_state)
    if not ranked:
        return None
    return str(ranked[0]["compound_id"])


def _completed_package(program_state: dict[str, Any], package_type: str) -> bool:
    return any(
        study.get("kind") == "preclinical_package" and study.get("package_type") == package_type and not study.get("stale_for_gate", False)
        for study in program_state.get("completed_studies", [])
    )


def _valid_trial_design(program_state: dict[str, Any], phase: str) -> bool:
    return any(
        design.get("phase") == phase and bool(design.get("valid"))
        for design in program_state.get("trial_designs", [])
    )


def _advance_time_plan(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if has_action(actions, "advance_time"):
        for action in actions:
            if action.get("action") != "advance_time" or action.get("program_id") is not None:
                continue
            required_args = set(action.get("required_args", []))
            if "to_next_event" in required_args:
                return _plan("advance_time", kwargs={"to_next_event": True}, reason="Advance to the next scheduled event.")
        return _plan("advance_time", kwargs={"months": 1}, reason="Advance one month when no event-driven move is available.")
    return None


def _budget_plan(portfolio_state: dict[str, Any], program_state: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    program_id = str(program_state["program_id"])
    if not has_action(actions, "allocate_budget"):
        return None
    top_up = recommended_budget_top_up(portfolio_state, program_state)
    if top_up < 15_000_000.0:
        return None
    new_budget = float(program_state.get("allocated_budget", 0.0)) + top_up
    return _plan(
        "allocate_budget",
        kwargs={"program_allocations": {program_id: new_budget}},
        reason=f"Top up {program_id} to maintain stage-appropriate capital.",
    )


def _launch_plan(portfolio_state: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not has_action(actions, "launch_program"):
        return None
    visible = rank_visible_opportunities(portfolio_state)
    if not visible:
        return None
    top_opportunity = visible[0]
    launch_budget = min(float(portfolio_state.get("unallocated_cash", 0.0)), 80_000_000.0)
    if launch_budget < 40_000_000.0:
        return None
    return _plan(
        "launch_program",
        kwargs={
            "opportunity_id": top_opportunity["opportunity_id"],
            "initial_budget": launch_budget,
        },
        reason="Launch the strongest visible opportunity with a standard initial budget.",
    )


def _stage_plan(program_state: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    program_id = str(program_state["program_id"])
    best_candidate_id = _best_candidate_id(program_state)
    stage = str(program_state.get("stage"))
    blockers = set(check_common_blocker_patterns(program_state))

    if program_state.get("operating_status") == "paused" and has_action(actions, "resume_program", program_id):
        return _plan("resume_program", program_id=program_id, reason="Resume paused work on a nonterminal program.")

    if stage == "hit_series" and has_action(actions, "optimize_candidate", program_id):
        return _plan(
            "optimize_candidate",
            program_id=program_id,
            kwargs={"objective_profile": OPTIMIZE_OBJECTIVE, "budget": 4_000_000.0, "cycles": 2},
            reason="Push the discovery series toward a lead-quality profile.",
        )

    if stage == "lead_series":
        if best_candidate_id and program_state.get("active_indication") is None and has_action(actions, "choose_indication", program_id):
            return _plan(
                "choose_indication",
                program_id=program_id,
                kwargs={
                    "candidate_id": best_candidate_id,
                    "indication": "target_enriched_population",
                    "biomarker_strategy": {"validated": False},
                },
                reason="Select an initial enriched indication before nomination.",
            )
        if best_candidate_id and not _completed_package(program_state, "translational") and has_action(actions, "generate_preclinical_evidence:translational", program_id):
            return _plan(
                "generate_preclinical_evidence:translational",
                program_id=program_id,
                kwargs={"candidate_id": best_candidate_id},
                reason="Generate translational evidence to support nomination.",
            )
        if best_candidate_id and has_action(actions, "nominate_candidate", program_id):
            return _plan(
                "nominate_candidate",
                program_id=program_id,
                kwargs={"candidate_id": best_candidate_id},
                reason="Nominate the strongest candidate once the gate opens.",
            )
        if "needs_translational_package" in blockers and best_candidate_id and has_action(actions, "generate_preclinical_evidence:exploratory", program_id):
            return _plan(
                "generate_preclinical_evidence:exploratory",
                program_id=program_id,
                kwargs={"candidate_id": best_candidate_id},
                reason="Fill an observable evidence gap before trying again.",
            )
        if has_action(actions, "optimize_candidate", program_id):
            return _plan(
                "optimize_candidate",
                program_id=program_id,
                kwargs={"objective_profile": OPTIMIZE_OBJECTIVE, "budget": 3_000_000.0, "cycles": 1},
                reason="Continue medicinal chemistry refinement in lead optimization.",
            )

    active_candidate_id = program_state.get("active_candidate_id") or best_candidate_id
    if stage == "development_candidate":
        if active_candidate_id and program_state.get("active_indication") is None and has_action(actions, "choose_indication", program_id):
            return _plan(
                "choose_indication",
                program_id=program_id,
                kwargs={
                    "candidate_id": active_candidate_id,
                    "indication": "target_enriched_population",
                    "biomarker_strategy": {"validated": False},
                },
                reason="Set a working indication on the nominated candidate.",
            )
        if active_candidate_id and not _completed_package(program_state, "IND_enabling") and has_action(actions, "generate_preclinical_evidence:IND_enabling", program_id):
            return _plan(
                "generate_preclinical_evidence:IND_enabling",
                program_id=program_id,
                kwargs={"candidate_id": active_candidate_id},
                reason="Build the IND-enabling package before filing.",
            )
        if not _valid_trial_design(program_state, "phase1") and has_action(actions, "design_clinical_trial:phase1", program_id):
            return _plan(
                "design_clinical_trial:phase1",
                program_id=program_id,
                kwargs=PHASE1_DESIGN,
                reason="Create a valid phase 1 design before preclinical handoff.",
            )
        if has_action(actions, "advance_program:mark_preclinical_ready", program_id):
            return _plan(
                "advance_program:mark_preclinical_ready",
                program_id=program_id,
                reason="Move into preclinical-ready once observable gates are satisfied.",
            )
        if has_action(actions, "run_additional_study:biomarker_validation", program_id):
            return _plan(
                "run_additional_study:biomarker_validation",
                program_id=program_id,
                kwargs={"parameters": {"assay": "circulating_marker"}},
                reason="Use a focused validation study when the gate is still closed.",
            )

    if stage == "preclinical_ready":
        if has_action(actions, "advance_program:file_IND", program_id):
            return _plan(
                "advance_program:file_IND",
                program_id=program_id,
                reason="File the IND as soon as the observable gate opens.",
            )
        if has_action(actions, "request_regulatory_feedback", program_id):
            return _plan(
                "request_regulatory_feedback",
                program_id=program_id,
                kwargs={"question_set": ["endpoint acceptability", "starting dose rationale"]},
                reason="Request focused regulatory feedback when IND filing is blocked.",
            )

    if stage == "IND_cleared":
        if not _valid_trial_design(program_state, "phase1") and has_action(actions, "design_clinical_trial:phase1", program_id):
            return _plan(
                "design_clinical_trial:phase1",
                program_id=program_id,
                kwargs=PHASE1_DESIGN,
                reason="Refresh or create phase 1 trial design after IND clearance.",
            )
        if has_action(actions, "advance_program:start_phase1", program_id):
            return _plan(
                "advance_program:start_phase1",
                program_id=program_id,
                reason="Start phase 1 when the design and gate are ready.",
            )

    if stage == "phase1_complete":
        if not _valid_trial_design(program_state, "phase2") and has_action(actions, "design_clinical_trial:phase2", program_id):
            return _plan(
                "design_clinical_trial:phase2",
                program_id=program_id,
                kwargs=PHASE2_DESIGN,
                reason="Prepare an enriched phase 2 design before advancement.",
            )
        if has_action(actions, "advance_program:start_phase2", program_id):
            return _plan(
                "advance_program:start_phase2",
                program_id=program_id,
                reason="Start phase 2 when the observable gate is open.",
            )
        if has_action(actions, "run_additional_study:dose_finding_substudy", program_id):
            return _plan(
                "run_additional_study:dose_finding_substudy",
                program_id=program_id,
                kwargs={"parameters": {"cohort_count": 2}},
                reason="Tighten dose selection when phase 2 is still blocked.",
            )

    if stage == "phase2_complete":
        if not _valid_trial_design(program_state, "phase3") and has_action(actions, "design_clinical_trial:phase3", program_id):
            return _plan(
                "design_clinical_trial:phase3",
                program_id=program_id,
                kwargs=PHASE3_DESIGN,
                reason="Prepare the confirmatory phase 3 package before advancement.",
            )
        if has_action(actions, "advance_program:start_phase3", program_id):
            return _plan(
                "advance_program:start_phase3",
                program_id=program_id,
                reason="Start phase 3 when the observable gate is open.",
            )

    if stage == "phase3_complete" and has_action(actions, "advance_program:submit_NDA", program_id):
        return _plan(
            "advance_program:submit_NDA",
            program_id=program_id,
            reason="Submit the NDA immediately after a valid phase 3 package is ready.",
        )

    return None


def choose_next_action(
    portfolio_state: dict[str, Any],
    program_states: dict[str, dict[str, Any]],
    available_actions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the next deterministic action plan for the simulator."""

    actions = normalize_available_actions(available_actions)
    active_programs = [
        state
        for state in program_states.values()
        if state.get("operating_status") in {"active", "paused"} and state.get("stage") not in {"terminated", "approved"}
    ]
    active_programs.sort(key=lambda state: score_stage_urgency(state, portfolio_state), reverse=True)

    for program_state in active_programs:
        if program_state.get("in_progress_work") and program_state.get("operating_status") != "paused":
            continue
        budget_plan = _budget_plan(portfolio_state, program_state, actions)
        if budget_plan is not None:
            return budget_plan
        stage_plan = _stage_plan(program_state, actions)
        if stage_plan is not None:
            return stage_plan

    if not active_programs:
        launch_plan = _launch_plan(portfolio_state, actions)
        if launch_plan is not None:
            return launch_plan

    return _advance_time_plan(actions)
