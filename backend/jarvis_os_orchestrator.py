#!/usr/bin/env python3
"""
Jarvis OS — Execution Orchestration Module

Main entry point for live execution in LP runtime.

Pipeline:
1. Intent received → create Job (queued)
2. Validate Job (validated)
3. ECC Gate evaluation (approved/blocked)
4. Route to Worker (running)
5. Execute (completed/failed)
6. Log full trace

Integrates with:
- execution_manager: Job lifecycle
- ecc_gate: ECC enforcement
- execution_router: Worker routing
- event_log: LP event logging
- audit_trail: LP audit trail
- execution_engine: LP task execution
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.jarvis_os_execution_router import (
    ExecutionType,
    ExecutionRouter,
    WorkerTarget,
    WORKER_REGISTRY,
    get_execution_router,
)
from backend.jarvis_os_execution_manager import (
    ExecutionManager,
    Job,
    JobStatus,
    get_execution_manager,
)
from backend.jarvis_os_ecc_gate import ECCEnforcement, get_ecc


# ─── Full Pipeline ───

class ExecutionOrchestrator:
    """
    Full execution pipeline: create → validate → approve → route → execute → complete.
    """

    def __init__(self):
        self.manager = get_execution_manager()
        self.ecc = get_ecc()
        self.router = get_execution_router()

    def create_and_route(
        self,
        task_id: str,
        intent: str,
        execution_type: ExecutionType,
        priority: int = 0,
        metadata: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Full pipeline: create job, validate, route, return plan.
        Does NOT execute — stops at approval step.
        """
        # Step 1: Create job (queued)
        job = self.manager.create_job(
            task_id=task_id,
            intent=intent,
            execution_type=execution_type,
            priority=priority,
            metadata=metadata,
        )

        # Step 2: Validate
        validated = self.manager.validate_job(job.job_id)
        if not validated:
            return {
                "success": False,
                "job_id": job.job_id,
                "status": "blocked",
                "error": job.error or "Validation failed",
            }

        # Step 3: Approve (requires validated status)
        approved = self.manager.approve_job(job.job_id)
        if not approved:
            return {
                "success": False,
                "job_id": job.job_id,
                "status": "blocked",
                "error": "Approval failed",
            }

        # Step 4: ECC Gate evaluation (checks approved status + UI/backend rules)
        ecc_result = self.ecc.evaluate_job(job)
        if not ecc_result.approved:
            self.manager.block_job(job.job_id, ecc_result.reason)
            return {
                "success": False,
                "job_id": job.job_id,
                "status": "blocked",
                "error": ecc_result.reason,
                "layer": ecc_result.violated_layer,
            }

        # Step 5: Route to worker
        target = self.router.route(job.execution_type)

        return {
            "success": True,
            "job_id": job.job_id,
            "status": "approved",
            "execution_type": execution_type.value,
            "worker_target": target.target,
            "worker_description": target.description,
            "ecc_decided": ecc_result.reason,
        }

    def start_execution(
        self,
        job_id: str,
        additional_info: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Start execution of an approved job.
        Returns execution plan.
        """
        job = self.manager.get_job(job_id)
        if not job:
            return {"success": False, "error": "Job not found"}

        # ECC re-check before execution
        ecc_result = self.ecc.evaluate_job(job)
        if not ecc_result.approved:
            return {
                "success": False,
                "error": ecc_result.reason,
                "layer": ecc_result.violated_layer,
            }

        # Route and log
        target = self.router.route_and_log(
            job,
            "execution_started",
            additional_info,
        )

        # Start job
        started = self.manager.start_job(job_id, routed_to=target.target)
        if not started:
            return {"success": False, "error": "Failed to start job"}

        return {
            "success": True,
            "job_id": job_id,
            "status": "running",
            "worker_target": target.target,
            "execution_type": job.execution_type.value,
        }

    def complete_execution(self, job_id: str) -> Dict[str, Any]:
        """Mark a running job as completed."""
        job = self.manager.get_job(job_id)
        if not job:
            return {"success": False, "error": "Job not found"}
        success = self.manager.complete_job(job_id)
        if not success:
            return {"success": False, "error": "Failed to complete job"}

        # Log completion
        self.router.log.log(
            job_id=job_id,
            execution_type=job.execution_type,
            worker_target="internal",
            decision_outcome="completed",
        )

        return {"success": True, "job_id": job_id, "status": "completed"}

    def fail_execution(self, job_id: str, error: str) -> Dict[str, Any]:
        """Mark a running job as failed."""
        success = self.manager.fail_job(job_id, error)
        if not success:
            return {"success": False, "error": "Failed to fail job"}

        return {"success": True, "job_id": job_id, "status": "failed"}

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get full status of a job."""
        job = self.manager.get_job(job_id)
        if not job:
            return None

        log_entries = self.router.log.get_job_log(job_id)
        ecc_decisions = self.ecc.get_decisions()

        return {
            "job_id": job.job_id,
            "task_id": job.task_id,
            "intent": job.intent,
            "execution_type": job.execution_type.value,
            "status": job.status.value,
            "priority": job.priority,
            "created": job.created,
            "validated_at": job.validated_at,
            "approved_at": job.approved_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "error": job.error,
            "routed_to": job.routed_to,
            "decision_outcome": job.decision_outcome,
            "execution_log": log_entries,
        }


# ─── Singleton ───

_orchestrator: Optional[ExecutionOrchestrator] = None


def get_orchestrator() -> ExecutionOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ExecutionOrchestrator()
    return _orchestrator


# ─── Convenience Functions ───

def create_job(task_id: str, intent: str, execution_type: ExecutionType, **kwargs) -> Dict:
    return get_orchestrator().create_and_route(task_id, intent, execution_type, **kwargs)


def start_job(job_id: str, **kwargs) -> Dict:
    return get_orchestrator().start_execution(job_id, **kwargs)


def complete_job(job_id: str) -> Dict:
    return get_orchestrator().complete_execution(job_id)


def fail_job(job_id: str, error: str) -> Dict:
    return get_orchestrator().fail_execution(job_id, error)


def get_job_status(job_id: str) -> Optional[Dict]:
    return get_orchestrator().get_status(job_id)


# ─── Public API ───

__all__ = [
    "ExecutionOrchestrator",
    "get_orchestrator",
    "create_job",
    "start_job",
    "complete_job",
    "fail_job",
    "get_job_status",
    "ExecutionType",
    "JobStatus",
]
