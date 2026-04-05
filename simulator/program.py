from __future__ import annotations

from .constants import DEFAULT_THRESHOLDS, TERMINAL_STAGES, WORKSTREAM_CLINICAL, WORKSTREAM_DISCOVERY
from .models import GateStatus, ProgramState, ProgramSummary, WorkItem, serialize


def meets_lead_entry_threshold(program: ProgramState, candidate_id: str) -> bool:
    candidate = program.candidate_states[candidate_id]
    threshold = DEFAULT_THRESHOLDS["lead_entry"]
    return all(candidate.observed_profile[key] >= value for key, value in threshold.items())


def meets_nomination_threshold(program: ProgramState, candidate_id: str) -> bool:
    candidate = program.candidate_states[candidate_id]
    threshold = DEFAULT_THRESHOLDS["nomination"]
    return all(candidate.observed_profile[key] >= value for key, value in threshold.items())


def active_work(program_id: str, event_queue: list[WorkItem]) -> list[WorkItem]:
    return [
        work
        for work in event_queue
        if work.program_id == program_id and work.status == "scheduled"
    ]


def has_preclinical_package(program: ProgramState, package_type: str, stale_sensitive: bool = True) -> bool:
    for study in program.completed_studies:
        if study.kind != "preclinical_package" or study.package_type != package_type:
            continue
        if study.candidate_id != program.active_candidate_id:
            continue
        if stale_sensitive and study.stale_for_gate:
            continue
        return True
    return False


def has_recommended_dose(program: ProgramState) -> bool:
    return any(
        result.phase == "phase1" and result.recommended_dose_supported
        for result in program.trial_results
    ) or any(
        study.study_type == "dose_finding_substudy" and study.recommended_dose_supported
        for study in program.completed_studies
    )


def has_pivotal_package(program: ProgramState) -> bool:
    return any(
        result.phase == "phase3"
        and result.registrational_support in {"pivotal", "surrogate_acceptable"}
        for result in program.trial_results
    )


def compute_gate_status(program: ProgramState) -> GateStatus:
    active_candidate = program.active_candidate()
    has_exploratory_package = has_preclinical_package(program, "exploratory", stale_sensitive=True)
    has_translational_package = has_preclinical_package(program, "translational", stale_sensitive=True)
    has_ind_enabling = has_preclinical_package(program, "IND_enabling", stale_sensitive=False)
    phase1_design_valid = program.latest_valid_design("phase1") is not None
    phase2_design_valid = program.latest_valid_design("phase2") is not None
    phase3_design_valid = program.latest_valid_design("phase3") is not None
    phase1_design = program.latest_valid_design("phase1")
    has_starting_dose_rationale = bool(
        phase1_design_valid and phase1_design and phase1_design.supports_starting_dose_rationale
    )
    has_first_in_human_protocol = bool(
        phase1_design_valid and phase1_design and phase1_design.protocol_complete
    )
    candidate_good_enough = bool(
        active_candidate is not None
        and program.active_candidate_id is not None
        and meets_nomination_threshold(program, program.active_candidate_id)
    )
    return GateStatus(
        can_nominate_candidate=(
            program.stage == "lead_series"
            and program.active_candidate_id is not None
            and program.active_indication is not None
            and candidate_good_enough
            and (has_exploratory_package or has_translational_package)
            and program.manufacturing_status != "failed"
            and program.nonclinical_safety_status != "failed"
            and "indication_changed_requires_revalidation" not in program.blocking_issues
        ),
        can_mark_preclinical_ready=(
            program.stage == "development_candidate"
            and program.active_candidate_id is not None
            and program.active_indication is not None
            and has_ind_enabling
            and program.manufacturing_status == "acceptable"
            and program.nonclinical_safety_status == "acceptable"
        ),
        can_file_IND=(
            program.stage == "preclinical_ready"
            and has_ind_enabling
            and phase1_design_valid
            and has_starting_dose_rationale
            and has_first_in_human_protocol
            and program.manufacturing_status == "acceptable"
            and program.nonclinical_safety_status == "acceptable"
        ),
        can_start_phase1=program.stage == "IND_cleared" and phase1_design_valid,
        can_start_phase2=(
            program.stage == "phase1_complete"
            and phase2_design_valid
            and has_recommended_dose(program)
            and program.safety_database_status != "failed"
        ),
        can_start_phase3=(
            program.stage == "phase2_complete"
            and phase3_design_valid
            and program.indication_locked is True
            and program.safety_database_status != "failed"
        ),
        can_submit_NDA=(
            program.stage == "phase3_complete"
            and has_pivotal_package(program)
            and program.manufacturing_status == "acceptable"
            and program.safety_database_status == "acceptable"
        ),
    )


def compute_blocking_issues(program: ProgramState, event_queue: list[WorkItem]) -> list[str]:
    issues: list[str] = []
    active = active_work(program.program_id, event_queue)
    if program.stage in TERMINAL_STAGES:
        return issues
    if program.operating_status == "paused":
        issues.append("program_paused")
    if any(work.workstream == WORKSTREAM_CLINICAL for work in active):
        issues.append("clinical_or_regulatory_work_already_in_progress")
    if any(work.workstream == WORKSTREAM_DISCOVERY for work in active):
        issues.append("discovery_or_preclinical_work_already_in_progress")
    if program.allocated_budget <= 0:
        issues.append("insufficient_allocated_budget")
    if program.active_candidate_id is None and program.stage in {
        "lead_series",
        "development_candidate",
        "preclinical_ready",
        "IND_cleared",
        "phase1_complete",
        "phase2_complete",
        "phase3_complete",
    }:
        issues.append("no_active_candidate")
    if program.active_indication is None and program.stage in {
        "lead_series",
        "development_candidate",
        "preclinical_ready",
        "IND_cleared",
        "phase1_complete",
        "phase2_complete",
        "phase3_complete",
    }:
        issues.append("no_active_indication")
    if (
        program.active_candidate_id is not None
        and program.stage in {"lead_series", "development_candidate"}
        and not meets_nomination_threshold(program, program.active_candidate_id)
    ):
        issues.append("candidate_below_nomination_threshold")
    if (
        program.stage in {"lead_series", "development_candidate"}
        and not (has_preclinical_package(program, "exploratory") or has_preclinical_package(program, "translational"))
    ):
        issues.append("missing_exploratory_or_translational_package")
    if program.stage in {"development_candidate", "preclinical_ready"} and not has_preclinical_package(
        program, "IND_enabling", stale_sensitive=False
    ):
        issues.append("missing_IND_enabling_package")
    if (
        program.stage in {"development_candidate", "preclinical_ready", "phase3_complete"}
        and program.manufacturing_status != "acceptable"
    ):
        issues.append("manufacturing_not_acceptable")
    if (
        program.stage in {"development_candidate", "preclinical_ready"}
        and program.nonclinical_safety_status != "acceptable"
    ):
        issues.append("nonclinical_safety_not_acceptable")
    if program.stage in {"preclinical_ready", "IND_cleared"} and program.latest_valid_design("phase1") is None:
        issues.append("missing_valid_phase1_design")
    phase1_design = program.latest_valid_design("phase1")
    if (
        program.stage == "preclinical_ready"
        and (phase1_design is None or not phase1_design.supports_starting_dose_rationale)
    ):
        issues.append("missing_starting_dose_rationale")
    if program.stage == "preclinical_ready" and (phase1_design is None or not phase1_design.protocol_complete):
        issues.append("missing_first_in_human_protocol")
    if program.stage == "phase1_complete" and not has_recommended_dose(program):
        issues.append("missing_recommended_dose")
    if program.stage == "phase1_complete" and program.latest_valid_design("phase2") is None:
        issues.append("missing_valid_phase2_design")
    if program.stage == "phase2_complete" and program.latest_valid_design("phase3") is None:
        issues.append("missing_valid_phase3_design")
    if program.stage == "phase2_complete" and program.indication_locked is not True:
        issues.append("indication_not_locked")
    if program.stage == "phase3_complete" and not has_pivotal_package(program):
        issues.append("missing_pivotal_package")
    if program.stage in {"phase1_complete", "phase2_complete", "phase3_complete"} and program.safety_database_status == "failed":
        issues.append("safety_database_not_acceptable")
    if "indication_changed_requires_revalidation" in program.known_findings:
        issues.append("indication_changed_requires_revalidation")
    deduped = []
    for issue in issues:
        if issue not in deduped:
            deduped.append(issue)
    return deduped


def refresh_program(program: ProgramState, event_queue: list[WorkItem]) -> None:
    program.blocking_issues = compute_blocking_issues(program, event_queue)
    program.gate_status = compute_gate_status(program)


def work_summary(work: WorkItem) -> dict:
    return {
        "work_id": work.work_id,
        "program_id": work.program_id,
        "kind": work.kind,
        "workstream": work.workstream,
        "start_month": work.start_month,
        "expected_end_month": work.expected_end_month,
        "reserved_cost": work.reserved_cost,
        "status": work.status,
    }


def program_summary(program: ProgramState, event_queue: list[WorkItem]) -> ProgramSummary:
    in_progress_count = len(active_work(program.program_id, event_queue))
    return ProgramSummary(
        program_id=program.program_id,
        stage=program.stage,
        operating_status=program.operating_status,
        allocated_budget=program.allocated_budget,
        active_candidate_id=program.active_candidate_id,
        active_indication=program.active_indication,
        gate_status=serialize(program.gate_status),
        blocking_issues=list(program.blocking_issues),
        in_progress_work_count=in_progress_count,
    )


def observable_state(program: ProgramState, event_queue: list[WorkItem]) -> dict:
    return {
        "program_id": program.program_id,
        "source_opportunity_id": program.source_opportunity_id,
        "target_class": program.target_class,
        "modality": program.modality,
        "stage": program.stage,
        "operating_status": program.operating_status,
        "allocated_budget": program.allocated_budget,
        "active_candidate_id": program.active_candidate_id,
        "active_indication": program.active_indication,
        "indication_locked": program.indication_locked,
        "biomarker_strategy": serialize(program.biomarker_strategy),
        "candidate_summaries": serialize(program.candidate_summaries()),
        "completed_studies": serialize(program.completed_studies),
        "trial_designs": serialize(program.trial_designs),
        "trial_results": serialize(program.trial_results),
        "manufacturing_status": program.manufacturing_status,
        "nonclinical_safety_status": program.nonclinical_safety_status,
        "safety_database_status": program.safety_database_status,
        "regulatory_interactions": serialize(program.regulatory_interactions),
        "in_progress_work": [work_summary(work) for work in active_work(program.program_id, event_queue)],
        "gate_status": serialize(program.gate_status),
        "blocking_issues": list(program.blocking_issues),
        "known_findings": list(program.known_findings),
    }
