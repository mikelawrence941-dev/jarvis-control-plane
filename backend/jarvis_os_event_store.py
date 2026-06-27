#!/usr/bin/env python3
"""
Durable Event Store — Append-only event log with sequence_id ordering, trace_id indexing,
and search/filter capabilities.

Usage:
    from jarvis_os_event_store import EventStore
    store = EventStore()
    store.append("job_completed", {"job_id": "abc", "trace_id": "xyz"})
    events = store.search(trace_id="xyz")
    replay = store.replay_trace("xyz")
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


RUNTIME_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(RUNTIME_ROOT, "lifepilot_runtime", "data")
EVENTS_FILE = os.path.join(DATA_DIR, "events_store.jsonl")


class EventStore:
    """Append-only event store with sequence_id, trace_id indexing, and search/filter."""

    def __init__(self, events_file: Optional[str] = None):
        self.events_file = events_file or EVENTS_FILE
        self._seq_counter = self._load_last_sequence()

    def _load_last_sequence(self) -> int:
        """Load the last sequence_id from the events file."""
        if not os.path.exists(self.events_file):
            return 0
        last_seq = 0
        try:
            with open(self.events_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        seq = event.get("sequence_id", 0)
                        if isinstance(seq, int) and seq > last_seq:
                            last_seq = seq
        except Exception:
            pass
        return last_seq

    def append(self, event_type: str, data: Dict[str, Any],
               trace_id: Optional[str] = None,
               job_id: Optional[str] = None) -> Dict[str, Any]:
        """Append a new event. Returns the event with sequence_id."""
        self._seq_counter += 1
        event = {
            "sequence_id": self._seq_counter,
            "event_type": event_type,
            "trace_id": trace_id,
            "job_id": job_id,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "appended",
        }

        with open(self.events_file, "a") as f:
            f.write(json.dumps(event) + "\n")

        return event

    def replay_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Replay all events for a given trace_id, ordered by sequence_id."""
        return self.search(trace_id=trace_id)

    def search(self, trace_id: Optional[str] = None,
               job_id: Optional[str] = None,
               event_type: Optional[str] = None,
               status: Optional[str] = None,
               limit: int = 100,
               offset: int = 0) -> List[Dict[str, Any]]:
        """Search/filter events. Returns events ordered by sequence_id."""
        results = []
        if not os.path.exists(self.events_file):
            return results

        try:
            with open(self.events_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)

                    # Apply filters
                    if trace_id is not None and event.get("trace_id") != trace_id:
                        continue
                    if job_id is not None and event.get("job_id") != job_id:
                        continue
                    if event_type is not None and event.get("event_type") != event_type:
                        continue
                    if status is not None and event.get("status") != status:
                        continue

                    results.append(event)
        except Exception:
            return []

        # Sort by sequence_id (should already be sorted, but ensure)
        results.sort(key=lambda e: e.get("sequence_id", 0))

        # Apply pagination
        return results[offset:offset + limit]

    def get_stats(self) -> Dict[str, Any]:
        """Return event store statistics."""
        stats = {
            "total_events": 0,
            "last_sequence_id": self._seq_counter,
            "event_types": {},
            "last_updated": None,
        }

        if not os.path.exists(self.events_file):
            return stats

        try:
            with open(self.events_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    event = json.loads(line)
                    stats["total_events"] += 1
                    event_type = event.get("event_type", "unknown")
                    stats["event_types"][event_type] = stats["event_types"].get(event_type, 0) + 1
                    stats["last_updated"] = event.get("timestamp")
        except Exception:
            pass

        return stats

    def clear(self):
        """Clear all events (admin-only, use with caution)."""
        with open(self.events_file, "w") as f:
            f.write("")
        self._seq_counter = 0


# Singleton
_store: Optional[EventStore] = None


def get_event_store() -> EventStore:
    """Get or create the singleton EventStore."""
    global _store
    if _store is None:
        _store = EventStore()
    return _store


# Convenience functions
def append_event(event_type: str, data: Dict[str, Any],
                 trace_id: Optional[str] = None,
                 job_id: Optional[str] = None) -> Dict[str, Any]:
    """Append a new event to the store."""
    return get_event_store().append(event_type, data, trace_id, job_id)


def search_events(trace_id: Optional[str] = None,
                  job_id: Optional[str] = None,
                  event_type: Optional[str] = None,
                  status: Optional[str] = None,
                  limit: int = 100,
                  offset: int = 0) -> List[Dict[str, Any]]:
    """Search/filter events."""
    return get_event_store().search(trace_id, job_id, event_type, status, limit, offset)


def replay_trace(trace_id: str) -> List[Dict[str, Any]]:
    """Replay all events for a trace."""
    return get_event_store().replay_trace(trace_id)


def get_event_store_stats() -> Dict[str, Any]:
    """Get event store statistics."""
    return get_event_store().get_stats()


__all__ = [
    "EventStore",
    "get_event_store",
    "append_event",
    "search_events",
    "replay_trace",
    "get_event_store_stats",
]
