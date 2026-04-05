# autoresearch2drugs

This repo is split into two related pieces:

- `simulator/`: a toy but runnable drug-development environment
- `agent/`: a spec for an LLM-oriented harness that will learn or iterate against that simulator

The basic idea is:

1. the simulator models portfolio-level drug development under uncertainty
2. the agent harness gives a coding agent like Codex or Claude Code prompts, tools, benchmarks, and policy slots to improve decision-making against that simulator

## Simulator

[`simulator/`](./simulator/) is the environment.

It already contains:

- a public Python API for portfolio and program actions
- partially observable program state and hidden truth generation
- discovery, preclinical, clinical, and regulatory workflows
- event-queue based time advancement
- structured observability artifacts for replay and debugging
- a simple baseline policy runner
- validation tests for stage rules, gate rules, budget invariants, and queue behavior

Read:

- [`simulator/SPEC.md`](./simulator/SPEC.md)
- [`simulator/README.md`](./simulator/README.md)

Run:

```bash
python3 -m simulator
python3 -m unittest discover -s simulator/tests -v
```

## Agent

[`agent/`](./agent/) is not the runtime policy itself yet.

Right now it contains a spec for the next layer of the project: an agentic harness designed for coding agents such as Codex and Claude Code to improve policy quality against the simulator.

That harness is intended to provide:

- prompt packs for iterative agent work
- a strict policy plug-in interface
- benchmark and episode-running tools
- run inspection and result summarization tools
- experiment logging and leaderboard tracking
- boundary checks so evaluated policies only use observable simulator outputs

Read:

- [`agent/SPEC.md`](./agent/SPEC.md)

## How They Fit Together

The simulator is the benchmark environment.
The agent harness is the workflow and scaffolding for improving a policy on that benchmark.

In other words:

- `simulator/` defines the game
- `agent/` will define how an LLM coding agent plays, evaluates, and improves at that game

## Current Status

- `simulator/` is implemented and runnable
- `agent/` is currently a design spec and has not been scaffolded yet

If you want to start from the working part of the repo today, start in [`simulator/`](./simulator/).
