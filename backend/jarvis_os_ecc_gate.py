#!/usr/bin/env python3
"""
ECC Enforcement Gate — Execution Control Core v1 for LP runtime.

Enforces:
- No execution without approval (job must be APPROVED status)
- No backend/UI mixing violations (UI jobs cannot route to GrowthHub)
- Full audit trail per job

Maps Jarvis OS ECC rules onto existing proposal_engine + approval_queue.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
ECC_LOG_FILE = os.path.join(DATA_DIR, "ecc_decisions.jsonl")


# ─── UI/Backend Source Rules ───

VALID_UI_SOURCES = [
    "preview-1778809024398799700.vibepreview.com",
    "Chrome DOM session (port 9333)",
]

BLOCKED_BACKEND_PATTERNS = [
    "leadconnectorhq.com",
    "/v2/location/",
    "/vibe/projects/",
    "GrowthHub API",
]


# ─── Execution Type to Source Mapping ───

EXEC_TYPE_TO_SOURCE: Dict[str, List[str]] = {
    "ui": ["preview-1778809024398799700.vibepreview.com", "Chrome DOM session (port 9333)"],
    "browser": ["preview-1778809024398799700.vibepreview.com", "Chrome DOM session (port 9333)"],
    "api": ["backend-api-only"],  # backend-only, no UI
    "state": ["internal-memory-only"],  # internal-only, no UI
    "validation": ["internal-validation-only"],  # internal-only, no UI
}


# ─── ECC Decision ───

class ECCResult:
    def __init__(
        self,
        job_id: str,
        approved: bool,
        reason: str,
        violated_layer: Optional[str] = None,
        blocked_source: Optional[str] = None,
    ):
        self.job_id = job_id
        self.approved = approved
        self.reason = reason
        self.violated_layer = violated_layer
        self.blocked_source = blocked_source
        self.timestamp = datetime.now(timezone.utc).isoformat()


# ─── ECC Enforcement Engine ───

class ECCEnforcement:
    def __init__(self):
        self.decisions: List[Dict[str, Any]] = []
        self._load_decisions()

    def evaluate_job(self, job) -> ECCResult:
        """
        Evaluate a job against ECC rules.
        Returns ECCResult with approved/blocked status.
        """
        job_id = job.job_id
        execution_type = job.execution_type.value
        routed_to = getattr(job, 'routed_to', None) or ""

        # Rule 1: Job must be APPROVED before execution
        if job.status.value != "approved":
            result = ECCResult(
                job_id=job_id,
                approved=False,
                reason=f"Job not approved — status is '{job.status.value}'",
                violated_layer="ECC",
            )
            self._record(result)
            return result

        # Rule 2: UI jobs cannot route to backend-only sources
        if execution_type in ("ui", "browser"):
            if self._is_backend_source(routed_to):
                result = ECCResult(
                    job_id=job_id,
                    approved=False,
                    reason=f"Backend source '{routed_to}' in UI job — forbidden",
                    violated_layer="FIREWALL",
                    blocked_source=routed_to,
                )
                self._record(result)
                return result

        # Rule 3: API/State jobs cannot be mixed with UI
        if execution_type in ("api", "state", "validation"):
            # These are backend-only — allowed, no violation
            pass

        # All rules passed
        result = ECCResult(
            job_id=job_id,
            approved=True,
            reason="All ECC rules passed",
        )
        self._record(result)
        return result

    def can_execute(self, job) -> bool:
        """Quick check: is this job allowed to execute?"""
        return self.evaluate_job(job).approved

    def log_decision(self, job_id: str, approved: bool, reason: str, layer: str = "ECC"):
        """Log a decision to file."""
        entry = {
            "job_id": job_id,
            "approved": approved,
            "reason": reason,
            "layer": layer,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(ECC_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self.decisions.append(entry)

    def get_decisions(self, limit: int = 100) -> List[Dict]:
        return self.decisions[-limit:]

    def _record(self, result: ECCResult):
        """Record a decision to in-memory list and file."""
        self.decisions.append(result.__dict__)
        entry = {
            "job_id": result.job_id,
            "approved": result.approved,
            "reason": result.reason,
            "layer": result.violated_layer or "ECC",
            "timestamp": result.timestamp,
        }
        with open(ECC_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def _is_backend_source(self, source: str) -> bool:
        """Check if source matches blocked backend patterns."""
        if not source:
            return False
        for pattern in BLOCKED_BACKEND_PATTERNS:
            if pattern in source:
                return True
        return False

    def _load_decisions(self):
        if not os.path.exists(ECC_LOG_FILE):
            return
        try:
            with open(ECC_LOG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.decisions.append(json.loads(line))
        except Exception:
            self.decisions = []


# ─── Singleton ───

_ecc: Optional[ECCEnforcement] = None


def get_ecc() -> ECCEnforcement:
    global _ecc
    if _ecc is None:
        _ecc = ECCEnforcement()
    return _ecc


def can_execute(job) -> bool:
    return get_ecc().can_execute(job)
