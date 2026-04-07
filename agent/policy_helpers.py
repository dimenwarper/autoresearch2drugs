from __future__ import annotations

from typing import Any


RISK_LEVELS = {"low": 0, "medium": 1, "high": 2}
PRIORITY_SCORES = {"speculative": 0, "balanced": 1, "attractive": 2}
STAGE_SCORES = {
    "hit_series": 1.0,
    "lead_series": 2.0,
    "development_candidate": 3.0,
    "preclinical_ready": 4.0,
    "IND_cleared": 5.0,
    "phase1_complete": 6.0,
    "phase2_complete": 7.0,
    "phase3_complete": 8.0,
    "submitted": 9.0,
    "approved": 10.0,
}
PROFILE_WEIGHTS = {
    "potency_estimate": 0.28,
    "selectivity_estimate": 0.22,
    "bioavailability_estimate": 0.20,
    "safety_margin_estimate": 0.20,
    "developability_estimate": 0.10,
}
TARGET_STAGE_BUDGETS = {
    "hit_series": 65_000_000.0,
    "lead_series": 85_000_000.0,
    "development_candidate": 135_000_000.0,
    "preclinical_ready": 150_000_000.0,
    "IND_cleared": 180_000_000.0,
    "phase1_complete": 220_000_000.0,
    "phase2_complete": 270_000_000.0,
    "phase3_complete": 330_000_000.0,
}


def rank_visible_opportunities(portfolio_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank visible opportunities using only observable hints."""

    opportunities = list(portfolio_state.get("visible_opportunities", []))

    def score(opportunity: dict[str, Any]) -> tuple[float, str]:
        risk_hints = opportunity.get("observable_risk_hints", {})
        risk_penalty = sum(RISK_LEVELS.get(str(level), 1) for level in risk_hints.values())
        tier_score = PRIORITY_SCORES.get(str(opportunity.get("priority_tier")), 0)
        hypothesis_bonus = min(len(opportunity.get("indication_hypotheses", [])), 2)
        total = (tier_score * 10.0) + hypothesis_bonus - (risk_penalty * 1.5)
        return total, str(opportunity.get("opportunity_id", ""))

    return sorted(opportunities, key=score, reverse=True)


def rank_candidates_within_program(program_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank active candidates by weighted observed profile strength."""

    candidates = [candidate for candidate in program_state.get("candidate_summaries", []) if candidate.get("is_active", True)]

    def score(candidate: dict[str, Any]) -> tuple[float, str]:
        observed_profile = candidate.get("observed_profile", {})
        weighted = 0.0
        for key, weight in PROFILE_WEIGHTS.items():
            weighted += float(observed_profile.get(key, 0.0)) * weight
        history_bonus = 0.02 * len(candidate.get("history", []))
        return weighted + history_bonus, str(candidate.get("compound_id", ""))

    return sorted(candidates, key=score, reverse=True)


def check_common_blocker_patterns(program_state: dict[str, Any]) -> list[str]:
    """Return a stable list of recognizable blocker patterns from observable state."""

    blockers = list(program_state.get("blocking_issues", []))
    patterns: set[str] = set()
    if program_state.get("in_progress_work"):
        patterns.add("work_in_progress")
    gate_status = program_state.get("gate_status", {})
    if not gate_status.get("can_nominate_candidate", True):
        patterns.add("nomination_gate_closed")
    if not gate_status.get("can_mark_preclinical_ready", True):
        patterns.add("preclinical_gate_closed")
    if not gate_status.get("can_file_IND", True):
        patterns.add("ind_gate_closed")
    if not gate_status.get("can_start_phase1", True):
        patterns.add("phase1_gate_closed")
    if not gate_status.get("can_start_phase2", True):
        patterns.add("phase2_gate_closed")
    if not gate_status.get("can_start_phase3", True):
        patterns.add("phase3_gate_closed")
    if not gate_status.get("can_submit_NDA", True):
        patterns.add("nda_gate_closed")

    for blocker in blockers:
        if "missing_exploratory_or_translational_package" in blocker:
            patterns.add("needs_translational_package")
        if "indication" in blocker:
            patterns.add("indication_problem")
        if "trial_design" in blocker:
            patterns.add("trial_design_problem")
        if "safety" in blocker:
            patterns.add("safety_problem")
        if "manufacturing" in blocker:
            patterns.add("manufacturing_problem")
        if "biomarker" in blocker:
            patterns.add("biomarker_problem")
    return sorted(patterns)


def score_stage_urgency(program_state: dict[str, Any], portfolio_state: dict[str, Any] | None = None) -> float:
    """Score how urgently an idle program should receive attention."""

    stage = str(program_state.get("stage", "hit_series"))
    urgency = STAGE_SCORES.get(stage, 0.0) * 10.0
    if program_state.get("operating_status") == "paused":
        urgency -= 5.0
    if not program_state.get("in_progress_work"):
        urgency += 2.0
    if program_state.get("active_candidate_id"):
        urgency += 1.0
    if program_state.get("active_indication"):
        urgency += 0.5
    gate_status = program_state.get("gate_status", {})
    urgency += sum(1.0 for gate_open in gate_status.values() if gate_open)
    urgency -= 0.25 * len(program_state.get("blocking_issues", []))
    if portfolio_state is not None:
        elapsed = float(portfolio_state.get("elapsed_months", 0))
        time_budget = float(portfolio_state.get("time_budget_months", 120))
        if time_budget > 0:
            urgency += (elapsed / time_budget) * 3.0
    return urgency


def normalize_available_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a stable normalized action menu sorted by action and program id."""

    normalized = []
    for action in actions:
        normalized.append(
            {
                "action": str(action.get("action", "")),
                "program_id": action.get("program_id"),
                "required_args": list(action.get("required_args", [])),
                "action_class": str(action.get("action_class", "")),
                "blocking_reasons": list(action.get("blocking_reasons", [])),
            }
        )
    return sorted(normalized, key=lambda item: (item["action"], str(item["program_id"] or "")))


def has_action(actions: list[dict[str, Any]], action_name: str, program_id: str | None = None) -> bool:
    """Return True when an action is present for the optional program scope."""

    for action in actions:
        if action.get("action") != action_name:
            continue
        if program_id is None:
            if action.get("program_id") is None:
                return True
            continue
        if action.get("program_id") == program_id:
            return True
    return False


def get_action(actions: list[dict[str, Any]], action_name: str, program_id: str | None = None) -> dict[str, Any] | None:
    """Return the first matching action descriptor if present."""

    for action in actions:
        if action.get("action") != action_name:
            continue
        if program_id is None and action.get("program_id") is None:
            return action
        if program_id is not None and action.get("program_id") == program_id:
            return action
    return None


def suggest_budget_target(program_state: dict[str, Any]) -> float:
    """Return a deterministic stage-specific target budget."""

    stage = str(program_state.get("stage", "hit_series"))
    return TARGET_STAGE_BUDGETS.get(stage, 65_000_000.0)


def recommended_budget_top_up(portfolio_state: dict[str, Any], program_state: dict[str, Any]) -> float:
    """Recommend a non-negative top-up amount using only observable state."""

    target_budget = suggest_budget_target(program_state)
    current_budget = float(program_state.get("allocated_budget", 0.0))
    unallocated_cash = float(portfolio_state.get("unallocated_cash", 0.0))
    if current_budget >= target_budget or unallocated_cash <= 0:
        return 0.0
    gap = target_budget - current_budget
    return max(0.0, min(gap, unallocated_cash))
