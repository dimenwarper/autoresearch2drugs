# Drug Development Simulator

This directory contains a toy but runnable drug-development portfolio simulator built from [`SPEC.md`](./SPEC.md).

It exposes a Python API for:

- launching programs from a visible opportunity deck
- allocating capital across a portfolio
- scheduling discovery, preclinical, clinical, and regulatory work
- advancing simulation time through an event queue
- reading observable-only state, gate flags, and action menus
- emitting structured artifacts for replay and debugging

## Requirements

- Python 3.10+
- No external dependencies

## Quick Start

Run the bundled baseline policy:

```bash
python3 -m simulator
```

Run the test suite:

```bash
python3 -m unittest discover -s simulator/tests -v
```

## Basic Usage

```python
from simulator import DrugDevelopmentSimulator, ActionError

sim = DrugDevelopmentSimulator(
    seed=7,
    scenario_preset="clean_winner",
)

portfolio = sim.get_portfolio_state()
opportunity_id = portfolio["visible_opportunities"][0]["opportunity_id"]

launch = sim.launch_program(opportunity_id, 80_000_000.0)
program_id = launch["program_summary"]["program_id"]

state = sim.get_program_state(program_id)
best_candidate = max(
    state["candidate_summaries"],
    key=lambda item: sum(item["observed_profile"].values()),
)

sim.optimize_candidate(
    program_id,
    objective_profile={
        "potency": 0.35,
        "selectivity": 0.20,
        "pk": 0.20,
        "safety_margin": 0.15,
        "developability": 0.10,
    },
    budget=4_000_000.0,
    cycles=2,
)

sim.advance_time(to_next_event=True)
state = sim.get_program_state(program_id)

sim.choose_indication(
    program_id,
    best_candidate["compound_id"],
    "target_enriched_population",
    biomarker_strategy={"validated": False},
)

actions = sim.get_available_actions(program_id=program_id, include_blocked=True)
```

If an action is invalid, the simulator raises `ActionError` with the blocking reason list flattened into a message.

## Main Control Loop

The simulator is event-driven.

- Instant actions update state immediately and do not move time.
- Scheduled actions create a work item, reserve/spend cost immediately, and complete later.
- Time only moves when you call `advance_time(months=...)` or `advance_time(to_next_event=True)`.

A typical agent loop looks like:

1. Call `get_portfolio_state()` and `get_available_actions(...)`.
2. Pick one legal action.
3. If the action schedules work, call `advance_time(...)` later to resolve it.
4. Read the new observable state and repeat until termination.

## Public API

Core read methods:

- `get_portfolio_state()`
- `get_program_state(program_id)`
- `get_available_actions(program_id=None, include_blocked=False)`
- `get_artifact_manifest()`

Portfolio actions:

- `launch_program(opportunity_id, initial_budget)`
- `allocate_budget(program_allocations)`
- `pause_program(program_id)`
- `resume_program(program_id)`
- `terminate_program(program_id, reason=None)`
- `advance_time(months=..., to_next_event=...)`

Program actions:

- `optimize_candidate(...)`
- `generate_preclinical_evidence(...)`
- `run_additional_study(...)`
- `choose_indication(...)`
- `nominate_candidate(...)`
- `design_clinical_trial(...)`
- `advance_program(program_id, action)`
- `request_regulatory_feedback(...)`

Lifecycle advancement is intentionally centralized in `advance_program(...)`. Supported advancement actions are:

- `mark_preclinical_ready`
- `file_IND`
- `start_phase1`
- `start_phase2`
- `start_phase3`
- `submit_NDA`

## Configuration

You can pass config directly:

```python
sim = DrugDevelopmentSimulator(
    seed=11,
    scenario_preset="subgroup_drug",
    initial_cash=600_000_000.0,
    time_budget_months=144,
    max_parallel_programs=4,
)
```

Or build it explicitly:

```python
from simulator import DrugDevelopmentSimulator, SimulatorConfig

config = SimulatorConfig(
    seed=11,
    scenario_preset="subgroup_drug",
    artifact_root="/tmp/drug-sim-artifacts",
)
sim = DrugDevelopmentSimulator(config=config)
```

## Supported Scenario Presets

The current build supports:

- `clean_winner`
- `dose_trap`
- `subgroup_drug`
- `beautiful_biology_bad_molecule`
- `good_molecule_wrong_target`
- `operationally_doomed`
- `regulatory_gray_zone`
- `crowded_market`
- `slow_enrollment`

## Artifacts

Each run writes a bundle to:

```text
simulator/artifacts/<run_id>/
```

The bundle includes:

- `run_manifest.json`
- `event_log.ndjson`
- `timeline_frames.ndjson`
- `reports/studies/*.json`
- `reports/trials/*.json`
- `reports/regulatory/*.json`
- `final_summary.json`

Use `get_artifact_manifest()` to retrieve the active run metadata from code.

## Notes

- The API is observable-only. Hidden biological and clinical truth is never returned by public actions.
- Gate flags and `blocking_issues` are derived from observable state, not hidden state.
- After termination, only read actions are valid.
- Running the simulator multiple times with the same config and seed will reuse the same deterministic `run_id`, so a later run will overwrite the earlier artifact bundle unless you change the seed or `artifact_root`.
