from __future__ import annotations

from .constants import TERMINAL_STAGES
from .models import PortfolioState, ProgramState, WorkItem, serialize
from .program import program_summary, refresh_program, work_summary


def sync_cash_invariant(portfolio: PortfolioState) -> None:
    allocated = sum(
        program.allocated_budget
        for program in portfolio.programs.values()
        if program.stage not in TERMINAL_STAGES
    )
    portfolio.unallocated_cash = portfolio.cash_on_hand - allocated


def active_program_count(portfolio: PortfolioState) -> int:
    return sum(
        1
        for program in portfolio.programs.values()
        if program.stage not in TERMINAL_STAGES and program.operating_status == "active"
    )


def active_work_items(portfolio: PortfolioState) -> list[WorkItem]:
    return [work for work in portfolio.event_queue if work.status == "scheduled"]


def refresh_all_programs(portfolio: PortfolioState) -> None:
    for program in portfolio.programs.values():
        refresh_program(program, portfolio.event_queue)
    sync_cash_invariant(portfolio)
    update_metrics(portfolio)


def next_identifier(portfolio: PortfolioState, attr_name: str, prefix: str) -> str:
    value = getattr(portfolio, attr_name)
    setattr(portfolio, attr_name, value + 1)
    return f"{prefix}-{value:04d}"


def schedule_work(
    portfolio: PortfolioState,
    program: ProgramState,
    *,
    kind: str,
    workstream: str,
    action_name: str,
    duration_months: int,
    reserved_cost: float,
    payload: dict,
) -> WorkItem:
    work = WorkItem(
        work_id=next_identifier(portfolio, "next_work_index", "work"),
        program_id=program.program_id,
        kind=kind,
        workstream=workstream,
        start_month=portfolio.elapsed_months,
        expected_end_month=portfolio.elapsed_months + duration_months,
        reserved_cost=reserved_cost,
        status="scheduled",
        action_name=action_name,
        payload=payload,
    )
    portfolio.event_queue.append(work)
    portfolio.event_queue.sort(key=lambda item: (item.expected_end_month, item.work_id))
    return work


def cancel_program_work(portfolio: PortfolioState, program_id: str) -> list[dict]:
    canceled = []
    retained = []
    for work in portfolio.event_queue:
        if work.program_id == program_id and work.status == "scheduled":
            work.status = "canceled"
            canceled.append(work_summary(work))
            continue
        retained.append(work)
    portfolio.event_queue = retained
    return canceled


def abandon_all_work(portfolio: PortfolioState) -> list[dict]:
    abandoned = []
    for work in portfolio.event_queue:
        if work.status == "scheduled":
            work.status = "canceled"
            abandoned.append(work_summary(work))
    portfolio.event_queue = []
    return abandoned


def update_metrics(portfolio: PortfolioState) -> None:
    approvals = sum(1 for program in portfolio.programs.values() if program.stage == "approved")
    ind_filings = sum(
        1
        for program in portfolio.programs.values()
        for note in program.regulatory_interactions
        if note.note_type == "IND_submission"
    )
    nda_submissions = sum(
        1
        for program in portfolio.programs.values()
        for note in program.regulatory_interactions
        if note.note_type == "NDA_submission"
    )
    successful_phase2 = sum(
        1
        for program in portfolio.programs.values()
        for result in program.trial_results
        if result.phase == "phase2" and result.primary_endpoint_estimate > 0
    )
    successful_phase3 = sum(
        1
        for program in portfolio.programs.values()
        for result in program.trial_results
        if result.phase == "phase3" and result.registrational_support in {"pivotal", "surrogate_acceptable"}
    )
    approved_programs = [program for program in portfolio.programs.values() if program.stage == "approved"]
    time_to_first_approval = None
    if approved_programs:
        approval_months = []
        for program in approved_programs:
            last_result = portfolio.time_budget_months
            for note in program.regulatory_interactions:
                if note.note_type == "NDA_decision_approved":
                    last_result = min(last_result, note.month)
            approval_months.append(last_result)
        time_to_first_approval = min(approval_months)
    commercially_weak = sum(1 for program in approved_programs if program.commercial_outlook_label == "weak")
    total_cost_spent = portfolio.initial_cash - portfolio.cash_on_hand
    portfolio.reported_metrics = {
        "primary_score": approvals / total_cost_spent if total_cost_spent > 0 else 0.0,
        "total_approvals": approvals,
        "total_IND_filings": ind_filings,
        "total_NDA_submissions": nda_submissions,
        "successful_phase2_count": successful_phase2,
        "successful_phase3_count": successful_phase3,
        "time_to_first_approval": time_to_first_approval,
        "total_elapsed_months": portfolio.elapsed_months,
        "total_cost_spent": total_cost_spent,
        "terminal_portfolio_status": portfolio.terminal_reason or "ongoing",
        "commercially_weak_approval_count": commercially_weak,
    }


def portfolio_state(portfolio: PortfolioState) -> dict:
    return {
        "cash_on_hand": portfolio.cash_on_hand,
        "unallocated_cash": portfolio.unallocated_cash,
        "elapsed_months": portfolio.elapsed_months,
        "time_budget_months": portfolio.time_budget_months,
        "max_parallel_programs": portfolio.max_parallel_programs,
        "program_summaries": serialize(
            [program_summary(program, portfolio.event_queue) for program in portfolio.programs.values()]
        ),
        "active_program_ids": [
            program.program_id
            for program in portfolio.programs.values()
            if program.operating_status == "active" and program.stage not in TERMINAL_STAGES
        ],
        "paused_program_ids": [
            program.program_id
            for program in portfolio.programs.values()
            if program.operating_status == "paused" and program.stage not in TERMINAL_STAGES
        ],
        "visible_opportunities": serialize(portfolio.visible_opportunities),
        "in_progress_events": [work_summary(work) for work in active_work_items(portfolio)],
        "reported_metrics": serialize(portfolio.reported_metrics),
    }


def program_terminal_summary(program: ProgramState, elapsed_months: int) -> dict:
    return {
        "program_id": program.program_id,
        "terminal_stage": program.stage,
        "total_spend": program.total_spend,
        "elapsed_time_since_launch": elapsed_months - program.launch_month,
        "last_completed_stage": program.last_completed_stage,
        "key_positive_findings": [finding for finding in program.known_findings if "positive" in finding or "supported" in finding][-5:],
        "key_blocking_or_failure_findings": [
            finding
            for finding in program.known_findings
            if "failed" in finding or "concern" in finding or "blocked" in finding
        ][-5:],
        "commercial_outlook_label": program.commercial_outlook_label,
    }
