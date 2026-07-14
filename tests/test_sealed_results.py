from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_deadline_expiration_pairs_real_result_with_infrastructure_failure(self) -> None:
        root = Path(self.tempdir.name) / "deadline-contract"
        deadline = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        initialized = sealed_results.initialize(
            root,
            ("sol", "opus"),
            deadline_utc=deadline,
        )
        tokens = initialized["capabilities"]
        contract = json.loads((root / "contract.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["deadline_utc"], "2035-01-02T03:04:05Z")
        sealed_results.submit(root, "sol", tokens["sol"], {"verdict": "pass"})

        with self.assertRaises(sealed_results.DeadlineNotReachedError):
            sealed_results.expire(
                root,
                "opus",
                "provider_quota",
                "Claude weekly limit reached",
                now=deadline - timedelta(seconds=1),
            )
        self.assertEqual(sealed_results.status(root)["missing"], ["opus"])

        receipt = sealed_results.expire(
            root,
            "opus",
            "provider_quota",
            "Claude weekly limit reached",
            now=deadline,
        )
        self.assertEqual(receipt["result_type"], "infrastructure_failure")
        status = sealed_results.status(root)
        self.assertTrue(status["ready"])
        self.assertEqual(status["results"]["opus"]["result_type"], "infrastructure_failure")
        self.assertTrue(status["results"]["opus"]["expired"])
        self.assertNotIn("payload", json.dumps(status))

        revealed = sealed_results.reveal(root)
        self.assertEqual(revealed["results"]["sol"]["result_type"], "model_result")
        self.assertEqual(revealed["results"]["sol"]["payload"], {"verdict": "pass"})
        failure = revealed["results"]["opus"]
        self.assertEqual(failure["result_type"], "infrastructure_failure")
        self.assertEqual(failure["infrastructure_failure"]["reason_code"], "provider_quota")
        self.assertNotIn("payload", failure)
        self.assertNotIn("verdict", json.dumps(failure))

        with self.assertRaises(sealed_results.AlreadySubmittedError):
            sealed_results.expire(
                root,
                "opus",
                "other",
                "must not overwrite",
                now=deadline + timedelta(minutes=1),
            )
        with self.assertRaises(sealed_results.AlreadySubmittedError):
            sealed_results.submit(root, "opus", tokens["opus"], {"verdict": "late"})

    def test_expire_cli_publishes_typed_marker_after_deadline(self) -> None:
        root = Path(self.tempdir.name) / "cli-deadline-contract"
        sealed_results.initialize(
            root,
            ("sol", "opus"),
            deadline_utc="2000-01-01T00:00:00Z",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "expire",
                "--root",
                str(root),
                "--team",
                "opus",
                "--reason-code",
                "provider_quota",
                "--message",
                "weekly subscription limit",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["result_type"], "infrastructure_failure")
        raw_marker = (root / "results" / "opus.json").read_text(encoding="utf-8")
        self.assertNotIn('"payload"', raw_marker)
        self.assertNotIn('"verdict"', raw_marker)

    def test_deadline_rejects_late_model_submit_and_preserves_expiration_slot(self) -> None:
        root = Path(self.tempdir.name) / "submission-deadline-contract"
        deadline = datetime(2040, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        initialized = sealed_results.initialize(
            root,
            ("sol", "opus"),
            deadline_utc=deadline,
        )
        tokens = initialized["capabilities"]

        sealed_results.submit(
            root,
            "sol",
            tokens["sol"],
            {"verdict": "on time"},
            now=deadline - timedelta(microseconds=1),
        )
        for late_clock in (deadline, deadline + timedelta(seconds=1)):
            with self.subTest(late_clock=late_clock):
                with self.assertRaisesRegex(
                    sealed_results.DeadlineExceededError,
                    "at or after deadline",
                ):
                    sealed_results.submit(
                        root,
                        "opus",
                        tokens["opus"],
                        {"verdict": "must not publish"},
                        now=late_clock,
                    )
                self.assertFalse((root / "results" / "opus.json").exists())

        sealed_results.expire(
            root,
            "opus",
            "timebox_elapsed",
            "orchestrator did not submit before its deadline",
            now=deadline,
        )
        self.assertEqual(
            sealed_results.reveal(root)["results"]["opus"]["result_type"],
            "infrastructure_failure",
        )

    def test_invalidation_blocks_operations_and_status_exposes_no_payload(self) -> None:
        sealed_results.submit(
            self.root,
            "sol",
            self.tokens["sol"],
            {"secret": "must remain sealed"},
        )
        invalidated_at = datetime(2042, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
        metadata = sealed_results.invalidate(
            self.root,
            "readiness_failed",
            "one child exited after the launch barrier",
            now=invalidated_at,
        )
        self.assertEqual(metadata["result_type"], "run_invalidation")
        self.assertEqual(metadata["invalidated_at_utc"], "2042-03-04T05:06:07Z")
        self.assertEqual(sealed_results.invalidation_status(self.root), metadata)

        status = sealed_results.status(self.root)
        self.assertFalse(status["valid"])
        self.assertFalse(status["ready"])
        self.assertEqual(status["invalidation"], metadata)
        self.assertEqual(status["results"], {})
        self.assertNotIn("payload", json.dumps(status))
        self.assertNotIn("must remain sealed", json.dumps(status))

        with self.assertRaises(sealed_results.RunInvalidatedError):
            sealed_results.submit(
                self.root,
                "opus",
                self.tokens["opus"],
                {"verdict": "late"},
            )
        with self.assertRaises(sealed_results.RunInvalidatedError):
            sealed_results.expire(
                self.root,
                "opus",
                "provider_quota",
                "must not publish",
            )
        with self.assertRaises(sealed_results.RunInvalidatedError):
            sealed_results.reveal(self.root)

    def test_duplicate_invalidation_is_refused_without_replacement(self) -> None:
        first = sealed_results.invalidate(self.root, "first", "original reason")
        with self.assertRaises(sealed_results.AlreadySubmittedError):
            sealed_results.invalidate(self.root, "second", "replacement reason")
        self.assertEqual(sealed_results.invalidation_status(self.root), first)

    def test_invalidation_during_submit_dominates_and_prevents_reveal(self) -> None:
        staged = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []

        original_write = sealed_results._write_bytes_and_sync

        def stage_then_release(fd: int, data: bytes) -> None:
            original_write(fd, data)
            if threading.current_thread() is threading.main_thread():
                return
            staged.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release staged invalidated result")

        def writer() -> None:
            try:
                sealed_results.submit(
                    self.root,
                    "sol",
                    self.tokens["sol"],
                    {"secret": "racing payload"},
                )
            except BaseException as exc:
                failures.append(exc)

        with mock.patch.object(
            sealed_results,
            "_write_bytes_and_sync",
            side_effect=stage_then_release,
        ):
            thread = threading.Thread(target=writer, name="racing-sealed-submit")
            thread.start()
            self.assertTrue(staged.wait(timeout=5))
            sealed_results.invalidate(self.root, "readiness_failed", "coordinator rollback")
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], sealed_results.RunInvalidatedError)
        self.assertFalse((self.root / "results" / "sol.json").exists())
        self.assertFalse(sealed_results.status(self.root)["valid"])
        with self.assertRaises(sealed_results.RunInvalidatedError):
            sealed_results.reveal(self.root)


if __name__ == "__main__":
    unittest.main()
