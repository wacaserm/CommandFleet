"""Shared CommandFleet wire framing and command-line validation."""

import argparse
import json
import re

MAX_FRAME = 8192
BOARD_SIZE = 10
ALIAS = re.compile(r"[A-Za-z0-9_-]{1,20}", re.ASCII)


class ProtocolError(ValueError):
    """A bad frame with a CommandFleet error code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def port_number(value):
    """Accept only usable TCP port numbers."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def valid_alias(value):
    return isinstance(value, str) and ALIAS.fullmatch(value) is not None


def encode(kind, **payload):
    """Serialize one compact UTF-8 JSON object followed by LF."""
    data = json.dumps(
        {"msg_type": kind, "payload": payload},
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ProtocolError("MESSAGE_TOO_LARGE", "Frame exceeds 8192 bytes.")
    return data + b"\n"


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"non-JSON constant: {value}")


def pop_message(buffer):
    """Return one message, or None until a full frame arrives."""
    end = buffer.find(b"\n")
    if end > MAX_FRAME or (end == -1 and len(buffer) > MAX_FRAME):
        raise ProtocolError("MESSAGE_TOO_LARGE", "Frame exceeds 8192 bytes.")
    if end == -1:
        return None
    raw = bytes(buffer[:end])
    del buffer[:end + 1]
    try:
        message = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_keys,
            parse_constant=reject_constant,
        )
    except (ValueError, RecursionError):
        raise ProtocolError(
            "INVALID_SCHEMA", "Expected a UTF-8 JSON object."
        ) from None
    if (
        not isinstance(message, dict)
        or set(message) != {"msg_type", "payload"}
        or not isinstance(message["msg_type"], str)
        or not isinstance(message["payload"], dict)
    ):
        raise ProtocolError(
            "INVALID_SCHEMA", "Expected msg_type and payload fields."
        )
    return message["msg_type"], message["payload"]
