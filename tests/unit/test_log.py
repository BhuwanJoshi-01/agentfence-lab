# SPDX-License-Identifier: Apache-2.0
"""Structured logging tests (AF-0106)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from agentfence.log import (
    LogConfigError,
    configure_logging,
    get_logger,
    level_from_env,
)


@pytest.fixture(autouse=True)
def _reset_logger() -> Iterator[None]:
    yield
    logger = logging.getLogger("agentfence")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("value", "level"),
    [
        ("", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("warn", logging.WARNING),
        ("Info", logging.INFO),
        ("DEBUG", logging.DEBUG),
    ],
)
def test_level_from_env(value: str, level: int) -> None:
    assert level_from_env({"AGENTFENCE_LOG_LEVEL": value}) == level


def test_unknown_level_is_an_error() -> None:
    with pytest.raises(LogConfigError, match="TRACE"):
        level_from_env({"AGENTFENCE_LOG_LEVEL": "TRACE"})


def test_json_lines_with_allowlisted_fields_only() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, json_lines=True, stream=stream, env={})
    get_logger("test").info(
        "run started", extra={"fields": {"run_id": "r-1", "count": 2, "api_key": "zzz"}}
    )
    record = json.loads(stream.getvalue())
    assert record["msg"] == "run started"
    assert record["level"] == "INFO"
    assert record["logger"] == "agentfence.test"
    assert record["run_id"] == "r-1"
    assert record["count"] == 2
    assert record["api_key"] == "[REDACTED]"
    assert record["ts"].endswith("+00:00")


def test_fields_cannot_overwrite_core_keys() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, json_lines=True, stream=stream, env={})
    get_logger("t").info("real", extra={"fields": {"msg": "fake", "level": "ERROR"}})
    record = json.loads(stream.getvalue())
    assert record["msg"] == "real"
    assert record["level"] == "INFO"


def test_human_format_scrubs_message_and_home() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream, env={"HOME": "/home/dev"})
    get_logger("t").warning(
        "wrote /home/dev/state with AF_FAKE_TOKEN_1 \x1b[2Jcleared",
        extra={"fields": {"status": "ok"}},
    )
    line = stream.getvalue()
    assert (
        line == "agentfence: warning: wrote $HOME/state with [SYNTHETIC_SECRET] cleared status=ok\n"
    )


def test_level_filtering() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.ERROR, stream=stream, env={})
    get_logger("t").warning("hidden")
    get_logger("t").error("shown")
    assert stream.getvalue() == "agentfence: error: shown\n"


def test_exceptions_log_type_not_traceback() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, json_lines=True, stream=stream, env={})
    try:
        raise RuntimeError("/home/x/private/path in message")  # noqa: TRY301
    except RuntimeError:
        get_logger("t").exception("collector failed")
    record = json.loads(stream.getvalue())
    assert record["exception"] == "RuntimeError"
    assert "Traceback" not in stream.getvalue()
    assert "private" not in stream.getvalue()


def test_output_is_capped_with_single_notice() -> None:
    stream = io.StringIO()
    handler = configure_logging(level=logging.INFO, stream=stream, max_bytes=300, env={})
    for i in range(100):
        get_logger("t").info("line %d %s", i, "x" * 40)
    out = stream.getvalue()
    assert handler.truncated
    assert out.count("log output truncated") == 1
    assert len(out.encode()) < 300 + 100
    assert handler.written <= 300


def test_cap_too_small_rejected() -> None:
    with pytest.raises(LogConfigError):
        configure_logging(max_bytes=10, stream=io.StringIO(), env={})


def test_reconfigure_replaces_handler() -> None:
    first, second = io.StringIO(), io.StringIO()
    configure_logging(level=logging.INFO, stream=first, env={})
    configure_logging(level=logging.INFO, stream=second, env={})
    get_logger("t").info("once")
    assert first.getvalue() == ""
    assert second.getvalue().count("once") == 1


def test_defaults_to_stderr_never_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level=logging.INFO, env={})
    get_logger("t").info("to stderr")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "to stderr" in captured.err


def test_formatting_error_does_not_raise() -> None:
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream, env={})
    logging.raiseExceptions = False
    try:
        get_logger("t").info("bad %d", "not-a-number")
    finally:
        logging.raiseExceptions = True
    assert stream.getvalue() == ""
