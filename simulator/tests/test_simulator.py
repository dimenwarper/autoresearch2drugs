from __future__ import annotations

import json
import unittest

from simulator import ActionError, DrugDevelopmentSimulator
from simulator.constants import ALLOWED_STAGE_TRANSITIONS, STAGES, TERMINAL_STAGES
from simulator.models import StudySummary, TrialDesignSummary
from simulator.portfolio import refresh_all_programs


def _launch_program(sim: DrugDevelopmentSimulator, budget: float = 80_000_000.0) -> str:
    opportunity_id = sim.get_portfolio_state()["visible_opportunities"][0]["opportunity_id"]
    return sim.launch_program(opportunity_id, budget)["program_summary"]["program_id"]


class StageGraphTests(unittest.TestCase):
    def test_stage_graph_points_only_to_declared_stages(self) -> None:
        declared = set(STAGES)
        for stage, targets in ALLOWED_STAGE_TRANSITIONS.items():
            self.assertIn(stage, declared)
            for target in targets:
                self.assertIn(target, declared)
        for terminal in TERMINAL_STAGES:
            self.assertEqual(ALLOWED_STAGE_TRANSITIONS[terminal], [])

    def test_illegal_transition_is_rejected(self) -> None:
        sim = DrugDevelopmentSimulator()
        program_id = _launch_program(sim)
        program = sim.portfolio.programs[program_id]
        with self.assertRaises(ActionError):
            sim._transition_stage(program, "approved")


class GateRuleTests(unittest.TestCase):
    def test_nomination_gate_requires_candidate_indication_and_package(self) -> None:
        sim = DrugDevelopmentSimulator()
        program_id = _launch_program(sim)
        program = sim.portfolio.programs[program_id]
        best = max(program.candidate_states.values(), key=lambda item: sum(item.observed_profile.values()))
        best_id = best.compound_id
        best.observed_profile.update(
            {
                "potency_estimate": 0.78,
                "selectivity_estimate": 0.74,
                "bioavailability_estimate": 0.68,
                "safety_margin_estimate": 0.72,
                "developability_estimate": 0.70,
            }
        )
        program.stage = "lead_series"
        program.active_candidate_id = best_id
        program.active_indication = "solid_tumor"
        program.manufacturing_status = "acceptable"
        program.nonclinical_safety_status = "acceptable"
        program.completed_studies.append(
            StudySummary(
                study_id="study-test",
                kind="preclinical_package",
                package_type="translational",
                candidate_id=best_id,
                summary="translational package",
            )
        )
        refresh_all_programs(sim.portfolio)
        self.assertTrue(program.gate_status.can_nominate_candidate)
        program.completed_studies.clear()
        refresh_all_programs(sim.portfolio)
        self.assertFalse(program.gate_status.can_nominate_candidate)
        self.assertIn("missing_exploratory_or_translational_package", program.blocking_issues)

    def test_file_ind_gate_requires_ind_enabling_and_valid_phase1_design(self) -> None:
        sim = DrugDevelopmentSimulator()
        program_id = _launch_program(sim)
        program = sim.portfolio.programs[program_id]
        best = max(program.candidate_states.values(), key=lambda item: sum(item.observed_profile.values()))
        best_id = best.compound_id
        program.stage = "preclinical_ready"
        program.active_candidate_id = best_id
        program.active_indication = "solid_tumor"
        program.manufacturing_status = "acceptable"
        program.nonclinical_safety_status = "acceptable"
        program.completed_studies.append(
            StudySummary(
                study_id="study-ind",
                kind="preclinical_package",
                package_type="IND_enabling",
                candidate_id=best_id,
                summary="IND enabling package",
            )
        )
        program.trial_designs.append(
            TrialDesignSummary(
                design_id="design-phase1",
                phase="phase1",
                population_definition="fih_population",
                endpoint="objective_biomarker",
                comparator="placebo",
                dose_strategy="moderate_escalation",
                duration=6,
                sample_size=40,
                enrichment_strategy=None,
                valid=True,
                invalid_reasons=[],
                protocol_complete=True,
                supports_starting_dose_rationale=True,
                projected_cost=9_000_000.0,
                projected_duration_range=(6, 9),
                projected_enrollment_rate=0.8,
                estimated_power_range=(0.4, 0.6),
                interpretability_score=0.6,
                regulatory_credibility_score=0.7,
                created_month=0,
            )
        )
        refresh_all_programs(sim.portfolio)
        self.assertTrue(program.gate_status.can_file_IND)
        program.trial_designs.clear()
        refresh_all_programs(sim.portfolio)
        self.assertFalse(program.gate_status.can_file_IND)
        self.assertIn("missing_valid_phase1_design", program.blocking_issues)


class EventQueueTests(unittest.TestCase):
    def test_due_work_completes_in_expected_end_month_then_work_id_order(self) -> None:
        sim = DrugDevelopmentSimulator()
        first_program = _launch_program(sim)
        second_program = _launch_program(sim)
        sim.optimize_candidate(
            first_program,
            {"potency": 0.4, "selectivity": 0.2, "pk": 0.2, "safety_margin": 0.1, "developability": 0.1},
            4_000_000.0,
            2,
        )
        sim.optimize_candidate(
            second_program,
            {"potency": 0.4, "selectivity": 0.2, "pk": 0.2, "safety_margin": 0.1, "developability": 0.1},
            4_000_000.0,
            2,
        )
        for work in sim.portfolio.event_queue:
            work.expected_end_month = 5
        sim.advance_time(months=5)
        with sim.observability.event_log_path.open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle]
        completed = [record for record in records if record["event_type"] == "work_completed"]
        self.assertGreaterEqual(len(completed), 2)
        self.assertEqual([item["summary"].split("(")[1].split(")")[0] for item in completed[:2]], ["work-0001", "work-0002"])
        self.assertEqual(sim.get_portfolio_state()["in_progress_events"], [])


class BudgetInvariantTests(unittest.TestCase):
    def test_budget_invariant_holds_across_launch_spend_reallocate_and_terminate(self) -> None:
        sim = DrugDevelopmentSimulator()
        self._assert_invariant(sim)
        program_id = _launch_program(sim, budget=50_000_000.0)
        self._assert_invariant(sim)
        sim.optimize_candidate(
            program_id,
            {"potency": 0.4, "selectivity": 0.2, "pk": 0.2, "safety_margin": 0.1, "developability": 0.1},
            4_000_000.0,
            2,
        )
        self._assert_invariant(sim)
        sim.allocate_budget({program_id: 40_000_000.0})
        self._assert_invariant(sim)
        sim.terminate_program(program_id, reason="portfolio_cleanup")
        self._assert_invariant(sim)

    def _assert_invariant(self, sim: DrugDevelopmentSimulator) -> None:
        portfolio = sim.get_portfolio_state()
        allocated = sum(
            summary["allocated_budget"]
            for summary in portfolio["program_summaries"]
            if summary["stage"] not in TERMINAL_STAGES
        )
        self.assertAlmostEqual(portfolio["cash_on_hand"], portfolio["unallocated_cash"] + allocated)


class ObservableActionLegalityTests(unittest.TestCase):
    def test_action_menu_is_unchanged_by_hidden_state_only_edits(self) -> None:
        sim = DrugDevelopmentSimulator()
        program_id = _launch_program(sim)
        before = sim.get_available_actions(program_id=program_id, include_blocked=True)
        program = sim.portfolio.programs[program_id]
        program.hidden_state.biology_hidden["target_validity"] = 0.0
        program.hidden_state.strategic_hidden["regulatory_strictness"] = 1.0
        refresh_all_programs(sim.portfolio)
        after = sim.get_available_actions(program_id=program_id, include_blocked=True)
        self.assertEqual(before, after)

    def test_terminal_state_only_exposes_read_actions(self) -> None:
        sim = DrugDevelopmentSimulator(time_budget_months=1)
        sim.advance_time(months=1)
        actions = sim.get_available_actions(include_blocked=True)
        self.assertEqual(
            {action["action"] for action in actions},
            {"get_portfolio_state", "get_program_state", "get_available_actions"},
        )


if __name__ == "__main__":
    unittest.main()
