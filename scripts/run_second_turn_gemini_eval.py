#!/usr/bin/env python3
"""Run or dry-run second-turn Gemini evaluation from generated prompts.

This script prepares the real second-turn experiment path, but it is safe to
run locally with --dry-run. In dry-run mode it does not import Google auth,
does not read API keys or ADC credentials, and does not make network calls.

Real API execution is guarded by --execute and writes raw responses under
outputs/second_turn_gemini_raw/, which is ignored by git.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROMPTS_PATH = Path("generated_prompts/second_turn_prompts.jsonl")
DEFAULT_OUTPUT_DIR = Path("outputs/second_turn_gemini_raw")
DEFAULT_DRY_RUN_MANIFEST = Path("/tmp/hcps_second_turn_gemini_dry_run_manifest.json")
DEFAULT_MODEL = "gemini-2.5-flash"


class GeminiAuthError(RuntimeError):
    pass


class GeminiExecutionError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Invalid JSONL at line {line_number}: record is not an object")
            record.setdefault("line_number", line_number)
            records.append(record)
    return records


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_model(model_arg: str | None) -> str:
    return model_arg or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def resolve_auth_mode(requested_auth: str) -> str:
    if requested_auth != "auto":
        return requested_auth
    if env_true("GOOGLE_GENAI_USE_VERTEXAI"):
        return "vertex-adc"
    if os.environ.get("GEMINI_API_KEY"):
        return "api-key"
    return "vertex-adc"


def resolve_project(project_arg: str | None) -> str | None:
    return project_arg or os.environ.get("GOOGLE_CLOUD_PROJECT")


def resolve_location(location_arg: str | None) -> str | None:
    return location_arg or os.environ.get("GOOGLE_CLOUD_LOCATION") or "us-central1"


def vertex_base_url(location: str) -> str:
    if location == "global":
        return "https://aiplatform.googleapis.com"
    return f"https://{location}-aiplatform.googleapis.com"


def verified_ssl_context():
    import ssl

    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def get_adc_token() -> str:
    try:
        import google.auth
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise GeminiAuthError(
            "Vertex AI ADC を使うには google-auth が必要です。"
            " `pip install google-auth requests` を実行してください。"
        ) from exc

    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
    except DefaultCredentialsError as exc:
        raise GeminiAuthError(
            "Application Default Credentials が見つかりません。"
            " `gcloud auth application-default login` を実行してください。"
        ) from exc
    except RefreshError as exc:
        raise GeminiAuthError(
            "Application Default Credentials の更新に失敗しました。"
            " `gcloud auth application-default login` をやり直してください。"
        ) from exc

    if not credentials.token:
        raise GeminiAuthError("Application Default Credentials のトークン取得に失敗しました。")
    return credentials.token


def call_gemini_http_json(
    *,
    url: str,
    payload: dict[str, Any],
    timeout: int,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    import urllib.error
    import urllib.request

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
        with urllib.request.urlopen(request, timeout=timeout, context=verified_ssl_context()) as response:
            response_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise GeminiExecutionError(f"Gemini API HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise GeminiExecutionError(f"Gemini API request failed: {exc}") from exc

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise GeminiExecutionError(f"Gemini API returned non-JSON response: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GeminiExecutionError("Gemini API response was not a JSON object.")
    return parsed


def build_payload(system_prompt: str, user_prompt: str, temperature: float) -> dict[str, Any]:
    return {
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


def generate_with_api_key(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
) -> dict[str, Any]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiAuthError("GEMINI_API_KEY が設定されていません。")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    return call_gemini_http_json(
        url=url,
        payload=build_payload(system_prompt, user_prompt, temperature),
        timeout=timeout,
    )


def generate_with_vertex_adc(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    project: str | None,
    location: str,
) -> dict[str, Any]:
    project_id = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise GeminiAuthError(
            "Vertex AI を使うには --project または GOOGLE_CLOUD_PROJECT が必要です。"
        )
    if not location:
        raise GeminiAuthError(
            "Vertex AI を使うには --location または GOOGLE_CLOUD_LOCATION が必要です。"
        )
    token = get_adc_token()
    url = (
        f"{vertex_base_url(location)}/v1/projects/{project_id}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    return call_gemini_http_json(
        url=url,
        payload=build_payload(system_prompt, user_prompt, temperature),
        timeout=timeout,
        bearer_token=token,
    )


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
) -> dict[str, Any]:
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
    raise GeminiAuthError(f"Unsupported auth mode: {auth_mode}")


def safe_id_fragment(value: Any) -> str:
    text = str(value or "unknown")
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in text)


def output_path_for_label(label: str) -> Path:
    safe_label = safe_id_fragment(label)
    return DEFAULT_OUTPUT_DIR / f"second_turn_gemini_outputs_{safe_label}.jsonl"


def select_records(records: list[dict[str, Any]], max_records: int | None) -> list[dict[str, Any]]:
    if max_records is None:
        return records
    return records[:max_records]


def prompt_record_base(
    *,
    prompt_record: dict[str, Any],
    model: str,
    trial: int,
    label: str,
) -> dict[str, Any]:
    scenario_id = prompt_record.get("scenario_id")
    record_id = prompt_record.get("record_id") or f"line_{prompt_record.get('line_number', 'unknown')}"
    return {
        "record_id": record_id,
        "scenario_id": scenario_id,
        "trajectory_id": f"second_turn_gemini_{safe_id_fragment(record_id)}_trial_{trial}",
        "source_trajectory_id": prompt_record.get("source_trajectory_id"),
        "source_prompt_record_id": record_id,
        "label": label,
        "model": model,
        "trial": trial,
        "created_at": now_iso(),
        "first_turn_agent_decision": prompt_record.get("first_turn_summary"),
        "rule_based_operator_response": prompt_record.get("operator_response"),
    }


def build_dry_run_manifest(
    *,
    prompts_path: Path,
    records: list[dict[str, Any]],
    selected_records: list[dict[str, Any]],
    model: str,
    auth: str,
    label: str,
    trial: int,
    max_records: int | None,
    output_path: Path,
) -> dict[str, Any]:
    return {
        "metadata": {
            "mode": "dry_run",
            "api_call_made": False,
            "credentials_read": False,
            "prompts_path": str(prompts_path),
            "records_loaded": len(records),
            "records_selected": len(selected_records),
            "max_records": max_records,
            "model": model,
            "auth_requested": auth,
            "auth_resolved": None,
            "label": label,
            "trial": trial,
            "would_write_raw_output_path": str(output_path),
            "created_at": now_iso(),
            "note": "Dry run only. No Gemini, Vertex AI, ADC, API key, or external network call is used.",
        },
        "records": [
            {
                "record_id": record.get("record_id"),
                "scenario_id": record.get("scenario_id"),
                "source_trajectory_id": record.get("source_trajectory_id"),
                "first_turn_summary": record.get("first_turn_summary"),
                "operator_response": record.get("operator_response"),
                "system_prompt_chars": len(record.get("system_prompt") or ""),
                "user_prompt_chars": len(record.get("user_prompt") or ""),
                "user_prompt_preview": (record.get("user_prompt") or "")[:300],
                "would_write_trajectory_id": prompt_record_base(
                    prompt_record=record,
                    model=model,
                    trial=trial,
                    label=label,
                )["trajectory_id"],
            }
            for record in selected_records
        ],
    }


def run_dry_run(args: argparse.Namespace) -> None:
    records = load_jsonl(args.prompts)
    selected = select_records(records, args.max_records)
    model = args.model or DEFAULT_MODEL
    output_path = args.output or output_path_for_label(args.label)
    manifest = build_dry_run_manifest(
        prompts_path=args.prompts,
        records=records,
        selected_records=selected,
        model=model,
        auth=args.auth,
        label=args.label,
        trial=args.trial,
        max_records=args.max_records,
        output_path=output_path,
    )
    write_json(args.dry_run_manifest, manifest)
    print(json.dumps(manifest["metadata"], indent=2, ensure_ascii=False))
    print(f"Dry-run manifest saved to {args.dry_run_manifest}")


def run_execute(args: argparse.Namespace) -> None:
    records = load_jsonl(args.prompts)
    selected = select_records(records, args.max_records)
    if not selected:
        raise SystemExit("No prompt records selected.")

    model = resolve_model(args.model)
    auth_mode = resolve_auth_mode(args.auth)
    project = resolve_project(args.project)
    location = resolve_location(args.location)
    output_path = args.output or output_path_for_label(args.label)
    raw_records: list[dict[str, Any]] = []

    for index, prompt_record in enumerate(selected, start=1):
        base = prompt_record_base(
            prompt_record=prompt_record,
            model=model,
            trial=args.trial,
            label=args.label,
        )
        base["request_metadata"] = {
            "prompt_record_index": index,
            "temperature": args.temperature,
            "auth_mode": auth_mode,
            "project": project,
            "location": location,
        }
        try:
            response = generate_one(
                auth_mode=auth_mode,
                model=model,
                system_prompt=prompt_record.get("system_prompt") or "",
                user_prompt=prompt_record.get("user_prompt") or "",
                temperature=args.temperature,
                timeout=args.timeout,
                project=project,
                location=location,
            )
            base["evaluation_status"] = "raw_response_received"
            base["gemini_response"] = response
        except (GeminiAuthError, GeminiExecutionError, OSError) as exc:
            base["evaluation_status"] = "api_error"
            base["api_error_message"] = str(exc)
            base["gemini_response"] = None
        raw_records.append(base)

        if args.sleep_seconds and index < len(selected):
            time.sleep(args.sleep_seconds)

    write_jsonl(output_path, raw_records)
    print(f"Saved {len(raw_records)} raw second-turn records to {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run or dry-run second-turn Gemini evaluation from generated prompt JSONL."
    )
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS_PATH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--label", default="3scenario_1trial")
    parser.add_argument("--model", default=None)
    parser.add_argument("--auth", choices=["auto", "vertex-adc", "api-key"], default="auto")
    parser.add_argument("--project", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--trial", type=int, default=1)
    parser.add_argument("--max-records", type=int, default=3)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true", help="Create a local manifest without API calls or credential reads.")
    parser.add_argument("--dry-run-manifest", type=Path, default=DEFAULT_DRY_RUN_MANIFEST)
    parser.add_argument("--execute", action="store_true", help="Required for real API execution.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.dry_run:
        run_dry_run(args)
        return
    if not args.execute:
        raise SystemExit(
            "Refusing to call Gemini/Vertex AI without --execute. "
            "Use --dry-run for a local safety check."
        )
    run_execute(args)


if __name__ == "__main__":
    main()
