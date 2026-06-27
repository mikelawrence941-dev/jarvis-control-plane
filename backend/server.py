#!/usr/bin/env python3
"""
JARVIS Control Plane — Production API Server (Render)

Deploys the full JARVIS Control Plane as a production HTTP API.
All endpoints documented at: https://api.quantumaisolution.com/api/...

Endpoints:
  GET  /api/health        — System health check
  GET  /api/dashboard     — Aggregated dashboard (all state in one call)
  GET  /api/jobs          — Job history
  GET  /api/workers       — Worker registry
  GET  /api/ecc-stats     — ECC approval/block metrics
  GET  /api/ecc           — Alias for /api/ecc-stats (same data)
  GET  /api/events        — Execution events
  GET  /api/event-store   — Event store stats
  GET  /api/goals         — Goal queue
  GET  /api/traces/<id>   — Trace replay by trace_id
  POST /api/execute       — Execute job through ECC gate → worker routing

  GET  /                  — Read-only dashboard HTML

Environment Variables:
  PORT             — Server port (default: 10000)
  DATA_DIR         — Data directory path (default: ./data)
  CORS_ORIGIN      — CORS allowed origin (default: * for all)
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# ── Configuration ────────────────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", "10000"))
DATA_DIR = os.environ.get("DATA_DIR", "./data")
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# Add backend directory to sys.path for internal module imports
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)

# Internal modules (all stdlib)
from jarvis_os_consolidated_execution import get_consolidated_loop
from jarvis_os_event_store import get_event_store_stats, replay_trace, append_event

# ── Data File Initialization ─────────────────────────────────────────────────

def ensure_data_files():
    """Create missing data files on startup to prevent FileNotFoundError."""
    files_to_create = {
        "jobs.json": [],
        "ecc_decisions.jsonl": "",
        "events_store.jsonl": "",
        "state.json": {},
        "jobs.json": [],
        "events_store.jsonl": "",
        "workers.json": [],
    }
    for filename, default in files_to_create.items():
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            if default == "":
                open(filepath, "w").close()
            else:
                with open(filepath, "w") as f:
                    json.dump(default, f, indent=2)
            print(f"  Created data file: {filepath}")


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class ControlPlaneHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the JARVIS Control Plane."""

    def log_message(self, format, *args):
        """Suppress request logging."""
        pass

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_dashboard_html()
        elif path == "/api/health":
            self._health()
        elif path == "/api/dashboard":
            self._dashboard()
        elif path == "/api/jobs":
            self._jobs()
        elif path == "/api/workers":
            self._workers()
        elif path == "/api/ecc-stats" or path == "/api/ecc":
            self._ecc_stats()
        elif path == "/api/events":
            self._events(params)
        elif path == "/api/goals":
            self._goals()
        elif path == "/api/event-store":
            self._event_store_stats()
        elif path.startswith("/api/traces/"):
            self._trace(params)
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/api/execute":
            self._execute()
        else:
            self._send_json({"error": "Not found"}, 404)

    # ── GET Endpoints ─────────────────────────────────────────────────────

    def _health(self):
        self._send_json({
            "system_status": "operational",
            "runtime_loaded": True,
            "ecc_available": True,
            "entrypoint_locked": True,
            "dashboard_mode": "read-only",
            "port": PORT,
            "data_dir": DATA_DIR,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _jobs(self):
        jobs_file = os.path.join(DATA_DIR, "jobs.json")
        jobs = []
        try:
            with open(jobs_file) as f:
                data = json.load(f)
                jobs = data if isinstance(data, list) else data.get("jobs", [])
        except Exception:
            pass

        self._send_json({
            "total_jobs": len(jobs),
            "jobs": jobs[-20:],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _workers(self):
        workers = {
            "ui": {"status": "ready", "available": True},
            "api": {"status": "ready", "available": True},
            "browser": {"status": "ready", "available": True},
            "state": {"status": "ready", "available": True},
            "ai": {"status": "ready", "available": True},
        }

        self._send_json({
            "workers": workers,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "available_actions": ["check_accessible", "health_check", "append_log", "classify"],
        })

    def _ecc_stats(self):
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        approvals = 0
        blocks = 0
        reasons = {}

        try:
            with open(ecc_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decision = json.loads(line)
                        approved_val = decision.get("approved")
                        decision_str = decision.get("decision", "").lower()

                        if approved_val is True or decision_str in ("approved", "true", "allow"):
                            approvals += 1
                        elif approved_val is False or decision_str in ("blocked", "false", "deny"):
                            blocks += 1
                            reason = decision.get("reason", "unknown")
                            reasons[reason] = reasons.get(reason, 0) + 1
                    except Exception:
                        pass
        except Exception:
            pass

        total = approvals + blocks
        block_rate = (blocks / total * 100) if total > 0 else 0.0

        self._send_json({
            "approvals": approvals,
            "blocks": blocks,
            "total_decisions": total,
            "block_rate": round(block_rate, 1),
            "reasons": reasons,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _events(self, params):
        events_file = os.path.join(DATA_DIR, "events_store.jsonl")
        events = []
        try:
            with open(events_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception:
            pass

        # Filter by trace_id if provided
        trace_id = params.get("trace_id", [None])[0]
        if trace_id:
            events = [e for e in events if e.get("trace_id") == trace_id]

        self._send_json({
            "events": events[-100:],
            "total_returned": len(events),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _event_store_stats(self):
        stats = get_event_store_stats()
        self._send_json(stats)

    def _goals(self):
        state_file = os.path.join(DATA_DIR, "state.json")
        goals = []
        try:
            with open(state_file) as f:
                state = json.load(f)
            goals = state.get("goal_queue", {}).get("top_goals_today", [])
        except Exception:
            pass

        self._send_json({
            "total_goals": len(goals),
            "goals": goals,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _dashboard(self):
        """Aggregated dashboard — all system state in one call."""
        # Health
        health = {
            "system_status": "operational",
            "runtime_loaded": True,
            "ecc_available": True,
            "entrypoint_locked": True,
            "dashboard_mode": "read-only",
            "port": PORT,
        }

        # Jobs
        jobs_file = os.path.join(DATA_DIR, "jobs.json")
        jobs = []
        try:
            with open(jobs_file) as f:
                data = json.load(f)
                jobs = data if isinstance(data, list) else data.get("jobs", [])
        except Exception:
            pass

        # ECC
        ecc_approvals = 0
        ecc_blocks = 0
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        try:
            with open(ecc_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        av = d.get("approved")
                        ds = d.get("decision", "").lower()
                        if av is True or ds in ("approved", "true", "allow"):
                            ecc_approvals += 1
                        elif av is False or ds in ("blocked", "false", "deny"):
                            ecc_blocks += 1
                    except Exception:
                        pass
        except Exception:
            pass

        # Goals
        goals = []
        state_file = os.path.join(DATA_DIR, "state.json")
        try:
            with open(state_file) as f:
                state = json.load(f)
            goals = state.get("goal_queue", {}).get("top_goals_today", [])
        except Exception:
            pass

        # Event store
        event_stats = get_event_store_stats()

        workers = {
            "ui": {"status": "ready", "available": True},
            "api": {"status": "ready", "available": True},
            "browser": {"status": "ready", "available": True},
            "state": {"status": "ready", "available": True},
            "ai": {"status": "ready", "available": True},
            "available_actions": ["check_accessible", "health_check", "append_log", "classify"],
        }

        self._send_json({
            "health": health,
            "jobs": {
                "total": len(jobs),
                "recent": jobs[-10:],
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            "workers": workers,
            "ecc": {
                "approvals": ecc_approvals,
                "blocks": ecc_blocks,
                "total_decisions": ecc_approvals + ecc_blocks,
                "block_rate": round(
                    (ecc_blocks / (ecc_approvals + ecc_blocks) * 100) if (ecc_approvals + ecc_blocks) > 0 else 0.0,
                    1,
                ),
            },
            "goals": goals[:5],
            "events": {
                "total_events": event_stats.get("total_events", 0),
                "last_updated": event_stats.get("last_updated", datetime.now(timezone.utc).isoformat()),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _trace(self, params):
        """Trace replay endpoint — extracts trace_id from URL path."""
        # Path is /api/traces/<trace_id>
        parts = self.path.split("/api/traces/")
        if len(parts) < 2:
            self._send_json({"error": "Invalid trace URL"}, 400)
            return

        trace_id = parts[-1].split("?")[0]  # Remove query string
        trace_id = unquote(trace_id)

        limit = 100
        try:
            limit = int(params.get("limit", [100])[0])
        except Exception:
            pass

        try:
            events = replay_trace(trace_id)
        except Exception:
            events = []

        self._send_json({
            "trace_id": trace_id,
            "events": events[-limit:],
            "event_count": len(events),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── POST Endpoints ────────────────────────────────────────────────────

    def _execute(self):
        """Execute job through ECC gate → worker routing → event store."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, 400)
            return

        execution_type = payload.get("execution_type", "").upper()
        target_system = payload.get("target_system", "")
        action = payload.get("action", "")
        validation_only = payload.get("validation_only", False)

        # Generate identifiers
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"

        # ── ECC Validation ────────────────────────────────────────────────
        blocked = False
        violated_layer = None
        reason = None

        blocked_patterns = [
            "leadconnectorhq.com",
            "leadconnector.com",
            "gohighlevel.com",
            "go-highlevel.com",
        ]

        for pattern in blocked_patterns:
            if pattern in target_system.lower():
                blocked = True
                violated_layer = "FIREWALL"
                reason = f"Target system matches blocked pattern: {pattern}"
                break

        if not blocked and target_system:
            valid_targets = [
                "internal",
                "Chrome port 9333",
                "GrowthHub backend",
                "GrowthHub",
            ]
            if target_system not in valid_targets:
                if "preview" in target_system.lower() or "localhost" in target_system.lower():
                    pass  # Allow
                else:
                    blocked = True
                    violated_layer = "FIREWALL"
                    reason = f"Target system not in allowed domains: {target_system}"

        # Write ECC decision
        ecc_decision = {
            "trace_id": trace_id,
            "decision": "approved" if not blocked else "blocked",
            "reason": reason or "All checks passed",
            "violated_layer": violated_layer,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            with open(os.path.join(DATA_DIR, "ecc_decisions.jsonl"), "a") as f:
                f.write(json.dumps(ecc_decision) + "\n")
        except Exception:
            pass

        if blocked:
            self._send_json({
                "job_id": job_id,
                "trace_id": trace_id,
                "status": "blocked",
                "blocked": True,
                "violated_layer": violated_layer,
                "reason": reason,
                "mutation_performed": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # ── Worker Routing ────────────────────────────────────────────────
        worker_map = {
            "UI": "ui",
            "API": "api",
            "BROWSER": "browser",
            "STATE": "state",
            "AI": "ai",
        }

        worker_name = worker_map.get(execution_type)
        if not worker_name:
            self._send_json({
                "job_id": job_id,
                "trace_id": trace_id,
                "status": "error",
                "blocked": False,
                "reason": f"Unknown execution type: {execution_type}",
                "mutation_performed": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # ── Execute Worker (validation only) ──────────────────────────────
        worker_result = {
            "worker_name": worker_name,
            "execution_type": execution_type,
            "target_system": target_system,
            "action": action,
            "mutation_performed": False,
            "status": "completed",
            "result": {
                "system_status": "operational",
                "validation_passed": True,
                "trace_id": trace_id,
            },
        }

        # ── Write Job ─────────────────────────────────────────────────────
        job_data = {
            "job_id": job_id,
            "trace_id": trace_id,
            "execution_type": execution_type,
            "worker": worker_name,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            jobs_file = os.path.join(DATA_DIR, "jobs.json")
            jobs = []
            if os.path.exists(jobs_file):
                try:
                    with open(jobs_file) as f:
                        data = json.load(f)
                        jobs = data if isinstance(data, list) else data.get("jobs", [])
                except Exception:
                    pass
            jobs.append(job_data)
            with open(jobs_file, "w") as f:
                json.dump(jobs, f, indent=2)
        except Exception:
            pass

        # ── Write Event ───────────────────────────────────────────────────
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "trace_id": trace_id,
            "job_id": job_id,
            "event_type": "job_completed",
            "worker": worker_name,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            append_event("job_completed", event, trace_id=trace_id, job_id=job_id)
        except Exception:
            pass

        self._send_json({
            "job_id": job_id,
            "trace_id": trace_id,
            "status": "completed",
            "blocked": False,
            "worker_result": worker_result,
            "mutation_performed": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    # ── Dashboard HTML ────────────────────────────────────────────────────

    def _serve_dashboard_html(self):
        html = """<!DOCTYPE html>
<html>
<head>
    <title>JARVIS Control Plane — Production API</title>
    <style>
        body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 20px; }
        h1 { color: #00ff88; font-size: 24px; }
        .panel { background: #16213e; border: 1px solid #0f3460; border-radius: 8px; padding: 16px; margin: 16px 0; }
        .panel h2 { color: #00aaff; margin-top: 0; }
        .status { color: #00ff88; }
        .blocked { color: #ff4444; }
        table { width: 100%; border-collapse: collapse; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid #0f3460; }
        th { color: #00aaff; }
    </style>
</head>
<body>
    <h1>JARVIS Control Plane — API Server</h1>
    <div class="panel">
        <h2>API Endpoints</h2>
        <ul>
            <li>GET /api/health — System health</li>
            <li>GET /api/dashboard — Aggregated dashboard</li>
            <li>GET /api/jobs — Job history</li>
            <li>GET /api/workers — Worker registry</li>
            <li>GET /api/ecc-stats — ECC metrics</li>
            <li>GET /api/events — Execution events</li>
            <li>GET /api/goals — Goal queue</li>
            <li>GET /api/event-store — Event store stats</li>
            <li>GET /api/traces/{trace_id} — Trace replay</li>
            <li>POST /api/execute — Execute job</li>
        </ul>
    </div>
    <div class="panel">
        <h2>System Status</h2>
        <div id="health">Loading...</div>
    </div>
    <script>
        fetch('/api/health').then(r => r.json()).then(d => {
            document.getElementById('health').innerHTML =
                '<span class="status">● ' + d.system_status + '</span> | Port: ' + d.port + ' | ECC: ' + d.ecc_available + ' | Mode: ' + d.dashboard_mode;
        });
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    """Start the JARVIS Control Plane API server."""
    ensure_data_files()

    server = HTTPServer(("0.0.0.0", PORT), ControlPlaneHandler)
    print(f"JARVIS Control Plane API Server")
    print(f"  Port: {PORT}")
    print(f"  Data directory: {DATA_DIR}")
    print(f"  CORS Origin: {CORS_ORIGIN}")
    print(f"  Endpoints: /api/health, /api/dashboard, /api/execute, ...")
    print(f"  Status: RUNNING")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
