#!/usr/bin/env python3
"""Normalize raw second-turn Gemini-like outputs into the local ATI schema.

This is a local-only preparation step. It does not call Gemini, Vertex AI, or
any external API, and it does not read credentials or tokens. The input is a
JSONL file containing saved/mock raw second-turn responses. The output matches
the schema consumed by convert_second_turn_output_to_ati_trajectory.py.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT_PATH = Path("data/hcps_mock/second_turn_gemini_raw_sample.jsonl")
DEFAULT_OUTPUT_PATH = Path("outputs/second_turn_gemini_agent_outputs.json")


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


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                records.append(
                    {
                        "trajectory_id": f"jsonl_line_{line_number}",
                        "evaluation_status": "parse_error",
                        "parse_error_message": f"Invalid JSONL record: {exc}",
                        "line_number": line_number,
                    }
                )
                continue
            record.setdefault("line_number", line_number)
            records.append(record)
    return records


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def extract_text_from_gemini_like_response(record: dict[str, Any]) -> str | None:
    for key in ("raw_output", "response_text", "text"):
        value = record.get(key)
        if isinstance(value, str):
            return value

    response = record.get("gemini_response") or record.get("raw_response")
    if not isinstance(response, dict):
        response = record

    try:
        parts = response["candidates"][0]["content"]["parts"]
        if parts and isinstance(parts[0].get("text"), str):
            return parts[0]["text"]
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return None


def parse_agent_response(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(record.get("response_json"), dict):
        return record["response_json"], None

    text = extract_text_from_gemini_like_response(record)
    if text is None:
        return None, "No response text found in raw record."

    try:
        parsed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(parsed, dict):
        return None, "Parsed response is not a JSON object."
    return parsed, None


def normalize_one(record: dict[str, Any]) -> dict[str, Any]:
    response_json, parse_error = parse_agent_response(record)
    evaluation_status = record.get("evaluation_status") or (
        "parse_error" if parse_error else "scored"
    )

    normalized = {
        "scenario_id": record.get("scenario_id"),
        "trajectory_id": record.get("trajectory_id")
        or f"second_turn_{record.get('scenario_id', 'unknown')}_{record.get('line_number', 'record')}",
        "source_trajectory_id": record.get("source_trajectory_id"),
        "source_prompt_record_id": record.get("source_prompt_record_id") or record.get("record_id"),
        "label": record.get("label", "second_turn_gemini_normalized"),
        "model": record.get("model"),
        "trial": record.get("trial"),
        "evaluation_status": evaluation_status,
        "parse_error_message": parse_error,
        "raw_output_available": extract_text_from_gemini_like_response(record) is not None,
        "first_turn_agent_decision": record.get("first_turn_agent_decision"),
        "rule_based_operator_response": record.get("rule_based_operator_response")
        or record.get("operator_response"),
        "second_turn_agent_decision": {
            "response_json": response_json,
        },
    }
    return normalized


def normalize(input_path: Path, output_path: Path) -> dict[str, Any]:
    raw_records = load_jsonl(input_path)
    normalized_records = [normalize_one(record) for record in raw_records]
    output = {
        "metadata": {
            "domain": "hcps_mock",
            "version": "phase2_8_second_turn_gemini_normalized",
            "note": "Normalized locally from saved Gemini-like second-turn records. No Gemini, Vertex AI, LLM judge, or external API call is used.",
            "source_raw_jsonl_path": str(input_path),
            "api_call_made": False,
        },
        "second_turn_agent_outputs": normalized_records,
    }
    write_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize saved second-turn Gemini-like JSONL outputs into the local ATI schema without calling any API."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Print normalized JSON and do not write files.")
    args = parser.parse_args()

    if args.dry_run:
        raw_records = load_jsonl(args.input)
        output = {
            "metadata": {
                "domain": "hcps_mock",
                "version": "phase2_8_second_turn_gemini_normalized",
                "note": "Dry run normalization only. No API call is used.",
                "source_raw_jsonl_path": str(args.input),
                "api_call_made": False,
            },
            "second_turn_agent_outputs": [normalize_one(record) for record in raw_records],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    output = normalize(args.input, args.output)
    print(json.dumps(output["second_turn_agent_outputs"], indent=2, ensure_ascii=False))
    print(f"\nSaved normalized second-turn outputs to {args.output}")


if __name__ == "__main__":
    main()
