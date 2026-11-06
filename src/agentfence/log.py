"""Structured, bounded, redacting logging to stderr (AF-0106).

* Logs go to **stderr only**, so ``--json`` stdout stays machine-parseable.
* Level comes from ``AGENTFENCE_LOG_LEVEL`` (``ERROR``, ``WARN``, ``INFO``, ``DEBUG``);
  default ``WARN``. An unknown value is a configuration error, not silently ignored.
* Structured fields are passed as ``extra={"fields": {...}}``. Only names in
  :data:`ALLOWED_LOG_FIELDS` are printed; anything else shows ``[REDACTED]``.
  Messages and allowed values go through :func:`agentfence.core.redaction.scrub_text`.
* Output is capped per process (default 1 MiB). After the cap one truncation notice is
  written and further records are dropped, so a runaway loop cannot fill the disk or
  the CI log.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, TextIO

from agentfence.core.redaction import redact_fields, scrub_text

ROOT_LOGGER: Final = "agentfence"
DEFAULT_MAX_LOG_BYTES: Final = 1024 * 1024
LEVELS: Final = {
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}
ALLOWED_LOG_FIELDS: Final = frozenset(
    {
        "adapter",
        "attempt_id",
        "backend",
        "bytes",
        "check_id",
        "command",
        "count",
        "duration_ms",
        "exit_code",
        "limit",
        "outcome",
        "path_alias",
        "profile",
        "reason_code",
        "run_id",
        "scenario_id",
        "scenario_version",
        "status",
        "variant",
        "version",
    }
)


class LogConfigError(ValueError):
    """Invalid logging configuration (maps to CLI exit code 2)."""


def level_from_env(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("AGENTFENCE_LOG_LEVEL", "").strip()
    if not raw:
        return logging.WARNING
    try:
        return LEVELS[raw.upper()]
    except KeyError:
        raise LogConfigError(
            f"AGENTFENCE_LOG_LEVEL={raw!r} is not one of ERROR, WARN, INFO, DEBUG"
        ) from None


class RedactingFormatter(logging.Formatter):
    def __init__(self, *, json_lines: bool, home: str | None) -> None:
        super().__init__()
        self.json_lines = json_lines
        self.home = home

    def format(self, record: logging.LogRecord) -> str:
        message = scrub_text(record.getMessage(), home=self.home)
        raw_fields = getattr(record, "fields", None)
        fields = (
            redact_fields(raw_fields, ALLOWED_LOG_FIELDS, home=self.home)
            if isinstance(raw_fields, Mapping)
            else {}
        )
        if record.exc_info and record.exc_info[0] is not None:
            # Exception type only: tracebacks can carry paths and fixture content.
            fields["exception"] = record.exc_info[0].__name__
        if self.json_lines:
            payload = {
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(
                    timespec="milliseconds"
                ),
                "level": record.levelname,
                "logger": record.name,
                "msg": message,
                **{k: v for k, v in fields.items() if k not in {"ts", "level", "logger", "msg"}},
            }
            return json.dumps(payload, sort_keys=True, ensure_ascii=False)
        suffix = "".join(f" {k}={v}" for k, v in fields.items())
        return f"agentfence: {record.levelname.lower()}: {message}{suffix}"


class BoundedStreamHandler(logging.Handler):
    """Write formatted records to a stream until ``max_bytes`` is reached."""

    def __init__(self, stream: TextIO, max_bytes: int) -> None:
        super().__init__()
        if max_bytes < 256:
            raise LogConfigError("log cap must be at least 256 bytes")
        self.stream = stream
        self.max_bytes = max_bytes
        self.written = 0
        self.truncated = False
        self._guard = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record) + "\n"
        except Exception:  # noqa: BLE001 - logging must never crash the CLI
            self.handleError(record)
            return
        size = len(line.encode("utf-8", "replace"))
        with self._guard:
            if self.truncated:
                return
            if self.written + size > self.max_bytes:
                self.truncated = True
                notice = f"agentfence: warning: log output truncated after {self.written} bytes\n"
                self.stream.write(notice)
                self.stream.flush()
                return
            self.written += size
            self.stream.write(line)
            self.stream.flush()


def configure_logging(
    *,
    level: int | None = None,
    json_lines: bool = False,
    stream: TextIO | None = None,
    max_bytes: int = DEFAULT_MAX_LOG_BYTES,
    env: Mapping[str, str] | None = None,
) -> BoundedStreamHandler:
    """(Re)configure the ``agentfence`` logger. Returns the handler for inspection."""
    env = os.environ if env is None else env
    resolved_level = level_from_env(env) if level is None else level
    handler = BoundedStreamHandler(stream if stream is not None else sys.stderr, max_bytes)
    handler.setFormatter(RedactingFormatter(json_lines=json_lines, home=env.get("HOME")))
    logger = logging.getLogger(ROOT_LOGGER)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(resolved_level)
    logger.propagate = False
    return handler


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}")
