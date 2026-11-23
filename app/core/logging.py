"""Structured JSON logging with request IDs and PII redaction helpers.

Application logs never contain full addresses unless LOG_ADDRESS_TEXT=true.
Use `redact()` whenever an address-like string might end up in a log line.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import sys
import time

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn's access log would print query strings (which can contain addresses
    # for /v1/address/search). We emit our own access log without query strings.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: object) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})


def text_fingerprint(text: str) -> str:
    """Stable, non-reversible fingerprint for correlating requests without storing PII."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def redact(text: str | None, allow: bool = False) -> str | None:
    if text is None:
        return None
    if allow:
        return text
    return f"<redacted len={len(text)} fp={text_fingerprint(text)}>"
