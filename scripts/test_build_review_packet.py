"""Behavioral checks for the packet helper; no external services or fixture files."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from build_review_packet import PacketError, build_packet


def valid_input():
    return {
        "draft_version": "v1",
        "research_question": "What follows from the supplied survey?",
        "discipline": "Education",
        "document_type": "Discussion",
        "review_scope": ["The supplied paragraph"],
        "draft": {"text": "Participation was associated with scores.", "context": ""},
        "sources": [{
            "source_id": "S1", "bibliographic_record": "Fictional test source",
            "access_scope": "excerpt", "locator": "Results",
            "content": "An association was observed.",
        }],
        "review_criteria": ["Claim-evidence fit"],
        "known_gaps": [],
    }


class PacketTests(unittest.TestCase):
    def test_valid_packet_preserves_material_and_hashes_payload(self):
        payload = valid_input()
        packet = build_packet(payload)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(packet["payload"], payload)
        self.assertEqual(packet["packet_id"], "sha256:" + hashlib.sha256(canonical.encode()).hexdigest())

    def test_key_order_does_not_change_identity(self):
        payload = valid_input()
        self.assertEqual(build_packet(payload)["packet_id"], build_packet(dict(reversed(list(payload.items()))))["packet_id"])

    def test_changed_claim_or_version_changes_identity(self):
        original = valid_input()
        for field in ("claim", "version"):
            changed = copy.deepcopy(original)
            if field == "claim":
                changed["draft"]["text"] = "Participation caused higher scores."
            else:
                changed["draft_version"] = "v2"
            self.assertNotEqual(build_packet(original)["packet_id"], build_packet(changed)["packet_id"])

    def test_private_fields_rejected_at_every_object_level(self):
        for location in ("root", "draft", "source"):
            with self.subTest(location=location):
                payload = valid_input()
                target = payload if location == "root" else payload["draft"] if location == "draft" else payload["sources"][0]
                target["private_cognitive_model"] = "CANARY"
                with self.assertRaises(PacketError):
                    build_packet(payload)

    def test_missing_fields_empty_text_and_wrong_types_rejected(self):
        for change in ("missing", "blank", "source_type", "criteria_type", "context_type"):
            payload = valid_input()
            if change == "missing":
                del payload["research_question"]
            elif change == "blank":
                payload["draft"]["text"] = "   "
            elif change == "source_type":
                payload["sources"] = {}
            elif change == "criteria_type":
                payload["review_criteria"] = "A criterion"
            else:
                payload["draft"]["context"] = False
            with self.subTest(change=change), self.assertRaises(PacketError):
                build_packet(payload)

    def test_duplicate_source_ids_and_invalid_access_rejected(self):
        payload = valid_input()
        payload["sources"].append(copy.deepcopy(payload["sources"][0]))
        with self.assertRaises(PacketError):
            build_packet(payload)
        payload = valid_input()
        payload["sources"][0]["access_scope"] = "assumed_full_text"
        with self.assertRaises(PacketError):
            build_packet(payload)

    def test_metadata_only_and_no_sources_are_permitted(self):
        payload = valid_input()
        payload["sources"][0].update(access_scope="metadata_only", locator="", content="")
        build_packet(payload)
        payload["sources"] = []
        build_packet(payload)

    def test_constructed_packet_is_not_aliased_to_input(self):
        payload = valid_input()
        packet = build_packet(payload)
        payload["draft"]["text"] = "Changed later"
        self.assertNotEqual(packet["payload"]["draft"]["text"], payload["draft"]["text"])

    def run_cli(self, raw):
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("build_review_packet.py"))],
            input=raw, capture_output=True, check=False,
        )

    def test_cli_handles_non_ascii_text(self):
        payload = valid_input()
        payload["draft"]["text"] = "The study’s findings support correlation only."
        result = self.run_cli(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["payload"], payload)

    def test_invalid_cli_input_emits_no_packet_or_private_content(self):
        payload = valid_input()
        payload["PRIVATE_CANARY_KEY"] = "PRIVATE_CANARY_VALUE"
        cases = [json.dumps(payload).encode(), b'{"private":"PRIVATE_CANARY_VALUE",', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff']
        for raw in cases:
            with self.subTest(raw=raw):
                result = self.run_cli(raw)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"CANARY", result.stderr)


if __name__ == "__main__":
    unittest.main()
