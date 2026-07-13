from __future__ import annotations

import importlib.util
import json
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


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

    def test_partial_staging_is_invisible_until_atomic_publish(self) -> None:
        sealed_results.submit(self.root, "opus", self.tokens["opus"], {"verdict": "ready"})
        staged = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        def blocked_write(fd: int, data: bytes) -> None:
            os_write = sealed_results.os.write
            written = os_write(fd, data[:7])
            self.assertEqual(written, 7)
            staged.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release blocked sealed-result writer")
            remaining = memoryview(data)[7:]
            while remaining:
                count = os_write(fd, remaining)
                remaining = remaining[count:]
            sealed_results.os.fsync(fd)

        def writer() -> None:
            try:
                sealed_results.submit(
                    self.root,
                    "sol",
                    self.tokens["sol"],
                    {"verdict": "complete only"},
                )
            except BaseException as exc:
                failures.append(exc)

        with mock.patch.object(sealed_results, "_write_bytes_and_sync", side_effect=blocked_write):
            thread = threading.Thread(target=writer)
            thread.start()
            self.assertTrue(staged.wait(timeout=5))
            waiting = sealed_results.status(self.root)
            self.assertFalse(waiting["ready"])
            self.assertEqual(waiting["missing"], ["sol"])
            self.assertFalse(waiting["results"]["sol"]["submitted"])
            with self.assertRaises(sealed_results.NotReadyError):
                sealed_results.reveal(self.root)
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            sealed_results.reveal(self.root)["results"]["sol"]["payload"],
            {"verdict": "complete only"},
        )

    def test_interrupted_staging_cleans_up_and_allows_retry(self) -> None:
        def interrupted_write(fd: int, data: bytes) -> None:
            sealed_results.os.write(fd, data[:5])
            raise InterruptedError("simulated interruption before publication")

        with mock.patch.object(
            sealed_results,
            "_write_bytes_and_sync",
            side_effect=interrupted_write,
        ):
            with self.assertRaises(InterruptedError):
                sealed_results.submit(
                    self.root,
                    "sol",
                    self.tokens["sol"],
                    {"attempt": 1},
                )

        self.assertFalse((self.root / "results" / "sol.json").exists())
        self.assertEqual(list((self.root / "results").glob(".sol.json.tmp-*")), [])
        receipt = sealed_results.submit(
            self.root,
            "sol",
            self.tokens["sol"],
            {"attempt": 2},
        )
        self.assertEqual(len(receipt["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
