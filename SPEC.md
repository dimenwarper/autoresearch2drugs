# Toy Drug Development Simulator Spec

## Purpose

This simulator is intended to explore what happens if an autonomous AI agent is tasked with optimizing the entire drug development pipeline, from early discovery to phase III deployment, autoresearch style. To simulate this, we need to simulate the decisions that such a system would take to generate hits, leads, optimize them medicinally and toxicologically, and shepherd them through clinical trial for effectiveness. 

The simulator models a full program with the ability to spawn new assets and progressing them from discovery through regulatory submission. It must support sequential decision-making by an external agent through a constrained tool API that resembles decisions and tasks that would be taken by an omni drug development overseer. The agent should not have access to hidden ground truth; it only observes study outputs, trial outcomes, program state, and selected regulatory/strategic feedback. The success of each decision in the environment is dictated by probabilities calibrated by success probabilities of each step according to general knowledge/the literature 

The simulator should be implemented as a partially observable Markov decision process with explicit action preconditions, state transitions, observation functions, and terminal conditions. Note that this spec is for the simulator itself, including state definitions, and the tool API given to the agent. The agent itself will be implemented elsewhere.

---

## 1. Formalism

### 1.1 Core model

Represent the environment as a POMDP:

* Hidden state `H_t`: latent biological, chemical, translational, clinical, regulatory, and economic truth at time `t`
* Observable state `O_t`: current program dashboard, completed studies, generated reports, stage, cash, elapsed time, candidate profile, trial results, known findings
* Action `A_t`: one permitted tool invocation, each action has a cost `C(A_t)` associated to it
* Transition function `T(H_t, O_t, A_t) -> H_{t+1}, O_{t+1}`
* Observation function `G(H_{t+1}, A_t) -> delta_observation`
* Budget `B` for spending
* Time budget `T_max` in months — the simulation ends when `elapsed_months >= T_max`
* Reward `R = N_approved / C_total` where `N_approved` is the number of drugs approved and `C_total` is the total cost expended across all programs. Total elapsed time is a secondary endpoint

The Markov property should hold over the full simulator state. That means all future evolution must depend only on the current internal state and the chosen action. However, the agent itself could very well store memory and use it for its decisions, only the simulator is strictly Markovian. 
Additionally, the Markovian property might break for the success probabilities themselves in case the agent decides to e.g. skip a section (see section below on Soft dependencies)

### 1.2 Design philosophy

All uncertainty should be tied to interpretable latent variables. Experimental and trial outputs should be noisy but with a non-negligent probability of being informative. The same hidden causes should influence multiple downstream observations.

Example: a low hidden translatability parameter should simultaneously degrade animal-to-human predictiveness, biomarker informativeness, and expected patient-level efficacy stability.

---

## 2. Stage Graph and Markov Dependencies

The program stage is itself a state variable:

```text
idea -> hit_series -> lead_series -> development_candidate -> preclinical_ready -> IND_cleared -> phase1_in_progress -> phase1_complete -> phase2_in_progress -> phase2_complete -> phase3_in_progress -> phase3_complete -> submitted -> approved | failed | terminated
```

Transitions from `phaseX_in_progress` to `phaseX_complete` are triggered automatically by the simulator when the trial duration elapses, not by agent action.

Not every program must pass through every stage in the same way, but the following dependencies must be enforced.

### 2.1 Hard stage preconditions

`optimize_candidate()` requires:

* stage in `{hit_series, lead_series}`
* at least one active series or candidate object

`generate_preclinical_evidence(package_type='exploratory')` requires:

* stage in `{hit_series, lead_series}`
* candidate or series exists

`generate_preclinical_evidence(package_type='IND-enabling')` requires:

* stage == `development_candidate`
* nominated candidate exists
* indication selected

`choose_indication()` requires:

* stage in `{lead_series, development_candidate}`
* candidate exists

`nominate_candidate()` requires:

* stage == `lead_series`
* candidate profile above minimum thresholds
* minimum evidence package complete

`advance_program(action='preclinical_ready')` requires:

* stage == `development_candidate`
* IND-enabling package complete
* formulation status not failed
* manufacturability status not failed

`advance_program(action='file_IND')` requires:

* stage == `preclinical_ready`
* IND-enabling package complete
* starting dose rationale exists
* first-in-human protocol draft exists
* sufficient cash

`advance_program(action='start_phase1')` requires:

* stage == `IND_cleared`
* phase I trial design exists
* sufficient cash

`advance_program(action='start_phase2')` requires:

* stage == `phase1_complete`
* phase II trial design exists
* recommended dose exists
* sufficient cash

`advance_program(action='start_phase3')` requires:

* stage == `phase2_complete`
* phase III trial design exists
* pivotal indication locked
* sufficient cash

`advance_program(action='submit_NDA')` requires:

* stage == `phase3_complete`
* at least one successful pivotal package or acceptable surrogate package
* CMC acceptable
* safety database acceptable
* sufficient cash

Once terminal stage in `{approved, failed, terminated}` is reached, only `get_program_state()` is allowed.

The entire simulation also terminates when `elapsed_months >= time_budget_months`. Any in-progress trials at that point are abandoned, and only approvals achieved before the deadline count toward the reward.

### 2.2 Soft dependencies

The simulator should also model practical dependencies that are not binary blockers but affect downstream probabilities and interpretability.

Examples:

* Advancing without a biomarker strategy decreases downstream interpretability.
* Choosing a broad patient population lowers expected effect size if responder fraction is low.
* Weak formulation quality increases delay and failure risk during preclinical and later manufacturing review.
* Weak Phase I PK/PD evidence increases Phase II dose miss probability.

---

## 3. Portfolio-level branching requirement

The simulator must not present a single obvious next action at each stage. At any nonterminal step, the agent should face a menu of mutually competing capital-allocation choices, including within-program actions, cross-program actions, and portfolio resets.

The environment should support at least these action classes:

* continue investing in the current program
* pause the current program and gather more evidence
* terminate the current program
* launch a new program from a fresh idea or target class
* run two programs in parallel if cash permits
* partner one program to extend runway for another
* narrow one program's scope to preserve optionality elsewhere

This is essential because real R&D optimization is not just local hill climbing inside one drug. It is constrained portfolio management under uncertainty.

Accordingly, the simulator state must support multiple concurrent programs and a global budget. The agent's problem is therefore:

1. choose which program(s) exist
2. choose which program gets marginal dollars and time
3. choose which experiment or stage-advance action to fund next

This should generally make clear that there is a trade-off between “push the current lead forward,” and “kill or pause this one and reallocate capital to a second asset with better expected information-adjusted value.”

## 4. Hidden State Variables

All hidden variables should be initialized at program start and evolve only where appropriate.

### 4.1 Biology

```python
biology = {
    "target_validity": float,            # causal relevance of target to disease, 0-1
    "pathway_redundancy": float,         # higher means compensation suppresses efficacy
    "species_translatability": float,    # predictiveness of animal models to human biology
    "responder_fraction": float,         # true fraction of patients with meaningful response
    "effect_size_base": float,           # expected efficacy in ideal responder under ideal exposure
    "disease_heterogeneity": float,      # broader variance across patient subtypes
    "biomarker_observability": float,    # ability to measure target engagement / predictive signal
    "disease_progression_rate": float    # relevant to event rates and endpoint sensitivity
}
```

### 4.2 Molecule / candidate

```python
candidate_hidden = {
    "potency_true": float,
    "selectivity_true": float,
    "oral_bioavailability_true": float,
    "clearance_true": float,
    "half_life_true": float,
    "tissue_penetration_true": float,
    "off_target_liability": float,
    "therapeutic_window": float,
    "formulation_risk": float,
    "polymorph_risk": float,
    "process_risk": float,
    "dose_response_slope": float
}
```

### 4.3 Clinical / operational

```python
clinical_hidden = {
    "placebo_noise": float,
    "dropout_risk": float,
    "enrollment_difficulty": float,
    "adherence_risk": float,
    "exposure_variability": float,
    "background_soc_effect": float,
    "safety_event_rate": float
}
```

### 4.4 Regulatory / commercial

```python
strategic_hidden = {
    "regulatory_strictness": float,
    "surrogate_acceptance": float,
    "market_size_base": float,
    "payer_stringency": float,
    "competitive_pressure": float
}
```

### 4.5 Resource state

These are observable but part of the Markov state.

```python
resources = {
    "cash": float,
    "elapsed_months": int,
    "time_budget_months": int,
    "burn_rate": float
}
```

---

## 5. Observable State

The observable state should exist at both portfolio and program levels.

```python
portfolio_observable_state = {
    "cash": float,
    "elapsed_months": int,
    "program_summaries": list[ProgramSummary],
    "active_program_ids": list[str],
    "max_parallel_programs": int,
    "capital_market_access_state": str
}
```

Each `ProgramSummary` should include the program-local observable state described below.

```python
observable_state = {
    "stage": str,
    "cash": float,
    "elapsed_months": int,
    "active_candidate_id": str | None,
    "active_indication": str | None,
    "biomarker_strategy": dict | None,
    "completed_studies": list[StudySummary],
    "trial_designs": list[TrialDesignSummary],
    "trial_results": list[TrialResultSummary],
    "manufacturing_status": str,
    "regulatory_interactions": list[RegulatoryNote],
    "known_findings": list[str]
}
```

The agent must only see the observable slice.

---

## 6. Candidate and Series Objects

The environment should support multiple compound proposals during lead stage, but only one nominated development candidate at a time for simplicity.

```python
Compound = {
    "compound_id": str,
    "observed_profile": {
        "potency_estimate": float,
        "selectivity_estimate": float,
        "solubility_estimate": float,
        "clearance_estimate": float,
        "bioavailability_estimate": float,
        "safety_margin_estimate": float,
        "developability_estimate": float
    },
    "history": list[StudyReference]
}
```

Observed values should be noisy estimators of hidden truth, with assay-specific noise.

---

## 7. Portfolio Object

The environment should support multiple programs concurrently.

```python
Program = {
    "program_id": str,
    "modality": str,
    "target_class": str,
    "stage": str,
    "active_candidate_id": str | None,
    "active_indication": str | None,
    "hidden_state": HiddenProgramState,
    "observable_state": ObservableProgramState,
    "status": str,   # active / paused / partnered / terminated / approved / failed
    "booked_budget": float
}
```

A `PortfolioState` should contain a dict of programs plus shared resources.

---

## 8. Action API

The simulator should include following callable environment actions (that will be used by the agent as tools).

### 8.0 `get_portfolio_state()`

Returns the full current portfolio-level observable state plus summaries for all programs.

No preconditions.

### 8.1 `get_program_state(program_id)`

Returns the full current observable state for one program.

Preconditions:

* `program_id` exists in portfolio

### 8.2 `launch_program(target_class, modality, initial_budget)`

Purpose: create a new program from a fresh idea/target family.

Inputs:

* `target_class`
* `modality`
* `initial_budget`

Preconditions:

* enough portfolio cash
* active program count below `max_parallel_programs` unless another program is paused or terminated

State transition:

* new hidden world sampled independently from the same initial prior distribution (see Section 14), regardless of existing programs or the agent's choice of `target_class` / `modality` — the agent's inputs are labels, not levers on the hidden state
* new observable program created at `idea` or `hit_series`
* cash decreases by committed startup cost
* elapsed months may increase slightly for team formation / setup

Observation:

* initial program brief
* rough tractability notes
* broad indication hypothesis set

This action is important because the agent must sometimes choose between rescuing a weak incumbent program and starting a new one.

### 8.3 `allocate_budget(program_allocations)`

Purpose: explicitly distribute capital across programs.

Inputs:

* mapping `program_id -> budget_amount`

Preconditions:

* total allocated budget <= available cash
* all program_ids valid

State transition:

* updates booked budget by program

Observation:

* allocation summary
* estimated runway by program

This may be implicit inside other actions in a minimal implementation, but an explicit allocator is preferred if the simulator is meant to demonstrate portfolio optimization difficulty.

### 8.4 `optimize_candidate(program_id, objective_profile, budget, cycles)`

Purpose: simulate an aggregated medicinal chemistry campaign.

Inputs:

* `objective_profile`: weights over potency, selectivity, PK, safety margin, developability
* `budget`: cash allocated
* `cycles`: number of chemistry cycles

Preconditions:

* `program_id` exists
* program stage in `{hit_series, lead_series}`
* enough cash

State transition:

* cash decreases
* elapsed months increase
* current series is modified or a new candidate proposal is generated
* observed candidate profiles updated

Observation:

* list of candidate profiles with changed observed properties
* synthesis success/failure notes
* campaign tradeoff summary

Key dynamics:

* property improvements should be correlated and antagonistic
* e.g. potency gain may worsen clearance or formulation risk
* diminishing returns should occur after repeated cycles

### 8.5 `pause_program(program_id)`

Preconditions:

* `program_id` exists
* program status == `active`

State transition:

* program status -> `paused`
* burn rate decreases
* elapsed time continues to accumulate against the global time budget

Observation:

* pause confirmation and carrying-cost summary

### 8.6 `resume_program(program_id)`

Preconditions:

* `program_id` exists
* program status == `paused`
* enough resources to resume

State transition:

* program status -> `active`

Observation:

* resume confirmation

### 8.7 `generate_preclinical_evidence(program_id, candidate_id, package_type)`

Package types:

* `exploratory`
* `translational`
* `IND-enabling`

Preconditions:

* candidate exists
* package-type-specific stage requirements
* enough cash

State transition:

* new studies appended
* elapsed months increase
* cash decreases
* may change manufacturing status if package includes developability work

Observation:

* efficacy model outputs
* ADME data
* tox findings
* PK summaries
* biomarker tractability observations
* formulation notes

Package-specific behavior:

`exploratory`:

* moderate cost, moderate time
* broad but noisy signal
* used in hit/lead stage

`translational`:

* stronger PK/PD and biomarker readout
* indication-contextual
* informs biomarker strategy and population selection

`IND-enabling`:

* GLP-like tox, safety pharmacology, formulation/process package
* may produce hard blockers or acceptable safety margin

### 8.8 `run_additional_study(program_id, study_type, parameters)`

Purpose: gather more information at the current stage without advancing. This allows the agent to reduce uncertainty before committing to the next stage gate.

Available study types vary by stage:

**hit_series / lead_series:**
* `secondary_assay` — re-test potency or selectivity with a different assay
* `alternate_scaffold` — explore a backup chemical series
* `formulation_screen` — early assessment of formulation feasibility
* `off_target_panel` — broader selectivity profiling

**development_candidate:**
* `additional_tox_species` — run tox in a second animal model
* `biomarker_validation` — test biomarker hypothesis in a relevant system
* `pk_bridging_study` — additional PK characterization (e.g., food effect, formulation comparison)
* `mechanism_confirmation` — target engagement or pathway modulation study

**IND_cleared / phaseX_complete:**
* `dose_finding_substudy` — additional PK/PD modeling from existing data
* `biomarker_retrospective` — reanalyze trial data with biomarker stratification
* `external_data_analysis` — analyze published or real-world evidence for indication support

Preconditions:
* `program_id` exists and is active
* `study_type` is valid for the current stage
* enough cash

State transition:
* cash decreases (lower cost than stage-advancing actions)
* elapsed months increase (shorter duration than stage-advancing actions)
* new study results appended to completed_studies

Observation:
* study-specific results with measurement noise
* may update observed candidate profile or known_findings

Key dynamics:
* additional studies have diminishing returns — repeating the same study type yields less new information
* results may confirm, contradict, or be ambiguous relative to prior evidence

### 8.9 `choose_indication(program_id, candidate_id, indication, biomarker_strategy=None)`

Preconditions:

* stage in `{lead_series, development_candidate}`
* candidate exists

State transition:

* active indication locked unless later changed with penalty
* active biomarker strategy set or updated
* downstream trial and market assumptions instantiated

Observation:

* indication profile summary
* expected enrollment difficulty range
* expected endpoint families
* market size estimate range
* standard of care burden

Changing indication later should incur:

* time penalty
* some evidence devaluation
* protocol redesign requirements

### 8.10 `nominate_candidate(program_id, candidate_id)`

Preconditions:

* stage == `lead_series`
* candidate meets minimal observed thresholds
* at least one exploratory or translational package complete
* active indication selected

State transition:

* stage -> `development_candidate`
* active candidate fixed

Observation:

* nomination memo summary
* identified development risks

### 8.11 `design_clinical_trial(program_id, phase, population_definition, endpoint, comparator, dose_strategy, duration, sample_size, enrichment_strategy=None)`

Phases:

* `phase1`
* `phase2`
* `phase3`

Preconditions:

* candidate nominated
* indication selected
* stage compatible with phase

State transition:

* trial design object created

Observation:

* projected cost
* projected duration
* projected enrollment rate
* estimated power range
* interpretability score
* regulatory credibility score

This tool should not reveal true future outcome probability exactly. It may provide noisy planning estimates.

### 8.12 `advance_program(program_id, action)`

Allowed actions:

* `preclinical_ready`
* `file_IND`
* `start_phase1`
* `start_phase2`
* `start_phase3`
* `submit_NDA`
* `request_approval_decision`
* `terminate`

This function exists to enforce explicit stage transitions and hard dependencies.

For `start_phaseX`, a valid trial design must already exist. Starting a phase is the agent's resource-commitment decision. Once started, the trial enters an `in_progress` state and runs for its sampled duration. Upon completion, the simulator automatically executes the trial using the hidden state and trial design, produces observable results, and advances the program stage to `phaseX_complete`. The agent does not explicitly choose to complete a trial — completion is an environment consequence of starting one and time elapsing.

If the agent takes other actions while a trial is in progress, those actions' time costs contribute toward the trial's remaining duration (see Section 12.1 on parallel activity).

`request_approval_decision` requires `submitted` state.

### 8.13 `request_regulatory_feedback(program_id, question_set)`

Preconditions:

* stage >= `development_candidate`
* enough cash/time

Observation:

* regulator minutes summary
* comments on endpoint acceptability, biomarker use, safety database expectations, surrogate plausibility, or label scope

The output should be informative but not omniscient.

---

## 9. Trial Execution Model

The most important part of the simulator is how trial outcomes are generated.

### 9.1 Patient population generation

When a trial is completed, instantiate a virtual patient cohort from the current active indication and trial population definition.

Each patient should have latent variables sampled from indication-specific distributions:

```python
Patient = {
    "severity": float,
    "subtype": int | str,
    "biomarker_positive": bool,
    "comorbidity_burden": float,
    "adherence": float,
    "pk_multiplier": float,
    "placebo_susceptibility": float
}
```

### 9.2 Exposure model

Observed exposure should depend on:

* dose strategy
* candidate hidden PK
* clinical exposure variability
* patient adherence
* patient PK multiplier

### 9.3 Efficacy model

For each patient, treatment effect should be generated as:

```text
effect = base_effect
       * target_validity
       * f(exposure)
       * responder_indicator
       * g(disease_stage, subtype)
       * (1 - pathway_redundancy)
       + noise
```

Where:

* `responder_indicator` is sampled based on true responder fraction and subtype/biomarker alignment
* `noise` depends on endpoint type and disease heterogeneity

### 9.4 Safety model

Adverse event probability should depend on:

* exposure
* therapeutic window
* off-target liability
* population comorbidity burden
* trial duration

### 9.5 Endpoint readout

Different endpoint families should have different noise models:

* objective biomarker endpoint: lower variance, lower clinical meaning
* symptom score: higher placebo noise
* survival/event endpoint: censoring and duration dependence
* binary response endpoint: thresholding noise

### 9.6 Result summary

`complete_phaseX` should output:

* primary endpoint estimate
* confidence interval / p-value equivalent
* subgroup analyses
* exposure-response summary
* safety summary
* dropout summary
* top-line interpretation string

Subgroup analyses should be present but subject to multiple-testing-style ambiguity.

---

## 10. Observation Model and Informational Limits

No tool should reveal hidden truth directly.

Permitted observation patterns:

* assay estimates with measurement noise
* trial forecasts with uncertainty bands
* regulatory feedback as textual guidance
* summarized interpretations derived only from current evidence

Forbidden outputs:

* exact probability of approval derived from hidden state
* true efficacy coefficient
* true responder fraction
* explicit statements like “target invalid” unless justified by observed evidence and even then probabilistic

The simulator should preserve the following asymmetry:

* success can become relatively interpretable when multiple evidence streams align
* failure should often remain underdetermined

---

## 11. Action Branching and Decision Menu Generation

At every decision step, the simulator should be able to enumerate a plausible action menu for the agent. This menu should include both local and portfolio-level actions.

Example decision menu at one timestep:

```python
[
  {"action": "optimize_candidate", "program_id": "P1"},
  {"action": "generate_preclinical_evidence", "program_id": "P1", "package_type": "translational"},
  {"action": "nominate_candidate", "program_id": "P1"},
  {"action": "pause_program", "program_id": "P1"},
  {"action": "launch_program", "target_class": "GPCR", "modality": "small_molecule"},
  {"action": "raise_capital"},
  {"action": "terminate_program", "program_id": "P1"}
]
```

This menu should be generated from current preconditions rather than hardcoded by stage alone.

The agent should therefore face realistic branching:

* exploit the current program
* buy more information in the current program
* stop funding the current program
* start a second program
* fund two programs suboptimally rather than one program fully
* seek financing or partnership instead of scientific progress

This is what makes the benchmark faithful to biotech reality.

## 12. Time and Cost Model

Every action consumes time and cash.

### 12.1 Parallel activity

Actions within the same program or across programs may run in parallel if the agent chooses to start them concurrently. When multiple actions overlap, elapsed time advances by the duration of the longest concurrent action, not the sum. Cash costs are always additive regardless of parallelism.

For example, an agent could run IND-enabling tox studies and formulation work simultaneously within one program, or run a Phase I trial in one program while optimizing a candidate in another. The simulator should track per-action start and end times and resolve elapsed months accordingly.

Actions that depend on each other's outputs (e.g., designing a trial requires a nominated candidate) cannot overlap — their preconditions enforce sequencing naturally.

Suggested baseline action costs:

```python
costs = {
    "optimize_candidate": (2e6, 6e6),
    "exploratory_preclinical": (1e6, 3e6),
    "translational_preclinical": (2e6, 5e6),
    "IND_enabling": (8e6, 20e6),
    "design_trial": (0.2e6, 1e6),
    "phase1": (5e6, 20e6),
    "phase2": (20e6, 80e6),
    "phase3": (80e6, 300e6),
    "regulatory_submission": (2e6, 10e6)
}
```

Suggested baseline durations in months:

```python
durations = {
    "optimize_candidate": (3, 9),
    "exploratory_preclinical": (2, 5),
    "translational_preclinical": (4, 8),
    "IND_enabling": (6, 12),
    "phase1": (6, 12),
    "phase2": (12, 24),
    "phase3": (18, 48),
    "regulatory_review": (6, 12)
}
```

Exact values should be indication- and design-dependent.

If portfolio cash drops below zero at any point and no financing action is taken, the portfolio should force one of:

* emergency termination of one or more programs
* distressed financing with penalty
* portfolio failure state

The implementation should make clear that capital is a shared resource across programs, not a local property of one asset.

---

## 13. Reward Functions

The primary reward is:

```
R = N_approved / C_total
```

Where `N_approved` is the number of drugs that reach `approved` status and `C_total` is the total cash expended across all programs. This captures the efficiency of the portfolio: more approvals per dollar spent is better.

The secondary endpoint is total elapsed time. Between two runs with equal primary reward, the one that completed faster is preferred.

The simulator should report both metrics at termination.

---

## 14. Initial World Generation

At reset, sample a coherent hidden world from a parameterized prior.

Important: hidden variables should not be independent. Sample from a structured prior with realistic correlations.

Examples:

* higher target validity tends to increase effect size base
* higher pathway redundancy reduces realized effect size and subgroup breadth
* poor biomarker observability tends to reduce enrichment success
* higher off-target liability reduces therapeutic window
* poor formulation/process properties should correlate with manufacturing risk

To aid in initial state generation, the simulator should support preset scenario families:

* `clean_winner`
* `dose_trap`
* `subgroup_drug`
* `beautiful_biology_bad_molecule`
* `good_molecule_wrong_target`
* `operationally_doomed`
* `regulatory_gray_zone`
* `crowded_market`
* `slow_enrollment`

These are not to rig outcomes, but to make illustrative runs reproducible.

---

## 15. Failure Modes to Preserve

A realistic toy simulator should be able to generate at least the following distinct failure modes:

1. target invalidity
2. insufficient exposure in humans
3. toxicity at biologically active dose
4. wrong patient population
5. endpoint too noisy / placebo heavy
6. biomarker weak or misaligned
7. formulation / CMC blocker
8. enrollment too slow / cash exhaustion
9. successful Phase II but failed Phase III due to smaller true effect
10. submission failure due to evidence or manufacturing deficiencies
11. successful drug but insufficient commercial viability (e.g., crowded market, tiny patient population, cost of goods too high)

The same outward observation should sometimes map to multiple hidden causes.

---

## 16. Example Minimal State Machine

```python
ALLOWED_TRANSITIONS = {
    "idea": ["hit_series"],
    "hit_series": ["lead_series", "terminated"],
    "lead_series": ["development_candidate", "terminated"],
    "development_candidate": ["preclinical_ready", "terminated"],
    "preclinical_ready": ["IND_cleared", "failed", "terminated"],
    "IND_cleared": ["phase1_in_progress", "failed", "terminated"],
    "phase1_in_progress": ["phase1_complete", "failed", "terminated"],
    "phase1_complete": ["phase2_in_progress", "terminated", "failed"],
    "phase2_in_progress": ["phase2_complete", "failed", "terminated"],
    "phase2_complete": ["phase3_in_progress", "terminated", "failed"],
    "phase3_in_progress": ["phase3_complete", "failed", "terminated"],
    "phase3_complete": ["submitted", "failed", "terminated"],
    "submitted": ["approved", "failed"],
    "approved": [],
    "failed": [],
    "terminated": []
}
```

Transitions from `phaseX_in_progress` to `phaseX_complete` are triggered automatically by the simulator when the trial duration elapses. All other transitions are agent-initiated.

---

## 17. Implementation Guidance

Recommended architecture:

* `world.py`: hidden state generation and transition logic
* `program.py`: observable state, stage machine, resource accounting
* `studies.py`: preclinical package generators
* `trials.py`: clinical design and virtual patient simulation
* `regulatory.py`: regulatory feedback and approval logic
* `api.py`: agent-facing tool interface
* `scenarios.py`: preset hidden-world seeds

Recommended implementation style:

* dataclasses or pydantic models for state objects
* deterministic reproducibility via seeded RNG
* clear separation between hidden and observable state
* every action returns both `observation` and `state_delta_summary`
* enforce all preconditions at API boundary

---

## 18. Minimal Example Action Sequence

A valid sequence should look like:

```text
get_program_state()
optimize_candidate(...)
generate_preclinical_evidence(candidate_id, 'exploratory')
choose_indication(candidate_id, 'oncology_biomarker_defined')
nominate_candidate(candidate_id)
generate_preclinical_evidence(candidate_id, 'IND-enabling')
advance_program('preclinical_ready')
request_regulatory_feedback([...])
advance_program('file_IND')
design_clinical_trial('phase1', ...)
advance_program('start_phase1')
advance_program('complete_phase1')
design_clinical_trial('phase2', ...)
advance_program('start_phase2')
advance_program('complete_phase2')
...
```

An invalid sequence should fail clearly.

Examples:

* attempting `advance_program('file_IND')` before IND-enabling package complete
* attempting `start_phase2` before Phase I results exist
* attempting `nominate_candidate()` without indication selection
* attempting `submit_NDA` without completed pivotal package

---

## 19. Portfolio Policies to Preserve

The simulator should support at least these qualitatively distinct rational policies:

1. single-asset focus: invest deeply in one promising program
2. barbell portfolio: keep one advanced asset and one exploratory backup
3. kill-fast strategy: terminate weak programs early and relaunch often
4. rescue strategy: spend on ambiguity-reducing studies before terminating
5. partnering strategy: externalize one asset to fund another

No single policy should dominate all seeded scenarios.

This matters because the agent should learn that optimal action selection depends jointly on program evidence, remaining cash, and alternative opportunities.

## 20. Deliverables for the Coding Agent

The coding agent should produce:

1. a runnable simulator environment
2. an agent-facing Python API for the tools above
3. scenario presets with fixed seeds
4. a simple example policy agent
5. logs showing state, observations, and stage transitions
6. validation tests for precondition enforcement and stage graph correctness