"""Bounded slow-operation diagnostics, independent of the archive database."""
from datetime import datetime, timezone
import json
from logging import LogRecord, INFO
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading


class PerformanceLog:
    def __init__(self, database):
        self.path = None if str(database) == ':memory:' else Path(database).parent / 'diagnostics' / 'performance.jsonl'
        self.lock = threading.Lock()
        self.handler = None

    def record(self, kind, operation, *, queue_ms=0, execution_ms=0, status=None):
        if self.path is None or queue_ms + execution_ms <= 1000:
            return
        # Callers pass code-owned operation names, never SQL, arguments or exception text.
        value = dict(at=datetime.now(timezone.utc).isoformat(), kind=kind, operation=operation,
                     queue_ms=round(queue_ms, 3), execution_ms=round(execution_ms, 3))
        if status is not None:
            value['status'] = status
        try:
            with self.lock:
                if self.handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.handler = RotatingFileHandler(self.path, maxBytes=2 * 1024 * 1024, backupCount=2, encoding='utf-8')
                self.handler.emit(LogRecord('bridge.performance', INFO, '', 0, json.dumps(value), (), None))
        except Exception:
            pass  # Diagnostics must never fail a business request.

    def close(self):
        with self.lock:
            if self.handler is not None:
                self.handler.close()
                self.handler = None
