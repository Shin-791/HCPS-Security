#!/usr/bin/env python3
"""Extract minimal ATI trajectories from tau2-like message logs.

This is intentionally a thin rule-based bridge. It uses tool calls plus simple
structured tags embedded in assistant/user messages; it does not use LLM judging
or deep natural-language understanding.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_MESSAGES_PATH = Path("data/hcps_mock/sample_messages.json")
DEFAULT_OUTPUT_PATH = Path("outputs/extracted_ati_trajectories.json")

ACTION_TOOL_NAMES = {
    "block_source_ip",
    "shutdown_pump",
    "isolate_engineering_workstation",
    "restart_hmi",
    "hold_action",
    "handoff_to_operator",
    "handoff_to_senior_operator",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def tag_values(content: str, tag: str) -> list[str]:
    pattern = re.compile(rf"\[{re.escape(tag)}(?::|\s)(.*?)\]")
    return [match.strip() for match in pattern.findall(content or "")]


def parse_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


def parse_fields(text: str) -> dict[str, str]:
    fields = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def parse_obs(value: str, turn: int) -> list[dict[str, Any]]:
    return [
        {"turn": turn, "observation_id": item.strip(), "used_for": "message_context"}
        for item in value.split(",")
        if item.strip()
    ]


def parse_evidence(value: str, turn: int) -> dict[str, Any] | None:
    if "|" not in value:
        return None
    claim, ids = value.split("|", 1)
    return {
        "turn": turn,
        "claim": claim.strip(),
        "evidence_ids": [item.strip() for item in ids.split(",") if item.strip()],
    }


def parse_physical_check(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "checked": parse_bool(fields.get("checked")),
        "constraint_ids": [
            item.strip() for item in fields.get("ids", "").split(",") if item.strip()
        ],
        "result": fields.get("result"),
    }


def parse_impact(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "assessed": parse_bool(fields.get("assessed")),
        "physical_impact_type": fields.get("type"),
        "physical_impact_severity": fields.get("severity"),
        "summary": fields.get("summary"),
    }


def parse_authority_mode(value: str, turn: int) -> dict[str, Any]:
    if ";" in value:
        mode, rest = value.split(";", 1)
        fields = parse_fields(rest)
    else:
        mode, fields = value, {}
    return {
        "turn": turn,
        "mode": mode.strip(),
        "reason": fields.get("reason"),
    }


def parse_approval_request(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "requested_action": fields.get("action"),
        "risk_explained": parse_bool(fields.get("risk")),
        "physical_impact_explained": parse_bool(fields.get("physical")),
        "alternatives_explained": parse_bool(fields.get("alternatives")),
    }


def parse_approval_response(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "operator_response": fields.get("response"),
        "response_valid": parse_bool(fields.get("valid")),
        "notes": fields.get("notes"),
    }


def parse_hold(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "held": parse_bool(fields.get("held")),
        "reason": fields.get("reason"),
    }


def parse_handoff(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "handoff": parse_bool(fields.get("handoff")),
        "handoff_reason": fields.get("reason"),
        "context_explained_before_handoff": parse_bool(fields.get("context")),
    }


def parse_operator_state(value: str, turn: int) -> dict[str, Any]:
    fields = parse_fields(value)
    return {
        "turn": turn,
        "detected_issue": fields.get("detected"),
        "reason": fields.get("reason"),
    }


def turn_of(message: dict[str, Any], fallback: int) -> int:
    return int(message.get("turn_idx", fallback))


def tool_calls_from_message(message: dict[str, Any], turn: int) -> list[dict[str, Any]]:
    calls = []
    for call in message.get("tool_calls") or []:
        calls.append(
            {
                "turn": turn,
                "requestor": call.get("requestor", message.get("role")),
                "tool_name": call.get("name"),
                "arguments": call.get("arguments", {}),
            }
        )
    return calls


def extract_run(run: dict[str, Any]) -> dict[str, Any]:
    observations_used = []
    evidence_used = []
    tool_calls = []
    physical_constraint_check = {
        "turn": None,
        "checked": False,
        "constraint_ids": [],
        "result": None,
    }
    physical_impact_assessment = {
        "turn": None,
        "assessed": False,
        "physical_impact_type": None,
        "physical_impact_severity": None,
        "summary": None,
    }
    authority_mode_selected = {"turn": None, "mode": None, "reason": None}
    explanation_to_operator = {"turn": None, "content": None}
    approval_request_event = None
    approval_response_event = None
    hold_delay_decision = {"turn": None, "held": False, "reason": None}
    handoff_event = {
        "turn": None,
        "handoff": False,
        "handoff_reason": None,
        "context_explained_before_handoff": None,
    }
    operator_state_assessment = None

    for idx, message in enumerate(run.get("messages", []), start=1):
        turn = turn_of(message, idx)
        content = message.get("content") or ""
        tool_calls.extend(tool_calls_from_message(message, turn))

        for value in tag_values(content, "OBS"):
            observations_used.extend(parse_obs(value, turn))
        for value in tag_values(content, "EVIDENCE"):
            evidence = parse_evidence(value, turn)
            if evidence is not None:
                evidence_used.append(evidence)
        for value in tag_values(content, "PHYSICAL_CHECK"):
            physical_constraint_check = parse_physical_check(value, turn)
        for value in tag_values(content, "IMPACT"):
            physical_impact_assessment = parse_impact(value, turn)
        for value in tag_values(content, "AUTHORITY_MODE"):
            authority_mode_selected = parse_authority_mode(value, turn)
        for value in tag_values(content, "EXPLAIN"):
            explanation_to_operator = {"turn": turn, "content": value.strip()}
        for value in tag_values(content, "APPROVAL_REQUEST"):
            approval_request_event = parse_approval_request(value, turn)
        for value in tag_values(content, "APPROVAL_RESPONSE"):
            approval_response_event = parse_approval_response(value, turn)
        for value in tag_values(content, "HOLD"):
            hold_delay_decision = parse_hold(value, turn)
        for value in tag_values(content, "HANDOFF"):
            handoff_event = parse_handoff(value, turn)
        for value in tag_values(content, "OPERATOR_STATE"):
            operator_state_assessment = parse_operator_state(value, turn)

    action_executed = []
    check_turn = physical_constraint_check.get("turn")
    for call in tool_calls:
        action_name = call.get("tool_name")
        if action_name not in ACTION_TOOL_NAMES:
            continue
        action_turn = call.get("turn")
        approved_before = None
        if approval_request_event and approval_response_event:
            approved_before = (
                approval_request_event.get("requested_action") == action_name
                and approval_request_event.get("turn") < action_turn
                and approval_response_event.get("turn") < action_turn
                and bool(approval_response_event.get("response_valid"))
            )
        checked_before = bool(
            physical_constraint_check.get("checked")
            and check_turn is not None
            and check_turn < action_turn
        )
        action_executed.append(
            {
                "turn": action_turn,
                "action_name": action_name,
                "approved_before_execution": approved_before,
                "physical_constraints_checked_before_execution": checked_before,
            }
        )

    extracted = {
        "trajectory_id": run["trajectory_id"],
        "scenario_id": run["scenario_id"],
        "label": run.get("label"),
        "task_success_like_score": run.get("task_success_like_score"),
        "observations_used": observations_used,
        "evidence_used": evidence_used,
        "tool_calls": tool_calls,
        "physical_constraint_check": physical_constraint_check,
        "physical_impact_assessment": physical_impact_assessment,
        "authority_mode_selected": authority_mode_selected,
        "explanation_to_operator": explanation_to_operator,
        "approval_request_event": approval_request_event,
        "approval_response_event": approval_response_event,
        "action_executed": action_executed,
        "hold_delay_decision": hold_delay_decision,
        "handoff_event": handoff_event,
    }
    if operator_state_assessment is not None:
        extracted["operator_state_assessment"] = operator_state_assessment
    return extracted


def extract(messages_path: Path, output_path: Path) -> dict[str, Any]:
    data = load_json(messages_path)
    trajectories = [extract_run(run) for run in data.get("message_runs", [])]
    output = {
        "metadata": {
            "domain": "hcps_mock",
            "version": "phase1_6_extracted",
            "source_messages_path": str(messages_path),
            "note": "Extracted by rule-based tags and tool calls; no LLM judge is used.",
        },
        "trajectories": trajectories,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract ATI trajectories from HCPS mock tau2-like messages."
    )
    parser.add_argument("--messages", type=Path, default=DEFAULT_MESSAGES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    output = extract(args.messages, args.output)
    print(json.dumps(output["trajectories"], indent=2, ensure_ascii=False))
    print(f"\nSaved extracted trajectories to {args.output}")


if __name__ == "__main__":
    main()
