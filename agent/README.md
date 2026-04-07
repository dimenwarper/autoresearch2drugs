# Agent Harness

This directory contains a minimal outer-loop harness for improving a deterministic simulator policy with `smolagents`.

The harness is built around:

- a fixed system prompt in `SYSTEM_PROMPT.md`
- a fixed helper substrate in `policy_helpers.py`
- a writable routing policy in `scratch.py`
- a single orchestration entrypoint in `run_agent.py`

The simulator environment remains in [`../simulator/`](../simulator/).

## Requirements

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- an OpenRouter API key for the live agent-improvement loop

The baseline evaluator does not require network access or an API key.

## Setup

From the repo root:

```bash
uv venv .venv
uv pip install --python .venv/bin/python smolagents openai
```

## Quick Start

Run a deterministic baseline evaluation only:

```bash
./.venv/bin/python agent/run_agent.py --skip-agent
```

Run the full baseline -> improve -> reevaluate loop:

```bash
export OPENROUTER_API_KEY=...
./.venv/bin/python agent/run_agent.py --model openai/gpt-4.1-mini
```

Useful options:

- `--scenarios clean_winner,dose_trap`
- `--seeds 7,11,19`
- `--max-steps 256`
- `--agent-max-steps 24`
- `--temperature 0.2`

Optional OpenRouter settings:

- `OPENROUTER_BASE_URL`
- `OPENROUTER_HTTP_REFERER`
- `OPENROUTER_APP_TITLE`

## What `run_agent.py` Does

`run_agent.py` owns benchmarking.

For a normal full run it will:

1. evaluate the current `scratch.py` policy over a deterministic seed/scenario panel
2. write a baseline summary
3. launch a restricted `smolagents` tool-calling agent through OpenRouter
4. allow that agent to inspect the fixed files, explore the simulator through public API tools, and overwrite only `scratch.py`
5. rerun the same evaluation panel
6. write updated summaries and a before/after comparison

If `--skip-agent` is used, only the deterministic evaluation path runs.

## File Roles

- `SPEC.md`: design spec for the harness
- `SYSTEM_PROMPT.md`: standing instructions passed to the `smolagents` agent
- `policy_helpers.py`: deterministic read-only helper functions
- `scratch.py`: writable deterministic routing policy surface
- `run_agent.py`: evaluation, summary writing, and agent orchestration

## Runtime Boundary

The policy is evaluated only against the public simulator API.

Allowed observable reads:

- `get_portfolio_state()`
- `get_program_state(program_id)`
- `get_available_actions(...)`

Allowed mutations:

- legal public simulator actions
- `advance_time(...)`

The policy and the live agent should not read simulator source files or simulator artifact files as planning shortcuts during an episode.

## Outputs

Each run writes outputs under:

```text
agent/results/<timestamp>/
```

It also refreshes:

- `latest_run_summary.json`
- `latest_run_summary.md`
- `latest_comparison.json`

These files include:

- mean primary score
- mean approvals
- mean spend
- zero-approval run count
- mean elapsed months
- scenario presets
- seed list
- per-episode results

## Notes

- `scratch.py` must keep a callable `choose_next_action(portfolio_state, program_states, available_actions)`.
- The evaluator will fall back to legal `advance_time(...)` actions if the policy produces an invalid plan, and it records those fallbacks in the summary.
- The current default policy is intentionally simple; the harness is meant to let the model iterate on it.
