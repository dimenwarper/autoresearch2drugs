from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


def serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {item.name: serialize(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, dict):
        return {key: serialize(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(serialize(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass
class SimulatorConfig:
    seed: int = 7
    scenario_preset: str = "clean_winner"
    initial_cash: float = 450_000_000.0
    time_budget_months: int = 120
    max_parallel_programs: int = 3
    visible_opportunity_pool_size: int = 5
    artifact_root: str | None = None


@dataclass
class OpportunityBrief:
    opportunity_id: str
    target_class: str
    modality: str
    tractability_notes: list[str]
    indication_hypotheses: list[str]
    observable_risk_hints: dict[str, str]
    priority_tier: str


@dataclass
class HiddenProgramState:
    biology_hidden: dict[str, float]
    candidate_hidden: dict[str, float]
    clinical_hidden: dict[str, float]
    strategic_hidden: dict[str, float]


@dataclass
class CandidateState:
    compound_id: str
    truth_profile: dict[str, float]
    observed_profile: dict[str, float]
    history: list[str] = field(default_factory=list)
    is_active: bool = True
    optimization_cycles: int = 0


@dataclass
class CompoundSummary:
    compound_id: str
    observed_profile: dict[str, float]
    history: list[str]
    is_active: bool


@dataclass
class StudySummary:
    study_id: str
    kind: str
    package_type: str | None = None
    study_type: str | None = None
    candidate_id: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    stale_for_gate: bool = False
    recommended_dose_supported: bool = False
    summary: str = ""
    month: int = 0


@dataclass
class TrialDesignSummary:
    design_id: str
    phase: str
    population_definition: str
    endpoint: str
    comparator: str
    dose_strategy: str
    duration: int
    sample_size: int
    enrichment_strategy: str | None
    valid: bool
    invalid_reasons: list[str]
    protocol_complete: bool
    supports_starting_dose_rationale: bool
    projected_cost: float
    projected_duration_range: tuple[int, int]
    projected_enrollment_rate: float
    estimated_power_range: tuple[float, float]
    interpretability_score: float
    regulatory_credibility_score: float
    created_month: int


@dataclass
class TrialResultSummary:
    trial_result_id: str
    phase: str
    primary_endpoint_estimate: float
    confidence_interval: tuple[float, float]
    p_value_equivalent: float
    recommended_dose_supported: bool
    subgroup_findings: list[str]
    exposure_response_summary: str
    safety_summary: str
    dropout_summary: str
    registrational_support: str
    topline_interpretation: str
    month: int


@dataclass
class RegulatoryNote:
    note_id: str
    note_type: str
    question_set: list[str]
    summary: str
    comments: list[str]
    month: int


@dataclass
class WorkItem:
    work_id: str
    program_id: str
    kind: str
    workstream: str
    start_month: int
    expected_end_month: int
    reserved_cost: float
    status: str
    action_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateStatus:
    can_nominate_candidate: bool = False
    can_mark_preclinical_ready: bool = False
    can_file_IND: bool = False
    can_start_phase1: bool = False
    can_start_phase2: bool = False
    can_start_phase3: bool = False
    can_submit_NDA: bool = False


@dataclass
class ProgramSummary:
    program_id: str
    stage: str
    operating_status: str | None
    allocated_budget: float
    active_candidate_id: str | None
    active_indication: str | None
    gate_status: dict[str, bool]
    blocking_issues: list[str]
    in_progress_work_count: int


@dataclass
class EventSummary:
    work_id: str
    program_id: str
    kind: str
    workstream: str
    start_month: int
    expected_end_month: int
    reserved_cost: float
    status: str


@dataclass
class ActionDescriptor:
    action: str
    program_id: str | None
    required_args: list[str]
    action_class: str
    est_cost_range: tuple[float, float] | None
    est_duration_range_months: tuple[int, int] | None
    blocking_reasons: list[str]


@dataclass
class ProgramState:
    program_id: str
    source_opportunity_id: str
    target_class: str
    modality: str
    stage: str
    operating_status: str | None
    allocated_budget: float
    hidden_state: HiddenProgramState
    launch_month: int
    active_candidate_id: str | None = None
    active_indication: str | None = None
    indication_locked: bool = False
    biomarker_strategy: dict[str, Any] | None = None
    candidate_states: dict[str, CandidateState] = field(default_factory=dict)
    completed_studies: list[StudySummary] = field(default_factory=list)
    trial_designs: list[TrialDesignSummary] = field(default_factory=list)
    trial_results: list[TrialResultSummary] = field(default_factory=list)
    manufacturing_status: str = "unknown"
    nonclinical_safety_status: str = "unknown"
    safety_database_status: str = "unknown"
    regulatory_interactions: list[RegulatoryNote] = field(default_factory=list)
    known_findings: list[str] = field(default_factory=list)
    gate_status: GateStatus = field(default_factory=GateStatus)
    blocking_issues: list[str] = field(default_factory=list)
    total_spend: float = 0.0
    optimization_campaigns: int = 0
    study_counts: dict[str, int] = field(default_factory=dict)
    commercial_outlook_label: str = "unclear"
    last_completed_stage: str = "hit_series"

    def active_candidate(self) -> CandidateState | None:
        if self.active_candidate_id is None:
            return None
        return self.candidate_states.get(self.active_candidate_id)

    def latest_valid_design(self, phase: str) -> TrialDesignSummary | None:
        for design in reversed(self.trial_designs):
            if design.phase == phase and design.valid:
                return design
        return None

    def candidate_summaries(self) -> list[CompoundSummary]:
        return [
            CompoundSummary(
                compound_id=candidate.compound_id,
                observed_profile=dict(candidate.observed_profile),
                history=list(candidate.history),
                is_active=candidate.is_active,
            )
            for candidate in self.candidate_states.values()
        ]


@dataclass
class PortfolioState:
    run_id: str
    cash_on_hand: float
    unallocated_cash: float
    elapsed_months: int
    time_budget_months: int
    max_parallel_programs: int
    initial_cash: float
    scenario_preset: str
    visible_opportunities: list[OpportunityBrief] = field(default_factory=list)
    opportunity_priors: dict[str, dict[str, float]] = field(default_factory=dict)
    programs: dict[str, ProgramState] = field(default_factory=dict)
    event_queue: list[WorkItem] = field(default_factory=list)
    reported_metrics: dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    terminal_reason: str | None = None
    next_program_index: int = 1
    next_candidate_index: int = 1
    next_work_index: int = 1
    next_study_index: int = 1
    next_design_index: int = 1
    next_trial_result_index: int = 1
    next_note_index: int = 1
    next_opportunity_index: int = 1

