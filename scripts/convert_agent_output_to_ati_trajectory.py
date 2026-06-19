#!/usr/bin/env python3
"""Convert semi-structured agent outputs into ATI trajectory JSON.

This script does not call an LLM. It reads manually pasted model outputs from
data/hcps_mock/agent_outputs_sample.json and normalizes them into the same
trajectory schema used by evaluate_authority_transfer.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT_PATH = Path("data/hcps_mock/agent_outputs_sample.json")
DEFAULT_OUTPUT_PATH = Path("outputs/agent_ati_trajectories.json")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def with_turn(items: list[dict[str, Any]], start_turn: int = 1) -> list[dict[str, Any]]:
    normalized = []
    for index, item in enumerate(items, start=start_turn):
        copied = dict(item)
        copied.setdefault("turn", index)
        normalized.append(copied)
    return normalized


def normalize_event(event: dict[str, Any] | None, default_turn: int) -> dict[str, Any] | None:
    if event is None:
        return None
    copied = dict(event)
    copied.setdefault("turn", default_turn)
    return copied


def normalize_single_object(
    value: dict[str, Any] | None,
    default: dict[str, Any],
    default_turn: int | None = None,
) -> dict[str, Any]:
    if value is None:
        value = {}
    result = dict(default)
    result.update(value)
    if default_turn is not None:
        if result.get("turn") is None:
            result["turn"] = default_turn
    return result


def convert_one(agent_output: dict[str, Any]) -> dict[str, Any]:
    response = agent_output.get("response_json")
    if isinstance(response, str):
        response = json.loads(response)
    if not isinstance(response, dict):
        raise ValueError(f"response_json must be object or JSON string: {agent_output}")

    scenario_id = agent_output.get("scenario_id") or response.get("scenario_id")
    trajectory_id = agent_output.get("trajectory_id") or f"agent_{scenario_id}"

    observations = with_turn(response.get("observations_used") or [], start_turn=1)
    evidence = with_turn(response.get("evidence_used") or [], start_turn=2)

    physical_check = normalize_single_object(
        response.get("physical_constraint_check"),
        {"turn": None, "checked": False, "constraint_ids": [], "result": None},
        default_turn=3,
    )
    impact = normalize_single_object(
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
    authority = normalize_single_object(
        response.get("authority_mode_selected"),
        {"turn": None, "mode": None, "reason": None},
        default_turn=5,
    )
    explanation = normalize_single_object(
        response.get("explanation_to_operator"),
        {"turn": None, "content": None},
        default_turn=6,
    )

    approval_request = normalize_event(response.get("approval_request_event"), 6)
    approval_response = normalize_event(response.get("approval_response_event"), 7)
    actions = with_turn(response.get("action_executed") or [], start_turn=8)
    hold = normalize_single_object(
        response.get("hold_delay_decision"),
        {"turn": None, "held": False, "reason": None},
        default_turn=8,
    )
    handoff = normalize_single_object(
        response.get("handoff_event"),
        {
            "turn": None,
            "handoff": False,
            "handoff_reason": None,
            "context_explained_before_handoff": None,
        },
        default_turn=8,
    )

    return {
        "trajectory_id": trajectory_id,
        "scenario_id": scenario_id,
        "label": agent_output.get("label", "agent_output"),
        "model": agent_output.get("model"),
        "task_success_like_score": agent_output.get("task_success_like_score", 80),
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
    trajectories = [convert_one(item) for item in data.get("agent_outputs", [])]
    output = {
        "metadata": {
            "domain": "hcps_mock",
            "version": "phase2_agent_ati_trajectories",
            "source_agent_outputs_path": str(input_path),
            "note": "Converted from semi-structured agent outputs; no LLM judge is used.",
        },
        "trajectories": trajectories,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert semi-structured HCPS agent outputs to ATI trajectories."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    output = convert(args.input, args.output)
    print(json.dumps(output["trajectories"], indent=2, ensure_ascii=False))
    print(f"\nSaved agent ATI trajectories to {args.output}")


if __name__ == "__main__":
    main()
