from __future__ import annotations

import random
from typing import Any

from .constants import ADDITIONAL_STUDY_SPECS, COST_BANDS, DURATION_BANDS
from .models import CandidateState, PortfolioState, ProgramState, StudySummary
from .portfolio import next_identifier
from .program import meets_lead_entry_threshold
from .world import clamp01


def _refresh_candidate_observation(rng: random.Random, candidate: CandidateState) -> None:
    candidate.observed_profile = {
        key: clamp01(rng.gauss(value, 0.05 if "developability" not in key else 0.06))
        for key, value in candidate.truth_profile.items()
    }


def _objective_weights(objective_profile: dict[str, float]) -> dict[str, float]:
    normalized = {
        "potency": max(0.0, float(objective_profile.get("potency", 0.0))),
        "selectivity": max(0.0, float(objective_profile.get("selectivity", 0.0))),
        "pk": max(0.0, float(objective_profile.get("pk", 0.0))),
        "safety_margin": max(0.0, float(objective_profile.get("safety_margin", 0.0))),
        "developability": max(0.0, float(objective_profile.get("developability", 0.0))),
    }
    total = sum(normalized.values())
    if total <= 0:
        return {key: 0.2 for key in normalized}
    return {key: value / total for key, value in normalized.items()}


def estimate_optimize_candidate_duration(program: ProgramState, cycles: int) -> int:
    minimum, maximum = DURATION_BANDS["optimize_candidate"]
    burden = (
        program.hidden_state.candidate_hidden["formulation_risk"]
        + program.hidden_state.candidate_hidden["process_risk"]
    ) / 2.0
    duration = minimum + int(round((maximum - minimum) * min(1.0, 0.25 * cycles + 0.6 * burden)))
    return max(minimum, min(maximum, duration))


def complete_optimize_candidate(
    portfolio: PortfolioState,
    program: ProgramState,
    work_item,
    rng: random.Random,
) -> dict[str, Any]:
    objective_profile = work_item.payload["objective_profile"]
    budget = float(work_item.payload["budget"])
    cycles = int(work_item.payload["cycles"])
    weights = _objective_weights(objective_profile)
    improvement_scale = min(0.18, 0.04 + budget / 55_000_000.0 + 0.015 * cycles)
    improvement_scale /= 1.0 + 0.35 * program.optimization_campaigns
    best_candidates = sorted(
        program.candidate_states.values(),
        key=lambda item: sum(item.observed_profile.values()),
        reverse=True,
    )[:2]
    tradeoffs = []
    for candidate in best_candidates:
        potency_push = improvement_scale * weights["potency"]
        candidate.truth_profile["potency_estimate"] = clamp01(candidate.truth_profile["potency_estimate"] + potency_push)
        candidate.truth_profile["selectivity_estimate"] = clamp01(
            candidate.truth_profile["selectivity_estimate"]
            + improvement_scale * weights["selectivity"]
            - 0.25 * potency_push
        )
        candidate.truth_profile["bioavailability_estimate"] = clamp01(
            candidate.truth_profile["bioavailability_estimate"] + improvement_scale * weights["pk"]
        )
        candidate.truth_profile["safety_margin_estimate"] = clamp01(
            candidate.truth_profile["safety_margin_estimate"]
            + improvement_scale * weights["safety_margin"]
            - 0.12 * potency_push
        )
        candidate.truth_profile["developability_estimate"] = clamp01(
            candidate.truth_profile["developability_estimate"]
            + improvement_scale * weights["developability"]
            - 0.08 * weights["potency"]
        )
        candidate.optimization_cycles += cycles
        candidate.history.append(f"chem_campaign_{cycles}_cycles")
        _refresh_candidate_observation(rng, candidate)
        tradeoffs.append(
            f"{candidate.compound_id}: potency {candidate.observed_profile['potency_estimate']:.2f}, "
            f"safety {candidate.observed_profile['safety_margin_estimate']:.2f}"
        )
    if cycles >= 2 and best_candidates:
        parent = best_candidates[0]
        new_truth = {
            "potency_estimate": clamp01(parent.truth_profile["potency_estimate"] + rng.uniform(-0.02, 0.06)),
            "selectivity_estimate": clamp01(parent.truth_profile["selectivity_estimate"] + rng.uniform(-0.05, 0.05)),
            "bioavailability_estimate": clamp01(parent.truth_profile["bioavailability_estimate"] + rng.uniform(-0.03, 0.05)),
            "safety_margin_estimate": clamp01(parent.truth_profile["safety_margin_estimate"] + rng.uniform(-0.05, 0.04)),
            "developability_estimate": clamp01(parent.truth_profile["developability_estimate"] + rng.uniform(-0.04, 0.04)),
        }
        compound_id = next_identifier(portfolio, "next_candidate_index", "cmpd")
        new_candidate = CandidateState(
            compound_id=compound_id,
            truth_profile=new_truth,
            observed_profile={},
            history=[f"derived_from_{parent.compound_id}", "alternate_series_spawn"],
        )
        _refresh_candidate_observation(rng, new_candidate)
        program.candidate_states[compound_id] = new_candidate
        tradeoffs.append(f"{compound_id}: new analog added to series")
    program.optimization_campaigns += 1
    if program.stage == "hit_series":
        for candidate_id in program.candidate_states:
            if meets_lead_entry_threshold(program, candidate_id):
                program.stage = "lead_series"
                program.known_findings.append("lead_entry_threshold_crossed_positive")
                break
    program.known_findings.append("medchem_tradeoffs_updated")
    return {
        "updated_candidate_summaries": [
            {
                "compound_id": candidate.compound_id,
                "observed_profile": dict(candidate.observed_profile),
                "history": list(candidate.history),
                "is_active": candidate.is_active,
            }
            for candidate in program.candidate_states.values()
        ],
        "campaign_tradeoff_memo": " | ".join(tradeoffs),
    }


def estimate_preclinical_package(program: ProgramState, package_type: str) -> tuple[float, int]:
    band_key = {
        "exploratory": "exploratory_preclinical",
        "translational": "translational_preclinical",
        "IND_enabling": "IND_enabling",
    }[package_type]
    cost_band = COST_BANDS[band_key]
    duration_band = DURATION_BANDS[band_key]
    chemistry_burden = (
        program.hidden_state.candidate_hidden["formulation_risk"]
        + program.hidden_state.candidate_hidden["process_risk"]
        + program.hidden_state.candidate_hidden["off_target_liability"]
    ) / 3.0
    cost = cost_band[0] + (cost_band[1] - cost_band[0]) * chemistry_burden
    duration = duration_band[0] + int(round((duration_band[1] - duration_band[0]) * chemistry_burden))
    return cost, duration


def _status_from_score(score: float, strict: bool = False) -> str:
    if strict:
        if score >= 0.62:
            return "acceptable"
        if score >= 0.42:
            return "concern"
        return "failed"
    if score >= 0.56:
        return "acceptable"
    if score >= 0.35:
        return "concern"
    return "failed"


def complete_preclinical_evidence(
    portfolio: PortfolioState,
    program: ProgramState,
    work_item,
    rng: random.Random,
) -> tuple[StudySummary, dict[str, Any]]:
    candidate = program.candidate_states[work_item.payload["candidate_id"]]
    package_type = work_item.payload["package_type"]
    formulation_score = clamp01(
        candidate.truth_profile["developability_estimate"]
        - 0.25 * program.hidden_state.candidate_hidden["formulation_risk"]
        - 0.20 * program.hidden_state.candidate_hidden["process_risk"]
    )
    safety_score = clamp01(
        candidate.truth_profile["safety_margin_estimate"]
        - 0.25 * program.hidden_state.candidate_hidden["off_target_liability"]
    )
    efficacy_score = clamp01(
        candidate.truth_profile["potency_estimate"]
        * program.hidden_state.biology_hidden["target_validity"]
        * program.hidden_state.biology_hidden["species_translatability"]
    )
    biomarker_score = clamp01(program.hidden_state.biology_hidden["biomarker_observability"])
    if package_type == "exploratory":
        program.manufacturing_status = _status_from_score(formulation_score * 0.9)
        program.nonclinical_safety_status = _status_from_score(safety_score * 0.9)
    elif package_type == "translational":
        program.manufacturing_status = _status_from_score(formulation_score)
        program.nonclinical_safety_status = _status_from_score(safety_score)
        if "indication_changed_requires_revalidation" in program.known_findings:
            program.known_findings = [
                finding
                for finding in program.known_findings
                if finding != "indication_changed_requires_revalidation"
            ]
    else:
        program.manufacturing_status = _status_from_score(formulation_score, strict=True)
        program.nonclinical_safety_status = _status_from_score(safety_score, strict=True)
    findings = [
        f"efficacy_model_signal_{efficacy_score:.2f}",
        f"adme_package_support_{candidate.truth_profile['bioavailability_estimate']:.2f}",
        f"tox_signal_{safety_score:.2f}",
        f"biomarker_tractability_{biomarker_score:.2f}",
        f"formulation_process_{formulation_score:.2f}",
    ]
    study = StudySummary(
        study_id=next_identifier(portfolio, "next_study_index", "study"),
        kind="preclinical_package",
        package_type=package_type,
        candidate_id=candidate.compound_id,
        findings=findings,
        summary=f"{package_type} package completed for {candidate.compound_id}",
        month=portfolio.elapsed_months,
    )
    program.completed_studies.append(study)
    program.known_findings.extend(findings)
    report = {
        "study_id": study.study_id,
        "program_id": program.program_id,
        "candidate_id": candidate.compound_id,
        "package_type": package_type,
        "efficacy_model_outputs": {
            "species_translatability": program.hidden_state.biology_hidden["species_translatability"],
            "model_signal_strength": efficacy_score,
        },
        "adme_summaries": {
            "bioavailability_support": candidate.observed_profile["bioavailability_estimate"],
            "clearance_risk": program.hidden_state.candidate_hidden["clearance_true"],
        },
        "tox_findings": {
            "nonclinical_safety_status": program.nonclinical_safety_status,
            "safety_score": safety_score,
        },
        "biomarker_tractability_notes": {
            "observable_signal": biomarker_score,
            "strategy": program.biomarker_strategy,
        },
        "formulation_process_notes": {
            "manufacturing_status": program.manufacturing_status,
            "formulation_score": formulation_score,
        },
        "summary": study.summary,
        "month": study.month,
    }
    return study, report


def estimate_additional_study(program: ProgramState, study_type: str) -> tuple[float, int]:
    spec = ADDITIONAL_STUDY_SPECS[study_type]
    cost = spec["cost"]
    minimum, maximum = spec["duration"]
    operational = program.hidden_state.clinical_hidden["enrollment_difficulty"]
    duration = minimum + int(round((maximum - minimum) * operational))
    return cost, max(minimum, min(maximum, duration))


def complete_additional_study(
    portfolio: PortfolioState,
    program: ProgramState,
    work_item,
    rng: random.Random,
) -> tuple[StudySummary, dict[str, Any]]:
    study_type = work_item.payload["study_type"]
    parameters = dict(work_item.payload.get("parameters", {}))
    candidate = program.active_candidate() or max(
        program.candidate_states.values(),
        key=lambda item: sum(item.observed_profile.values()),
    )
    findings: list[str] = []
    recommended_dose_supported = False
    if study_type == "secondary_assay":
        candidate.observed_profile["potency_estimate"] = clamp01(candidate.observed_profile["potency_estimate"] + 0.03)
        candidate.observed_profile["selectivity_estimate"] = clamp01(candidate.observed_profile["selectivity_estimate"] + 0.02)
        findings.append("secondary_assay_supported_on_target_potency")
    elif study_type == "alternate_scaffold":
        new_truth = {
            key: clamp01(value + rng.uniform(-0.06, 0.08))
            for key, value in candidate.truth_profile.items()
        }
        compound_id = next_identifier(portfolio, "next_candidate_index", "cmpd")
        scaffold = CandidateState(
            compound_id=compound_id,
            truth_profile=new_truth,
            observed_profile={},
            history=[f"alternate_scaffold_from_{candidate.compound_id}"],
        )
        _refresh_candidate_observation(rng, scaffold)
        program.candidate_states[compound_id] = scaffold
        findings.append(f"alternate_scaffold_generated_{compound_id}")
    elif study_type == "formulation_screen":
        candidate.truth_profile["developability_estimate"] = clamp01(candidate.truth_profile["developability_estimate"] + 0.08)
        _refresh_candidate_observation(rng, candidate)
        program.manufacturing_status = _status_from_score(candidate.truth_profile["developability_estimate"])
        findings.append("formulation_screen_reduced_cmc_risk")
    elif study_type == "off_target_panel":
        candidate.observed_profile["safety_margin_estimate"] = clamp01(
            candidate.observed_profile["safety_margin_estimate"]
            - 0.05 * program.hidden_state.candidate_hidden["off_target_liability"]
            + 0.03
        )
        findings.append("off_target_panel_refined_safety_liability")
    elif study_type == "additional_tox_species":
        tox_score = clamp01(
            candidate.truth_profile["safety_margin_estimate"]
            - 0.20 * program.hidden_state.candidate_hidden["off_target_liability"]
        )
        program.nonclinical_safety_status = _status_from_score(tox_score, strict=True)
        findings.append(f"additional_tox_species_{program.nonclinical_safety_status}")
    elif study_type == "biomarker_validation":
        biomarker_support = clamp01(program.hidden_state.biology_hidden["biomarker_observability"] + 0.06)
        program.biomarker_strategy = {
            "validated": biomarker_support >= 0.55,
            "support_score": biomarker_support,
            "parameters": parameters,
        }
        findings.append("biomarker_validation_completed")
    elif study_type == "pk_bridging_study":
        candidate.observed_profile["bioavailability_estimate"] = clamp01(
            candidate.observed_profile["bioavailability_estimate"] + 0.04
        )
        findings.append("pk_bridging_supported_human_exposure")
    elif study_type == "mechanism_confirmation":
        findings.append("mechanism_confirmation_supported_target_engagement")
    elif study_type == "dose_finding_substudy":
        recommended_dose_supported = (
            candidate.observed_profile["bioavailability_estimate"] >= 0.50
            and candidate.observed_profile["safety_margin_estimate"] >= 0.45
        )
        findings.append(f"dose_finding_supported_{recommended_dose_supported}")
    elif study_type == "biomarker_retrospective":
        findings.append("retrospective_biomarker_signal_detected")
    elif study_type == "external_data_analysis":
        findings.append("external_data_contextualized_endpoint_variability")
    study = StudySummary(
        study_id=next_identifier(portfolio, "next_study_index", "study"),
        kind="additional_study",
        study_type=study_type,
        candidate_id=candidate.compound_id,
        parameters=parameters,
        findings=findings,
        recommended_dose_supported=recommended_dose_supported,
        summary=f"{study_type} completed for {candidate.compound_id}",
        month=portfolio.elapsed_months,
    )
    program.completed_studies.append(study)
    program.known_findings.extend(findings)
    report = {
        "study_id": study.study_id,
        "program_id": program.program_id,
        "study_type": study_type,
        "candidate_id": candidate.compound_id,
        "parameters": parameters,
        "findings": findings,
        "recommended_dose_supported": recommended_dose_supported,
        "summary": study.summary,
        "month": study.month,
    }
    return study, report
