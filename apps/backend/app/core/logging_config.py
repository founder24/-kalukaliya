import logging
import json
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON for observability pipelines."""
    # CONTRACT: user_id must always be MongoDB ObjectId, never email or PII

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "user_id": getattr(record, "user_id", None),
            "lang": getattr(record, "lang", None),
            "provider": getattr(record, "provider", None),
            "latency_ms": getattr(record, "latency_ms", None),
        }

        # Include additional extra fields passed via the extra dict
        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "relativeCreated",
            "pathname",
            "filename",
            "module",
            "funcName",
            "lineno",
            "levelno",
            "levelname",
            "exc_info",
            "exc_text",
            "stack_info",
            "thread",
            "threadName",
            "process",
            "processName",
            "message",
            "msecs",
            "taskName",
            # Already handled above
            "request_id",
            "user_id",
            "lang",
            "provider",
            "latency_ms",
        }
        for key, value in record.__dict__.items():
            if key not in standard_attrs and not key.startswith("_"):
                log_entry[key] = value

        return json.dumps(log_entry, default=str)


def setup_logging() -> None:
    """Configure the root logger with structured JSON output to stdout."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # HF-121: Only add custom handler without removing platform-injected handlers
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    # Avoid duplicate handlers on repeated calls
    if not any(isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JSONFormatter) for h in root_logger.handlers):
        root_logger.addHandler(handler)
