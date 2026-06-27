#!/usr/bin/env python3
"""
Execution Router — Routes jobs to appropriate workers.

Maps execution types to workers:
    UI       → Vibe Coder (browser session on port 9333)
    API      → GrowthHub backend API
    Browser  → Chrome session (CDP)
    State    → Internal memory update (state.json)
    Validation → ECC / approval gate

Integrates with existing bot_registry and execution_engine.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jarvis_os_execution_manager import ExecutionType

RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
EXECUTION_LOG_FILE = os.path.join(DATA_DIR, "execution_logs.jsonl")


# ─── Worker Target ───

class WorkerTarget:
    def __init__(
        self,
        worker_type: str,
        target: str,
        description: str,
        requires_approval: bool = True,
    ):
        self.worker_type = worker_type
        self.target = target
        self.description = description
        self.requires_approval = requires_approval


# ─── Worker Registry ───

WORKER_REGISTRY = {
    ExecutionType.UI: WorkerTarget(
        worker_type="ui",
        target="Vibe Coder (browser session port 9333)",
        description="UI editing and DOM manipulation via Vibe Coder",
        requires_approval=True,
    ),
    ExecutionType.API: WorkerTarget(
        worker_type="api",
        target="GrowthHub backend API",
        description="CRM/workflow backend operations only — no UI",
        requires_approval=True,
    ),
    ExecutionType.BROWSER: WorkerTarget(
        worker_type="browser",
        target="Chrome session (CDP, port 9333)",
        description="Browser automation via CDP",
        requires_approval=True,
    ),
    ExecutionType.STATE: WorkerTarget(
        worker_type="state",
        target="Internal memory (state.json)",
        description="Internal state management — no external calls",
        requires_approval=True,
    ),
    ExecutionType.VALIDATION: WorkerTarget(
        worker_type="validation",
        target="ECC gate / approval queue",
        description="ECC policy evaluation and approval gate",
        requires_approval=False,
    ),
}


# ─── Execution Log ───

class ExecutionLog:
    def __init__(self):
        self.entries: List[Dict[str, Any]] = []
        self._load()

    def log(
        self,
        job_id: str,
        execution_type: ExecutionType,
        worker_target: str,
        decision_outcome: str,
        additional: Optional[Dict] = None,
    ):
        """Log an execution event."""
        entry = {
            "job_id": job_id,
            "execution_type": execution_type.value,
            "worker_target": worker_target,
            "decision_outcome": decision_outcome,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if additional:
            entry.update(additional)

        # Write to file
        with open(EXECUTION_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Keep in memory
        self.entries.append(entry)

    def get_job_log(self, job_id: str) -> List[Dict]:
        return [e for e in self.entries if e.get("job_id") == job_id]

    def get_all(self, limit: int = 100) -> List[Dict]:
        return self.entries[-limit:]

    def _load(self):
        if not os.path.exists(EXECUTION_LOG_FILE):
            return
        try:
            with open(EXECUTION_LOG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.entries.append(json.loads(line))
        except Exception:
            self.entries = []


# ─── Execution Router ───

class ExecutionRouter:
    def __init__(self):
        self.log = ExecutionLog()

    def route(self, execution_type: ExecutionType) -> WorkerTarget:
        """Route an execution type to its worker."""
        target = WORKER_REGISTRY.get(execution_type)
        if not target:
            # Default to UI
            return WORKER_REGISTRY[ExecutionType.UI]
        return target

    def route_and_log(
        self,
        job,
        decision_outcome: str,
        additional: Optional[Dict] = None,
    ) -> WorkerTarget:
        """Route a job, log the decision, return worker target."""
        execution_type = job.execution_type
        target = self.route(execution_type)

        self.log.log(
            job_id=job.job_id,
            execution_type=execution_type,
            worker_target=target.target,
            decision_outcome=decision_outcome,
            additional=additional,
        )

        return target


# ─── Singleton ───

_router: Optional[ExecutionRouter] = None


def get_execution_router() -> ExecutionRouter:
    global _router
    if _router is None:
        _router = ExecutionRouter()
    return _router


def route_job(job, decision_outcome: str, **kwargs) -> WorkerTarget:
    return get_execution_router().route_and_log(job, decision_outcome, kwargs)


# ─── Public API ───

__all__ = [
    "ExecutionType",
    "WorkerTarget",
    "WORKER_REGISTRY",
    "ExecutionRouter",
    "ExecutionLog",
    "get_execution_router",
    "route_job",
]
