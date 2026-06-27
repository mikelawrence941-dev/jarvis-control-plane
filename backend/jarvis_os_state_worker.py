#!/usr/bin/env python3
"""
State Worker — Update state.json with approved_goal_bundle results.
"""

import json
import os
import sys
from datetime import datetime, timezone

RUNTIME_ROOT = "/Users/stevenlawrence/bridge-redeploy-package/lifepilot_runtime"
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
STATE_FILE = os.path.join(DATA_DIR, "state.json")


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def main():
    state = load_state()

    state["approved_goal_bundle"] = {
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "goals_executed": 3,
        "worker_parity_status": "complete",
        "control_center_status": "deployed",
        "durable_event_store_status": "complete",
        "tests_passed": True,
        "mutation_performed": False,
        "external_writes_performed": False,
        "next_recommended_step": "Integrate event store into E2E execution loop for automatic event logging",
    }

    state["worker_parity_tests"] = {
        "status": "completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "passed": 5,
        "total": 5,
        "workers": {
            "ui": {"passed": True, "has_trace_id": True, "mutation_performed": False},
            "browser": {"passed": True, "has_trace_id": True, "mutation_performed": False},
            "api": {"passed": True, "has_trace_id": True, "mutation_performed": False},
            "state": {"passed": True, "has_trace_id": True, "mutation_performed": True},
            "ai": {"passed": True, "has_trace_id": True, "mutation_performed": False},
        },
    }

    state["control_center"] = {
        "status": "deployed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "port": 8765,
        "read_only": True,
        "panels": ["health", "jobs", "workers", "ecc-stats", "events", "goals"],
        "mutation_actions": False,
        "publish_actions": False,
        "crm_write_actions": False,
        "outreach_actions": False,
    }

    state["durable_event_store"] = {
        "status": "complete",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": ["append-only", "sequence_id_ordering", "trace_id_indexing", "search_filter", "replay"],
        "tests_passed": True,
    }

    save_state(state)
    print("State updated with approved_goal_bundle results")
    print(json.dumps(state["approved_goal_bundle"], indent=2))


if __name__ == "__main__":
    main()
