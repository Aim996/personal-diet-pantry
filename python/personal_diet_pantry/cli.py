"""Strict one-request/one-response JSON Lines command entry point."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
import sys
from typing import Any, BinaryIO, TextIO

from .input_limits import validate_json_value, validate_meal_payload
from .service import (
    DietService,
    internal_error_response,
    invalid_input_response,
    startup_error_response,
)


LOGGER = logging.getLogger(__name__)
_MAX_REQUEST_BYTES = 1024 * 1024


def main(
    *,
    stdin: TextIO | BinaryIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    service_factory: Callable[[], DietService] = DietService,
) -> int:
    """Read exactly one request line and emit exactly one compact response line."""

    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    diagnostic_stream = stderr or sys.stderr
    if stdin is None:
        _configure_utf8(input_stream)
    if stdout is None:
        _configure_utf8(output_stream)
    if stderr is None:
        _configure_utf8(diagnostic_stream)
    logging.basicConfig(stream=diagnostic_stream, level=logging.INFO, force=True)

    try:
        try:
            request = _read_validated_request(input_stream)
        except (json.JSONDecodeError, UnicodeError, ValueError):
            response = invalid_input_response("Input must be one complete JSON object")
            _write_response(output_stream, response)
            return 0

        try:
            service = service_factory()
        except Exception as error:
            LOGGER.exception("Diet service startup failed")
            _write_response(output_stream, startup_error_response(error))
            return 2

        try:
            try:
                response = service.dispatch(request)
            except Exception:
                LOGGER.exception("Unexpected uncaught dispatch failure")
                response = internal_error_response()
            _write_response(output_stream, response)
            return 0
        finally:
            try:
                service.close()
            except Exception:
                LOGGER.exception("Diet service shutdown failed")
    except Exception:
        LOGGER.exception("Unexpected CLI input failure")
        _write_response(output_stream, internal_error_response())
        return 0


def _read_validated_request(stream: TextIO | BinaryIO) -> dict[str, Any]:
    binary = getattr(stream, "buffer", None)
    if binary is None and isinstance(stream.read(0), bytes):
        binary = stream
    request_text = (
        _read_binary_request(binary)
        if binary is not None
        else _read_text_request(stream)
    )
    request: Any = json.loads(
        request_text,
        parse_constant=_reject_json_constant,
    )
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object")
    validate_json_value(request)
    _validate_action_payload_limits(request)
    return request


def _validate_action_payload_limits(request: Mapping[str, Any]) -> None:
    """Apply action-specific structural limits before service construction."""

    if request.get("domain") != "meal":
        return
    payload = request.get("payload")
    if not isinstance(payload, Mapping):
        return
    action = request.get("action")
    draft: Any
    if action == "preview_record":
        draft = payload
    elif action == "update":
        draft = payload.get("draft")
    else:
        return
    if isinstance(draft, Mapping):
        validate_meal_payload(draft)


def _read_binary_request(stream: BinaryIO) -> str:
    line = stream.readline(_MAX_REQUEST_BYTES + 3)
    if not isinstance(line, bytes):
        raise ValueError("Binary input stream must return bytes")
    if line.endswith(b"\r\n"):
        content = line[:-2]
    elif line.endswith(b"\n"):
        content = line[:-1]
    else:
        content = line
    if len(content) > _MAX_REQUEST_BYTES:
        raise ValueError("Input exceeds the maximum JSON Lines request size")
    if stream.read(1) != b"":
        raise ValueError("Input must contain exactly one request line")
    return content.decode("utf-8")


def _read_text_request(stream: TextIO | BinaryIO) -> str:
    parts: list[str] = []
    byte_count = 0
    while True:
        chunk = stream.read(4096)
        if not isinstance(chunk, str):
            raise ValueError("Text input stream must return text")
        if not chunk:
            break
        newline = chunk.find("\n")
        if newline >= 0:
            parts.append(chunk[:newline])
            if chunk[newline + 1 :] or stream.read(1):
                raise ValueError("Input must contain exactly one request line")
            break
        parts.append(chunk)
        byte_count += len(chunk.encode("utf-8"))
        if byte_count > _MAX_REQUEST_BYTES:
            raise ValueError("Input exceeds the maximum JSON Lines request size")
    request_text = "".join(parts)
    if request_text.endswith("\r"):
        request_text = request_text[:-1]
    if len(request_text.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("Input exceeds the maximum JSON Lines request size")
    return request_text


def _write_response(stream: TextIO, response: Any) -> None:
    stream.write(
        json.dumps(
            response,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    )
    stream.flush()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
