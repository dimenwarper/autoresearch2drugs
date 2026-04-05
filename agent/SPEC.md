# LLM Agent Harness Spec

## Purpose

This spec defines the `agent/` product as an agentic harness for coding agents such as Codex and Claude Code.

The harness exists to help a coding agent:

- bootstrap against the simulator in [`simulator/`](../simulator/)
- run episodes and benchmark sweeps
- inspect observable outcomes and artifacts
- edit an agent policy implementation
- iterate toward higher reward

This is not a spec for a fixed hand-authored portfolio policy by itself.
It is a spec for the prompt, tool, evaluation, and policy-plug-in framework that lets a coding agent improve that policy over time.

The simulator remains the environment of record.

The north-star metric remains:

```text
primary_score = N_approved / C_total
```

where:

- `N_approved` is the number of approved programs before termination
- `C_total` is total portfolio cash spent

---

## 1. Product Definition

The `agent/` directory should become a self-contained experimentation harness with four parts:

1. prompt pack for Codex / Claude Code
2. tool entrypoints for running and inspecting simulator episodes
3. pluggable runtime policy code
4. benchmark and experiment tracking utilities

The harness should make it easy for a coding agent to answer:

- what is the current baseline score?
- what failure mode is hurting performance?
- what policy change should be tried next?
- did the change improve held-out performance?

---

## 2. Core Design Goal

The harness should optimize for fast iterative improvement by an external coding agent.

That means it should be:

- easy to start from a cold repo state
- explicit about what files the coding agent should edit
- explicit about what commands it should run
- deterministic enough to compare changes across seeds
- strict about the public simulator boundary during runtime evaluation

The coding agent should not need to invent a workflow.
The workflow should already be encoded in the harness.

---

## 3. Users

### 3.1 Primary user

The primary user is a coding agent with:

- shell access
- file editing ability
- ability to read and write code
- ability to run repeated evaluations

Examples:

- Codex
- Claude Code

### 3.2 Secondary user

A human researcher should also be able to:

- run the same benchmark scripts
- inspect experiment summaries
- compare policies
- override prompts or evaluation settings

---

## 4. Harness Boundary

### 4.1 What the harness may do

The harness may:

- call the public simulator API
- read simulator-produced artifact bundles after a run completes
- compare aggregate results across many runs
- write policy code under `agent/`
- write experiment summaries under `agent/`

### 4.2 What the runtime policy must not do

The runtime policy being evaluated must not:

- access simulator hidden state directly
- import private simulator internals to leak truth
- use debug-only outputs
- inspect current-run artifacts as part of decision making

The evaluated runtime policy must act only from observable simulator outputs.

### 4.3 Benchmark integrity rule

The harness should treat `simulator/` as frozen during policy evaluation.

The coding agent may read simulator code for understanding, but benchmark scripts should evaluate only changes under `agent/` unless the user explicitly asks to change simulator behavior.

---

## 5. Required Directory Layout

Recommended target structure:

```text
agent/
  SPEC.md
  README.md
  prompts/
    SYSTEM.md
    BOOTSTRAP.md
    ITERATE.md
    ANALYZE.md
    SHIP.md
  policy/
    __init__.py
    interface.py
    baseline.py
    working.py
    heuristics.py
    scoring.py
    memory.py
  tools/
    run_episode.py
    benchmark.py
    inspect_run.py
    summarize_results.py
    check_policy_boundary.py
  experiments/
    leaderboard.json
    latest_summary.md
    runs/
  tests/
    test_policy_interface.py
    test_boundary.py
    test_benchmark_smoke.py
```

The exact filenames may vary, but the harness should clearly separate:

- prompts
- runtime policy code
- executable tools
- experiment outputs

---

## 6. Prompt Pack

The prompt pack should be first-class, not incidental.

### 6.1 `SYSTEM.md`

Defines the standing instructions for the coding agent.

It should include:

- goal: improve benchmark score
- constraint: runtime policy must use only observable simulator state
- workflow: evaluate, inspect, patch, re-evaluate
- output discipline: record results after each experiment
- anti-cheating rule: do not modify simulator to expose hidden truth

### 6.2 `BOOTSTRAP.md`

Used when starting from scratch.

It should tell the coding agent to:

1. run the baseline benchmark
2. inspect score summaries
3. inspect a small number of representative failed runs
4. identify the biggest failure mode
5. propose the smallest policy change likely to help

### 6.3 `ITERATE.md`

Used for the standard improvement loop.

It should tell the coding agent to:

1. compare current policy against baseline
2. focus on one clear hypothesis at a time
3. make a bounded code change
4. rerun the benchmark slice
5. log the result and next hypothesis

### 6.4 `ANALYZE.md`

Used when performance stagnates.

It should emphasize:

- stage-specific failure clustering
- budget misallocation
- poor trial design choices
- over-persistence in weak programs
- under-launching or over-launching

### 6.5 `SHIP.md`

Used when preparing a candidate best policy.

It should require:

- final benchmark run
- summary vs baseline
- known weaknesses
- list of files changed

---

## 7. Policy Plug-In Contract

The harness should define a strict policy interface so benchmark tools do not depend on ad hoc code.

### 7.1 Required interface

The recommended interface is:

```python
class Policy:
    def reset(self, run_context: dict) -> None: ...
    def select_action(
        self,
        portfolio_state: dict,
        program_states: dict[str, dict],
        available_actions: list[dict],
    ) -> dict: ...
    def observe(self, action: dict, observation: dict) -> None: ...
```

### 7.2 Action output format

The policy should return a structured action request like:

```python
{
    "action": "launch_program",
    "kwargs": {
        "opportunity_id": "opp-001",
        "initial_budget": 80000000.0,
    },
}
```

### 7.3 Policy input rules

The benchmark harness should pass the policy only:

- `get_portfolio_state()` output
- `get_program_state(program_id)` outputs
- `get_available_actions(...)` outputs

The policy should not receive the simulator object directly if that would make boundary enforcement weaker.

---

## 8. Required Tool Entry Points

The harness should provide stable command-line entrypoints a coding agent can rely on.

### 8.1 `run_episode`

Run a single policy episode against one scenario and seed.

Required inputs:

- policy module or policy name
- scenario preset
- seed
- optional max steps
- optional output directory

Required outputs:

- primary score
- approvals
- spend
- elapsed months
- terminal summary
- simulator artifact manifest
- policy decision log location

### 8.2 `benchmark`

Run a policy over a fixed evaluation panel.

Required features:

- deterministic seed set
- multiple scenario presets
- JSON summary output
- table/markdown summary output
- comparison against baseline policy

### 8.3 `inspect_run`

Summarize one run for failure analysis.

It should surface:

- action timeline
- stage progression by program
- major blockers encountered
- terminal outcomes
- likely policy mistakes

### 8.4 `summarize_results`

Aggregate recent experiment outputs into:

- leaderboard
- latest best policy summary
- notable regressions

### 8.5 `check_policy_boundary`

Static or runtime check that the evaluated policy does not import forbidden simulator internals.

---

## 9. Evaluation Workflow

The default coding-agent workflow should be:

1. run baseline benchmark
2. inspect failures
3. edit policy code
4. run a small smoke benchmark
5. if improved, run the full benchmark panel
6. update experiment log and leaderboard

The harness should make this loop cheap.

### 9.1 Smoke evaluation

A small fast panel for iteration:

- 1 to 2 seeds per scenario
- a reduced scenario subset
- quick enough for repeated use during development

### 9.2 Full evaluation

A slower comparison panel for claiming improvement.

At minimum include:

- `clean_winner`
- `dose_trap`
- `subgroup_drug`
- `beautiful_biology_bad_molecule`
- `good_molecule_wrong_target`
- `operationally_doomed`
- `regulatory_gray_zone`
- `crowded_market`
- `slow_enrollment`

---

## 10. Experiment Logging

Every benchmark run should produce a policy-side experiment record under `agent/experiments/`.

At minimum record:

- timestamp
- git commit or workspace marker if available
- policy name
- benchmark panel used
- aggregate metrics
- comparison vs baseline
- short hypothesis being tested
- free-text notes on what changed

The harness should maintain:

- a machine-readable leaderboard
- a human-readable latest summary

---

## 11. Metrics

### 11.1 Primary metric

```text
primary_score = N_approved / C_total
```

### 11.2 Required secondary metrics

The harness should report at least:

- mean primary score
- median primary score
- total approvals
- approvals per run
- total spend
- time to first approval
- zero-approval run fraction
- phase transition counts
- approval rate by scenario preset

### 11.3 Improvement test

A new policy should only be considered better if it beats the baseline on the agreed panel, not just on one favorite seed.

---

## 12. Failure Analysis Expectations

The harness should help the coding agent diagnose policy failures, not just emit a scalar score.

Common categories to highlight:

- failed to fund a promising program enough to reach the next milestone
- over-invested in a weak program
- advanced into expensive clinical work too early
- failed to gather the right gate-clearing evidence
- designed poor trials
- underused pause or terminate
- overused low-value studies
- failed to launch enough opportunities

Run-inspection tooling should summarize these patterns from observable traces and artifacts.

---

## 13. Coding-Agent Guidance

The harness should explicitly guide Codex / Claude Code toward productive behavior.

Recommended standing guidance:

- start with baseline measurement, not guesswork
- change one policy hypothesis at a time
- prefer small, testable edits
- use smoke benchmarks during iteration
- inspect concrete failed runs before broad refactors
- do not tune only to one scenario
- do not modify simulator scoring or hidden-state exposure

---

## 14. Non-Goals

The first version of the harness does not need:

- reinforcement learning infrastructure
- distributed hyperparameter search
- fine-tuning pipelines
- a web UI
- multi-agent orchestration inside the runtime policy

Those can come later if needed.

The initial goal is a strong iterative coding-agent workflow around a pluggable policy.

---

## 15. Deliverables

The intended implementation of `agent/` should produce:

1. a prompt pack for Codex / Claude Code
2. a strict runtime policy interface
3. a baseline policy
4. a working policy slot for iterative edits
5. CLI tools for episode runs, benchmarks, run inspection, and result summaries
6. experiment logging and leaderboard files
7. tests for policy interface, benchmark smoke runs, and boundary enforcement

The important thing is that a coding agent can clone the workflow immediately:

- read the prompt
- run the benchmark
- inspect the result
- patch the policy
- measure improvement

without inventing the scaffolding from scratch.
