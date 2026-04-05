from __future__ import annotations

import math
import random
from statistics import mean
from typing import Any

from .constants import COST_BANDS, DURATION_BANDS, PHASE_COMPATIBLE_ENDPOINTS
from .models import PortfolioState, ProgramState, TrialDesignSummary, TrialResultSummary
from .portfolio import next_identifier
from .world import clamp01


ENDPOINT_NOISE = {
    "objective_biomarker": 0.08,
    "symptom_score": 0.18,
    "survival_or_event": 0.14,
    "binary_response": 0.12,
}


def _design_cost(phase: str, sample_size: int, duration: int) -> float:
    low, high = COST_BANDS["design_trial"]
    phase_weight = {"phase1": 0.9, "phase2": 1.0, "phase3": 1.2}[phase]
    normalized = min(1.0, sample_size / 600.0 + duration / 36.0)
    return low + (high - low) * 0.5 * phase_weight * normalized


def _power_range(sample_size: int, endpoint: str, comparator: str) -> tuple[float, float]:
    base = min(0.92, 0.22 + sample_size / 700.0 - 0.4 * ENDPOINT_NOISE[endpoint])
    if comparator.lower() in {"placebo", "standard_of_care", "soc"}:
        base += 0.05
    base = clamp01(base)
    return (round(max(0.05, base - 0.10), 2), round(min(0.99, base + 0.08), 2))


def design_trial(
    portfolio: PortfolioState,
    program: ProgramState,
    *,
    phase: str,
    population_definition: str,
    endpoint: str,
    comparator: str,
    dose_strategy: str,
    duration: int,
    sample_size: int,
    enrichment_strategy: str | None,
) -> tuple[TrialDesignSummary, dict[str, Any], float]:
    invalid_reasons = []
    if sample_size <= 0:
        invalid_reasons.append("sample_size_must_be_positive")
    if duration <= 0:
        invalid_reasons.append("duration_must_be_positive")
    if endpoint not in PHASE_COMPATIBLE_ENDPOINTS[phase]:
        invalid_reasons.append("endpoint_not_stage_appropriate")
    if not dose_strategy:
        invalid_reasons.append("dose_strategy_required")
    valid = not invalid_reasons
    cost = _design_cost(phase, sample_size, duration)
    power_range = _power_range(sample_size, endpoint, comparator)
    enrollment_penalty = program.hidden_state.clinical_hidden["enrollment_difficulty"]
    projected_duration = (
        max(1, duration - 1),
        duration + 1 + int(round(6 * enrollment_penalty)),
    )
    projected_enrollment_rate = round(max(0.1, 1.2 - enrollment_penalty), 2)
    interpretability = clamp01(
        0.45
        + 0.20 * (1.0 - ENDPOINT_NOISE[endpoint])
        + 0.15 * (0.08 if comparator else -0.1)
        + 0.10 * (0.1 if enrichment_strategy else 0.0)
    )
    credibility = clamp01(
        0.40
        + 0.20 * (endpoint in {"objective_biomarker", "survival_or_event"})
        + 0.15 * bool(comparator)
        + 0.10 * (program.biomarker_strategy is not None)
    )
    phase1_supports_dose = False
    if phase == "phase1":
        phase1_supports_dose = (
            any(
                study.kind == "preclinical_package"
                and study.package_type == "IND_enabling"
                and study.candidate_id == program.active_candidate_id
                for study in program.completed_studies
            )
            and program.nonclinical_safety_status != "failed"
        )
    design = TrialDesignSummary(
        design_id=next_identifier(portfolio, "next_design_index", "design"),
        phase=phase,
        population_definition=population_definition,
        endpoint=endpoint,
        comparator=comparator,
        dose_strategy=dose_strategy,
        duration=duration,
        sample_size=sample_size,
        enrichment_strategy=enrichment_strategy,
        valid=valid,
        invalid_reasons=invalid_reasons,
        protocol_complete=phase == "phase1" and valid,
        supports_starting_dose_rationale=phase1_supports_dose,
        projected_cost=phase_trial_cost(phase, sample_size, duration),
        projected_duration_range=projected_duration,
        projected_enrollment_rate=projected_enrollment_rate,
        estimated_power_range=power_range,
        interpretability_score=round(interpretability, 2),
        regulatory_credibility_score=round(credibility, 2),
        created_month=portfolio.elapsed_months,
    )
    observation = {
        "projected_cost": design.projected_cost,
        "projected_duration_range_months": projected_duration,
        "projected_enrollment_rate": projected_enrollment_rate,
        "estimated_power_range": power_range,
        "interpretability_score": design.interpretability_score,
        "regulatory_credibility_score": design.regulatory_credibility_score,
        "valid": valid,
        "invalid_reasons": invalid_reasons,
    }
    return design, observation, cost


def phase_trial_cost(phase: str, sample_size: int, duration: int) -> float:
    band = COST_BANDS[phase]
    normalized = min(1.0, sample_size / {"phase1": 120, "phase2": 320, "phase3": 900}[phase] + duration / 36.0)
    return band[0] + (band[1] - band[0]) * min(1.0, normalized / 1.6)


def phase_trial_duration(program: ProgramState, phase: str, design: TrialDesignSummary) -> int:
    minimum, maximum = DURATION_BANDS[phase]
    operational = (
        program.hidden_state.clinical_hidden["enrollment_difficulty"]
        + program.hidden_state.clinical_hidden["dropout_risk"]
    ) / 2.0
    design_factor = min(1.0, design.duration / maximum)
    duration = minimum + int(round((maximum - minimum) * (0.45 * operational + 0.55 * design_factor)))
    return max(minimum, min(maximum, duration))


def _dose_factor(dose_strategy: str) -> float:
    lower = dose_strategy.lower()
    if "aggressive" in lower or "high" in lower:
        return 1.15
    if "low" in lower or "conservative" in lower:
        return 0.82
    return 1.0


def _population_alignment(program: ProgramState, design: TrialDesignSummary) -> float:
    alignment = 0.85
    if program.biomarker_strategy and program.biomarker_strategy.get("validated"):
        alignment += 0.10
    if design.enrichment_strategy:
        alignment += 0.10 * program.hidden_state.biology_hidden["biomarker_observability"]
    if "broad" in design.population_definition.lower():
        alignment -= 0.08 * program.hidden_state.biology_hidden["disease_heterogeneity"]
    return clamp01(alignment)


def _simulate_patients(
    rng: random.Random,
    program: ProgramState,
    design: TrialDesignSummary,
) -> tuple[list[float], list[float], list[float], list[bool]]:
    hidden = program.hidden_state
    candidate = program.active_candidate()
    if candidate is None:
        raise ValueError("Active candidate required for trial simulation.")
    n = min(max(40, design.sample_size), 500)
    alignment = _population_alignment(program, design)
    placebo_noise = hidden.clinical_hidden["placebo_noise"]
    exposure_values = []
    effects = []
    safety_flags = []
    biomarker_flags = []
    for _ in range(n):
        severity = clamp01(rng.gauss(0.55, 0.16))
        adherence = clamp01(1.0 - hidden.clinical_hidden["adherence_risk"] + rng.gauss(0.0, 0.12))
        pk_multiplier = max(0.35, rng.gauss(1.0, 0.15 + 0.25 * hidden.clinical_hidden["exposure_variability"]))
        biomarker_positive = rng.random() < clamp01(
            hidden.biology_hidden["biomarker_observability"] * alignment
        )
        exposure = clamp01(
            _dose_factor(design.dose_strategy)
            * candidate.truth_profile["bioavailability_estimate"]
            * hidden.candidate_hidden["tissue_penetration_true"]
            * adherence
            * pk_multiplier
            / (0.55 + hidden.candidate_hidden["clearance_true"])
        )
        responder = rng.random() < clamp01(hidden.biology_hidden["responder_fraction"] * (1.1 if biomarker_positive else 0.9))
        effect = (
            hidden.biology_hidden["effect_size_base"]
            * hidden.biology_hidden["target_validity"]
            * exposure
            * (1.0 if responder else 0.2)
            * alignment
            * (1.0 - hidden.biology_hidden["pathway_redundancy"])
            - severity * hidden.clinical_hidden["background_soc_effect"] * 0.15
            + rng.gauss(0.0, ENDPOINT_NOISE[design.endpoint] + 0.08 * placebo_noise)
        )
        adverse_prob = clamp01(
            0.05
            + 0.45 * exposure
            + 0.30 * hidden.candidate_hidden["off_target_liability"]
            - 0.20 * hidden.candidate_hidden["therapeutic_window"]
        )
        exposure_values.append(exposure)
        effects.append(effect)
        safety_flags.append(rng.random() < adverse_prob)
        biomarker_flags.append(biomarker_positive)
    return exposure_values, effects, biomarker_flags, safety_flags


def complete_trial(
    portfolio: PortfolioState,
    program: ProgramState,
    work_item,
    rng: random.Random,
) -> tuple[TrialResultSummary, dict[str, Any], bool]:
    design_data = work_item.payload["design"]
    design = TrialDesignSummary(**design_data)
    exposure_values, effects, biomarker_flags, safety_flags = _simulate_patients(rng, program, design)
    mean_effect = mean(effects)
    control_mean = 0.03 + 0.18 * program.hidden_state.clinical_hidden["background_soc_effect"]
    estimate = mean_effect - control_mean
    endpoint_noise = ENDPOINT_NOISE[design.endpoint] + 0.10 * program.hidden_state.clinical_hidden["placebo_noise"]
    se = max(0.01, endpoint_noise / math.sqrt(max(20, min(design.sample_size, 500))))
    ci = (round(estimate - 1.96 * se, 3), round(estimate + 1.96 * se, 3))
    zscore = abs(estimate) / se
    p_value = min(1.0, math.erfc(zscore / math.sqrt(2.0)))
    mean_exposure = mean(exposure_values)
    safety_rate = sum(1 for flag in safety_flags if flag) / len(safety_flags)
    dropout_rate = clamp01(
        program.hidden_state.clinical_hidden["dropout_risk"]
        + 0.10 * program.hidden_state.clinical_hidden["enrollment_difficulty"]
        + 0.08 * safety_rate
    )
    biomarker_positive_effect = mean(
        effect for effect, biomarker_positive in zip(effects, biomarker_flags) if biomarker_positive
    ) if any(biomarker_flags) else estimate
    biomarker_negative_effect = mean(
        effect for effect, biomarker_positive in zip(effects, biomarker_flags) if not biomarker_positive
    ) if any(not flag for flag in biomarker_flags) else estimate
    subgroup_findings = []
    if biomarker_positive_effect - biomarker_negative_effect > 0.10:
        subgroup_findings.append("biomarker_positive_subgroup_outperformed")
    if dropout_rate > 0.30:
        subgroup_findings.append("dropout_compromised_interpretability")
    recommended_dose_supported = design.phase == "phase1" and mean_exposure >= 0.42 and safety_rate < 0.32
    registrational_support = "none"
    if design.phase == "phase2" and p_value < 0.10 and estimate > 0.08:
        registrational_support = "supportive"
    if design.phase == "phase3" and p_value < 0.05 and estimate > 0.10 and safety_rate < 0.35:
        if design.endpoint == "objective_biomarker" and program.hidden_state.strategic_hidden["surrogate_acceptance"] >= 0.60:
            registrational_support = "surrogate_acceptable"
        else:
            registrational_support = "pivotal"
    if safety_rate > 0.40:
        program.safety_database_status = "failed"
    elif design.phase in {"phase2", "phase3"} or recommended_dose_supported:
        program.safety_database_status = "acceptable"
    else:
        program.safety_database_status = "concern"
    topline = "benefit-risk looks supportive"
    catastrophic_failure = False
    if safety_rate > 0.45:
        topline = "catastrophic safety failure"
        catastrophic_failure = True
    elif estimate < -0.15:
        topline = "deeply negative efficacy signal"
        catastrophic_failure = True
    elif estimate < 0.02 and design.phase in {"phase2", "phase3"}:
        topline = "signal remained ambiguous"
    result = TrialResultSummary(
        trial_result_id=next_identifier(portfolio, "next_trial_result_index", "trial"),
        phase=design.phase,
        primary_endpoint_estimate=round(estimate, 3),
        confidence_interval=ci,
        p_value_equivalent=round(p_value, 4),
        recommended_dose_supported=recommended_dose_supported,
        subgroup_findings=subgroup_findings,
        exposure_response_summary=f"mean exposure {mean_exposure:.2f} with dose strategy {design.dose_strategy}",
        safety_summary=f"AE rate {safety_rate:.2f}; safety database {program.safety_database_status}",
        dropout_summary=f"dropout rate {dropout_rate:.2f}",
        registrational_support=registrational_support,
        topline_interpretation=topline,
        month=portfolio.elapsed_months,
    )
    program.trial_results.append(result)
    report = {
        "trial_result_id": result.trial_result_id,
        "program_id": program.program_id,
        "phase": design.phase,
        "design": design_data,
        "primary_endpoint_estimate": result.primary_endpoint_estimate,
        "confidence_interval": result.confidence_interval,
        "p_value_equivalent": result.p_value_equivalent,
        "recommended_dose_supported": result.recommended_dose_supported,
        "subgroup_findings": subgroup_findings,
        "exposure_response_summary": result.exposure_response_summary,
        "safety_summary": result.safety_summary,
        "dropout_summary": result.dropout_summary,
        "registrational_support": result.registrational_support,
        "topline_interpretation": topline,
        "month": result.month,
    }
    return result, report, catastrophic_failure
