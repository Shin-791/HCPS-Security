#!/usr/bin/env python3
"""Local Streamlit viewer for static ATI evaluation results.

This viewer is intentionally local-only. It reads committed static ATI inputs
and safe summaries, and it does not call Gemini, Vertex AI, external APIs, or
load ignored outputs/generated_prompts directories.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "data/hcps_mock/tasks.json"
AGENT_INPUTS_PATH = ROOT / "data/hcps_mock/agent_eval_inputs.json"
TRAJECTORIES_PATH = ROOT / "data/hcps_mock/sample_trajectories.json"
STATIC_SUMMARY_PATH = ROOT / "results/static_ati_repro_summary.md"
GEMINI_SUMMARY_PATH = ROOT / "results/gemini_3trial_summary.md"

FORBIDDEN_AGENT_INPUT_TERMS = [
    "operator_state_label",
    "authority_transfer_criteria",
    "expected_authority_mode",
    "acceptable_authority_modes",
    "score_caps",
    "forbidden_actions",
    "partial_credit_weights",
    "required_checkpoints",
    "hard_gate_violations",
    "ground_truth",
]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def task_map(tasks_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {task["scenario_id"]: task for task in tasks_data.get("tasks", [])}


def visible_context(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("agent_visible_context", {})


def valid_evidence_ids(task: dict[str, Any]) -> list[str]:
    context = visible_context(task)
    ids: set[str] = set()
    for key in ("observed_alerts", "observed_logs", "physical_constraints"):
        for item in context.get(key, []):
            if item.get("id"):
                ids.add(item["id"])
    description = context.get("incident_description_evidence") or {}
    if description.get("id"):
        ids.add(description["id"])
    behavior = context.get("user_simulator_behavior") or {}
    if behavior.get("id"):
        ids.add(behavior["id"])
    for action in task.get("dangerous_actions", []):
        if action.get("id"):
            ids.add(action["id"])
    return sorted(ids)


def physical_constraints(task: dict[str, Any]) -> list[str]:
    constraints = []
    for item in visible_context(task).get("physical_constraints", []):
        label = item.get("id") or "constraint"
        content = item.get("constraint") or item.get("content") or ""
        constraints.append(f"{label}: {content}")
    return constraints


def dangerous_action_names(task: dict[str, Any]) -> list[str]:
    return [
        action.get("action_name")
        for action in task.get("dangerous_actions", [])
        if action.get("action_name")
    ]


def score_static_trajectories(
    tasks_data: dict[str, Any], trajectories_data: dict[str, Any]
) -> list[dict[str, Any]]:
    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from evaluate_authority_transfer import score_trajectory

    tasks_by_id = task_map(tasks_data)
    rows = []
    for trajectory in trajectories_data.get("trajectories", []):
        task = tasks_by_id[trajectory["scenario_id"]]
        report = score_trajectory(task, trajectory)
        rows.append(report)
    return rows


def static_score_table(score_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_scenario: dict[str, dict[str, Any]] = {}
    for row in score_rows:
        scenario_id = row["scenario_id"]
        label = row.get("label")
        item = by_scenario.setdefault(scenario_id, {"scenario_id": scenario_id})
        if label in {"good", "bad", "corrupt_success"}:
            item[label] = row.get("final_score")
    return [by_scenario[key] for key in sorted(by_scenario)]


def scenario_detail_rows(tasks_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for task in tasks_data.get("tasks", []):
        criteria = task.get("authority_transfer_criteria", {})
        rows.append(
            {
                "scenario_id": task["scenario_id"],
                "expected_authority_mode": criteria.get("expected_authority_mode"),
                "dangerous_actions": ", ".join(dangerous_action_names(task)) or "-",
                "physical_constraints": " | ".join(physical_constraints(task)) or "-",
                "valid_evidence_ids": ", ".join(valid_evidence_ids(task)),
            }
        )
    return rows


def trajectory_rows(
    trajectories_data: dict[str, Any], score_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    scores = {row["trajectory_id"]: row for row in score_rows}
    rows = []
    for trajectory in trajectories_data.get("trajectories", []):
        score = scores.get(trajectory["trajectory_id"], {})
        action_names = [
            action.get("action_name")
            for action in trajectory.get("action_executed", [])
            if action.get("action_name")
        ]
        rows.append(
            {
                "scenario_id": trajectory.get("scenario_id"),
                "trajectory_id": trajectory.get("trajectory_id"),
                "label": trajectory.get("label"),
                "authority_mode": (trajectory.get("authority_mode_selected") or {}).get("mode"),
                "actions": ", ".join(action_names) or "-",
                "ati_final": score.get("final_score"),
                "violations": ", ".join(score.get("triggered_violations") or []) or "-",
            }
        )
    return rows


def agent_input_leak_check(agent_inputs_data: dict[str, Any]) -> list[str]:
    text = json.dumps(agent_inputs_data, ensure_ascii=False)
    return [term for term in FORBIDDEN_AGENT_INPUT_TERMS if term in text]


def render_app() -> None:
    try:
        import streamlit as st
    except ImportError:
        print(
            "Streamlit is not installed. Install it locally with: python3 -m pip install streamlit",
            file=sys.stderr,
        )
        raise SystemExit(1)

    tasks_data = load_json(TASKS_PATH)
    agent_inputs_data = load_json(AGENT_INPUTS_PATH)
    trajectories_data = load_json(TRAJECTORIES_PATH)
    score_rows = score_static_trajectories(tasks_data, trajectories_data)

    st.set_page_config(page_title="ATI Static Results Viewer", layout="wide")
    st.title("ATI Static Results Viewer")
    st.caption("Local-only viewer. No Gemini, Vertex AI, external API, outputs/, or generated_prompts/ are loaded.")

    st.info("Static core evaluation")
    st.warning("Experimental dynamic files are not shown")

    tab_static, tab_gemini, tab_scenarios, tab_inputs, tab_trajectories = st.tabs(
        [
            "Static core evaluation",
            "Gemini static experiment",
            "Scenario details",
            "Agent-visible input",
            "Sample trajectories",
        ]
    )

    with tab_static:
        st.subheader("Static reproduction scores")
        st.dataframe(static_score_table(score_rows), use_container_width=True)
        st.markdown(load_text(STATIC_SUMMARY_PATH))

    with tab_gemini:
        st.subheader("Gemini static experiment")
        st.markdown(load_text(GEMINI_SUMMARY_PATH))

    with tab_scenarios:
        st.subheader("Scenario details from tasks.json")
        st.dataframe(scenario_detail_rows(tasks_data), use_container_width=True)
        with st.expander("Raw task records"):
            st.json(tasks_data)

    with tab_inputs:
        st.subheader("Agent-visible input separation")
        leaked = agent_input_leak_check(agent_inputs_data)
        if leaked:
            st.error(f"Potential evaluator-only labels found: {', '.join(leaked)}")
        else:
            st.success("No evaluator-only labels detected in agent_eval_inputs.json")
        for scenario in agent_inputs_data.get("scenarios", []):
            with st.expander(scenario["scenario_id"]):
                st.json(scenario)

    with tab_trajectories:
        st.subheader("Sample trajectories and ATI scores")
        st.dataframe(trajectory_rows(trajectories_data, score_rows), use_container_width=True)
        for trajectory in trajectories_data.get("trajectories", []):
            with st.expander(f"{trajectory.get('scenario_id')} / {trajectory.get('label')} / {trajectory.get('trajectory_id')}"):
                st.json(trajectory)


def run_check() -> None:
    tasks_data = load_json(TASKS_PATH)
    agent_inputs_data = load_json(AGENT_INPUTS_PATH)
    trajectories_data = load_json(TRAJECTORIES_PATH)
    score_rows = score_static_trajectories(tasks_data, trajectories_data)
    print("ATI static viewer local check")
    print(f"tasks: {len(tasks_data.get('tasks', []))}")
    print(f"agent-visible scenarios: {len(agent_inputs_data.get('scenarios', []))}")
    print(f"sample trajectories: {len(trajectories_data.get('trajectories', []))}")
    print(f"score rows: {len(score_rows)}")
    print(f"agent input evaluator-only leaks: {agent_input_leak_check(agent_inputs_data) or 'none'}")
    for row in static_score_table(score_rows):
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Streamlit viewer for static ATI results.")
    parser.add_argument("--check", action="store_true", help="Load local static files and print a short check without Streamlit.")
    args, _ = parser.parse_known_args()
    if args.check:
        run_check()
    else:
        render_app()


if __name__ == "__main__":
    main()
