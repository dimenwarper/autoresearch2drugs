SPEC_VERSION = "0.1.0"
ARTIFACT_SCHEMA_VERSION = "1.0.0"

STAGES = [
    "hit_series",
    "lead_series",
    "development_candidate",
    "preclinical_ready",
    "ind_under_review",
    "IND_cleared",
    "phase1_in_progress",
    "phase1_complete",
    "phase2_in_progress",
    "phase2_complete",
    "phase3_in_progress",
    "phase3_complete",
    "nda_under_review",
    "approved",
    "failed",
    "terminated",
]

TERMINAL_STAGES = {"approved", "failed", "terminated"}
OPERATING_STATUS = ("active", "paused", None)

ALLOWED_STAGE_TRANSITIONS = {
    "hit_series": ["lead_series", "terminated"],
    "lead_series": ["development_candidate", "terminated"],
    "development_candidate": ["preclinical_ready", "terminated", "failed"],
    "preclinical_ready": ["ind_under_review", "terminated", "failed"],
    "ind_under_review": ["IND_cleared", "failed"],
    "IND_cleared": ["phase1_in_progress", "terminated", "failed"],
    "phase1_in_progress": ["phase1_complete", "failed", "terminated"],
    "phase1_complete": ["phase2_in_progress", "terminated", "failed"],
    "phase2_in_progress": ["phase2_complete", "failed", "terminated"],
    "phase2_complete": ["phase3_in_progress", "terminated", "failed"],
    "phase3_in_progress": ["phase3_complete", "failed", "terminated"],
    "phase3_complete": ["nda_under_review", "terminated", "failed"],
    "nda_under_review": ["approved", "failed"],
    "approved": [],
    "failed": [],
    "terminated": [],
}

WORKSTREAM_DISCOVERY = "discovery_or_preclinical"
WORKSTREAM_CLINICAL = "clinical_or_regulatory"

COST_BANDS = {
    "optimize_candidate": (2_000_000.0, 6_000_000.0),
    "exploratory_preclinical": (1_000_000.0, 3_000_000.0),
    "translational_preclinical": (2_000_000.0, 5_000_000.0),
    "IND_enabling": (8_000_000.0, 20_000_000.0),
    "design_trial": (200_000.0, 1_000_000.0),
    "phase1": (5_000_000.0, 20_000_000.0),
    "phase2": (20_000_000.0, 80_000_000.0),
    "phase3": (80_000_000.0, 300_000_000.0),
    "regulatory_submission": (2_000_000.0, 10_000_000.0),
    "regulatory_feedback": (300_000.0, 1_200_000.0),
}

DURATION_BANDS = {
    "optimize_candidate": (3, 9),
    "exploratory_preclinical": (2, 5),
    "translational_preclinical": (4, 8),
    "IND_enabling": (6, 12),
    "phase1": (6, 12),
    "phase2": (12, 24),
    "phase3": (18, 48),
    "regulatory_review": (6, 12),
}

DEFAULT_THRESHOLDS = {
    "lead_entry": {
        "potency_estimate": 0.45,
        "selectivity_estimate": 0.40,
        "bioavailability_estimate": 0.35,
        "safety_margin_estimate": 0.35,
    },
    "nomination": {
        "potency_estimate": 0.60,
        "selectivity_estimate": 0.55,
        "bioavailability_estimate": 0.50,
        "safety_margin_estimate": 0.55,
        "developability_estimate": 0.50,
    },
}

CANONICAL_BLOCKING_ISSUES = [
    "program_paused",
    "no_active_candidate",
    "no_active_indication",
    "candidate_below_nomination_threshold",
    "missing_exploratory_or_translational_package",
    "missing_IND_enabling_package",
    "manufacturing_not_acceptable",
    "nonclinical_safety_not_acceptable",
    "missing_valid_phase1_design",
    "missing_starting_dose_rationale",
    "missing_first_in_human_protocol",
    "missing_recommended_dose",
    "missing_valid_phase2_design",
    "missing_valid_phase3_design",
    "indication_not_locked",
    "missing_pivotal_package",
    "safety_database_not_acceptable",
    "indication_changed_requires_revalidation",
    "clinical_or_regulatory_work_already_in_progress",
    "discovery_or_preclinical_work_already_in_progress",
    "insufficient_allocated_budget",
]

ENDPOINT_FAMILIES = (
    "objective_biomarker",
    "symptom_score",
    "survival_or_event",
    "binary_response",
)

PHASE_COMPATIBLE_ENDPOINTS = {
    "phase1": {"objective_biomarker", "symptom_score", "binary_response"},
    "phase2": set(ENDPOINT_FAMILIES),
    "phase3": set(ENDPOINT_FAMILIES),
}

STUDY_TYPE_STAGE_MAP = {
    "hit_series": {
        "secondary_assay",
        "alternate_scaffold",
        "formulation_screen",
        "off_target_panel",
    },
    "lead_series": {
        "secondary_assay",
        "alternate_scaffold",
        "formulation_screen",
        "off_target_panel",
    },
    "development_candidate": {
        "additional_tox_species",
        "biomarker_validation",
        "pk_bridging_study",
        "mechanism_confirmation",
    },
    "IND_cleared": {
        "dose_finding_substudy",
        "biomarker_retrospective",
        "external_data_analysis",
    },
    "phase1_complete": {
        "dose_finding_substudy",
        "biomarker_retrospective",
        "external_data_analysis",
    },
    "phase2_complete": {
        "dose_finding_substudy",
        "biomarker_retrospective",
        "external_data_analysis",
    },
    "phase3_complete": {
        "dose_finding_substudy",
        "biomarker_retrospective",
        "external_data_analysis",
    },
}

ADDITIONAL_STUDY_SPECS = {
    "secondary_assay": {"cost": 800_000.0, "duration": (1, 3)},
    "alternate_scaffold": {"cost": 1_500_000.0, "duration": (3, 6)},
    "formulation_screen": {"cost": 1_000_000.0, "duration": (2, 4)},
    "off_target_panel": {"cost": 1_200_000.0, "duration": (2, 4)},
    "additional_tox_species": {"cost": 3_000_000.0, "duration": (4, 6)},
    "biomarker_validation": {"cost": 2_500_000.0, "duration": (3, 5)},
    "pk_bridging_study": {"cost": 1_800_000.0, "duration": (2, 4)},
    "mechanism_confirmation": {"cost": 2_200_000.0, "duration": (3, 5)},
    "dose_finding_substudy": {"cost": 4_000_000.0, "duration": (3, 6)},
    "biomarker_retrospective": {"cost": 1_200_000.0, "duration": (1, 3)},
    "external_data_analysis": {"cost": 800_000.0, "duration": (1, 2)},
}
