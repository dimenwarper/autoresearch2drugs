# Minimal Smolagents Harness Spec

## Purpose

The `agent/` directory should be a very small outer-loop optimization harness built around:

- `smolagents`
- an OpenRouter-backed model
- one read-only helper file
- one writable scratch pad file
- one entrypoint script that evaluates and then invokes the agent

The simulator remains the environment in [`simulator/`](../simulator/).

The harness exists only to help an LLM improve policy logic against that simulator.

The benchmark objective remains:

```text
primary_score = N_approved / C_total
```

where:

- `N_approved` is the number of approved programs before termination
- `C_total` is total portfolio cash spent

This spec is intentionally minimal for outer-loop optimization.

---

## 1. Minimal File Set

The harness should stay close to this layout:

```text
agent/
  SPEC.md
  SYSTEM_PROMPT.md
  policy_helpers.py
  scratch.py
  run_agent.py
```

Optional generated outputs are allowed:

```text
agent/
  results/
  latest_run_summary.json
  latest_run_summary.md
  latest_comparison.json
```

But the authored source of truth should remain only:

- one system prompt
- one read-only helper module
- one writable policy-routing module
- one orchestration script

---

## 2. Core Design

The agent loop should work like this:

- `run_agent.py` jumpstarts the simulations, the following is repeated N times:
   - A `smolagents` agent using an OpenRouter model is started
   - The agent reads:
     - `SYSTEM_PROMPT.md`
     - `policy_helpers.py`
  - `run_agent.py` creates a fresh `DrugDevelopmentSimulator` from `simulator/api.py`
  - The agent then drives that simulator step by step using the same public loop shape shown in `simulator/example_agent.py`
  - At each step the agent will:
    - Read observable state from `sim.get_portfolio_state()`, `sim.get_program_state(program_id)`, and `sim.get_available_actions(...)`
    - Read and use helpers from `policy_helpers.py` to guide its decisions
    - Edit the `scratch.py` file with any useful notes/helpers
    - Choose one legal simulator API action to perform next
    - Advance the simulation timestep with the public API
    - End the simulation if time/budget runs out
- The agent then summarizes the result of all simulator runs

Note that the LLM does not own benchmarking.

---

## 3. Backend

The backend should be `smolagents`, not Codex Desktop or Claude Code.

### 3.1 Model source

The model should be accessed through OpenRouter.

The entrypoint should require:

- `OPENROUTER_API_KEY` in the environment

If `OPENROUTER_API_KEY` is missing, `run_agent.py` should fail fast with a clear error.

### 3.2 Model selection

`run_agent.py` should accept a model string argument, for example:

- `--model`

The value should be passed through to the OpenRouter-backed `smolagents` model configuration.

### 3.3 Tooling stance

The `smolagents` instance should be configured as narrowly as practical.

Preferred tool surface:

- file read
- file write restricted to `agent/scratch.py`
- optional simple note output

Avoid:

- arbitrary shell execution
- arbitrary Python execution outside the harness
- network tools beyond the LLM call itself
- direct reads of simulator source or artifact files as live planning input

The agent should behave like a controlled editor over `scratch.py` and a constrained consumer of the public simulator API, not a free-form autonomous shell user.

---

## 4. File Responsibilities

### 4.1 `SYSTEM_PROMPT.md`

This file is the standing instruction block passed to the `smolagents` agent.

It should explain:

- what the simulator is
- what the optimization metric is
- that the agent is improving a routing policy over observable simulator state
- that only `agent/scratch.py` is writable
- that `agent/policy_helpers.py` is read-only
- that simulator interaction happens only through the public API in `simulator/api.py`
- that `simulator/example_agent.py` shows the intended turn-taking pattern
- that evaluation is run externally by `agent/run_agent.py`
- that edits should be small and measurable


### 4.2 `policy_helpers.py`

This file is read-only.

It should contain deterministic helper functions that the agent may inspect and use, but not edit.
For starters and to jumpstart this file, let's put the following helpers:

- ranking visible opportunities
- ranking candidates within a program
- checking common blocker patterns
- scoring stage urgency
- normalizing available actions
- budget heuristics


See sections belowfor an example

`policy_helpers.py` should be:

- deterministic
- pure
- side-effect free
- observable-only

It must not contain:

- hidden-state access
- filesystem writes
- subprocess calls
- network calls

The outer loop should treat this file as part of the fixed substrate.

### 4.3 `scratch.py`

This file is the only intended optimization surface.

It should contain the top-level policy routing logic that decides what to do next from observable simulator inputs.

This file is writable by the `smolagents` agent.

It should stay deterministic and should be limited to routing and action choice.

### 4.4 `run_agent.py`

This is the single entrypoint.

It should:

1. validate `OPENROUTER_API_KEY`
2. evaluate the current policy over `N` deterministic runs
3. write summary files
4. launch the `smolagents` agent with the system prompt and relevant context, as well as relevant permission structure
5. rerun the same evaluation panel
6. write a before/after comparison

This file owns:

- simulation/agent orchestration
- seed selection
- scenario selection
- summary generation
- agent invocation

---

## 5. Runtime Boundary

The evaluated policy must act only on observable simulator outputs from a live `DrugDevelopmentSimulator` instance in `simulator/api.py`.

Allowed runtime inputs:

- `sim.get_portfolio_state()`
- `sim.get_program_state(program_id)`
- `sim.get_available_actions(...)`
- legal simulator action methods selected from the available-action menu
- `sim.advance_time(...)`

Forbidden runtime inputs:

- simulator hidden state
- internal simulator dataclasses
- debug-only outputs
- reading `simulator/*.py` or `simulator/artifacts/*` as live planning context during a run
- current-run artifact files used as a live planning shortcut

`simulator/example_agent.py` is the reference shape for this interaction loop.
The runtime policy should be treated as a clean consumer of the public simulator API.

---

## 6. `policy_helpers.py` Should Be Read-Only

This requirement is deliberate.

`policy_helpers.py` should hold stable, reusable primitives so the outer-loop agent is not constantly rewriting low-level utilities.

The harness should reinforce this in two ways:

1. prompt-level instruction
2. tool-level write restriction, if possible

If file-level tool restriction is feasible, only `scratch.py` should be writable.

## 7 Minimum output

After `run_agent.py` finishes all simulations, it should summarize all runs via:

- mean primary score
- mean approvals
- mean spend
- zero-approval run count
- mean elapsed months
- scenario preset(s)
- seed list

Also write a compact comparison file summarizing:

- baseline metrics
- updated metrics
- delta metrics
