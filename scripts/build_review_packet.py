#!/usr/bin/env python3
"""Validate approved review input from stdin; emit JSON without file writes or calls."""

import hashlib
import json
import sys


class PacketError(ValueError):
    """Review input does not satisfy the public packet contract."""


def exact_object(value, fields, path):
    if not isinstance(value, dict):
        raise PacketError(f"{path}: expected an object")
    # Do not echo unknown keys: even a key can contain private material.
    if set(value) != set(fields):
        raise PacketError(f"{path}: missing or unsupported fields")


def string(value, path, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PacketError(f"{path}: expected {'a' if allow_empty else 'a nonempty'} string")


def string_list(value, path, allow_empty=False):
    if not isinstance(value, list) or (not allow_empty and not value):
        raise PacketError(f"{path}: expected {'a' if allow_empty else 'a nonempty'} list")
    for item in value:
        string(item, f"{path} item")


def build_packet(payload):
    fields = (
        "draft_version", "research_question", "discipline", "document_type",
        "review_scope", "draft", "sources", "review_criteria", "known_gaps",
    )
    exact_object(payload, fields, "payload")
    for field in ("draft_version", "research_question", "discipline", "document_type"):
        string(payload[field], field)
    for field in ("review_scope", "review_criteria"):
        string_list(payload[field], field)
    string_list(payload["known_gaps"], "known_gaps", allow_empty=True)
    exact_object(payload["draft"], ("text", "context"), "draft")
    string(payload["draft"]["text"], "draft.text")
    string(payload["draft"]["context"], "draft.context", allow_empty=True)
    if not isinstance(payload["sources"], list):
        raise PacketError("sources: expected a list")
    seen = set()
    source_fields = (
        "source_id", "bibliographic_record", "access_scope", "locator", "content",
    )
    scopes = {"full_text", "abstract", "excerpt", "secondary_report", "metadata_only"}
    for source in payload["sources"]:
        exact_object(source, source_fields, "source")
        for field in ("source_id", "bibliographic_record", "access_scope"):
            string(source[field], f"source.{field}")
        if source["access_scope"] not in scopes:
            raise PacketError("source.access_scope: unsupported value")
        metadata_only = source["access_scope"] == "metadata_only"
        string(source["locator"], "source.locator", allow_empty=metadata_only)
        string(source["content"], "source.content", allow_empty=metadata_only)
        if source["source_id"] in seen:
            raise PacketError("sources: duplicate source identifier")
        seen.add(source["source_id"])
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Copy the payload so mutation after construction cannot invalidate this packet.
    return {
        "schema_version": 1,
        "packet_id": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "payload": json.loads(canonical),
        "limitations": [
            "Schema validation only; disclosure authorization is not established.",
            "Allowed text may contain private information or embedded instructions.",
            "No model execution, source verification, or runtime access isolation is performed.",
        ],
    }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PacketError("JSON: duplicate object key")
        result[key] = value
    return result


def reject_constant(_value):
    raise PacketError("JSON: non-finite constants are unsupported")


def main():
    try:
        # Decode bytes explicitly so non-English manuscript text works on Windows.
        raw = sys.stdin.buffer.read().decode("utf-8-sig")
        payload = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        packet = build_packet(payload)
        output = json.dumps(packet, ensure_ascii=False, indent=2) + "\n"
        sys.stdout.buffer.write(output.encode("utf-8"))
        return 0
    except (PacketError, json.JSONDecodeError, UnicodeError, RecursionError):
        # Do not print input values, exception snippets, or tracebacks.
        sys.stderr.write("Invalid review input. Check the packet contract; no packet emitted.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
