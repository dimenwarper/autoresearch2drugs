from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import ARTIFACT_SCHEMA_VERSION, SPEC_VERSION
from .models import SimulatorConfig, serialize


class ObservabilityManager:
    def __init__(self, config: SimulatorConfig, run_id: str):
        root = Path(config.artifact_root or (Path(__file__).resolve().parent / "artifacts"))
        self.run_dir = root / run_id
        self.reports_dir = self.run_dir / "reports"
        self.study_dir = self.reports_dir / "studies"
        self.trial_dir = self.reports_dir / "trials"
        self.regulatory_dir = self.reports_dir / "regulatory"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.study_dir.mkdir(parents=True, exist_ok=True)
        self.trial_dir.mkdir(parents=True, exist_ok=True)
        self.regulatory_dir.mkdir(parents=True, exist_ok=True)
        self.event_log_path = self.run_dir / "event_log.ndjson"
        self.timeline_path = self.run_dir / "timeline_frames.ndjson"
        self.final_summary_path = self.run_dir / "final_summary.json"
        self.manifest_path = self.run_dir / "run_manifest.json"
        self.event_index = 0
        self.frame_index = 0
        self.report_refs: list[str] = []
        self.event_log_path.write_text("", encoding="utf-8")
        self.timeline_path.write_text("", encoding="utf-8")
        self.manifest = {
            "run_id": run_id,
            "spec_version": SPEC_VERSION,
            "rng_seed": config.seed,
            "scenario_preset": config.scenario_preset,
            "initial_portfolio_config": serialize(config),
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "artifact_files": {
                "event_log": "event_log.ndjson",
                "timeline_frames": "timeline_frames.ndjson",
                "final_summary": "final_summary.json",
                "reports": self.report_refs,
            },
        }
        self._write_manifest()

    def _append_json_line(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True))
            handle.write("\n")

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8")

    def emit_event(
        self,
        *,
        elapsed_months: int,
        event_type: str,
        action_name: str | None,
        program_id: str | None,
        stage_before: str | None,
        stage_after: str | None,
        summary: str,
        blocking_reasons: list[str] | None = None,
        artifact_refs: list[str] | None = None,
    ) -> int:
        payload = {
            "run_id": self.manifest["run_id"],
            "event_index": self.event_index,
            "elapsed_months": elapsed_months,
            "event_type": event_type,
            "action_name": action_name,
            "program_id": program_id,
            "stage_before": stage_before,
            "stage_after": stage_after,
            "summary": summary,
            "blocking_reasons": blocking_reasons,
            "artifact_refs": artifact_refs or [],
        }
        self._append_json_line(self.event_log_path, payload)
        self.event_index += 1
        return payload["event_index"]

    def emit_frame(
        self,
        *,
        elapsed_months: int,
        portfolio_snapshot: dict[str, Any],
        program_snapshots: list[dict[str, Any]],
        visible_opportunities: list[dict[str, Any]],
        active_work: list[dict[str, Any]],
        recent_event_refs: list[int],
    ) -> int:
        payload = {
            "run_id": self.manifest["run_id"],
            "frame_index": self.frame_index,
            "event_index": recent_event_refs[-1] if recent_event_refs else None,
            "elapsed_months": elapsed_months,
            "portfolio_snapshot": serialize(portfolio_snapshot),
            "program_snapshots": serialize(program_snapshots),
            "visible_opportunities": serialize(visible_opportunities),
            "active_work": serialize(active_work),
            "recent_event_refs": recent_event_refs,
        }
        self._append_json_line(self.timeline_path, payload)
        self.frame_index += 1
        return payload["frame_index"]

    def write_report(self, kind: str, report_id: str, payload: dict[str, Any]) -> str:
        if kind == "study":
            path = self.study_dir / f"{report_id}.json"
            ref = f"reports/studies/{report_id}.json"
        elif kind == "trial":
            path = self.trial_dir / f"{report_id}.json"
            ref = f"reports/trials/{report_id}.json"
        elif kind == "regulatory":
            path = self.regulatory_dir / f"{report_id}.json"
            ref = f"reports/regulatory/{report_id}.json"
        else:
            raise ValueError(f"Unsupported report kind: {kind}")
        path.write_text(json.dumps(serialize(payload), indent=2, sort_keys=True), encoding="utf-8")
        if ref not in self.report_refs:
            self.report_refs.append(ref)
            self._write_manifest()
        return ref

    def finalize(self, payload: dict[str, Any]) -> None:
        self.final_summary_path.write_text(
            json.dumps(serialize(payload), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self._write_manifest()

    def get_manifest(self) -> dict[str, Any]:
        return serialize(self.manifest)
