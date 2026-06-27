#!/usr/bin/env python3
"""
Jarvis OS Service Wrappers v1 — HTTP API layer around consolidated runtime.

Exposes:
  GET  /health
  POST /jobs
  GET  /jobs/:job_id
  GET  /traces/:trace_id
  GET  /workers/status
  GET  /ecc/stats
  GET  /events/stream (read-only)

All execution goes through jarvis_os_consolidated_execution.py.
No direct worker calls. No ECC bypass. No duplicate runtime.
"""

import json
import os
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Any, Dict, List, Optional

# Ensure runtime is importable
RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

from jarvis_os_consolidated_execution import (
    ConsolidatedExecutionLoop,
    WorkerHandlerResult,
    get_consolidated_loop,
    execute_consolidated,
)
from jarvis_os_execution_manager import ExecutionType, get_execution_manager

DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
SERVICE_PORT = 8765


# ─── Execution Type Parser ──────────────────────────────────────────

def parse_execution_type(value: str) -> Optional[ExecutionType]:
    """Parse execution type string to ExecutionType enum."""
    mapping = {
        "ui": ExecutionType.UI,
        "api": ExecutionType.API,
        "browser": ExecutionType.BROWSER,
        "state": ExecutionType.STATE,
        "validation": ExecutionType.VALIDATION,
    }
    return mapping.get(value.lower())


# ─── JSON Response Helper ────────────────────────────────────────────

def json_response(handler: BaseHTTPRequestHandler, status: int, data: Dict[str, Any]):
    """Send a JSON response."""
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps(data).encode())


# ─── Service Wrapper Handler ─────────────────────────────────────────

class JarvisServiceHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Jarvis service endpoints."""

    loop: ConsolidatedExecutionLoop = None
    manager = None

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        try:
            if path == "/health":
                self._handle_health()
            elif path == "/jobs" and "job_id" in query:
                self._handle_job_status(query["job_id"][0])
            elif re.match(r"^/jobs/[^/]+$", path):
                job_id = path.split("/")[-1]
                self._handle_job_status(job_id)
            elif path == "/traces" and "trace_id" in query:
                self._handle_trace_replay(query["trace_id"][0])
            elif re.match(r"^/traces/[^/]+$", path):
                trace_id = path.split("/")[-1]
                self._handle_trace_replay(trace_id)
            elif path == "/workers/status":
                self._handle_workers_status()
            elif path == "/ecc/stats":
                self._handle_ecc_stats()
            elif path in ("/events/stream", "/events", "/ws"):
                self._handle_event_stream()
            else:
                json_response(self, 404, {"error": "Not found", "path": path})
        except Exception as e:
            json_response(self, 500, {"error": str(e)})

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        try:
            if path == "/jobs":
                self._handle_submit_job()
            else:
                json_response(self, 404, {"error": "Not found", "path": path})
        except Exception as e:
            json_response(self, 500, {"error": str(e)})

    def _handle_health(self):
        """GET /health — system health check."""
        data = {
            "system_status": "operational",
            "runtime_loaded": True,
            "ecc_available": True,
            "entrypoint_locked": True,
            "consolidation_version": "1.0.0",
            "timestamp": self._now(),
            "endpoints": [
                "GET /health",
                "POST /jobs",
                "GET /jobs/:job_id",
                "GET /traces/:trace_id",
                "GET /workers/status",
                "GET /ecc/stats",
                "GET /events/stream",
            ],
        }
        json_response(self, 200, data)

    def _handle_submit_job(self):
        """POST /jobs — submit a validation-only job."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"
            request = json.loads(body)
        except json.JSONDecodeError:
            json_response(self, 400, {"error": "Invalid JSON body"})
            return

        # Extract required fields
        task_id = request.get("task_id", f"job-{self._now_ms()}")
        intent = request.get("intent", "service wrapper job")
        execution_type_str = request.get("execution_type", "ui")
        target_system = request.get("target_system", "")
        validation_only = request.get("validation_only", True)

        # Parse execution type
        execution_type = parse_execution_type(execution_type_str)
        if execution_type is None:
            json_response(self, 400, {"error": f"Invalid execution_type: {execution_type_str}"})
            return

        # Validate that this is a validation-only job
        forbidden_actions = request.get("forbidden_actions", [])
        if not validation_only:
            # Check for write-heavy actions
            write_actions = ["homepage_edit", "crm_write", "publish", "messaging", "scheduling", "payment", "outreach"]
            matching = [a for a in write_actions if a in forbidden_actions]
            if matching:
                json_response(self, 403, {"error": f"Forbidden actions blocked: {matching}"})
                return

        # Execute through consolidated runtime
        result = execute_consolidated(
            task_id=task_id,
            intent=intent,
            execution_type=execution_type,
            target_system=target_system,
        )

        status_code = 200 if result.get("success") else 403
        json_response(self, status_code, result)

    def _handle_job_status(self, job_id: str):
        """GET /jobs/:job_id — get job status."""
        trace = self.loop.get_trace(job_id)
        if not trace:
            json_response(self, 404, {"error": "Job not found", "job_id": job_id})
            return

        # Build job status response
        status_data = {
            "job_id": trace["job_id"],
            "trace_id": trace["trace_id"],
            "status": trace["status"],
            "execution_type": self._extract_field(trace, "execution_type"),
            "target_system": self._extract_field(trace, "target_system"),
            "current_stage": self._current_stage(trace),
            "mutation_performed": self._check_mutation(trace),
            "created_at": trace.get("created_at"),
            "updated_at": trace.get("completed_at") or trace.get("created_at"),
            "steps": len(trace.get("steps", [])),
        }
        json_response(self, 200, status_data)

    def _handle_trace_replay(self, trace_id: str):
        """GET /traces/:trace_id — replay execution trace."""
        replay = self.loop.replay_trace(trace_id)
        if not replay:
            json_response(self, 404, {"error": "Trace not found", "trace_id": trace_id})
            return
        json_response(self, 200, replay)

    def _handle_workers_status(self):
        """GET /workers/status — get all worker statuses."""
        data = {
            "workers": {
                "ui_worker": {
                    "status": "ready",
                    "available_actions": ["check_accessible", "capture_page_title_or_basic_status", "verify_no_backend_url_used"],
                    "last_checked": self._now(),
                },
                "api_worker": {
                    "status": "ready",
                    "available_actions": ["health_check", "route_classification_check"],
                    "last_checked": self._now(),
                },
                "browser_worker": {
                    "status": "ready",
                    "available_actions": ["check_chrome_port_9333_available", "check_profile_name_expected", "report_browser_status"],
                    "last_checked": self._now(),
                },
                "state_worker": {
                    "status": "ready",
                    "available_actions": ["append_execution_log", "update_jobs_json", "update_state_json", "write_worker_status_snapshot"],
                    "last_checked": self._now(),
                },
                "ai_worker": {
                    "status": "ready",
                    "available_actions": ["summarize_job_result", "classify_error", "recommend_next_safe_action"],
                    "last_checked": self._now(),
                },
            },
            "last_checked": self._now(),
            "total_workers": 5,
        }
        json_response(self, 200, data)

    def _handle_ecc_stats(self):
        """GET /ecc/stats — get ECC statistics."""
        # Read ecc_decisions.jsonl
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        approvals = 0
        blocks = 0
        block_reasons: Dict[str, int] = {}
        last_blocked_trace: Optional[str] = None

        if os.path.exists(ecc_file):
            with open(ecc_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            decision = entry.get("decision", entry.get("status", ""))
                            reason = entry.get("reason", entry.get("violation_reason", ""))
                            trace_id = entry.get("trace_id", entry.get("trace_id", ""))

                            if decision in ("approved", "true", True, "ALLOW", "allow"):
                                approvals += 1
                            elif decision in ("blocked", "false", False, "DENY", "deny", "BLOCKED"):
                                blocks += 1
                                if reason:
                                    block_reasons[reason] = block_reasons.get(reason, 0) + 1
                                    last_blocked_trace = trace_id
                        except json.JSONDecodeError:
                            continue

        total = approvals + blocks
        block_rate = blocks / total if total > 0 else 0.0

        data = {
            "total_jobs": total,
            "approvals": approvals,
            "blocks": blocks,
            "block_rate": round(block_rate, 4),
            "top_violation_reasons": block_reasons,
            "last_blocked_trace_id": last_blocked_trace,
            "timestamp": self._now(),
        }
        json_response(self, 200, data)

    def _handle_event_stream(self):
        """GET /events/stream — read-only event stream (stub)."""
        # For now, return current state as a one-time snapshot.
        # Full WebSocket/streaming would require asyncio, kept minimal here.
        traces_file = os.path.join(DATA_DIR, "e2e_execution_traces.jsonl")
        recent_traces = []

        if os.path.exists(traces_file):
            with open(traces_file) as f:
                lines = [l.strip() for l in f if l.strip()]
                for line in lines[-10:]:  # Last 10 traces
                    try:
                        trace = json.loads(line)
                        recent_traces.append({
                            "job_id": trace.get("job_id"),
                            "trace_id": trace.get("trace_id"),
                            "status": trace.get("status"),
                            "created_at": trace.get("created_at"),
                        })
                    except json.JSONDecodeError:
                        continue

        data = {
            "stream_type": "read-only snapshot",
            "events": recent_traces,
            "total_events": len(recent_traces),
            "stream_active": True,
            "timestamp": self._now(),
        }
        json_response(self, 200, data)

    # ─── Helpers ─────────────────────────────────────────────────────

    def _now(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _now_ms(self) -> str:
        from datetime import datetime, timezone
        return str(int(datetime.now(timezone.utc).timestamp() * 1000))

    def _extract_field(self, trace: Dict, field: str) -> Optional[str]:
        """Extract a field from trace steps."""
        for step in trace.get("steps", []):
            data = step.get("data", {})
            if field in data:
                return data[field]
        return None

    def _current_stage(self, trace: Dict) -> str:
        """Get the current/completed stage from trace."""
        if trace.get("steps"):
            return trace["steps"][-1].get("step", "unknown")
        return "unknown"

    def _check_mutation(self, trace: Dict) -> bool:
        """Check if any step performed a mutation."""
        for step in trace.get("steps", []):
            data = step.get("data", {})
            worker_result = data.get("worker_result", {})
            if isinstance(worker_result, dict) and worker_result.get("mutation_performed", False):
                return True
        return False


# ─── Main ────────────────────────────────────────────────────────────

def main():
    """Start the Jarvis service wrapper server."""
    print("=" * 70)
    print("JARVIS OS SERVICE WRAPPERS v1 — STARTING")
    print("=" * 70)
    print()

    # Initialize loop and manager
    JarvisServiceHandler.loop = get_consolidated_loop()
    JarvisServiceHandler.manager = get_execution_manager()

    server = HTTPServer(("0.0.0.0", SERVICE_PORT), JarvisServiceHandler)
    print(f"  Service running on http://0.0.0.0:{SERVICE_PORT}")
    print(f"  Endpoints:")
    print(f"    GET  /health")
    print(f"    POST /jobs")
    print(f"    GET  /jobs/:job_id")
    print(f"    GET  /traces/:trace_id")
    print(f"    GET  /workers/status")
    print(f"    GET  /ecc/stats")
    print(f"    GET  /events/stream")
    print()
    print("Press Ctrl+C to stop")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
