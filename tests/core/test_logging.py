"""Tests for the structured JSON logging helpers in src/core/logging.py."""

from __future__ import annotations

import json
import logging

from src.core.logging import get_logger


def _record(**kwargs: object) -> logging.LogRecord:
    defaults: dict[str, object] = {
        "name": "openpact.test",
        "level": logging.INFO,
        "pathname": __file__,
        "lineno": 1,
        "msg": "hello %s",
        "args": ("world",),
        "exc_info": None,
    }
    defaults.update(kwargs)
    return logging.LogRecord(
        name=str(defaults["name"]),
        level=int(defaults["level"]),  # type: ignore[arg-type]
        pathname=str(defaults["pathname"]),
        lineno=int(defaults["lineno"]),  # type: ignore[arg-type]
        msg=defaults["msg"],
        args=defaults["args"],  # type: ignore[arg-type]
        exc_info=defaults["exc_info"],  # type: ignore[arg-type]
    )


def _format(record: logging.LogRecord) -> dict[str, object]:
    # The formatter is attached by get_logger; reuse it for direct formatting.
    logger = get_logger("openpact.test.formatter")
    formatter = logger.handlers[0].formatter
    assert formatter is not None
    return json.loads(formatter.format(record))


def test_json_formatter_emits_core_fields_and_renders_args() -> None:
    payload = _format(_record(name="openpact.svc", level=logging.WARNING))
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "openpact.svc"
    assert payload["msg"] == "hello world"  # %-args rendered via getMessage()
    assert isinstance(payload["ts"], str) and payload["ts"]
    assert "exc" not in payload
    assert "extra" not in payload


def test_json_formatter_includes_exception_text_when_present() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record(msg="failed", args=(), exc_info=sys.exc_info())
    payload = _format(record)
    assert "exc" in payload
    assert "ValueError: boom" in str(payload["exc"])


def test_json_formatter_includes_extra_payload_when_set() -> None:
    record = _record(msg="with extra", args=())
    record._extra = {"member": "A000001", "count": 3}  # type: ignore[attr-defined]
    payload = _format(record)
    assert payload["extra"] == {"member": "A000001", "count": 3}


def test_json_formatter_falls_back_to_str_for_non_serializable_extra() -> None:
    record = _record(msg="weird", args=())
    record._extra = {"ids": {1, 2, 3}}  # a set is not JSON-serializable
    # default=str must keep format() from raising.
    payload = _format(record)
    assert "extra" in payload
    assert isinstance(payload["extra"]["ids"], str)  # type: ignore[index]


def test_get_logger_sets_level_and_json_stream_handler() -> None:
    logger = get_logger("openpact.test.unique.alpha", level=logging.DEBUG)
    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter.__class__.__name__ == "_JSONFormatter"


def test_get_logger_is_idempotent_and_does_not_duplicate_handlers() -> None:
    name = "openpact.test.unique.beta"
    first = get_logger(name)
    assert first.level == logging.INFO  # default level
    handler_count = len(first.handlers)
    second = get_logger(name, level=logging.ERROR)
    assert second is first  # same shared logger
    assert len(second.handlers) == handler_count  # no duplicate handler added
    assert second.level == logging.ERROR  # level is updated on each call
