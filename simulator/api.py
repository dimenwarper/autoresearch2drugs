from __future__ import annotations

import hashlib
import random
from typing import Any

from .constants import (
    ALLOWED_STAGE_TRANSITIONS,
    COST_BANDS,
    ENDPOINT_FAMILIES,
    PHASE_COMPATIBLE_ENDPOINTS,
    STUDY_TYPE_STAGE_MAP,
    TERMINAL_STAGES,
    WORKSTREAM_CLINICAL,
    WORKSTREAM_DISCOVERY,
)
from .models import ActionDescriptor, PortfolioState, ProgramState, SimulatorConfig, serialize
from .observability import ObservabilityManager
from .portfolio import (
    abandon_all_work,
    active_program_count,
    active_work_items,
    cancel_program_work,
    next_identifier,
    portfolio_state,
    program_terminal_summary,
    refresh_all_programs,
    schedule_work,
)
from .program import active_work, observable_state
from .regulatory import (
    complete_review,
    create_regulatory_note,
    instant_feedback,
    regulatory_feedback_fee,
    regulatory_review_duration,
    regulatory_submission_cost,
    submission_note,
)
from .studies import (
    complete_additional_study,
    complete_optimize_candidate,
    complete_preclinical_evidence,
    estimate_additional_study,
    estimate_optimize_candidate_duration,
    estimate_preclinical_package,
)
from .trials import complete_trial, design_trial, phase_trial_duration
from .world import (
    build_opportunity_pool,
    generate_opportunity,
    initialize_candidate_series,
    sample_hidden_program_state,
)


class ActionError(RuntimeError):
    pass


class DrugDevelopmentSimulator:
    def __init__(self, config: SimulatorConfig | None = None, **kwargs: Any):
        self.config = config or SimulatorConfig(**kwargs)
        self.rng = random.Random(self.config.seed)
        self.portfolio: PortfolioState | None = None
        self.observability: ObservabilityManager | None = None
        self.reset()

    def reset(
        self,
        *,
        seed: int | None = None,
        scenario_preset: str | None = None,
        initial_cash: float | None = None,
        time_budget_months: int | None = None,
        max_parallel_programs: int | None = None,
    ) -> dict[str, Any]:
        if seed is not None:
            self.config.seed = seed
        if scenario_preset is not None:
            self.config.scenario_preset = scenario_preset
        if initial_cash is not None:
            self.config.initial_cash = initial_cash
        if time_budget_months is not None:
            self.config.time_budget_months = time_budget_months
        if max_parallel_programs is not None:
            self.config.max_parallel_programs = max_parallel_programs
        self.rng = random.Random(self.config.seed)
        run_id = self._build_run_id()
        visible_opportunities, priors, next_opportunity_index = build_opportunity_pool(
            self.rng,
            self.config.scenario_preset,
            self.config.visible_opportunity_pool_size,
        )
        self.portfolio = PortfolioState(
            run_id=run_id,
            cash_on_hand=self.config.initial_cash,
            unallocated_cash=self.config.initial_cash,
            elapsed_months=0,
            time_budget_months=self.config.time_budget_months,
            max_parallel_programs=self.config.max_parallel_programs,
            initial_cash=self.config.initial_cash,
            scenario_preset=self.config.scenario_preset,
            visible_opportunities=visible_opportunities,
            opportunity_priors=priors,
            next_opportunity_index=next_opportunity_index,
        )
        self.observability = ObservabilityManager(self.config, run_id)
        refresh_all_programs(self.portfolio)
        return self.get_portfolio_state()

    def get_portfolio_state(self) -> dict[str, Any]:
        return portfolio_state(self._portfolio)

    def get_program_state(self, program_id: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        return observable_state(program, self._portfolio.event_queue)

    def get_available_actions(
        self,
        program_id: str | None = None,
        include_blocked: bool = False,
    ) -> list[dict[str, Any]]:
        portfolio = self._portfolio
        descriptors: list[ActionDescriptor] = []
        if portfolio.terminal:
            terminal_actions = [
                ActionDescriptor("get_portfolio_state", None, [], "instant", None, None, []),
                ActionDescriptor("get_available_actions", None, ["program_id", "include_blocked"], "instant", None, None, []),
            ]
            if program_id is None:
                terminal_actions.append(ActionDescriptor("get_program_state", None, ["program_id"], "instant", None, None, []))
            else:
                terminal_actions.append(ActionDescriptor("get_program_state", program_id, [], "instant", None, None, []))
            return [serialize(item) for item in terminal_actions]
        descriptors.extend(self._global_actions(include_blocked))
        if program_id is None:
            for program in portfolio.programs.values():
                descriptors.extend(self._program_actions(program, include_blocked))
        else:
            descriptors.extend(self._program_actions(self._get_program(program_id), include_blocked))
        if not include_blocked:
            descriptors = [descriptor for descriptor in descriptors if not descriptor.blocking_reasons]
        return [serialize(descriptor) for descriptor in descriptors]

    def launch_program(self, opportunity_id: str, initial_budget: float) -> dict[str, Any]:
        portfolio = self._portfolio
        self._ensure_not_terminal("launch_program")
        request_ref = self._action_requested("launch_program", None, None)
        reasons = []
        opportunity = next((item for item in portfolio.visible_opportunities if item.opportunity_id == opportunity_id), None)
        if opportunity is None:
            reasons.append("opportunity_not_visible")
        if initial_budget <= 0:
            reasons.append("initial_budget_must_be_positive")
        if initial_budget > portfolio.unallocated_cash:
            reasons.append("initial_budget_exceeds_unallocated_cash")
        if active_program_count(portfolio) >= portfolio.max_parallel_programs:
            reasons.append("max_parallel_programs_reached")
        if reasons:
            self._reject("launch_program", None, reasons, request_ref)
        hidden_state, commercial_outlook = sample_hidden_program_state(
            self.rng,
            portfolio.opportunity_priors[opportunity_id],
            portfolio.scenario_preset,
        )
        candidates, portfolio.next_candidate_index = initialize_candidate_series(
            self.rng,
            hidden_state,
            portfolio.next_candidate_index,
        )
        program = ProgramState(
            program_id=next_identifier(portfolio, "next_program_index", "prog"),
            source_opportunity_id=opportunity_id,
            target_class=opportunity.target_class,
            modality=opportunity.modality,
            stage="hit_series",
            operating_status="active",
            allocated_budget=initial_budget,
            hidden_state=hidden_state,
            launch_month=portfolio.elapsed_months,
            candidate_states=candidates,
            commercial_outlook_label=commercial_outlook,
        )
        portfolio.programs[program.program_id] = program
        portfolio.visible_opportunities = [item for item in portfolio.visible_opportunities if item.opportunity_id != opportunity_id]
        portfolio.opportunity_priors.pop(opportunity_id, None)
        replacement, prior = generate_opportunity(self.rng, portfolio.scenario_preset, portfolio.next_opportunity_index)
        portfolio.next_opportunity_index += 1
        portfolio.visible_opportunities.append(replacement)
        portfolio.opportunity_priors[replacement.opportunity_id] = prior
        refresh_all_programs(portfolio)
        recent_refs = [request_ref]
        recent_refs.append(
            self._emit_event(
                "action_accepted",
                "launch_program",
                None,
                None,
                None,
                "Program launched from visible opportunity deck.",
            )
        )
        recent_refs.append(
            self._emit_event(
                "stage_changed",
                "launch_program",
                program.program_id,
                None,
                program.stage,
                f"{program.program_id} entered lifecycle at {program.stage}.",
            )
        )
        recent_refs.extend(self._emit_gate_updates([program.program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {
            "program_summary": self.get_program_state(program.program_id),
            "initial_candidate_summaries": serialize(program.candidate_summaries()),
            "launch_note": f"Launched {program.program_id} from {opportunity_id} with {initial_budget:.0f} allocated.",
        }

    def allocate_budget(self, program_allocations: dict[str, float]) -> dict[str, Any]:
        portfolio = self._portfolio
        self._ensure_not_terminal("allocate_budget")
        request_ref = self._action_requested("allocate_budget", None, None)
        reasons = []
        for program_id, target in program_allocations.items():
            if program_id not in portfolio.programs:
                reasons.append(f"{program_id}:unknown_program")
                continue
            if portfolio.programs[program_id].stage in TERMINAL_STAGES:
                reasons.append(f"{program_id}:program_terminal")
            if target < 0:
                reasons.append(f"{program_id}:negative_target_budget")
        new_total = 0.0
        for program_id, program in portfolio.programs.items():
            if program.stage in TERMINAL_STAGES:
                continue
            new_total += program_allocations.get(program_id, program.allocated_budget)
        if new_total > portfolio.cash_on_hand:
            reasons.append("allocated_budget_exceeds_cash_on_hand")
        if reasons:
            self._reject("allocate_budget", None, reasons, request_ref)
        for program_id, target in program_allocations.items():
            portfolio.programs[program_id].allocated_budget = target
        refresh_all_programs(portfolio)
        recent_refs = [request_ref]
        recent_refs.append(
            self._emit_event(
                "budget_reallocated",
                "allocate_budget",
                None,
                None,
                None,
                "Program budgets reallocated at portfolio level.",
            )
        )
        recent_refs.extend(self._emit_gate_updates(list(program_allocations)))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        runway = {
            program_id: round(
                portfolio.programs[program_id].allocated_budget / 5_000_000.0,
                1,
            )
            for program_id in program_allocations
        }
        return {
            "portfolio_allocation_summary": self.get_portfolio_state(),
            "runway_estimate_by_program_months": runway,
        }

    def pause_program(self, program_id: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("pause_program", program_id)
        request_ref = self._action_requested("pause_program", program_id, program.stage)
        reasons = []
        if program.stage in TERMINAL_STAGES:
            reasons.append("program_terminal")
        if program.operating_status != "active":
            reasons.append("program_not_active")
        if reasons:
            self._reject("pause_program", program_id, reasons, request_ref, stage_before=program.stage)
        program.operating_status = "paused"
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "pause_program", program_id, program.stage, program.stage, "Program paused."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {"confirmation": f"{program_id} paused"}

    def resume_program(self, program_id: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("resume_program", program_id)
        request_ref = self._action_requested("resume_program", program_id, program.stage)
        reasons = []
        if program.stage in TERMINAL_STAGES:
            reasons.append("program_terminal")
        if program.operating_status != "paused":
            reasons.append("program_not_paused")
        if active_program_count(self._portfolio) >= self._portfolio.max_parallel_programs:
            reasons.append("max_parallel_programs_reached")
        if reasons:
            self._reject("resume_program", program_id, reasons, request_ref, stage_before=program.stage)
        program.operating_status = "active"
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "resume_program", program_id, program.stage, program.stage, "Program resumed."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {"confirmation": f"{program_id} resumed"}

    def terminate_program(self, program_id: str, reason: str | None = None) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("terminate_program", program_id)
        request_ref = self._action_requested("terminate_program", program_id, program.stage)
        reasons = []
        if program.stage in TERMINAL_STAGES:
            reasons.append("program_terminal")
        if reasons:
            self._reject("terminate_program", program_id, reasons, request_ref, stage_before=program.stage)
        stage_before = program.stage
        budget_returned = program.allocated_budget
        canceled = cancel_program_work(self._portfolio, program_id)
        program.allocated_budget = 0.0
        self._transition_stage(program, "terminated")
        program.known_findings.append(f"terminated:{reason or 'portfolio_decision'}")
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(
            self._emit_event(
                "program_terminated",
                "terminate_program",
                program_id,
                stage_before,
                program.stage,
                f"Program terminated. Reason: {reason or 'not provided'}.",
            )
        )
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {
            "termination_confirmation": f"{program_id} terminated",
            "canceled_work_summary": canceled,
            "budget_returned_to_portfolio": budget_returned,
        }

    def advance_time(self, *, months: int | None = None, to_next_event: bool = False) -> dict[str, Any]:
        portfolio = self._portfolio
        self._ensure_not_terminal("advance_time")
        request_ref = self._action_requested("advance_time", None, None)
        reasons = []
        if (months is None) == (not to_next_event):
            reasons.append("exactly_one_of_months_or_to_next_event_required")
        if months is not None and months <= 0:
            reasons.append("months_must_be_positive_integer")
        if to_next_event and not active_work_items(portfolio):
            reasons.append("no_scheduled_work")
        if reasons:
            self._reject("advance_time", None, reasons, request_ref)
        if to_next_event:
            target_month = min(
                item.expected_end_month for item in portfolio.event_queue if item.status == "scheduled"
            )
        else:
            target_month = portfolio.elapsed_months + int(months or 0)
        target_month = min(target_month, portfolio.time_budget_months)
        portfolio.elapsed_months = target_month
        due_items = [
            item
            for item in portfolio.event_queue
            if item.status == "scheduled" and item.expected_end_month <= portfolio.elapsed_months
        ]
        portfolio.event_queue = [
            item
            for item in portfolio.event_queue
            if not (item.status == "scheduled" and item.expected_end_month <= portfolio.elapsed_months)
        ]
        due_items.sort(key=lambda item: (item.expected_end_month, item.work_id))
        completed_outputs = []
        stage_transitions = []
        recent_refs = [request_ref]
        recent_refs.append(
            self._emit_event(
                "action_accepted",
                "advance_time",
                None,
                None,
                None,
                f"Clock advanced to month {portfolio.elapsed_months}.",
            )
        )
        for work_item in due_items:
            program = self._get_program(work_item.program_id)
            stage_before = program.stage
            work_item.status = "completed"
            artifact_refs: list[str] = []
            output: dict[str, Any]
            if work_item.kind == "optimize_candidate":
                output = complete_optimize_candidate(portfolio, program, work_item, self.rng)
            elif work_item.kind == "preclinical_package":
                study, report = complete_preclinical_evidence(portfolio, program, work_item, self.rng)
                artifact_refs.append(self.observability.write_report("study", study.study_id, report))
                output = {"study_summary": serialize(study)}
                recent_refs.append(
                    self._emit_event(
                        "study_completed",
                        work_item.action_name,
                        program.program_id,
                        stage_before,
                        program.stage,
                        study.summary,
                        artifact_refs=artifact_refs,
                    )
                )
            elif work_item.kind == "additional_study":
                study, report = complete_additional_study(portfolio, program, work_item, self.rng)
                artifact_refs.append(self.observability.write_report("study", study.study_id, report))
                output = {"study_summary": serialize(study)}
                recent_refs.append(
                    self._emit_event(
                        "study_completed",
                        work_item.action_name,
                        program.program_id,
                        stage_before,
                        program.stage,
                        study.summary,
                        artifact_refs=artifact_refs,
                    )
                )
            elif work_item.kind == "clinical_trial":
                result, report, catastrophic_failure = complete_trial(portfolio, program, work_item, self.rng)
                artifact_refs.append(self.observability.write_report("trial", result.trial_result_id, report))
                if work_item.payload["phase"] == "phase1":
                    self._transition_stage(program, "failed" if catastrophic_failure else "phase1_complete")
                elif work_item.payload["phase"] == "phase2":
                    self._transition_stage(program, "failed" if catastrophic_failure else "phase2_complete")
                elif work_item.payload["phase"] == "phase3":
                    self._transition_stage(program, "failed" if catastrophic_failure else "phase3_complete")
                output = {"trial_result_summary": serialize(result)}
                recent_refs.append(
                    self._emit_event(
                        "trial_completed",
                        work_item.action_name,
                        program.program_id,
                        stage_before,
                        program.stage,
                        result.topline_interpretation,
                        artifact_refs=artifact_refs,
                    )
                )
            elif work_item.kind == "regulatory_review":
                note, report, decision = complete_review(portfolio, program, work_item)
                program.regulatory_interactions.append(note)
                artifact_refs.append(self.observability.write_report("regulatory", note.note_id, report))
                if work_item.payload["review_type"] == "IND":
                    self._transition_stage(program, decision)
                else:
                    self._transition_stage(program, decision)
                output = {"regulatory_note": serialize(note)}
                recent_refs.append(
                    self._emit_event(
                        "regulatory_feedback_issued",
                        work_item.action_name,
                        program.program_id,
                        stage_before,
                        program.stage,
                        note.summary,
                        artifact_refs=artifact_refs,
                    )
                )
            else:
                raise ActionError(f"Unknown work item kind: {work_item.kind}")
            recent_refs.append(
                self._emit_event(
                    "work_completed",
                    work_item.action_name,
                    program.program_id,
                    stage_before,
                    program.stage,
                    f"Completed {work_item.kind} ({work_item.work_id}).",
                    artifact_refs=artifact_refs,
                )
            )
            refresh_all_programs(portfolio)
            if program.stage != stage_before:
                stage_transitions.append(
                    {"program_id": program.program_id, "stage_before": stage_before, "stage_after": program.stage}
                )
                recent_refs.append(
                    self._emit_event(
                        "stage_changed",
                        work_item.action_name,
                        program.program_id,
                        stage_before,
                        program.stage,
                        f"Stage advanced from {stage_before} to {program.stage}.",
                    )
                )
            recent_refs.extend(self._emit_gate_updates([program.program_id]))
            self._emit_frame(recent_refs)
            completed_outputs.append(output)
        abandoned = []
        if portfolio.elapsed_months >= portfolio.time_budget_months and active_work_items(portfolio):
            abandoned = abandon_all_work(portfolio)
        refresh_all_programs(portfolio)
        if not due_items:
            self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {
            "completed_work_outputs": completed_outputs,
            "stage_transition_summaries": stage_transitions,
            "updated_portfolio_clock": {
                "elapsed_months": portfolio.elapsed_months,
                "time_budget_months": portfolio.time_budget_months,
            },
            "abandoned_work": abandoned,
        }

    def optimize_candidate(
        self,
        program_id: str,
        objective_profile: dict[str, float],
        budget: float,
        cycles: int,
    ) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("optimize_candidate", program_id)
        request_ref = self._action_requested("optimize_candidate", program_id, program.stage)
        reasons = self._scheduled_common_reasons(
            program,
            allowed_stages={"hit_series", "lead_series"},
            budget=budget,
            workstream=WORKSTREAM_DISCOVERY,
        )
        if cycles <= 0:
            reasons.append("cycles_must_be_positive")
        if reasons:
            self._reject("optimize_candidate", program_id, reasons, request_ref, stage_before=program.stage)
        self._apply_spend(program, budget)
        duration = estimate_optimize_candidate_duration(program, cycles)
        work = schedule_work(
            self._portfolio,
            program,
            kind="optimize_candidate",
            workstream=WORKSTREAM_DISCOVERY,
            action_name="optimize_candidate",
            duration_months=duration,
            reserved_cost=budget,
            payload={"objective_profile": objective_profile, "budget": budget, "cycles": cycles},
        )
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "optimize_candidate", program_id, program.stage, program.stage, "Medicinal chemistry campaign scheduled."))
        recent_refs.append(self._emit_event("work_scheduled", "optimize_candidate", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._finalize_if_terminal(recent_refs)
        return {
            "work_id": work.work_id,
            "expected_end_month": work.expected_end_month,
            "reserved_cost": work.reserved_cost,
        }

    def generate_preclinical_evidence(self, program_id: str, candidate_id: str, package_type: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("generate_preclinical_evidence", program_id)
        request_ref = self._action_requested("generate_preclinical_evidence", program_id, program.stage)
        reasons = self._scheduled_common_reasons(
            program,
            allowed_stages={"hit_series", "lead_series", "development_candidate"},
            budget=None,
            workstream=WORKSTREAM_DISCOVERY,
        )
        if candidate_id not in program.candidate_states:
            reasons.append("candidate_not_found")
        if package_type not in {"exploratory", "translational", "IND_enabling"}:
            reasons.append("invalid_package_type")
            cost = None
            duration = None
        else:
            cost, duration = estimate_preclinical_package(program, package_type)
        if package_type == "exploratory" and program.stage not in {"hit_series", "lead_series"}:
            reasons.append("stage_incompatible_with_package")
        if package_type == "translational" and program.stage not in {"lead_series", "development_candidate"}:
            reasons.append("stage_incompatible_with_package")
        if package_type == "IND_enabling" and not (program.stage == "development_candidate" and program.active_indication is not None):
            reasons.append("stage_incompatible_with_package")
        if cost is not None and program.allocated_budget < cost:
            reasons.append("insufficient_allocated_budget")
        if reasons:
            self._reject("generate_preclinical_evidence", program_id, reasons, request_ref, stage_before=program.stage)
        self._apply_spend(program, cost)
        work = schedule_work(
            self._portfolio,
            program,
            kind="preclinical_package",
            workstream=WORKSTREAM_DISCOVERY,
            action_name="generate_preclinical_evidence",
            duration_months=duration,
            reserved_cost=cost,
            payload={"candidate_id": candidate_id, "package_type": package_type},
        )
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "generate_preclinical_evidence", program_id, program.stage, program.stage, f"{package_type} package scheduled."))
        recent_refs.append(self._emit_event("work_scheduled", "generate_preclinical_evidence", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._finalize_if_terminal(recent_refs)
        return {"work_id": work.work_id, "package_type": package_type, "expected_end_month": work.expected_end_month}

    def run_additional_study(self, program_id: str, study_type: str, parameters: dict[str, Any]) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("run_additional_study", program_id)
        request_ref = self._action_requested("run_additional_study", program_id, program.stage)
        reasons = self._scheduled_common_reasons(
            program,
            allowed_stages={program.stage},
            budget=None,
            workstream=WORKSTREAM_DISCOVERY,
        )
        allowed = STUDY_TYPE_STAGE_MAP.get(program.stage, set())
        if study_type not in allowed:
            reasons.append("study_type_not_supported_at_stage")
            cost = None
            duration = None
        else:
            cost, duration = estimate_additional_study(program, study_type)
        if cost is not None and program.allocated_budget < cost:
            reasons.append("insufficient_allocated_budget")
        if reasons:
            self._reject("run_additional_study", program_id, reasons, request_ref, stage_before=program.stage)
        self._apply_spend(program, cost)
        work = schedule_work(
            self._portfolio,
            program,
            kind="additional_study",
            workstream=WORKSTREAM_DISCOVERY,
            action_name="run_additional_study",
            duration_months=duration,
            reserved_cost=cost,
            payload={"study_type": study_type, "parameters": parameters},
        )
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "run_additional_study", program_id, program.stage, program.stage, f"{study_type} scheduled."))
        recent_refs.append(self._emit_event("work_scheduled", "run_additional_study", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._finalize_if_terminal(recent_refs)
        return {"work_id": work.work_id, "study_type": study_type, "expected_end_month": work.expected_end_month}

    def choose_indication(
        self,
        program_id: str,
        candidate_id: str,
        indication: str,
        biomarker_strategy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("choose_indication", program_id)
        request_ref = self._action_requested("choose_indication", program_id, program.stage)
        reasons = self._active_program_reasons(
            program,
            allowed_stages={"lead_series", "development_candidate", "preclinical_ready", "IND_cleared", "phase1_complete"},
        )
        if candidate_id not in program.candidate_states:
            reasons.append("candidate_not_found")
        if program.indication_locked:
            reasons.append("indication_locked")
        if any(work.workstream == WORKSTREAM_CLINICAL for work in active_work(program_id, self._portfolio.event_queue)):
            reasons.append("clinical_or_regulatory_work_already_in_progress")
        if reasons:
            self._reject("choose_indication", program_id, reasons, request_ref, stage_before=program.stage)
        changed_after_nomination = program.active_indication not in {None, indication} and program.stage != "lead_series"
        program.active_candidate_id = candidate_id
        program.active_indication = indication
        program.biomarker_strategy = biomarker_strategy
        if changed_after_nomination:
            for study in program.completed_studies:
                if study.kind == "preclinical_package" and study.package_type == "translational":
                    study.stale_for_gate = True
            for design in program.trial_designs:
                if design.phase in {"phase2", "phase3"}:
                    design.valid = False
                    design.invalid_reasons = list(dict.fromkeys(design.invalid_reasons + ["indication_changed"]))
            if "indication_changed_requires_revalidation" not in program.known_findings:
                program.known_findings.append("indication_changed_requires_revalidation")
            program.indication_locked = False
        refresh_all_programs(self._portfolio)
        enrollment = self._range_from_level(program.hidden_state.clinical_hidden["enrollment_difficulty"])
        market = self._range_from_level(program.hidden_state.strategic_hidden["market_size_base"])
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "choose_indication", program_id, program.stage, program.stage, f"Indication set to {indication}."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {
            "indication_profile_summary": f"{indication} selected for {candidate_id}",
            "enrollment_difficulty_range": enrollment,
            "endpoint_family_suggestions": list(ENDPOINT_FAMILIES),
            "market_size_range": market,
        }

    def nominate_candidate(self, program_id: str, candidate_id: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("nominate_candidate", program_id)
        request_ref = self._action_requested("nominate_candidate", program_id, program.stage)
        reasons = self._active_program_reasons(program, allowed_stages={"lead_series"})
        if candidate_id not in program.candidate_states:
            reasons.append("candidate_not_found")
        if not program.gate_status.can_nominate_candidate:
            reasons.extend(program.blocking_issues or ["nomination_gate_closed"])
        if reasons:
            self._reject("nominate_candidate", program_id, reasons, request_ref, stage_before=program.stage)
        stage_before = program.stage
        program.active_candidate_id = candidate_id
        self._transition_stage(program, "development_candidate")
        program.known_findings.append("nomination_supported")
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "nominate_candidate", program_id, stage_before, program.stage, "Candidate nominated to development candidate."))
        recent_refs.append(self._emit_event("stage_changed", "nominate_candidate", program_id, stage_before, program.stage, f"{program.program_id} advanced to development candidate."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {"nomination_memo": f"{candidate_id} nominated", "key_development_risks": list(program.blocking_issues)}

    def design_clinical_trial(
        self,
        program_id: str,
        phase: str,
        population_definition: str,
        endpoint: str,
        comparator: str,
        dose_strategy: str,
        duration: int,
        sample_size: int,
        enrichment_strategy: str | None = None,
    ) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("design_clinical_trial", program_id)
        request_ref = self._action_requested("design_clinical_trial", program_id, program.stage)
        if phase not in {"phase1", "phase2", "phase3"}:
            self._reject("design_clinical_trial", program_id, ["invalid_phase"], request_ref, stage_before=program.stage)
        compatible = {
            "phase1": {"development_candidate", "preclinical_ready"},
            "phase2": {"IND_cleared", "phase1_complete"},
            "phase3": {"phase2_complete"},
        }
        reasons = self._active_program_reasons(program, allowed_stages=compatible[phase])
        if program.active_candidate_id is None:
            reasons.append("no_active_candidate")
        if program.active_indication is None:
            reasons.append("no_active_indication")
        design, observation, fee = design_trial(
            self._portfolio,
            program,
            phase=phase,
            population_definition=population_definition,
            endpoint=endpoint,
            comparator=comparator,
            dose_strategy=dose_strategy,
            duration=duration,
            sample_size=sample_size,
            enrichment_strategy=enrichment_strategy,
        )
        if program.allocated_budget < fee:
            reasons.append("insufficient_allocated_budget")
        if reasons:
            self._reject("design_clinical_trial", program_id, reasons, request_ref, stage_before=program.stage)
        self._apply_spend(program, fee)
        program.trial_designs = [item for item in program.trial_designs if item.phase != phase]
        program.trial_designs.append(design)
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "design_clinical_trial", program_id, program.stage, program.stage, f"{phase} trial design stored."))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return observation

    def advance_program(self, program_id: str, action: str) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("advance_program", program_id)
        request_ref = self._action_requested("advance_program", program_id, program.stage)
        stage_before = program.stage
        if action == "mark_preclinical_ready":
            reasons = self._active_program_reasons(program, allowed_stages={"development_candidate"})
            if not program.gate_status.can_mark_preclinical_ready:
                reasons.extend(program.blocking_issues or ["preclinical_ready_gate_closed"])
            if reasons:
                self._reject("advance_program", program_id, reasons, request_ref, stage_before=program.stage)
            self._transition_stage(program, "preclinical_ready")
            refresh_all_programs(self._portfolio)
            recent_refs = [request_ref]
            recent_refs.append(self._emit_event("action_accepted", "advance_program", program_id, stage_before, program.stage, "Program marked preclinical ready."))
            recent_refs.append(self._emit_event("stage_changed", "advance_program", program_id, stage_before, program.stage, "Preclinical readiness confirmed."))
            recent_refs.extend(self._emit_gate_updates([program_id]))
            self._emit_frame(recent_refs)
            self._finalize_if_terminal(recent_refs)
            return {"transition": {"stage_before": stage_before, "stage_after": program.stage}}
        if action == "file_IND":
            reasons = self._active_program_reasons(program, allowed_stages={"preclinical_ready"})
            if not program.gate_status.can_file_IND:
                reasons.extend(program.blocking_issues or ["IND_gate_closed"])
            if any(work.workstream == WORKSTREAM_CLINICAL for work in active_work(program_id, self._portfolio.event_queue)):
                reasons.append("clinical_or_regulatory_work_already_in_progress")
            cost = regulatory_submission_cost(program)
            if program.allocated_budget < cost:
                reasons.append("insufficient_allocated_budget")
            if reasons:
                self._reject("advance_program", program_id, reasons, request_ref, stage_before=program.stage)
            self._apply_spend(program, cost)
            self._transition_stage(program, "ind_under_review")
            note = submission_note(self._portfolio, review_type="IND")
            program.regulatory_interactions.append(note)
            report_ref = self.observability.write_report(
                "regulatory",
                note.note_id,
                {
                    "note_id": note.note_id,
                    "program_id": program.program_id,
                    "review_type": "IND",
                    "summary": note.summary,
                    "comments": note.comments,
                    "month": note.month,
                },
            )
            work = schedule_work(
                self._portfolio,
                program,
                kind="regulatory_review",
                workstream=WORKSTREAM_CLINICAL,
                action_name="advance_program",
                duration_months=regulatory_review_duration(program),
                reserved_cost=cost,
                payload={"review_type": "IND"},
            )
            refresh_all_programs(self._portfolio)
            recent_refs = [request_ref]
            recent_refs.append(self._emit_event("action_accepted", "advance_program", program_id, stage_before, program.stage, "IND filed and review started.", artifact_refs=[report_ref]))
            recent_refs.append(self._emit_event("stage_changed", "advance_program", program_id, stage_before, program.stage, "Program moved into IND review."))
            recent_refs.append(self._emit_event("work_scheduled", "advance_program", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
            recent_refs.extend(self._emit_gate_updates([program_id]))
            self._finalize_if_terminal(recent_refs)
            return {"work_id": work.work_id, "stage_after": program.stage}
        if action in {"start_phase1", "start_phase2", "start_phase3"}:
            gate_map = {
                "start_phase1": ("phase1", {"IND_cleared"}, "can_start_phase1", "phase1_in_progress"),
                "start_phase2": ("phase2", {"phase1_complete"}, "can_start_phase2", "phase2_in_progress"),
                "start_phase3": ("phase3", {"phase2_complete"}, "can_start_phase3", "phase3_in_progress"),
            }
            phase, stages, gate_attr, next_stage = gate_map[action]
            reasons = self._active_program_reasons(program, allowed_stages=stages)
            if not getattr(program.gate_status, gate_attr):
                reasons.extend(program.blocking_issues or [f"{action}_gate_closed"])
            if any(work.workstream == WORKSTREAM_CLINICAL for work in active_work(program_id, self._portfolio.event_queue)):
                reasons.append("clinical_or_regulatory_work_already_in_progress")
            design = program.latest_valid_design(phase)
            if design is None:
                reasons.append(f"missing_valid_{phase}_design")
            cost = design.projected_cost if design is not None else 0.0
            if program.allocated_budget < cost:
                reasons.append("insufficient_allocated_budget")
            if reasons:
                self._reject("advance_program", program_id, reasons, request_ref, stage_before=program.stage)
            self._apply_spend(program, cost)
            if action == "start_phase2":
                program.indication_locked = True
            self._transition_stage(program, next_stage)
            work = schedule_work(
                self._portfolio,
                program,
                kind="clinical_trial",
                workstream=WORKSTREAM_CLINICAL,
                action_name="advance_program",
                duration_months=phase_trial_duration(program, phase, design),
                reserved_cost=cost,
                payload={"phase": phase, "design": serialize(design)},
            )
            refresh_all_programs(self._portfolio)
            recent_refs = [request_ref]
            recent_refs.append(self._emit_event("action_accepted", "advance_program", program_id, stage_before, program.stage, f"{phase} started."))
            recent_refs.append(self._emit_event("stage_changed", "advance_program", program_id, stage_before, program.stage, f"Program moved into {phase} execution."))
            recent_refs.append(self._emit_event("work_scheduled", "advance_program", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
            recent_refs.extend(self._emit_gate_updates([program_id]))
            self._finalize_if_terminal(recent_refs)
            return {"work_id": work.work_id, "stage_after": program.stage}
        if action == "submit_NDA":
            reasons = self._active_program_reasons(program, allowed_stages={"phase3_complete"})
            if not program.gate_status.can_submit_NDA:
                reasons.extend(program.blocking_issues or ["NDA_gate_closed"])
            if any(work.workstream == WORKSTREAM_CLINICAL for work in active_work(program_id, self._portfolio.event_queue)):
                reasons.append("clinical_or_regulatory_work_already_in_progress")
            cost = regulatory_submission_cost(program)
            if program.allocated_budget < cost:
                reasons.append("insufficient_allocated_budget")
            if reasons:
                self._reject("advance_program", program_id, reasons, request_ref, stage_before=program.stage)
            self._apply_spend(program, cost)
            self._transition_stage(program, "nda_under_review")
            note = submission_note(self._portfolio, review_type="NDA")
            program.regulatory_interactions.append(note)
            report_ref = self.observability.write_report(
                "regulatory",
                note.note_id,
                {
                    "note_id": note.note_id,
                    "program_id": program.program_id,
                    "review_type": "NDA",
                    "summary": note.summary,
                    "comments": note.comments,
                    "month": note.month,
                },
            )
            work = schedule_work(
                self._portfolio,
                program,
                kind="regulatory_review",
                workstream=WORKSTREAM_CLINICAL,
                action_name="advance_program",
                duration_months=regulatory_review_duration(program),
                reserved_cost=cost,
                payload={"review_type": "NDA"},
            )
            refresh_all_programs(self._portfolio)
            recent_refs = [request_ref]
            recent_refs.append(self._emit_event("action_accepted", "advance_program", program_id, stage_before, program.stage, "NDA submitted.", artifact_refs=[report_ref]))
            recent_refs.append(self._emit_event("stage_changed", "advance_program", program_id, stage_before, program.stage, "Program moved into NDA review."))
            recent_refs.append(self._emit_event("work_scheduled", "advance_program", program_id, program.stage, program.stage, f"{work.work_id} scheduled through month {work.expected_end_month}."))
            recent_refs.extend(self._emit_gate_updates([program_id]))
            self._finalize_if_terminal(recent_refs)
            return {"work_id": work.work_id, "stage_after": program.stage}
        raise ActionError(f"Unsupported advance action: {action}")

    def request_regulatory_feedback(self, program_id: str, question_set: list[str]) -> dict[str, Any]:
        program = self._get_program(program_id)
        self._ensure_not_terminal("request_regulatory_feedback", program_id)
        request_ref = self._action_requested("request_regulatory_feedback", program_id, program.stage)
        reasons = self._active_program_reasons(
            program,
            allowed_stages={"development_candidate", "preclinical_ready", "IND_cleared", "phase1_complete", "phase2_complete", "phase3_complete"},
        )
        fee = regulatory_feedback_fee(question_set)
        if program.allocated_budget < fee:
            reasons.append("insufficient_allocated_budget")
        if reasons:
            self._reject("request_regulatory_feedback", program_id, reasons, request_ref, stage_before=program.stage)
        self._apply_spend(program, fee)
        note, report = instant_feedback(self._portfolio, program, question_set)
        program.regulatory_interactions.append(note)
        report_ref = self.observability.write_report("regulatory", note.note_id, report)
        refresh_all_programs(self._portfolio)
        recent_refs = [request_ref]
        recent_refs.append(self._emit_event("action_accepted", "request_regulatory_feedback", program_id, program.stage, program.stage, "Regulatory feedback requested.", artifact_refs=[report_ref]))
        recent_refs.append(self._emit_event("regulatory_feedback_issued", "request_regulatory_feedback", program_id, program.stage, program.stage, note.summary, artifact_refs=[report_ref]))
        recent_refs.extend(self._emit_gate_updates([program_id]))
        self._emit_frame(recent_refs)
        self._finalize_if_terminal(recent_refs)
        return {
            "regulator_minutes_summary": note.summary,
            "comments": note.comments,
        }

    def get_artifact_manifest(self) -> dict[str, Any]:
        return self.observability.get_manifest()

    @property
    def _portfolio(self) -> PortfolioState:
        if self.portfolio is None:
            raise RuntimeError("Simulator not initialized.")
        return self.portfolio

    def _build_run_id(self) -> str:
        seed_material = f"{self.config.scenario_preset}|{self.config.seed}|{self.config.initial_cash}|{self.config.time_budget_months}|{self.config.max_parallel_programs}"
        digest = hashlib.sha1(seed_material.encode("utf-8")).hexdigest()[:12]
        return f"run-{self.config.scenario_preset}-{digest}"

    def _get_program(self, program_id: str) -> ProgramState:
        program = self._portfolio.programs.get(program_id)
        if program is None:
            raise ActionError(f"Unknown program_id: {program_id}")
        return program

    def _apply_spend(self, program: ProgramState, amount: float) -> None:
        program.allocated_budget -= amount
        program.total_spend += amount
        self._portfolio.cash_on_hand -= amount

    def _transition_stage(self, program: ProgramState, new_stage: str) -> None:
        if new_stage == program.stage:
            return
        if new_stage not in ALLOWED_STAGE_TRANSITIONS[program.stage]:
            raise ActionError(f"Illegal stage transition {program.stage} -> {new_stage}")
        program.stage = new_stage
        if new_stage in TERMINAL_STAGES:
            program.operating_status = None
        if not new_stage.endswith("_in_progress") and new_stage not in {"ind_under_review", "nda_under_review"}:
            program.last_completed_stage = new_stage

    def _action_requested(self, action_name: str, program_id: str | None, stage_before: str | None) -> int:
        return self._emit_event(
            "action_requested",
            action_name,
            program_id,
            stage_before,
            stage_before,
            f"{action_name} requested.",
        )

    def _reject(
        self,
        action_name: str,
        program_id: str | None,
        reasons: list[str],
        request_ref: int,
        stage_before: str | None = None,
    ) -> None:
        self._emit_event(
            "action_rejected",
            action_name,
            program_id,
            stage_before,
            stage_before,
            "Action rejected.",
            blocking_reasons=reasons,
        )
        raise ActionError("; ".join(dict.fromkeys(reasons)))

    def _emit_event(
        self,
        event_type: str,
        action_name: str | None,
        program_id: str | None,
        stage_before: str | None,
        stage_after: str | None,
        summary: str,
        blocking_reasons: list[str] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> int:
        return self.observability.emit_event(
            elapsed_months=self._portfolio.elapsed_months,
            event_type=event_type,
            action_name=action_name,
            program_id=program_id,
            stage_before=stage_before,
            stage_after=stage_after,
            summary=summary,
            blocking_reasons=blocking_reasons,
            artifact_refs=artifact_refs,
        )

    def _emit_gate_updates(self, program_ids: list[str]) -> list[int]:
        refs = []
        for program_id in program_ids:
            if program_id not in self._portfolio.programs:
                continue
            program = self._portfolio.programs[program_id]
            refs.append(
                self._emit_event(
                    "gate_status_updated",
                    None,
                    program_id,
                    program.stage,
                    program.stage,
                    f"Gate status updated: {serialize(program.gate_status)} with blockers {program.blocking_issues}",
                )
            )
        return refs

    def _emit_frame(self, recent_event_refs: list[int]) -> None:
        self.observability.emit_frame(
            elapsed_months=self._portfolio.elapsed_months,
            portfolio_snapshot={
                "cash_on_hand": self._portfolio.cash_on_hand,
                "unallocated_cash": self._portfolio.unallocated_cash,
                "reported_metrics": self._portfolio.reported_metrics,
            },
            program_snapshots=[
                observable_state(program, self._portfolio.event_queue)
                for program in self._portfolio.programs.values()
            ],
            visible_opportunities=serialize(self._portfolio.visible_opportunities),
            active_work=serialize(active_work_items(self._portfolio)),
            recent_event_refs=recent_event_refs,
        )

    def _finalize_if_terminal(self, recent_event_refs: list[int]) -> None:
        portfolio = self._portfolio
        if portfolio.terminal:
            return
        reason = None
        if portfolio.elapsed_months >= portfolio.time_budget_months:
            reason = "time_budget_reached"
        elif (
            not active_work_items(portfolio)
            and not self._state_changing_actions_excluding_time()
        ):
            reason = "no_legal_actions_remaining"
        elif (
            portfolio.cash_on_hand <= 0
            and not active_work_items(portfolio)
            and not any(descriptor["action"] == "launch_program" for descriptor in self.get_available_actions(include_blocked=False))
        ):
            reason = "cash_exhausted"
        if reason is None:
            return
        portfolio.terminal = True
        portfolio.terminal_reason = reason
        refresh_all_programs(portfolio)
        recent_event_refs.append(
            self._emit_event(
                "simulation_terminated",
                None,
                None,
                None,
                None,
                f"Simulation terminated: {reason}",
            )
        )
        self._emit_frame(recent_event_refs)
        self.observability.finalize(
            {
                "terminal_portfolio_state": self.get_portfolio_state(),
                "reported_metrics": portfolio.reported_metrics,
                "program_terminal_summaries": [
                    program_terminal_summary(program, portfolio.elapsed_months)
                    for program in portfolio.programs.values()
                    if program.stage in TERMINAL_STAGES
                ],
                "artifact_counts": {
                    "event_count": self.observability.event_index,
                    "frame_count": self.observability.frame_index,
                    "report_count": len(self.observability.report_refs),
                },
                "run_end_reason": reason,
                "final_event_index": self.observability.event_index - 1,
                "final_frame_index": self.observability.frame_index - 1,
            }
        )

    def _state_changing_actions_excluding_time(self) -> list[dict[str, Any]]:
        actions = self.get_available_actions(include_blocked=False)
        excluded = {"get_portfolio_state", "get_program_state", "get_available_actions", "advance_time"}
        return [action for action in actions if action["action"] not in excluded]

    def _ensure_not_terminal(self, action_name: str, program_id: str | None = None) -> None:
        if self._portfolio.terminal:
            raise ActionError(f"{action_name} is invalid after termination. Only read actions are allowed.")

    def _active_program_reasons(self, program: ProgramState, *, allowed_stages: set[str]) -> list[str]:
        reasons = []
        if program.stage in TERMINAL_STAGES:
            reasons.append("program_terminal")
        if program.operating_status != "active":
            reasons.append("program_paused")
        if program.stage not in allowed_stages:
            reasons.append("stage_incompatible")
        return reasons

    def _scheduled_common_reasons(
        self,
        program: ProgramState,
        *,
        allowed_stages: set[str],
        budget: float | None,
        workstream: str,
    ) -> list[str]:
        reasons = self._active_program_reasons(program, allowed_stages=allowed_stages)
        if budget is not None and budget <= 0:
            reasons.append("budget_must_be_positive")
        if budget is not None and budget > program.allocated_budget:
            reasons.append("insufficient_allocated_budget")
        if any(work.workstream == workstream for work in active_work(program.program_id, self._portfolio.event_queue)):
            reasons.append(
                "discovery_or_preclinical_work_already_in_progress"
                if workstream == WORKSTREAM_DISCOVERY
                else "clinical_or_regulatory_work_already_in_progress"
            )
        return reasons

    def _global_actions(self, include_blocked: bool) -> list[ActionDescriptor]:
        portfolio = self._portfolio
        descriptors = [
            ActionDescriptor("get_portfolio_state", None, [], "instant", None, None, []),
            ActionDescriptor("get_available_actions", None, ["program_id", "include_blocked"], "instant", None, None, []),
            ActionDescriptor("get_program_state", None, ["program_id"], "instant", None, None, []),
        ]
        launch_reasons = []
        if not portfolio.visible_opportunities:
            launch_reasons.append("no_visible_opportunities")
        if portfolio.unallocated_cash <= 0:
            launch_reasons.append("no_unallocated_cash")
        if active_program_count(portfolio) >= portfolio.max_parallel_programs:
            launch_reasons.append("max_parallel_programs_reached")
        descriptors.append(
            ActionDescriptor(
                "launch_program",
                None,
                ["opportunity_id", "initial_budget"],
                "instant",
                None,
                None,
                launch_reasons,
            )
        )
        allocate_reasons = []
        if not any(program.stage not in TERMINAL_STAGES for program in portfolio.programs.values()):
            allocate_reasons.append("no_nonterminal_programs")
        descriptors.append(
            ActionDescriptor("allocate_budget", None, ["program_allocations"], "instant", None, None, allocate_reasons)
        )
        descriptors.append(
            ActionDescriptor(
                "advance_time",
                None,
                ["months"],
                "instant",
                None,
                None,
                [] if portfolio.elapsed_months < portfolio.time_budget_months else ["time_budget_reached"],
            )
        )
        descriptors.append(
            ActionDescriptor(
                "advance_time",
                None,
                ["to_next_event"],
                "instant",
                None,
                None,
                [] if active_work_items(portfolio) else ["no_scheduled_work"],
            )
        )
        return descriptors if include_blocked else [descriptor for descriptor in descriptors if not descriptor.blocking_reasons]

    def _program_actions(self, program: ProgramState, include_blocked: bool) -> list[ActionDescriptor]:
        descriptors: list[ActionDescriptor] = [
            ActionDescriptor("get_program_state", program.program_id, [], "instant", None, None, []),
        ]
        pause_reasons = []
        if program.stage in TERMINAL_STAGES:
            pause_reasons.append("program_terminal")
        if program.operating_status != "active":
            pause_reasons.append("program_not_active")
        resume_reasons = []
        if program.stage in TERMINAL_STAGES:
            resume_reasons.append("program_terminal")
        if program.operating_status != "paused":
            resume_reasons.append("program_not_paused")
        if active_program_count(self._portfolio) >= self._portfolio.max_parallel_programs and program.operating_status == "paused":
            resume_reasons.append("max_parallel_programs_reached")
        terminate_reasons = ["program_terminal"] if program.stage in TERMINAL_STAGES else []
        descriptors.extend(
            [
                ActionDescriptor("pause_program", program.program_id, [], "instant", None, None, pause_reasons),
                ActionDescriptor("resume_program", program.program_id, [], "instant", None, None, resume_reasons),
                ActionDescriptor("terminate_program", program.program_id, ["reason"], "instant", None, None, terminate_reasons),
            ]
        )
        descriptors.append(
            ActionDescriptor(
                "optimize_candidate",
                program.program_id,
                ["objective_profile", "budget", "cycles"],
                "scheduled",
                COST_BANDS["optimize_candidate"],
                (3, 9),
                self._scheduled_common_reasons(
                    program,
                    allowed_stages={"hit_series", "lead_series"},
                    budget=max(1.0, program.allocated_budget),
                    workstream=WORKSTREAM_DISCOVERY,
                ),
            )
        )
        for package_type in ("exploratory", "translational", "IND_enabling"):
            reasons = self._active_program_reasons(program, allowed_stages={"hit_series", "lead_series", "development_candidate"})
            if package_type == "IND_enabling" and program.stage != "development_candidate":
                reasons.append("stage_incompatible_with_package")
            descriptors.append(
                ActionDescriptor(
                    f"generate_preclinical_evidence:{package_type}",
                    program.program_id,
                    ["candidate_id"],
                    "scheduled",
                    COST_BANDS[{"exploratory": "exploratory_preclinical", "translational": "translational_preclinical", "IND_enabling": "IND_enabling"}[package_type]],
                    None,
                    list(dict.fromkeys(reasons + (["insufficient_allocated_budget"] if program.allocated_budget <= 0 else []))),
                )
            )
        for study_type in sorted(set().union(*STUDY_TYPE_STAGE_MAP.values())):
            reasons = []
            if study_type not in STUDY_TYPE_STAGE_MAP.get(program.stage, set()):
                reasons.append("study_type_not_supported_at_stage")
            if program.operating_status != "active":
                reasons.append("program_paused")
            if any(work.workstream == WORKSTREAM_DISCOVERY for work in active_work(program.program_id, self._portfolio.event_queue)):
                reasons.append("discovery_or_preclinical_work_already_in_progress")
            descriptors.append(
                ActionDescriptor(
                    f"run_additional_study:{study_type}",
                    program.program_id,
                    ["parameters"],
                    "scheduled",
                    None,
                    None,
                    reasons,
                )
            )
        choose_reasons = self._active_program_reasons(program, allowed_stages={"lead_series", "development_candidate", "preclinical_ready", "IND_cleared", "phase1_complete"})
        if program.indication_locked:
            choose_reasons.append("indication_locked")
        descriptors.append(
            ActionDescriptor("choose_indication", program.program_id, ["candidate_id", "indication", "biomarker_strategy"], "instant", None, None, choose_reasons)
        )
        descriptors.append(
            ActionDescriptor("nominate_candidate", program.program_id, ["candidate_id"], "instant", None, None, [] if program.gate_status.can_nominate_candidate else list(program.blocking_issues))
        )
        for phase in ("phase1", "phase2", "phase3"):
            compatible = {
                "phase1": {"development_candidate", "preclinical_ready"},
                "phase2": {"IND_cleared", "phase1_complete"},
                "phase3": {"phase2_complete"},
            }[phase]
            reasons = self._active_program_reasons(program, allowed_stages=compatible)
            if program.active_candidate_id is None:
                reasons.append("no_active_candidate")
            if program.active_indication is None:
                reasons.append("no_active_indication")
            descriptors.append(
                ActionDescriptor(
                    f"design_clinical_trial:{phase}",
                    program.program_id,
                    ["population_definition", "endpoint", "comparator", "dose_strategy", "duration", "sample_size", "enrichment_strategy"],
                    "instant",
                    COST_BANDS["design_trial"],
                    None,
                    list(dict.fromkeys(reasons)),
                )
            )
        advance_actions = {
            "mark_preclinical_ready": program.gate_status.can_mark_preclinical_ready,
            "file_IND": program.gate_status.can_file_IND,
            "start_phase1": program.gate_status.can_start_phase1,
            "start_phase2": program.gate_status.can_start_phase2,
            "start_phase3": program.gate_status.can_start_phase3,
            "submit_NDA": program.gate_status.can_submit_NDA,
        }
        for name, gate_open in advance_actions.items():
            descriptors.append(
                ActionDescriptor(
                    f"advance_program:{name}",
                    program.program_id,
                    [],
                    "scheduled" if name != "mark_preclinical_ready" else "instant",
                    None,
                    None,
                    [] if gate_open else list(program.blocking_issues),
                )
            )
        feedback_reasons = self._active_program_reasons(program, allowed_stages={"development_candidate", "preclinical_ready", "IND_cleared", "phase1_complete", "phase2_complete", "phase3_complete"})
        descriptors.append(
            ActionDescriptor("request_regulatory_feedback", program.program_id, ["question_set"], "instant", COST_BANDS["regulatory_feedback"], None, feedback_reasons)
        )
        return descriptors if include_blocked else [descriptor for descriptor in descriptors if not descriptor.blocking_reasons]

    def _range_from_level(self, level: float) -> tuple[float, float]:
        lower = round(max(0.0, level - 0.12), 2)
        upper = round(min(1.0, level + 0.12), 2)
        return (lower, upper)
