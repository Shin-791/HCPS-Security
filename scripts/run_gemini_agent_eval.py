#!/usr/bin/env python3
"""Run Gemini on HCPS mock scenarios and evaluate outputs with ATI.

This script stays outside tau2/tau3 internals. It supports:
- Gemini API key via GEMINI_API_KEY
- Vertex AI Application Default Credentials via google-auth

No API keys or credential files are read from the repository or hard-coded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from convert_agent_output_to_ati_trajectory import convert as convert_agent_outputs
from evaluate_authority_transfer import evaluate as evaluate_ati


DEFAULT_PROMPT_PATH = Path("prompts/hcps_agent_prompt.md")
DEFAULT_INPUTS_PATH = Path("data/hcps_mock/agent_eval_inputs.json")
DEFAULT_RAW_OUTPUT_PATH = Path("outputs/gemini_agent_outputs.json")
DEFAULT_TRAJECTORY_OUTPUT_PATH = Path("outputs/gemini_ati_trajectories.json")
DEFAULT_SCORE_REPORT_PATH = Path("outputs/ati_score_report_gemini.json")
DEFAULT_SCORE_SUMMARY_PATH = Path("outputs/ati_score_summary_gemini.md")
DEFAULT_FINAL_REPORT_PATH = Path("outputs/gemini_evaluation_report.json")
DEFAULT_TASKS_PATH = Path("data/hcps_mock/tasks.json")

REQUIRED_RESPONSE_FIELDS = [
    "scenario_id",
    "observations_used",
    "evidence_used",
    "physical_constraint_check",
    "physical_impact_assessment",
    "authority_mode_selected",
    "explanation_to_operator",
    "approval_request_event",
    "approval_response_event",
    "action_executed",
    "hold_delay_decision",
    "handoff_event",
]


class GeminiAuthError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def parse_agent_json(raw_output: str) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    try:
        parsed = json.loads(strip_code_fence(raw_output))
    except json.JSONDecodeError as exc:
        return None, str(exc), REQUIRED_RESPONSE_FIELDS
    if not isinstance(parsed, dict):
        return None, "Top-level response is not a JSON object.", REQUIRED_RESPONSE_FIELDS
    missing = [field for field in REQUIRED_RESPONSE_FIELDS if field not in parsed]
    return parsed, None, missing


def safe_scenario_for_prompt(scenario: dict[str, Any]) -> dict[str, Any]:
    """Return only agent-visible scenario fields.

    This deliberately excludes evaluator-only labels and scoring criteria.
    """
    return {
        "scenario_id": scenario["scenario_id"],
        "incident_description": scenario["incident_description"],
        "agent_visible_context": scenario["agent_visible_context"],
        "available_actions": scenario["available_actions"],
        "dangerous_actions": scenario.get("dangerous_actions", []),
    }


def build_user_prompt(scenario: dict[str, Any]) -> str:
    safe = safe_scenario_for_prompt(scenario)
    return (
        "Evaluate the following HCPS/SOC-OT scenario and return only the required JSON.\n\n"
        + json.dumps(safe, indent=2, ensure_ascii=False)
    )


def generate_with_api_key(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAuthError("GEMINI_API_KEY が設定されていません。")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    return call_gemini_http(url, payload, timeout=timeout)


def get_adc_token() -> str:
    try:
        import google.auth
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise GeminiAuthError(
            "Vertex AI ADC を使うには google-auth が必要です。"
            " `pip install google-auth requests` を実行してください。"
        ) from exc
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(Request())
    if not credentials.token:
        raise GeminiAuthError("Application Default Credentials のトークン取得に失敗しました。")
    return credentials.token


def generate_with_vertex_adc(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    project: str | None,
    location: str,
) -> str:
    project_id = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise GeminiAuthError(
            "Vertex AI を使うには --project または GOOGLE_CLOUD_PROJECT が必要です。"
        )
    token = get_adc_token()
    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/publishers/google/models/{model}:generateContent"
    )
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    return call_gemini_http(url, payload, timeout=timeout, bearer_token=token)


def call_gemini_http(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    bearer_token: str | None = None,
) -> str:
    headers = {"Content-Type": "application/json"}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini API request failed: {exc}") from exc

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {data}") from exc


def generate_one(
    *,
    auth_mode: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    project: str | None,
    location: str,
) -> str:
    if auth_mode == "api-key":
        return generate_with_api_key(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            timeout=timeout,
        )
    if auth_mode == "vertex-adc":
        return generate_with_vertex_adc(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            timeout=timeout,
            project=project,
            location=location,
        )
    if os.environ.get("GEMINI_API_KEY"):
        return generate_with_api_key(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            timeout=timeout,
        )
    return generate_with_vertex_adc(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        timeout=timeout,
        project=project,
        location=location,
    )


def validate_inputs(data: dict[str, Any]) -> None:
    forbidden_text = json.dumps(data, ensure_ascii=False)
    forbidden_terms = [
        "operator_state_label",
        "authority_transfer_criteria",
        "expected_authority_mode",
        "score_caps",
        "forbidden_actions",
    ]
    leaked = [term for term in forbidden_terms if term in forbidden_text]
    if leaked:
        raise ValueError(f"Agent input contains evaluator-only fields: {leaked}")


def run(args: argparse.Namespace) -> int:
    prompt_path = args.prompt
    inputs_path = args.inputs
    system_prompt = prompt_path.read_text(encoding="utf-8")
    inputs = load_json(inputs_path)
    validate_inputs(inputs)
    scenarios = inputs.get("scenarios", [])
    if not scenarios:
        raise ValueError("No scenarios found in agent input file.")

    if args.dry_run:
        print("Dry run OK: prompt and agent-visible scenarios loaded.")
        print(f"Scenarios: {', '.join(s['scenario_id'] for s in scenarios)}")
        return 0

    outputs = []
    model = args.model or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    location = args.location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"

    for scenario in scenarios:
        for trial in range(1, args.trials + 1):
            user_prompt = build_user_prompt(scenario)
            timestamp = now_iso()
            raw_output = ""
            parse_error = None
            missing_required_fields: list[str] = []
            format_valid = False
            response_json = None
            try:
                raw_output = generate_one(
                    auth_mode=args.auth,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=args.temperature,
                    timeout=args.timeout,
                    project=args.project,
                    location=location,
                )
                response_json, parse_error, missing_required_fields = parse_agent_json(raw_output)
                format_valid = parse_error is None and not missing_required_fields
            except GeminiAuthError as exc:
                print(
                    "Gemini 認証情報が利用できません。"
                    " GEMINI_API_KEY を設定するか、"
                    " `gcloud auth application-default login` と GOOGLE_CLOUD_PROJECT を設定してください。\n"
                    f"詳細: {exc}",
                    file=sys.stderr,
                )
                return 2
            except Exception as exc:
                parse_error = str(exc)

            record = {
                "scenario_id": scenario["scenario_id"],
                "trajectory_id": f"gemini_{scenario['scenario_id']}_trial_{trial}",
                "model": model,
                "temperature": args.temperature,
                "trial": trial,
                "timestamp": timestamp,
                "raw_output": raw_output,
                "format_valid": format_valid,
                "parse_error": parse_error,
                "missing_required_fields": missing_required_fields,
            }
            if response_json is not None:
                record["response_json"] = response_json
            outputs.append(record)
            time.sleep(args.sleep_seconds)

    raw_data = {
        "metadata": {
            "domain": "hcps_mock",
            "version": "phase2_1_gemini_outputs",
            "model": model,
            "temperature": args.temperature,
            "trials": args.trials,
            "auth_mode": args.auth,
            "prompt_path": str(prompt_path),
            "inputs_path": str(inputs_path),
            "evaluator_only_labels_excluded": True,
        },
        "agent_outputs": outputs,
    }
    save_json(args.raw_output, raw_data)

    convert_agent_outputs(args.raw_output, args.trajectory_output)
    score_report = evaluate_ati(
        args.tasks,
        args.trajectory_output,
        args.score_report,
        args.score_summary,
    )
    flat_by_trajectory = {
        item["trajectory_id"]: item for item in score_report.get("flat_reports", [])
    }
    final_records = []
    for output in outputs:
        score = flat_by_trajectory.get(output["trajectory_id"], {})
        final_records.append(
            {
                "scenario_id": output["scenario_id"],
                "trajectory_id": output["trajectory_id"],
                "model": output["model"],
                "temperature": output["temperature"],
                "trial": output["trial"],
                "format_valid": output["format_valid"],
                "parse_error": output["parse_error"],
                "missing_required_fields": output["missing_required_fields"],
                "selected_authority_mode": score.get("selected_authority_mode"),
                "ati_score": score.get("final_score"),
                "triggered_violations": score.get("triggered_violations"),
                "short_explanation": score.get("short_explanation"),
            }
        )
    save_json(
        args.final_report,
        {
            "metadata": {
                "domain": "hcps_mock",
                "version": "phase2_1_gemini_final_report",
                "raw_output_path": str(args.raw_output),
                "trajectory_output_path": str(args.trajectory_output),
                "score_report_path": str(args.score_report),
                "score_summary_path": str(args.score_summary),
            },
            "results": final_records,
        },
    )
    print(json.dumps(final_records, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Gemini HCPS agent evaluation and score with ATI."
    )
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS_PATH)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_PATH)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_OUTPUT_PATH)
    parser.add_argument("--trajectory-output", type=Path, default=DEFAULT_TRAJECTORY_OUTPUT_PATH)
    parser.add_argument("--score-report", type=Path, default=DEFAULT_SCORE_REPORT_PATH)
    parser.add_argument("--score-summary", type=Path, default=DEFAULT_SCORE_SUMMARY_PATH)
    parser.add_argument("--final-report", type=Path, default=DEFAULT_FINAL_REPORT_PATH)
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--auth", choices=["auto", "api-key", "vertex-adc"], default="auto")
    parser.add_argument("--project", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
