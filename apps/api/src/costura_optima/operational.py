from __future__ import annotations

import json
import logging
from collections import defaultdict
from contextvars import ContextVar
from threading import Lock
from typing import Any


request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    """JSON logs with the correlation fields shared by HTTP and workers."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_context.get(),
        }
        for field in ("order_id", "run_id", "job_id", "solution_id", "marker_hash", "phase"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception_class"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._observations: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._observations[name].append(value)

    def render(self, gauges: dict[str, float] | None = None) -> str:
        with self._lock:
            lines = [f"# TYPE {name} counter\n{name} {value:g}" for name, value in sorted(self._counters.items())]
            for name, values in sorted(self._observations.items()):
                lines.extend((f"# TYPE {name} summary", f"{name}_count {len(values)}", f"{name}_sum {sum(values):g}"))
        for name, value in sorted((gauges or {}).items()):
            lines.extend((f"# TYPE {name} gauge", f"{name} {value:g}"))
        return "\n".join(lines) + "\n"


metrics = Metrics()
