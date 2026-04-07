from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = AGENT_DIR / "SYSTEM_PROMPT.md"
POLICY_HELPERS_PATH = AGENT_DIR / "policy_helpers.py"
SCRATCH_PATH = AGENT_DIR / "scratch.py"
RESULTS_DIR = AGENT_DIR / "results"
LATEST_SUMMARY_JSON = AGENT_DIR / "latest_run_summary.json"
LATEST_SUMMARY_MD = AGENT_DIR / "latest_run_summary.md"
LATEST_COMPARISON_JSON = AGENT_DIR / "latest_comparison.json"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4.1-mini"
READ_ONLY_ACTIONS = {"get_portfolio_state", "get_program_state", "get_available_actions"}

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from simulator import ActionError, DrugDevelopmentSimulator  # noqa: E402


@dataclass(frozen=True)
class EvaluationCase:
    seed: int
    scenario_preset: str


@dataclass
class EpisodeResult:
    seed: int
    scenario_preset: str
    run_id: str
    primary_score: float
    approvals: int
    spend: float
    elapsed_months: int
    terminal_portfolio_status: str
    action_count: int
    approval_like_events: int
    policy_error_count: int
    fallback_count: int
    terminal: bool
    action_trace: list[dict[str, Any]]


class HarnessError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate and improve the editable agent policy against the simulator.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model id passed through to smolagents.")
    parser.add_argument("--scenarios", default="clean_winner", help="Comma-separated scenario preset list.")
    parser.add_argument("--seeds", default="7,11,19", help="Comma-separated deterministic seed list.")
    parser.add_argument("--max-steps", type=int, default=256, help="Maximum policy decisions per evaluation episode.")
    parser.add_argument("--agent-max-steps", type=int, default=24, help="Maximum smolagents tool-calling steps.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Model temperature for the OpenRouter call.")
    parser.add_argument("--skip-agent", action="store_true", help="Run baseline evaluation only and skip the smolagents improvement pass.")
    return parser.parse_args()


def parse_csv_list(raw: str) -> list[str]:
    items = [item.strip() for item in raw.split(",")]
    return [item for item in items if item]


def parse_seed_list(raw: str) -> list[int]:
    return [int(item) for item in parse_csv_list(raw)]


def load_scratch_module():
    module_name = f"agent_scratch_{os.getpid()}_{datetime.now(timezone.utc).timestamp():.6f}".replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, SCRATCH_PATH)
    if spec is None or spec.loader is None:
        raise HarnessError(f"Unable to load scratch module from {SCRATCH_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy_function() -> Callable[[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]], dict[str, Any] | None]:
    module = load_scratch_module()
    policy_fn = getattr(module, "choose_next_action", None)
    if not callable(policy_fn):
        raise HarnessError("agent/scratch.py must define a callable choose_next_action(portfolio_state, program_states, available_actions).")
    return policy_fn


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _program_states(sim: DrugDevelopmentSimulator, portfolio_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    program_states: dict[str, dict[str, Any]] = {}
    for summary in portfolio_state.get("program_summaries", []):
        program_id = str(summary["program_id"])
        program_states[program_id] = sim.get_program_state(program_id)
    return program_states


def _fallback_plan(legal_actions: list[dict[str, Any]], reason: str) -> dict[str, Any] | None:
    for action in legal_actions:
        if action.get("action") != "advance_time" or action.get("program_id") is not None:
            continue
        required_args = set(action.get("required_args", []))
        if "to_next_event" in required_args:
            return {
                "action": "advance_time",
                "program_id": None,
                "kwargs": {"to_next_event": True},
                "reason": reason,
            }
    for action in legal_actions:
        if action.get("action") != "advance_time" or action.get("program_id") is not None:
            continue
        required_args = set(action.get("required_args", []))
        if "months" in required_args:
            return {
                "action": "advance_time",
                "program_id": None,
                "kwargs": {"months": 1},
                "reason": reason,
            }
    return None


def _matching_descriptor(
    legal_actions: list[dict[str, Any]],
    action_name: str,
    program_id: str | None,
    kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    for descriptor in legal_actions:
        if descriptor.get("action") != action_name:
            continue
        descriptor_program_id = descriptor.get("program_id")
        if descriptor_program_id is not None and descriptor_program_id != program_id:
            continue
        if descriptor_program_id is None and program_id is not None and action_name not in {"allocate_budget"}:
            continue
        required_args = set(descriptor.get("required_args", []))
        if required_args.issubset(kwargs.keys()):
            return descriptor
    return None


def _coerce_policy_plan(
    raw_plan: Any,
    legal_actions: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    if raw_plan is None:
        return None, "policy_returned_none"
    if not isinstance(raw_plan, dict):
        return None, "policy_did_not_return_dict"
    action_name = raw_plan.get("action")
    if not isinstance(action_name, str) or not action_name:
        return None, "policy_missing_action_name"
    if action_name in READ_ONLY_ACTIONS:
        return None, "policy_chose_read_only_action"
    program_id = raw_plan.get("program_id")
    if program_id is not None and not isinstance(program_id, str):
        return None, "policy_program_id_must_be_string"
    kwargs = raw_plan.get("kwargs", {})
    if not isinstance(kwargs, dict):
        return None, "policy_kwargs_must_be_dict"
    descriptor = _matching_descriptor(legal_actions, action_name, program_id, kwargs)
    if descriptor is None:
        return None, "policy_chose_illegal_or_malformed_action"
    return {
        "action": action_name,
        "program_id": descriptor.get("program_id"),
        "kwargs": kwargs,
        "reason": str(raw_plan.get("reason", "")).strip(),
    }, None


def _dispatch_action(sim: DrugDevelopmentSimulator, plan: dict[str, Any]) -> dict[str, Any]:
    action_name = str(plan["action"])
    program_id = plan.get("program_id")
    kwargs = dict(plan.get("kwargs", {}))
    if action_name == "launch_program":
        return sim.launch_program(str(kwargs["opportunity_id"]), float(kwargs["initial_budget"]))
    if action_name == "allocate_budget":
        allocations = {str(key): float(value) for key, value in dict(kwargs["program_allocations"]).items()}
        return sim.allocate_budget(allocations)
    if action_name == "pause_program":
        return sim.pause_program(str(program_id))
    if action_name == "resume_program":
        return sim.resume_program(str(program_id))
    if action_name == "terminate_program":
        return sim.terminate_program(str(program_id), kwargs.get("reason"))
    if action_name == "advance_time":
        return sim.advance_time(**kwargs)
    if action_name == "optimize_candidate":
        objective = {str(key): float(value) for key, value in dict(kwargs["objective_profile"]).items()}
        return sim.optimize_candidate(str(program_id), objective, float(kwargs["budget"]), int(kwargs["cycles"]))
    if action_name == "choose_indication":
        biomarker_strategy = kwargs.get("biomarker_strategy")
        return sim.choose_indication(
            str(program_id),
            str(kwargs["candidate_id"]),
            str(kwargs["indication"]),
            biomarker_strategy if biomarker_strategy is None else dict(biomarker_strategy),
        )
    if action_name == "nominate_candidate":
        return sim.nominate_candidate(str(program_id), str(kwargs["candidate_id"]))
    if action_name == "request_regulatory_feedback":
        return sim.request_regulatory_feedback(str(program_id), list(kwargs["question_set"]))
    if ":" not in action_name:
        raise HarnessError(f"Unsupported action dispatch: {action_name}")

    base_action, variant = action_name.split(":", 1)
    if base_action == "generate_preclinical_evidence":
        return sim.generate_preclinical_evidence(str(program_id), str(kwargs["candidate_id"]), variant)
    if base_action == "run_additional_study":
        return sim.run_additional_study(str(program_id), variant, dict(kwargs["parameters"]))
    if base_action == "design_clinical_trial":
        return sim.design_clinical_trial(
            str(program_id),
            variant,
            str(kwargs["population_definition"]),
            str(kwargs["endpoint"]),
            str(kwargs["comparator"]),
            str(kwargs["dose_strategy"]),
            int(kwargs["duration"]),
            int(kwargs["sample_size"]),
            kwargs.get("enrichment_strategy"),
        )
    if base_action == "advance_program":
        return sim.advance_program(str(program_id), variant)
    raise HarnessError(f"Unsupported action dispatch: {action_name}")


def run_policy_episode(case: EvaluationCase, max_steps: int) -> EpisodeResult:
    policy_fn = load_policy_function()
    sim = DrugDevelopmentSimulator(seed=case.seed, scenario_preset=case.scenario_preset)
    trace: list[dict[str, Any]] = []
    policy_error_count = 0
    fallback_count = 0
    approval_like_events = 0

    for step in range(max_steps):
        portfolio_state = sim.get_portfolio_state()
        if portfolio_state["reported_metrics"]["terminal_portfolio_status"] != "ongoing":
            break

        legal_actions = sim.get_available_actions(include_blocked=False)
        program_states = _program_states(sim, portfolio_state)
        try:
            raw_plan = policy_fn(portfolio_state, program_states, legal_actions)
            plan, policy_issue = _coerce_policy_plan(raw_plan, legal_actions)
        except Exception as exc:
            plan = None
            policy_issue = f"policy_exception:{type(exc).__name__}:{exc}"

        if plan is None:
            policy_error_count += 1
            fallback_count += 1
            plan = _fallback_plan(legal_actions, policy_issue or "policy_plan_invalid")
            if plan is None:
                raise HarnessError("No legal fallback action was available while the portfolio was still ongoing.")

        try:
            result = _dispatch_action(sim, plan)
            approval_like_events += int("submit_NDA" in plan["action"] or "start_phase3" in plan["action"])
            trace.append(
                {
                    "step": step,
                    "action": plan["action"],
                    "program_id": plan.get("program_id"),
                    "reason": plan.get("reason"),
                    "kwargs": plan.get("kwargs", {}),
                    "status": "ok",
                    "result_keys": sorted(result.keys()),
                }
            )
        except ActionError as exc:
            fallback_count += 1
            fallback_plan = _fallback_plan(legal_actions, f"action_error:{exc}")
            if fallback_plan is None:
                raise
            fallback_result = _dispatch_action(sim, fallback_plan)
            trace.append(
                {
                    "step": step,
                    "action": fallback_plan["action"],
                    "program_id": fallback_plan.get("program_id"),
                    "reason": fallback_plan.get("reason"),
                    "kwargs": fallback_plan.get("kwargs", {}),
                    "status": "fallback",
                    "error": str(exc),
                    "result_keys": sorted(fallback_result.keys()),
                }
            )
    else:
        while True:
            portfolio_state = sim.get_portfolio_state()
            if portfolio_state["reported_metrics"]["terminal_portfolio_status"] != "ongoing":
                break
            legal_actions = sim.get_available_actions(include_blocked=False)
            fallback_plan = _fallback_plan(legal_actions, "max_steps_reached")
            if fallback_plan is None:
                break
            _dispatch_action(sim, fallback_plan)

    portfolio_state = sim.get_portfolio_state()
    metrics = portfolio_state["reported_metrics"]
    manifest = sim.get_artifact_manifest()
    terminal = metrics["terminal_portfolio_status"] != "ongoing"
    return EpisodeResult(
        seed=case.seed,
        scenario_preset=case.scenario_preset,
        run_id=str(manifest["run_id"]),
        primary_score=float(metrics["primary_score"]),
        approvals=int(metrics["total_approvals"]),
        spend=float(metrics["total_cost_spent"]),
        elapsed_months=int(metrics["total_elapsed_months"]),
        terminal_portfolio_status=str(metrics["terminal_portfolio_status"]),
        action_count=len(trace),
        approval_like_events=approval_like_events,
        policy_error_count=policy_error_count,
        fallback_count=fallback_count,
        terminal=terminal,
        action_trace=trace,
    )


def build_summary(cases: list[EvaluationCase], results: list[EpisodeResult], *, label: str) -> dict[str, Any]:
    scratch_text = read_text(SCRATCH_PATH)
    summary = {
        "label": label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "policy_file": str(SCRATCH_PATH),
        "policy_sha256": hash_text(scratch_text),
        "run_count": len(results),
        "mean_primary_score": mean(result.primary_score for result in results),
        "mean_approvals": mean(result.approvals for result in results),
        "mean_spend": mean(result.spend for result in results),
        "zero_approval_run_count": sum(result.approvals == 0 for result in results),
        "mean_elapsed_months": mean(result.elapsed_months for result in results),
        "mean_policy_error_count": mean(result.policy_error_count for result in results),
        "mean_fallback_count": mean(result.fallback_count for result in results),
        "terminal_run_count": sum(result.terminal for result in results),
        "scenario_presets": sorted({case.scenario_preset for case in cases}),
        "seed_list": [case.seed for case in cases],
        "episode_results": [asdict(result) for result in results],
    }
    return summary


def summary_to_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['label'].replace('_', ' ').title()} Summary",
        "",
        f"- Generated at: {summary['generated_at']}",
        f"- Policy SHA256: `{summary['policy_sha256']}`",
        f"- Runs: {summary['run_count']}",
        f"- Mean primary score: {summary['mean_primary_score']:.8f}",
        f"- Mean approvals: {summary['mean_approvals']:.3f}",
        f"- Mean spend: {summary['mean_spend']:.2f}",
        f"- Zero-approval run count: {summary['zero_approval_run_count']}",
        f"- Mean elapsed months: {summary['mean_elapsed_months']:.2f}",
        f"- Scenario presets: {', '.join(summary['scenario_presets'])}",
        f"- Seed list: {', '.join(str(seed) for seed in summary['seed_list'])}",
        "",
        "| Scenario | Seed | Score | Approvals | Spend | Months | Terminal | Policy errors | Fallbacks |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
    ]
    for result in summary["episode_results"]:
        lines.append(
            "| {scenario} | {seed} | {score:.8f} | {approvals} | {spend:.2f} | {months} | {terminal} | {policy_errors} | {fallbacks} |".format(
                scenario=result["scenario_preset"],
                seed=result["seed"],
                score=result["primary_score"],
                approvals=result["approvals"],
                spend=result["spend"],
                months=result["elapsed_months"],
                terminal=result["terminal_portfolio_status"],
                policy_errors=result["policy_error_count"],
                fallbacks=result["fallback_count"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def evaluate_cases(cases: list[EvaluationCase], max_steps: int, *, label: str) -> dict[str, Any]:
    results = [run_policy_episode(case, max_steps) for case in cases]
    return build_summary(cases, results, label=label)


def build_comparison(baseline: dict[str, Any], updated: dict[str, Any], *, agent_skipped: bool, agent_summary: str | None) -> dict[str, Any]:
    metric_names = [
        "mean_primary_score",
        "mean_approvals",
        "mean_spend",
        "zero_approval_run_count",
        "mean_elapsed_months",
        "mean_policy_error_count",
        "mean_fallback_count",
    ]
    comparison = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "agent_skipped": agent_skipped,
        "agent_summary": agent_summary,
        "baseline_metrics": {name: baseline[name] for name in metric_names},
        "updated_metrics": {name: updated[name] for name in metric_names},
        "delta_metrics": {name: updated[name] - baseline[name] for name in metric_names},
        "baseline_policy_sha256": baseline["policy_sha256"],
        "updated_policy_sha256": updated["policy_sha256"],
        "scenario_presets": updated["scenario_presets"],
        "seed_list": updated["seed_list"],
    }
    return comparison


class HarnessState:
    def __init__(self, baseline_summary: dict[str, Any], default_case: EvaluationCase):
        self.baseline_summary = baseline_summary
        self.notes: list[str] = []
        self.default_case = default_case
        self.sim = DrugDevelopmentSimulator(seed=default_case.seed, scenario_preset=default_case.scenario_preset)

    def reset_episode(self, seed: int, scenario_preset: str) -> dict[str, Any]:
        self.sim = DrugDevelopmentSimulator(seed=seed, scenario_preset=scenario_preset)
        return {
            "seed": seed,
            "scenario_preset": scenario_preset,
            "portfolio_state": self.sim.get_portfolio_state(),
            "available_actions": self.sim.get_available_actions(include_blocked=True),
        }


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def launch_smolagents_improvement(
    *,
    baseline_summary: dict[str, Any],
    cases: list[EvaluationCase],
    model_id: str,
    agent_max_steps: int,
    temperature: float,
) -> str:
    try:
        from smolagents import OpenAIServerModel, Tool, ToolCallingAgent
    except ModuleNotFoundError as exc:
        raise HarnessError(
            "smolagents and openai must be installed before launching the agent. "
            "Use `uv pip install --python .venv/bin/python smolagents openai`."
        ) from exc

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise HarnessError("OPENROUTER_API_KEY is required unless --skip-agent is used.")

    state = HarnessState(baseline_summary, cases[0])

    class ReadAgentContextTool(Tool):
        name = "read_agent_context"
        description = (
            "Read one allowed agent file. "
            "Valid file_name values are SYSTEM_PROMPT.md, policy_helpers.py, and scratch.py."
        )
        inputs = {
            "file_name": {
                "type": "string",
                "description": "One of SYSTEM_PROMPT.md, policy_helpers.py, or scratch.py.",
            }
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True
            self.allowed = {
                "SYSTEM_PROMPT.md": SYSTEM_PROMPT_PATH,
                "policy_helpers.py": POLICY_HELPERS_PATH,
                "scratch.py": SCRATCH_PATH,
            }

        def forward(self, file_name: str) -> str:
            path = self.allowed.get(file_name)
            if path is None:
                return _json_text({"ok": False, "error": f"Unsupported file_name: {file_name}"})
            return read_text(path)

    class ReadBaselineSummaryTool(Tool):
        name = "read_baseline_summary"
        description = "Read the current baseline evaluation summary for the policy before any edits."
        inputs = {}
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self) -> str:
            return _json_text(state.baseline_summary)

    class WriteScratchTool(Tool):
        name = "write_scratch"
        description = "Overwrite agent/scratch.py. The content must remain valid Python and preserve choose_next_action(...)."
        inputs = {
            "content": {
                "type": "string",
                "description": "The full new contents of agent/scratch.py.",
            }
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, content: str) -> str:
            try:
                compile(content, str(SCRATCH_PATH), "exec")
            except SyntaxError as exc:
                return _json_text(
                    {
                        "ok": False,
                        "error": f"SyntaxError: {exc.msg}",
                        "line": exc.lineno,
                        "offset": exc.offset,
                    }
                )
            write_text(SCRATCH_PATH, content)
            return _json_text({"ok": True, "policy_sha256": hash_text(content), "bytes_written": len(content.encode('utf-8'))})

    class RememberNoteTool(Tool):
        name = "remember_note"
        description = "Store a short scratch note in memory for the current agent run."
        inputs = {
            "note": {
                "type": "string",
                "description": "A short note about a policy observation or hypothesis.",
            }
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, note: str) -> str:
            state.notes.append(note.strip())
            return _json_text({"ok": True, "note_count": len(state.notes)})

    class ResetExplorationEpisodeTool(Tool):
        name = "reset_exploration_episode"
        description = "Start a fresh exploratory simulator episode for the given deterministic seed and scenario preset."
        inputs = {
            "seed": {"type": "integer", "description": "Deterministic simulator seed."},
            "scenario_preset": {"type": "string", "description": "Simulator scenario preset."},
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, seed: int, scenario_preset: str) -> str:
            return _json_text(state.reset_episode(int(seed), scenario_preset))

    class ReadPortfolioStateTool(Tool):
        name = "read_portfolio_state"
        description = "Read the current observable portfolio state from the live exploration simulator."
        inputs = {}
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self) -> str:
            return _json_text(state.sim.get_portfolio_state())

    class ReadProgramStateTool(Tool):
        name = "read_program_state"
        description = "Read the current observable program state for one program id from the live exploration simulator."
        inputs = {
            "program_id": {"type": "string", "description": "Program identifier such as prog-0001."}
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, program_id: str) -> str:
            try:
                return _json_text(state.sim.get_program_state(program_id))
            except Exception as exc:
                return _json_text({"ok": False, "error": str(exc)})

    class ReadAvailableActionsTool(Tool):
        name = "read_available_actions"
        description = (
            "Read the current available-action menu from the live exploration simulator. "
            "Pass an empty program_id string for portfolio/global actions."
        )
        inputs = {
            "program_id": {
                "type": "string",
                "description": "Program id or empty string for all actions.",
            },
            "include_blocked": {
                "type": "boolean",
                "description": "Whether to include blocked actions and blocking reasons.",
            },
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, program_id: str = "", include_blocked: bool = False) -> str:
            scoped_program_id = program_id or None
            actions = state.sim.get_available_actions(program_id=scoped_program_id, include_blocked=include_blocked)
            return _json_text(actions)

    class ApplySimulatorActionTool(Tool):
        name = "apply_simulator_action"
        description = (
            "Apply one legal simulator action from the available-action menu. "
            "Use the exact action name returned by read_available_actions, pass the program id separately if the action is program-scoped, "
            "and pass a JSON object string containing only the required args."
        )
        inputs = {
            "action_name": {"type": "string", "description": "Exact action string from the action menu."},
            "program_id": {
                "type": "string",
                "description": "Program id for program-scoped actions, or empty string for global actions.",
            },
            "arguments_json": {
                "type": "string",
                "description": "JSON object string containing the required args for the action.",
            },
        }
        output_type = "string"

        def __init__(self) -> None:
            super().__init__()
            self.is_initialized = True

        def forward(self, action_name: str, program_id: str = "", arguments_json: str = "{}") -> str:
            try:
                kwargs = json.loads(arguments_json or "{}")
                if not isinstance(kwargs, dict):
                    raise ValueError("arguments_json must decode to a JSON object.")
            except Exception as exc:
                return _json_text({"ok": False, "error": f"Invalid JSON: {exc}"})

            legal_actions = state.sim.get_available_actions(include_blocked=False)
            descriptor = _matching_descriptor(legal_actions, action_name, program_id or None, kwargs)
            if descriptor is None:
                return _json_text({"ok": False, "error": "Action is not currently legal with the provided args."})

            plan = {
                "action": action_name,
                "program_id": descriptor.get("program_id"),
                "kwargs": kwargs,
                "reason": "manual_tool_call",
            }
            try:
                result = _dispatch_action(state.sim, plan)
            except Exception as exc:
                return _json_text({"ok": False, "error": str(exc)})
            return _json_text({"ok": True, "result": result, "portfolio_state": state.sim.get_portfolio_state()})

    client_kwargs: dict[str, Any] = {}
    http_referer = os.environ.get("OPENROUTER_HTTP_REFERER")
    app_title = os.environ.get("OPENROUTER_APP_TITLE", "autoresearch2drugs")
    if http_referer or app_title:
        headers: dict[str, str] = {}
        if http_referer:
            headers["HTTP-Referer"] = http_referer
        if app_title:
            headers["X-Title"] = app_title
        client_kwargs["default_headers"] = headers

    model = OpenAIServerModel(
        model_id=model_id,
        api_base=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL),
        api_key=api_key,
        client_kwargs=client_kwargs or None,
        temperature=temperature,
    )
    instructions = read_text(SYSTEM_PROMPT_PATH)
    tools = [
        ReadAgentContextTool(),
        ReadBaselineSummaryTool(),
        WriteScratchTool(),
        RememberNoteTool(),
        ResetExplorationEpisodeTool(),
        ReadPortfolioStateTool(),
        ReadProgramStateTool(),
        ReadAvailableActionsTool(),
        ApplySimulatorActionTool(),
    ]
    agent = ToolCallingAgent(
        tools=tools,
        model=model,
        instructions=instructions,
        max_steps=agent_max_steps,
        planning_interval=4,
        add_base_tools=False,
    )

    task = "\n".join(
        [
            "Improve agent/scratch.py to raise the externally evaluated mean primary score.",
            "",
            "Current deterministic evaluation panel:",
            _json_text([asdict(case) for case in cases]),
            "",
            "Current baseline aggregate metrics:",
            _json_text({key: baseline_summary[key] for key in baseline_summary if key != "episode_results"}),
            "",
            "Use the allowed tools only.",
            "Read policy_helpers.py and scratch.py before editing.",
            "Make a single coherent update to scratch.py and keep the policy deterministic.",
            "If you run exploration episodes, stay within the public simulator API and do not use artifact files.",
            "End with a short final summary of the change.",
        ]
    )
    final_answer = agent.run(task)
    return str(final_answer)


def main() -> int:
    args = parse_args()
    scenarios = parse_csv_list(args.scenarios)
    seeds = parse_seed_list(args.seeds)
    cases = [EvaluationCase(seed=seed, scenario_preset=scenario) for scenario in scenarios for seed in seeds]
    if not cases:
        raise HarnessError("At least one evaluation case is required.")

    if not args.skip_agent and not os.environ.get("OPENROUTER_API_KEY"):
        raise HarnessError("OPENROUTER_API_KEY is required unless --skip-agent is used.")

    run_dir = RESULTS_DIR / now_stamp()
    baseline_summary = evaluate_cases(cases, args.max_steps, label="baseline")
    write_json(run_dir / "baseline_summary.json", baseline_summary)
    write_text(run_dir / "baseline_summary.md", summary_to_markdown(baseline_summary))

    agent_summary: str | None = None
    if args.skip_agent:
        updated_summary = baseline_summary
    else:
        agent_summary = launch_smolagents_improvement(
            baseline_summary=baseline_summary,
            cases=cases,
            model_id=args.model,
            agent_max_steps=args.agent_max_steps,
            temperature=args.temperature,
        )
        updated_summary = evaluate_cases(cases, args.max_steps, label="updated")

    comparison = build_comparison(
        baseline_summary,
        updated_summary,
        agent_skipped=args.skip_agent,
        agent_summary=agent_summary,
    )
    write_json(run_dir / "updated_summary.json", updated_summary)
    write_text(run_dir / "updated_summary.md", summary_to_markdown(updated_summary))
    write_json(run_dir / "comparison.json", comparison)
    write_json(LATEST_SUMMARY_JSON, updated_summary)
    write_text(LATEST_SUMMARY_MD, summary_to_markdown(updated_summary))
    write_json(LATEST_COMPARISON_JSON, comparison)

    print(_json_text({"run_dir": str(run_dir), "latest_summary": str(LATEST_SUMMARY_JSON), "latest_comparison": str(LATEST_COMPARISON_JSON)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
