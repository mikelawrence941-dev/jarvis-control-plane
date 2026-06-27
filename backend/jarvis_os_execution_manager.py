#!/usr/bin/env python3
"""
Jarvis OS Execution Manager — Job lifecycle for LP runtime.

Maps incoming tasks to Jobs with lifecycle:
    queued → validated → approved → running → completed / failed

Integrates with existing proposal_engine + execution_engine.
No new systems created — wraps existing infrastructure.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")
E2E_TRACES_FILE = os.path.join(DATA_DIR, "e2e_execution_traces.jsonl")


# ─── Execution Types (shared) ───

class ExecutionType(Enum):
    UI = "ui"
    API = "api"
    BROWSER = "browser"
    STATE = "state"
    VALIDATION = "validation"


# ─── Job Lifecycle States ───

class JobStatus(Enum):
    QUEUED = "queued"
    VALIDATED = "validated"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


VALID_JOB_TRANSITIONS = {
    JobStatus.QUEUED: [JobStatus.VALIDATED, JobStatus.BLOCKED],
    JobStatus.VALIDATED: [JobStatus.APPROVED, JobStatus.BLOCKED],
    JobStatus.APPROVED: [JobStatus.RUNNING, JobStatus.BLOCKED],
    JobStatus.RUNNING: [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.BLOCKED],
    JobStatus.COMPLETED: [],
    JobStatus.FAILED: [],
    JobStatus.BLOCKED: [],
}


# ─── Job Model ───

class Job:
    def __init__(
        self,
        task_id: str,
        intent: str,
        execution_type: ExecutionType,
        priority: int = 0,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        self.job_id = f"job-{uuid.uuid4().hex[:12]}"
        self.task_id = task_id
        self.intent = intent
        self.execution_type = execution_type
        self.trace_id = trace_id or f"trace-{uuid.uuid4().hex[:16]}"
        self.status = JobStatus.QUEUED
        self.priority = priority
        self.metadata = metadata or {}
        self.created = datetime.now(timezone.utc).isoformat()
        self.validated_at: Optional[str] = None
        self.approved_at: Optional[str] = None
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.error: Optional[str] = None
        self.routed_to: Optional[str] = None
        self.decision_outcome: Optional[str] = None
        self.decision_reason: Optional[str] = None
        self.worker_result: Optional[Dict] = None


# ─── Execution Manager ───

class ExecutionManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self._load_from_disk()

    # ─── Lifecycle Methods ───

    def create_job(
        self,
        task_id: str,
        intent: str,
        execution_type: ExecutionType,
        priority: int = 0,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Job:
        """Queue a new job."""
        job = Job(task_id, intent, execution_type, priority, trace_id, metadata)
        self.jobs[job.job_id] = job
        self._save_to_disk()
        return job

    def validate_job(self, job_id: str) -> bool:
        """Validate job meets requirements (non-empty intent, valid type)."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.intent.strip() == "":
            job.error = "Empty intent text"
            job.status = JobStatus.BLOCKED
            self._save_to_disk()
            return False
        job.status = JobStatus.VALIDATED
        job.validated_at = datetime.now(timezone.utc).isoformat()
        self._save_to_disk()
        return True

    def approve_job(self, job_id: str) -> bool:
        """
        Approve job for execution.
        Requires: validated status + ECC approval (see ec_gate).
        """
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status != JobStatus.VALIDATED:
            job.error = f"Cannot approve job in state {job.status.value}"
            self._save_to_disk()
            return False
        job.status = JobStatus.APPROVED
        job.approved_at = datetime.now(timezone.utc).isoformat()
        job.decision_outcome = "approved"
        self._save_to_disk()
        return True

    def reject_job(self, job_id: str, reason: str) -> bool:
        """Reject and block a job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.error = reason
        job.status = JobStatus.BLOCKED
        job.decision_outcome = "blocked"
        job.decision_reason = reason
        self._save_to_disk()
        return True

    def start_job(self, job_id: str, routed_to: str) -> bool:
        """Start executing a job."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status != JobStatus.APPROVED:
            job.error = f"Cannot start job in state {job.status.value}"
            self._save_to_disk()
            return False
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc).isoformat()
        job.routed_to = routed_to
        self._save_to_disk()
        return True

    def complete_job(self, job_id: str) -> bool:
        """Mark a job as completed."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status != JobStatus.RUNNING:
            job.error = f"Cannot complete job in state {job.status.value}"
            self._save_to_disk()
            return False
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(timezone.utc).isoformat()
        job.decision_outcome = "completed"
        self._save_to_disk()
        return True

    def fail_job(self, job_id: str, error: str) -> bool:
        """Mark a job as failed."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status != JobStatus.RUNNING:
            job.error = f"Cannot fail job in state {job.status.value}"
            self._save_to_disk()
            return False
        job.status = JobStatus.FAILED
        job.completed_at = datetime.now(timezone.utc).isoformat()
        job.error = error
        job.decision_outcome = "failed"
        self._save_to_disk()
        return True

    def block_job(self, job_id: str, reason: str) -> bool:
        """Block a job (ECC enforcement)."""
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.status = JobStatus.BLOCKED
        job.error = reason
        job.decision_outcome = "blocked"
        job.decision_reason = reason
        self._save_to_disk()
        return True

    # ─── Query Methods ───

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.jobs.get(job_id)

    def list_jobs(self, status: Optional[JobStatus] = None) -> List[Job]:
        if status:
            return [j for j in self.jobs.values() if j.status == status]
        return list(self.jobs.values())

    def get_pending_count(self) -> int:
        """Jobs awaiting execution (approved or running)."""
        return len([
            j for j in self.jobs.values()
            if j.status in (JobStatus.APPROVED, JobStatus.RUNNING)
        ])

    # ─── Persistence ───

    def _save_to_disk(self):
        data = {}
        for job_id, job in self.jobs.items():
            data[job_id] = {
                "job_id": job.job_id,
                "task_id": job.task_id,
                "intent": job.intent,
                "execution_type": job.execution_type.value,
                "trace_id": job.trace_id,
                "status": job.status.value,
                "priority": job.priority,
                "metadata": job.metadata,
                "created": job.created,
                "validated_at": job.validated_at,
                "approved_at": job.approved_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
                "error": job.error,
                "routed_to": job.routed_to,
                "decision_outcome": job.decision_outcome,
                "worker_result": job.worker_result,
            }
        tmp = JOBS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, JOBS_FILE)

    def _load_from_disk(self):
        if not os.path.exists(JOBS_FILE):
            return
        try:
            with open(JOBS_FILE) as f:
                data = json.load(f)
            for job_id, job_data in data.items():
                job = Job(
                    task_id=job_data["task_id"],
                    intent=job_data["intent"],
                    execution_type=ExecutionType(job_data["execution_type"]),
                    priority=job_data.get("priority", 0),
                    trace_id=job_data.get("trace_id"),
                    metadata=job_data.get("metadata", {}),
                )
                job.job_id = job_data["job_id"]
                job.status = JobStatus(job_data["status"])
                job.validated_at = job_data.get("validated_at")
                job.approved_at = job_data.get("approved_at")
                job.started_at = job_data.get("started_at")
                job.completed_at = job_data.get("completed_at")
                job.error = job_data.get("error")
                job.routed_to = job_data.get("routed_to")
                job.decision_outcome = job_data.get("decision_outcome")
                job.worker_result = job_data.get("worker_result")
                self.jobs[job.job_id] = job
        except Exception:
            self.jobs = {}


# ─── Singleton ───

_execution_manager: Optional[ExecutionManager] = None


def get_execution_manager() -> ExecutionManager:
    global _execution_manager
    if _execution_manager is None:
        _execution_manager = ExecutionManager()
    return _execution_manager


# ─── Convenience ───

def create_job(task_id: str, intent: str, execution_type: ExecutionType, **kwargs) -> Job:
    return get_execution_manager().create_job(task_id, intent, execution_type, **kwargs)


def approve_job(job_id: str) -> bool:
    return get_execution_manager().approve_job(job_id)


def list_jobs(status: Optional[JobStatus] = None) -> List[Job]:
    return get_execution_manager().list_jobs(status)
