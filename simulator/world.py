from __future__ import annotations

import random
from typing import Any

from .models import CandidateState, HiddenProgramState, OpportunityBrief


TARGET_CLASSES = [
    "kinase",
    "gpcr",
    "cytokine",
    "ion_channel",
    "epigenetic",
    "metabolic_enzyme",
]

MODALITIES = ["small_molecule", "antibody", "peptide"]

INDICATIONS = {
    "kinase": ["solid_tumor", "hematologic_malignancy", "fibrosis"],
    "gpcr": ["pain", "metabolic_disease", "pulmonary_disease"],
    "cytokine": ["autoimmune_disease", "dermatology", "inflammation"],
    "ion_channel": ["neurology", "cardiology", "pain"],
    "epigenetic": ["oncology", "rare_disease", "immunology"],
    "metabolic_enzyme": ["metabolic_disease", "hepatology", "rare_disease"],
}

PRESET_PROFILES = {
    "clean_winner": {
        "biology": 0.82,
        "chemistry": 0.76,
        "translation": 0.74,
        "operations": 0.68,
        "commercial": 0.70,
        "overrides": {"regulatory_strictness": 0.35, "competitive_pressure": 0.35},
    },
    "dose_trap": {
        "biology": 0.72,
        "chemistry": 0.56,
        "translation": 0.52,
        "operations": 0.60,
        "commercial": 0.65,
        "overrides": {"therapeutic_window": 0.24, "off_target_liability": 0.74},
    },
    "subgroup_drug": {
        "biology": 0.74,
        "chemistry": 0.68,
        "translation": 0.62,
        "operations": 0.60,
        "commercial": 0.55,
        "overrides": {"responder_fraction": 0.34, "biomarker_observability": 0.84},
    },
    "beautiful_biology_bad_molecule": {
        "biology": 0.86,
        "chemistry": 0.38,
        "translation": 0.64,
        "operations": 0.54,
        "commercial": 0.62,
        "overrides": {"formulation_risk": 0.78, "process_risk": 0.76},
    },
    "good_molecule_wrong_target": {
        "biology": 0.28,
        "chemistry": 0.80,
        "translation": 0.42,
        "operations": 0.65,
        "commercial": 0.58,
        "overrides": {"target_validity": 0.24, "pathway_redundancy": 0.78},
    },
    "operationally_doomed": {
        "biology": 0.70,
        "chemistry": 0.66,
        "translation": 0.60,
        "operations": 0.26,
        "commercial": 0.56,
        "overrides": {"enrollment_difficulty": 0.82, "dropout_risk": 0.76},
    },
    "regulatory_gray_zone": {
        "biology": 0.68,
        "chemistry": 0.66,
        "translation": 0.58,
        "operations": 0.58,
        "commercial": 0.58,
        "overrides": {"regulatory_strictness": 0.78, "surrogate_acceptance": 0.36},
    },
    "crowded_market": {
        "biology": 0.72,
        "chemistry": 0.70,
        "translation": 0.62,
        "operations": 0.62,
        "commercial": 0.32,
        "overrides": {"competitive_pressure": 0.86, "payer_stringency": 0.72},
    },
    "slow_enrollment": {
        "biology": 0.72,
        "chemistry": 0.64,
        "translation": 0.60,
        "operations": 0.36,
        "commercial": 0.56,
        "overrides": {"enrollment_difficulty": 0.84},
    },
}


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _sample(rng: random.Random, mean: float, spread: float = 0.12) -> float:
    return clamp01(rng.gauss(mean, spread))


def _risk_label(quality: float) -> str:
    if quality >= 0.68:
        return "low"
    if quality >= 0.42:
        return "medium"
    return "high"


def _priority_tier(score: float) -> str:
    if score >= 0.7:
        return "attractive"
    if score >= 0.48:
        return "balanced"
    return "speculative"


def get_preset_profile(name: str) -> dict[str, Any]:
    return PRESET_PROFILES.get(name, PRESET_PROFILES["clean_winner"])


def build_opportunity_pool(
    rng: random.Random,
    scenario_preset: str,
    size: int,
    starting_index: int = 1,
) -> tuple[list[OpportunityBrief], dict[str, dict[str, float]], int]:
    opportunities = []
    priors = {}
    next_index = starting_index
    for _ in range(size):
        opportunity, prior = generate_opportunity(rng, scenario_preset, next_index)
        opportunities.append(opportunity)
        priors[opportunity.opportunity_id] = prior
        next_index += 1
    return opportunities, priors, next_index


def generate_opportunity(
    rng: random.Random,
    scenario_preset: str,
    index: int,
) -> tuple[OpportunityBrief, dict[str, float]]:
    preset = get_preset_profile(scenario_preset)
    target_class = rng.choice(TARGET_CLASSES)
    modality = rng.choice(MODALITIES)
    indications = rng.sample(INDICATIONS[target_class], k=min(2, len(INDICATIONS[target_class])))
    biology = clamp01(_sample(rng, preset["biology"]))
    chemistry = clamp01(_sample(rng, preset["chemistry"]))
    translation = clamp01(_sample(rng, preset["translation"]))
    operations = clamp01(_sample(rng, preset["operations"]))
    commercial = clamp01(_sample(rng, preset["commercial"]))
    tractability_notes = [
        f"{target_class} program with {modality} tractability profile",
        f"observable chemistry burden looks {_risk_label(chemistry)}",
        f"translation burden looks {_risk_label(translation)}",
    ]
    opportunity = OpportunityBrief(
        opportunity_id=f"opp-{index:03d}",
        target_class=target_class,
        modality=modality,
        tractability_notes=tractability_notes,
        indication_hypotheses=indications,
        observable_risk_hints={
            "biology": _risk_label(biology),
            "chemistry": _risk_label(chemistry),
            "translation": _risk_label(translation),
            "operations": _risk_label(operations),
            "commercial": _risk_label(commercial),
        },
        priority_tier=_priority_tier((biology + chemistry + translation + commercial) / 4.0),
    )
    prior = {
        "biology": biology,
        "chemistry": chemistry,
        "translation": translation,
        "operations": operations,
        "commercial": commercial,
    }
    return opportunity, prior


def sample_hidden_program_state(
    rng: random.Random,
    prior: dict[str, float],
    scenario_preset: str,
) -> tuple[HiddenProgramState, str]:
    preset = get_preset_profile(scenario_preset)
    overrides = preset.get("overrides", {})
    biology_hidden = {
        "target_validity": overrides.get("target_validity", _sample(rng, prior["biology"], 0.10)),
        "pathway_redundancy": overrides.get("pathway_redundancy", _sample(rng, 1.0 - prior["biology"], 0.10)),
        "species_translatability": _sample(rng, prior["translation"], 0.12),
        "responder_fraction": overrides.get("responder_fraction", _sample(rng, (prior["biology"] + prior["translation"]) / 2.0, 0.15)),
        "effect_size_base": _sample(rng, 0.35 + 0.45 * prior["biology"], 0.12),
        "disease_heterogeneity": _sample(rng, 1.0 - prior["translation"], 0.12),
        "biomarker_observability": overrides.get("biomarker_observability", _sample(rng, prior["translation"], 0.12)),
        "disease_progression_rate": _sample(rng, 0.45, 0.18),
    }
    chemistry = prior["chemistry"]
    candidate_hidden = {
        "potency_true": _sample(rng, chemistry, 0.10),
        "selectivity_true": _sample(rng, chemistry, 0.12),
        "oral_bioavailability_true": _sample(rng, 0.15 + 0.75 * chemistry, 0.12),
        "clearance_true": _sample(rng, 1.0 - (0.25 + 0.6 * chemistry), 0.10),
        "half_life_true": _sample(rng, 0.35 + 0.45 * chemistry, 0.12),
        "tissue_penetration_true": _sample(rng, 0.30 + 0.50 * chemistry, 0.12),
        "off_target_liability": overrides.get("off_target_liability", _sample(rng, 1.0 - chemistry, 0.12)),
        "therapeutic_window": overrides.get("therapeutic_window", _sample(rng, 0.20 + 0.60 * chemistry, 0.12)),
        "formulation_risk": overrides.get("formulation_risk", _sample(rng, 1.0 - chemistry, 0.10)),
        "polymorph_risk": _sample(rng, 0.50 - 0.25 * chemistry, 0.10),
        "process_risk": overrides.get("process_risk", _sample(rng, 1.0 - chemistry, 0.10)),
        "dose_response_slope": _sample(rng, 0.40 + 0.40 * chemistry, 0.10),
    }
    clinical_hidden = {
        "placebo_noise": _sample(rng, 0.20 + 0.45 * (1.0 - prior["translation"]), 0.10),
        "dropout_risk": overrides.get("dropout_risk", _sample(rng, 1.0 - prior["operations"], 0.12)),
        "enrollment_difficulty": overrides.get("enrollment_difficulty", _sample(rng, 1.0 - prior["operations"], 0.12)),
        "adherence_risk": _sample(rng, 0.20 + 0.45 * (1.0 - prior["operations"]), 0.10),
        "exposure_variability": _sample(rng, 0.25 + 0.35 * (1.0 - chemistry), 0.12),
        "background_soc_effect": _sample(rng, 0.15 + 0.30 * (1.0 - prior["commercial"]), 0.10),
        "safety_event_rate": _sample(rng, 0.15 + 0.35 * candidate_hidden["off_target_liability"], 0.10),
    }
    strategic_hidden = {
        "regulatory_strictness": overrides.get("regulatory_strictness", _sample(rng, 0.45, 0.15)),
        "surrogate_acceptance": overrides.get("surrogate_acceptance", _sample(rng, 0.55 + 0.20 * prior["translation"], 0.12)),
        "market_size_base": _sample(rng, prior["commercial"], 0.14),
        "payer_stringency": overrides.get("payer_stringency", _sample(rng, 0.30 + 0.30 * (1.0 - prior["commercial"]), 0.12)),
        "competitive_pressure": overrides.get("competitive_pressure", _sample(rng, 0.28 + 0.30 * (1.0 - prior["commercial"]), 0.12)),
    }
    commercial_score = (
        strategic_hidden["market_size_base"]
        - 0.5 * strategic_hidden["payer_stringency"]
        - 0.5 * strategic_hidden["competitive_pressure"]
    )
    if commercial_score >= 0.30:
        commercial_outlook = "strong"
    elif commercial_score >= 0.05:
        commercial_outlook = "moderate"
    else:
        commercial_outlook = "weak"
    return (
        HiddenProgramState(
            biology_hidden=biology_hidden,
            candidate_hidden=candidate_hidden,
            clinical_hidden=clinical_hidden,
            strategic_hidden=strategic_hidden,
        ),
        commercial_outlook,
    )


def _observed_profile(rng: random.Random, truth_profile: dict[str, float]) -> dict[str, float]:
    return {
        key: clamp01(rng.gauss(value, 0.06 if "developability" not in key else 0.07))
        for key, value in truth_profile.items()
    }


def initialize_candidate_series(
    rng: random.Random,
    hidden_state: HiddenProgramState,
    starting_index: int,
    count: int = 3,
) -> tuple[dict[str, CandidateState], int]:
    candidates = {}
    base = hidden_state.candidate_hidden
    next_index = starting_index
    for offset in range(count):
        potency = clamp01(base["potency_true"] - 0.12 + 0.08 * offset + rng.uniform(-0.04, 0.04))
        selectivity = clamp01(base["selectivity_true"] - 0.10 + 0.06 * offset + rng.uniform(-0.04, 0.04))
        bioavailability = clamp01(base["oral_bioavailability_true"] - 0.10 + 0.05 * offset + rng.uniform(-0.04, 0.04))
        safety_margin = clamp01(
            base["therapeutic_window"] - 0.35 * base["off_target_liability"] + rng.uniform(-0.05, 0.05)
        )
        developability = clamp01(
            1.0 - (0.4 * base["formulation_risk"] + 0.3 * base["polymorph_risk"] + 0.3 * base["process_risk"])
            + rng.uniform(-0.05, 0.05)
        )
        truth_profile = {
            "potency_estimate": potency,
            "selectivity_estimate": selectivity,
            "bioavailability_estimate": bioavailability,
            "safety_margin_estimate": safety_margin,
            "developability_estimate": developability,
        }
        compound_id = f"cmpd-{next_index:04d}"
        candidates[compound_id] = CandidateState(
            compound_id=compound_id,
            truth_profile=truth_profile,
            observed_profile=_observed_profile(rng, truth_profile),
            history=[f"seed_series_rank_{offset + 1}"],
        )
        next_index += 1
    return candidates, next_index
