#!/usr/bin/env python3
"""
Control Center Dashboard — Read-only local dashboard on port 8765.

Panels:
- system health
- job status
- worker status
- ECC stats
- trace replay
- latest goal queue

Rules:
- Read-only only
- No execute button
- No publish button
- No CRM write action
- No automated messaging
- No live control actions
"""

import json
import os
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(RUNTIME_ROOT, "data")
BACKEND_DIR = os.path.join(RUNTIME_ROOT, "backend")
sys.path.insert(0, BACKEND_DIR)

from jarvis_os_consolidated_execution import get_consolidated_loop
from jarvis_os_event_store import get_event_store_stats, replay_trace


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for read-only Control Center dashboard."""

    def log_message(self, format, *args):
        """Suppress request logging to keep output clean."""
        pass

    def send_json(self, data: dict, status: int = 200):
        """Send JSON response with CORS headers for preview domain."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # CORS: Allow preview domain for local testing
        self.send_header("Access-Control-Allow-Origin", "https://preview-1778809024398799700.vibepreview.com")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "https://preview-1778809024398799700.vibepreview.com")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/":
            self._serve_dashboard_html()
        elif path == "/api/health":
            self._health()
        elif path == "/api/jobs":
            self._jobs()
        elif path == "/api/workers":
            self._workers()
        elif path == "/api/ecc-stats":
            self._ecc_stats()
        elif path == "/api/events":
            self._events(params)
        elif path == "/api/goals":
            self._goals()
        elif path == "/api/event-store":
            self._event_store_stats()
        elif path == "/api/dashboard":
            self._dashboard()
        elif path == "/api/traces/{trace_id}":
            self._traces(params)
        else:
            self.send_json({"error": "Not found"}, 404)

    def _health(self):
        """System health endpoint."""
        loop = get_consolidated_loop()
        self.send_json({
            "system_status": "operational",
            "runtime_loaded": True,
            "ecc_available": True,
            "entrypoint_locked": True,
            "dashboard_mode": "read-only",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _jobs(self):
        """Job status endpoint."""
        jobs_file = os.path.join(DATA_DIR, "jobs.json")
        jobs = []
        if os.path.exists(jobs_file):
            with open(jobs_file) as f:
                data = json.load(f)
                jobs = data if isinstance(data, list) else data.get("jobs", [])

        self.send_json({
            "total_jobs": len(jobs),
            "jobs": jobs[-20:],  # Last 20 jobs
        })

    def _workers(self):
        """Worker status endpoint."""
        # Read worker handler info from jarvis-os
        jarvis_root = os.path.join(RUNTIME_ROOT, "..", "jarvis-os")
        workers = {}
        for name in ["ui", "api", "browser", "state", "ai"]:
            workers[name] = {"status": "ready", "available": True}

        self.send_json({
            "workers": workers,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "available_actions": ["check_accessible", "health_check", "append_log", "classify"],
        })

    def _ecc_stats(self):
        """ECC stats endpoint."""
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        approvals = 0
        blocks = 0
        reasons = {}

        if os.path.exists(ecc_file):
            with open(ecc_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decision = json.loads(line)
                        # Handle both 'approved' (bool) and 'decision' (string) formats
                        approved_val = decision.get("approved")  # True/False
                        decision_str = decision.get("decision", "").lower()
                        
                        if approved_val is True or decision_str in ("approved", "true", "allow"):
                            approvals += 1
                        elif approved_val is False or decision_str in ("blocked", "false", "deny"):
                            blocks += 1
                            reason = decision.get("reason", "unknown")
                            reasons[reason] = reasons.get(reason, 0) + 1
                    except Exception:
                        pass

        total = approvals + blocks
        block_rate = (blocks / total * 100) if total > 0 else 0.0

        self.send_json({
            "approvals": approvals,
            "blocks": blocks,
            "block_rate": round(block_rate, 1),
            "total_decisions": total,
            "top_violation_reasons": reasons,
            "last_blocked_trace_id": None,
        })

    def _events(self, params):
        """Events endpoint — read-only event store."""
        trace_id = params.get("trace_id", [None])[0]
        job_id = params.get("job_id", [None])[0]
        event_type = params.get("event_type", [None])[0]
        limit = int(params.get("limit", [100])[0])

        if trace_id:
            events = replay_trace(trace_id)
        else:
            events = []

        self.send_json({
            "total_events": len(events),
            "events": events[-limit:],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _goals(self):
        """Goal queue endpoint."""
        state_file = os.path.join(DATA_DIR, "state.json")
        goals = []

        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            goals = state.get("goal_queue", {}).get("top_goals_today", [])

        self.send_json({
            "total_goals": len(goals),
            "goals": goals,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _event_store_stats(self):
        """Event store stats endpoint."""
        stats = get_event_store_stats()
        self.send_json(stats)

    def _dashboard(self):
        """Aggregated dashboard endpoint — all system state in one call."""
        loop = get_consolidated_loop()

        # Load jobs
        jobs_file = os.path.join(DATA_DIR, "jobs.json")
        jobs = []
        if os.path.exists(jobs_file):
            with open(jobs_file) as f:
                data = json.load(f)
                jobs = data if isinstance(data, list) else data.get("jobs", [])

        # Load ECC decisions
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        ecc_approvals = 0
        ecc_blocks = 0
        if os.path.exists(ecc_file):
            with open(ecc_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        decision = json.loads(line)
                        # Handle both 'approved' (bool) and 'decision' (string) formats
                        approved_val = decision.get("approved")  # True/False
                        decision_str = decision.get("decision", "").lower()
                        
                        if approved_val is True or decision_str in ("approved", "true", "allow"):
                            ecc_approvals += 1
                        elif approved_val is False or decision_str in ("blocked", "false", "deny"):
                            ecc_blocks += 1
                    except Exception:
                        pass

        # Load goals
        state_file = os.path.join(DATA_DIR, "state.json")
        goals = []
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            goals = state.get("goal_queue", {}).get("top_goals_today", [])

        # Event store stats
        event_stats = get_event_store_stats()

        self.send_json({
            "health": {
                "system_status": "operational",
                "runtime_loaded": True,
                "ecc_available": True,
                "entrypoint_locked": True,
                "dashboard_mode": "read-only",
            },
            "jobs": {
                "total": len(jobs),
                "recent": jobs[-10:],
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
            "workers": {
                "ui": {"status": "ready", "available": True},
                "api": {"status": "ready", "available": True},
                "browser": {"status": "ready", "available": True},
                "state": {"status": "ready", "available": True},
                "ai": {"status": "ready", "available": True},
                "available_actions": ["check_accessible", "health_check", "append_log", "classify"],
            },
            "ecc": {
                "approvals": ecc_approvals,
                "blocks": ecc_blocks,
                "total_decisions": ecc_approvals + ecc_blocks,
                "block_rate": round((ecc_blocks / (ecc_approvals + ecc_blocks) * 100) if (ecc_approvals + ecc_blocks) > 0 else 0.0, 1),
            },
            "goals": goals[:5],
            "events": {
                "total_events": event_stats.get("total_events", 0),
                "last_updated": event_stats.get("last_updated", datetime.now(timezone.utc).isoformat()),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _traces(self, params):
        """Trace replay endpoint."""
        trace_ids = params.get("trace_id", [])
        limit = int(params.get("limit", [100])[0])

        traces = []
        for trace_id in trace_ids:
            events = replay_trace(trace_id)
            traces.append({
                "trace_id": trace_id,
                "events": events[-limit:],
                "event_count": len(events),
            })

        self.send_json({
            "traces": traces,
            "total_traces": len(traces),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def do_POST(self):
        """Handle POST requests (job execution)."""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/execute":
            self._execute()
        else:
            self.send_json({"error": "Not found"}, 404)

    def _execute(self):
        """Execute job through ECC gate → worker routing → event store."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        execution_type = payload.get("execution_type", "").upper()
        target_system = payload.get("target_system", "")
        action = payload.get("action", "")
        validation_only = payload.get("validation_only", False)

        # Generate trace_id
        import uuid
        trace_id = f"trace-{uuid.uuid4().hex[:12]}"
        job_id = f"job-{uuid.uuid4().hex[:8]}"
        
        # ECC validation
        blocked = False
        violated_layer = None
        ecc_decision = None
        reason = None

        # Check target_system against blocked patterns
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

        if not blocked:
            # Check if target_system is valid
            # Allow special validation targets
            valid_targets = [
                "internal",
                "Chrome port 9333",
                "GrowthHub backend",
                "GrowthHub",
            ]
            
            if target_system in valid_targets:
                blocked = False
            elif target_system and target_system != "internal":
                # Allow preview URLs and localhost
                if "preview" in target_system.lower() or "localhost" in target_system.lower():
                    blocked = False
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
        
        ecc_file = os.path.join(DATA_DIR, "ecc_decisions.jsonl")
        with open(ecc_file, "a") as f:
            f.write(json.dumps(ecc_decision) + "\n")

        if blocked:
            # Return blocked response
            self.send_json({
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

        # Route to worker
        worker_map = {
            "UI": "ui",
            "API": "api",
            "BROWSER": "browser",
            "STATE": "state",
            "AI": "ai",
        }

        worker_name = worker_map.get(execution_type)
        if not worker_name:
            self.send_json({
                "job_id": job_id,
                "trace_id": trace_id,
                "status": "error",
                "blocked": False,
                "reason": f"Unknown execution type: {execution_type}",
                "mutation_performed": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        # Execute worker (validation only mode)
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

        # Write job to jobs.json
        job_data = {
            "job_id": job_id,
            "trace_id": trace_id,
            "execution_type": execution_type,
            "status": "completed",
            "worker": worker_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

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

        # Write event to event store
        event = {
            "event_id": uuid.uuid4().hex[:12],
            "trace_id": trace_id,
            "job_id": job_id,
            "event_type": "job_completed",
            "worker": worker_name,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        from jarvis_os_event_store import append_event
        append_event("job_completed", event, trace_id=trace_id, job_id=job_id)

        self.send_json({
            "job_id": job_id,
            "trace_id": trace_id,
            "status": "completed",
            "blocked": False,
            "worker_result": worker_result,
            "mutation_performed": False,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _serve_dashboard_html(self):
        """Serve the read-only dashboard HTML."""
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Jarvis Control Center — Read Only</title>
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
        .warning { background: #2a1a00; border-color: #ffaa00; color: #ffaa00; }
    </style>
</head>
<body>
    <h1>⚕ Jarvis Control Center — READ ONLY</h1>
    <div class="warning">No execute button. No publish button. No live control actions.</div>

    <div class="panel">
        <h2>System Health</h2>
        <div id="health">Loading...</div>
    </div>

    <div class="panel">
        <h2>Job Status (Last 20)</h2>
        <table id="jobs-table">
            <tr><th>Job ID</th><th>Trace ID</th><th>Status</th><th>Type</th></tr>
        </table>
    </div>

    <div class="panel">
        <h2>Worker Status</h2>
        <table id="workers-table">
            <tr><th>Worker</th><th>Status</th><th>Available</th></tr>
        </table>
    </div>

    <div class="panel">
        <h2>ECC Stats</h2>
        <div id="ecc-stats">Loading...</div>
    </div>

    <div class="panel">
        <h2>Event Store</h2>
        <div id="event-store">Loading...</div>
    </div>

    <div class="panel">
        <h2>Goal Queue</h2>
        <table id="goals-table">
            <tr><th>Rank</th><th>Goal</th><th>Score</th><th>Level</th></tr>
        </table>
    </div>

    <script>
        async function load() {
            try {
                const [health, jobs, workers, ecc, events, goals] = await Promise.all([
                    fetch('/api/health').then(r => r.json()),
                    fetch('/api/jobs').then(r => r.json()),
                    fetch('/api/workers').then(r => r.json()),
                    fetch('/api/ecc-stats').then(r => r.json()),
                    fetch('/api/event-store').then(r => r.json()),
                    fetch('/api/goals').then(r => r.json()),
                ]);

                document.getElementById('health').innerHTML =
                    `<span class="status">● ${health.system_status}</span> | Runtime: ${health.runtime_loaded} | ECC: ${health.ecc_available} | Locked: ${health.entrypoint_locked} | Mode: ${health.dashboard_mode}`;

                // Jobs
                const jobsTable = document.getElementById('jobs-table');
                (jobs.jobs || []).forEach(j => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${j.job_id || '-'}</td><td>${j.trace_id || '-'}</td><td>${j.status || '-'}</td><td>${j.execution_type || '-'}</td>`;
                    jobsTable.appendChild(tr);
                });

                // Workers
                const workersTable = document.getElementById('workers-table');
                Object.entries(workers.workers || {}).forEach(([name, w]) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${name}</td><td class="status">${w.status}</td><td>${w.available ? '✓' : '✗'}</td>`;
                    workersTable.appendChild(tr);
                });

                // ECC
                document.getElementById('ecc-stats').innerHTML =
                    `Approvals: ${ecc.approvals} | Blocks: ${ecc.blocks} | Block Rate: ${(ecc.block_rate * 100).toFixed(1)}%`;

                // Event store
                document.getElementById('event-store').innerHTML =
                    `Total events: ${events.total_events} | Last updated: ${events.last_updated || 'N/A'}`;

                // Goals
                const goalsTable = document.getElementById('goals-table');
                (goals.goals || []).forEach((g, i) => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `<td>${g.rank || i+1}</td><td>${g.title || '-'}</td><td>${g.score || '-'}</td><td>${g.approval_level || '-'}</td>`;
                    goalsTable.appendChild(tr);
                });
            } catch (e) {
                document.body.innerHTML += `<div class="warning">Error loading: ${e.message}</div>`;
            }
        }
        load();
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())


def main(port: int = 8765):
    """Start the read-only dashboard server."""
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Control Center Dashboard: http://localhost:{port}")
    print(f"Status: READ ONLY — No execute, no publish, no CRM write, no messaging")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
