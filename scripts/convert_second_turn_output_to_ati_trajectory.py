#!/usr/bin/env python3
"""Convert second-turn agent outputs into ATI trajectory JSON.

This is a local-only schema/conversion preparation step. It does not call
Gemini, Vertex AI, or any external API. It converts hand-written or saved
second-turn outputs into the same trajectory shape consumed by
scripts/evaluate_authority_transfer.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT_PATH = Path("data/hcps_mock/second_turn_agent_outputs_sample.json")
DEFAULT_OUTPUT_PATH = Path("/tmp/hcps_second_turn_ati_trajectories.json")
EXCLUDED_EVALUATION_STATUSES = {"api_error", "parse_error", "format_invalid"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


def parse_second_turn_decision(record: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    decision = record.get("second_turn_agent_decision")
    if isinstance(decision, dict):
        response = decision.get("response_json")
        if response is None and decision.get("raw_output"):
            response = strip_code_fence(decision["raw_output"])
    else:
        response = record.get("response_json")
        if response is None and record.get("raw_output"):
            response = strip_code_fence(record["raw_output"])

    if isinstance(response, dict):
        return response, None
    if isinstance(response, str):
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as exc:
            return {}, str(exc)
        if isinstance(parsed, dict):
            return parsed, None
        return {}, "second-turn response JSON must be an object"
    return {}, "second-turn response_json/raw_output is missing"


def normalize_items(items: list[dict[str, Any]], default_turn: int) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        copied = dict(item)
        copied.setdefault("turn", default_turn)
        normalized.append(copied)
    return normalized


def normalize_single(
    value: dict[str, Any] | None,
    default: dict[str, Any],
    default_turn: int | None = None,
) -> dict[str, Any]:
    result = dict(default)
    if isinstance(value, dict):
        result.update(value)
    if default_turn is not None and result.get("turn") is None:
        result["turn"] = default_turn
    return result


def normalize_event(event: dict[str, Any] | None, default_turn: int) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    copied = dict(event)
    copied.setdefault("turn", default_turn)
    return copied


def minimal_non_evaluable_trajectory(
    record: dict[str, Any],
    evaluation_status: str,
    conversion_error: str | None,
) -> dict[str, Any]:
    scenario_id = record.get("scenario_id")
    trajectory_id = record.get("trajectory_id") or f"second_turn_{scenario_id}_parse_error"
    return {
        "trajectory_id": trajectory_id,
        "scenario_id": scenario_id,
        "label": record.get("label", "second_turn_agent_output"),
        "evaluation_status": evaluation_status,
        "conversion_error": conversion_error,
        "source_trajectory_id": record.get("source_trajectory_id"),
        "dynamic_interaction": {
            "type": "rule_based_second_turn_output",
            "first_turn_agent_decision": record.get("first_turn_agent_decision"),
            "rule_based_operator_response": record.get("rule_based_operator_response"),
            "second_turn_agent_decision": None,
        },
        "task_success_like_score": None,
        "observations_used": [],
        "evidence_used": [],
        "tool_calls": [],
        "physical_constraint_check": {"turn": None, "checked": False, "constraint_ids": [], "result": None},
        "physical_impact_assessment": {
            "turn": None,
            "assessed": False,
            "physical_impact_type": None,
            "physical_impact_severity": None,
            "summary": None,
        },
        "authority_mode_selected": {"turn": None, "mode": None, "reason": None},
        "explanation_to_operator": {"turn": None, "content": None},
        "approval_request_event": None,
        "approval_response_event": None,
        "action_executed": [],
        "hold_delay_decision": {"turn": None, "held": False, "reason": None},
        "handoff_event": {
            "turn": None,
            "handoff": False,
            "handoff_reason": None,
            "context_explained_before_handoff": None,
        },
    }


def convert_one(record: dict[str, Any]) -> dict[str, Any]:
    scenario_id = record.get("scenario_id")
    trajectory_id = record.get("trajectory_id") or f"second_turn_{scenario_id}"
    evaluation_status = record.get("evaluation_status", "scored")

    response, parse_error = parse_second_turn_decision(record)
    if parse_error and evaluation_status == "scored":
        evaluation_status = "parse_error"
    if evaluation_status in EXCLUDED_EVALUATION_STATUSES:
        return minimal_non_evaluable_trajectory(record, evaluation_status, parse_error)

    observations = normalize_items(response.get("observations_used") or [], default_turn=3)
    evidence = normalize_items(response.get("evidence_used") or [], default_turn=4)
    physical_check = normalize_single(
        response.get("physical_constraint_check"),
        {"turn": None, "checked": False, "constraint_ids": [], "result": None},
        default_turn=4,
    )
    impact = normalize_single(
        response.get("physical_impact_assessment"),
        {
            "turn": None,
            "assessed": False,
            "physical_impact_type": None,
            "physical_impact_severity": None,
            "summary": None,
        },
        default_turn=4,
    )
    authority = normalize_single(
        response.get("authority_mode_selected"),
        {"turn": None, "mode": None, "reason": None},
        default_turn=5,
    )
    explanation = normalize_single(
        response.get("explanation_to_operator"),
        {"turn": None, "content": None},
        default_turn=5,
    )
    approval_request = normalize_event(response.get("approval_request_event"), 5)
    approval_response = normalize_event(response.get("approval_response_event"), 5)
    actions = normalize_items(response.get("action_executed") or [], default_turn=5)
    hold = normalize_single(
        response.get("hold_delay_decision"),
        {"turn": None, "held": False, "reason": None},
        default_turn=5,
    )
    handoff = normalize_single(
        response.get("handoff_event"),
        {
            "turn": None,
            "handoff": False,
            "handoff_reason": None,
            "context_explained_before_handoff": None,
        },
        default_turn=5,
    )

    return {
        "trajectory_id": trajectory_id,
        "scenario_id": scenario_id or response.get("scenario_id"),
        "label": record.get("label", "second_turn_agent_output"),
        "model": record.get("model"),
        "source_trajectory_id": record.get("source_trajectory_id"),
        "source_prompt_record_id": record.get("source_prompt_record_id"),
        "evaluation_status": evaluation_status,
        "task_success_like_score": record.get("task_success_like_score", 85),
        "dynamic_interaction": {
            "type": "rule_based_second_turn_output",
            "first_turn_agent_decision": record.get("first_turn_agent_decision"),
            "rule_based_operator_response": record.get("rule_based_operator_response"),
            "second_turn_agent_decision": response,
        },
        "observations_used": observations,
        "evidence_used": evidence,
        "tool_calls": response.get("tool_calls") or [],
        "physical_constraint_check": physical_check,
        "physical_impact_assessment": impact,
        "authority_mode_selected": authority,
        "explanation_to_operator": explanation,
        "approval_request_event": approval_request,
        "approval_response_event": approval_response,
        "action_executed": actions,
        "hold_delay_decision": hold,
        "handoff_event": handoff,
    }


def convert(input_path: Path, output_path: Path) -> dict[str, Any]:
    data = load_json(input_path)
    trajectories = [convert_one(item) for item in data.get("second_turn_agent_outputs", [])]
    output = {
        "metadata": {
            "domain": "hcps_mock",
            "version": "phase2_7_second_turn_ati_trajectories",
            "source_second_turn_outputs_path": str(input_path),
            "note": "Converted locally from second-turn sample outputs. No Gemini, Vertex AI, LLM judge, or external API call is used.",
        },
        "trajectories": trajectories,
    }
    write_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert second-turn HCPS agent outputs to ATI trajectories without calling any model API."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    output = convert(args.input, args.output)
    print(json.dumps(output["trajectories"], indent=2, ensure_ascii=False))
    print(f"\nSaved second-turn ATI trajectories to {args.output}")


if __name__ == "__main__":
    main()
