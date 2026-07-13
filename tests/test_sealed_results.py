from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sealed_results.py"
SPEC = importlib.util.spec_from_file_location("sealed_results", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sealed_results = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sealed_results)


class SealedResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "contract"
        initialized = sealed_results.initialize(self.root, ("sol", "opus"))
        self.tokens = initialized["capabilities"]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_refuses_overwrite(self) -> None:
        sealed_results.submit(self.root, "sol", self.tokens["sol"], {"answer": 1})
        with self.assertRaises(sealed_results.AlreadySubmittedError):
            sealed_results.submit(self.root, "sol", self.tokens["sol"], {"answer": 2})

        stored = json.loads((self.root / "results" / "sol.json").read_text())
        self.assertEqual(stored["payload"], {"answer": 1})
        mode = stat.S_IMODE((self.root / "results" / "sol.json").stat().st_mode)
        self.assertEqual(mode & 0o222, 0)

    def test_rejects_bad_team_capability(self) -> None:
        with self.assertRaises(sealed_results.AuthenticationError):
            sealed_results.submit(self.root, "sol", self.tokens["opus"], {"answer": 1})
        self.assertFalse((self.root / "results" / "sol.json").exists())

    def test_reveal_waits_without_exposing_first_payload(self) -> None:
        sealed_results.submit(self.root, "sol", self.tokens["sol"], {"secret": "first"})
        waiting = sealed_results.status(self.root)
        self.assertFalse(waiting["ready"])
        self.assertEqual(waiting["missing"], ["opus"])
        self.assertNotIn("payload", json.dumps(waiting))
        self.assertNotIn("secret", json.dumps(waiting))
        with self.assertRaises(sealed_results.NotReadyError):
            sealed_results.reveal(self.root)

    def test_reveals_both_results_together(self) -> None:
        sol_receipt = sealed_results.submit(
            self.root, "sol", self.tokens["sol"], {"verdict": "pass"}
        )
        opus_receipt = sealed_results.submit(
            self.root, "opus", self.tokens["opus"], {"verdict": "fail"}
        )

        revealed = sealed_results.reveal(self.root)
        self.assertTrue(revealed["ready"])
        self.assertEqual(revealed["results"]["sol"]["payload"], {"verdict": "pass"})
        self.assertEqual(revealed["results"]["opus"]["payload"], {"verdict": "fail"})
        self.assertEqual(revealed["results"]["sol"]["sha256"], sol_receipt["sha256"])
        self.assertEqual(revealed["results"]["opus"]["sha256"], opus_receipt["sha256"])

    def test_canonical_hash_is_stable_across_key_order(self) -> None:
        first = {"nested": {"z": 3, "a": 1}, "items": [2, 1]}
        second = {"items": [2, 1], "nested": {"a": 1, "z": 3}}
        first_envelope = {"schema_version": 1, "team": "sol", "payload": first}
        second_envelope = {"payload": second, "team": "sol", "schema_version": 1}
        self.assertEqual(
            sealed_results.sha256_hex(sealed_results.canonical_json(first_envelope)),
            sealed_results.sha256_hex(sealed_results.canonical_json(second_envelope)),
        )


if __name__ == "__main__":
    unittest.main()
