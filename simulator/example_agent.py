from __future__ import annotations

from typing import Any

from .api import DrugDevelopmentSimulator


def run_example_policy(
    *,
    seed: int = 7,
    scenario_preset: str = "clean_winner",
    max_steps: int = 64,
) -> dict[str, Any]:
    sim = DrugDevelopmentSimulator(seed=seed, scenario_preset=scenario_preset)
    steps = 0
    while sim.get_portfolio_state()["reported_metrics"]["terminal_portfolio_status"] == "ongoing" and steps < max_steps:
        portfolio = sim.get_portfolio_state()
        active_programs = [
            summary["program_id"]
            for summary in portfolio["program_summaries"]
            if summary["operating_status"] == "active"
        ]
        if not active_programs and portfolio["visible_opportunities"] and portfolio["unallocated_cash"] >= 40_000_000:
            attractive = next(
                (opp for opp in portfolio["visible_opportunities"] if opp["priority_tier"] == "attractive"),
                portfolio["visible_opportunities"][0],
            )
            sim.launch_program(attractive["opportunity_id"], 80_000_000.0)
            steps += 1
            continue
        acted = False
        for program_id in active_programs:
            state = sim.get_program_state(program_id)
            if state["in_progress_work"]:
                continue
            if state["allocated_budget"] < 25_000_000.0 and portfolio["unallocated_cash"] > 20_000_000.0:
                top_up = min(60_000_000.0, portfolio["unallocated_cash"])
                sim.allocate_budget({program_id: state["allocated_budget"] + top_up})
                acted = True
                break
            if state["stage"] == "hit_series":
                sim.optimize_candidate(
                    program_id,
                    {"potency": 0.35, "selectivity": 0.20, "pk": 0.20, "safety_margin": 0.15, "developability": 0.10},
                    4_000_000.0,
                    2,
                )
                acted = True
                break
            if state["stage"] == "lead_series":
                best_candidate = max(state["candidate_summaries"], key=lambda item: sum(item["observed_profile"].values()))
                if state["active_indication"] is None:
                    sim.choose_indication(program_id, best_candidate["compound_id"], "target_enriched_population", {"validated": False})
                elif not any(study["package_type"] == "translational" for study in state["completed_studies"] if study["kind"] == "preclinical_package"):
                    sim.generate_preclinical_evidence(program_id, best_candidate["compound_id"], "translational")
                elif state["gate_status"]["can_nominate_candidate"]:
                    sim.nominate_candidate(program_id, best_candidate["compound_id"])
                else:
                    sim.optimize_candidate(
                        program_id,
                        {"potency": 0.25, "selectivity": 0.20, "pk": 0.20, "safety_margin": 0.20, "developability": 0.15},
                        3_000_000.0,
                        1,
                    )
                acted = True
                break
            if state["stage"] == "development_candidate":
                if not any(study["package_type"] == "IND_enabling" for study in state["completed_studies"] if study["kind"] == "preclinical_package"):
                    sim.generate_preclinical_evidence(program_id, state["active_candidate_id"], "IND_enabling")
                elif not any(design["phase"] == "phase1" and design["valid"] for design in state["trial_designs"]):
                    sim.design_clinical_trial(
                        program_id,
                        "phase1",
                        "dose_escalation_then_expansion",
                        "objective_biomarker",
                        "placebo",
                        "moderate_escalation",
                        6,
                        48,
                    )
                elif state["gate_status"]["can_mark_preclinical_ready"]:
                    sim.advance_program(program_id, "mark_preclinical_ready")
                else:
                    sim.run_additional_study(program_id, "biomarker_validation", {"assay": "circulating_marker"})
                acted = True
                break
            if state["stage"] == "preclinical_ready":
                if state["gate_status"]["can_file_IND"]:
                    sim.advance_program(program_id, "file_IND")
                else:
                    sim.request_regulatory_feedback(program_id, ["endpoint acceptability", "starting dose rationale"])
                acted = True
                break
            if state["stage"] == "IND_cleared":
                if not any(design["phase"] == "phase1" and design["valid"] for design in state["trial_designs"]):
                    sim.design_clinical_trial(
                        program_id,
                        "phase1",
                        "dose_escalation_then_expansion",
                        "objective_biomarker",
                        "placebo",
                        "moderate_escalation",
                        6,
                        48,
                    )
                elif state["gate_status"]["can_start_phase1"]:
                    sim.advance_program(program_id, "start_phase1")
                acted = True
                break
            if state["stage"] == "phase1_complete":
                if not any(design["phase"] == "phase2" and design["valid"] for design in state["trial_designs"]):
                    sim.design_clinical_trial(
                        program_id,
                        "phase2",
                        "biomarker_enriched_population",
                        "binary_response",
                        "standard_of_care",
                        "adaptive_mid_dose",
                        12,
                        140,
                        "biomarker_positive",
                    )
                elif state["gate_status"]["can_start_phase2"]:
                    sim.advance_program(program_id, "start_phase2")
                else:
                    sim.run_additional_study(program_id, "dose_finding_substudy", {"cohort_count": 2})
                acted = True
                break
            if state["stage"] == "phase2_complete":
                if not any(design["phase"] == "phase3" and design["valid"] for design in state["trial_designs"]):
                    sim.design_clinical_trial(
                        program_id,
                        "phase3",
                        "broad_intent_to_treat",
                        "survival_or_event",
                        "standard_of_care",
                        "commercial_dose",
                        24,
                        420,
                    )
                elif state["gate_status"]["can_start_phase3"]:
                    sim.advance_program(program_id, "start_phase3")
                acted = True
                break
            if state["stage"] == "phase3_complete" and state["gate_status"]["can_submit_NDA"]:
                sim.advance_program(program_id, "submit_NDA")
                acted = True
                break
        if not acted:
            try:
                sim.advance_time(to_next_event=True)
            except Exception:
                sim.advance_time(months=1)
        steps += 1
        if sim.get_portfolio_state()["reported_metrics"]["terminal_portfolio_status"] != "ongoing":
            break
    return {
        "portfolio_state": sim.get_portfolio_state(),
        "artifact_manifest": sim.get_artifact_manifest(),
    }
