from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "spawn_subscription_fleet.py"


class SpawnSubscriptionFleetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.repo = self.base / "target"
        self.repo.mkdir()
        self._git("init", "-q")
        (self.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git(
            "-c",
            "user.name=CMUX Test",
            "-c",
            "user.email=cmux-test@example.invalid",
            "commit",
            "-qm",
            "baseline",
        )
        self.project = f"fleet-test-{uuid.uuid4().hex}"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            ["python3", str(LAUNCHER), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def _dry_run(self, *task_args: str, project: str | None = None) -> dict:
        result = self._run(
            "--repo",
            str(self.repo),
            "--project",
            project or self.project,
            "--mirrored",
            "--dry-run",
            *task_args,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def _command_topology(value: str) -> tuple[str, str, str, str]:
        parts = shlex.split(value)
        binary = Path(parts[0]).name
        model = parts[parts.index("--model") + 1]
        if binary == "codex":
            effort = parts[parts.index("-c") + 1]
            policy = parts[parts.index("--sandbox") + 1]
        else:
            effort = parts[parts.index("--effort") + 1]
            policy = parts[parts.index("--permission-mode") + 1]
        return binary, model, effort, policy

    def _assert_no_artifacts(self, project: str) -> None:
        team_root = ROOT / ".team"
        artifacts = list(team_root.glob(f"{project}.*")) if team_root.exists() else []
        self.assertEqual(artifacts, [])
        self.assertFalse((team_root / "sealed-results" / project).exists())

    def test_mirrored_dry_run_accepts_inline_task_and_task_file(self) -> None:
        task_file = self.base / "task.txt"
        task_file.write_text("Inspect the same immutable evidence.\n", encoding="utf-8")
        cases = (
            (("--task", "Inspect the same immutable evidence."), "Inspect the same immutable evidence."),
            (("--task-file", str(task_file)), "Inspect the same immutable evidence.\n"),
        )

        for index, (task_args, expected_task) in enumerate(cases):
            with self.subTest(task_args=task_args):
                project = f"{self.project}-{index}"
                status_before = self._git("status", "--porcelain=v1", "--untracked-files=all").stdout
                self._assert_no_artifacts(project)
                plan = self._dry_run(*task_args, project=project)
                self._assert_no_artifacts(project)
                status_after = self._git("status", "--porcelain=v1", "--untracked-files=all").stdout

                self.assertEqual(status_before, status_after)
                self.assertTrue(plan["mirrored"])
                self.assertEqual(set(plan["team_workspaces"]), {"codex", "claude"})
                self.assertEqual(set(plan["layouts"]), {"codex", "claude"})
                self.assertEqual(set(plan["orchestrator_layouts"]), {"codex", "claude"})
                self.assertEqual(set(plan["orchestrators"]), {"codex", "claude"})
                self.assertEqual(plan["envelope"]["task"], expected_task)
                self.assertFalse(plan["provider_api_keys_injected"])

                codex_orchestrator = plan["orchestrator_layouts"]["codex"]["pane"]["surfaces"][0]["command"]
                claude_orchestrator = plan["orchestrator_layouts"]["claude"]["pane"]["surfaces"][0]["command"]
                self.assertEqual(self._command_topology(codex_orchestrator)[0], "codex")
                self.assertEqual(self._command_topology(claude_orchestrator)[0], "claude")
                self.assertIn(expected_task, codex_orchestrator)
                self.assertIn(expected_task, claude_orchestrator)

                self.assertEqual(set(plan["commands"]["codex"]), set(plan["commands"]["claude"]))
                for role in plan["commands"]["codex"]:
                    self.assertEqual(
                        self._command_topology(plan["commands"]["codex"][role]),
                        self._command_topology(plan["commands"]["claude"][role]),
                    )

                shared = plan["envelope"]
                shared_text = json.dumps(shared, sort_keys=True)
                self.assertNotIn("SEALED_RESULTS_TOKEN", shared_text)
                self.assertNotIn("REDACTED", shared_text)
                self.assertNotIn('"capabilities"', shared_text)
                self.assertEqual(
                    set(shared["sealed_results"]["capability_sha256"]),
                    {"codex", "claude"},
                )
                self.assertTrue(
                    all(
                        len(value) == 64
                        for value in shared["sealed_results"]["capability_sha256"].values()
                    )
                )

    def test_mirrored_requires_nonempty_task(self) -> None:
        for task_args in ((), ("--task", "")):
            with self.subTest(task_args=task_args):
                result = self._run(
                    "--repo",
                    str(self.repo),
                    "--project",
                    self.project,
                    "--mirrored",
                    "--dry-run",
                    *task_args,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--mirrored requires a non-empty", result.stderr)

    def test_mutation_mode_remains_fail_closed(self) -> None:
        result = self._run(
            "--repo",
            str(self.repo),
            "--project",
            self.project,
            "--mode",
            "mutate",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mutation mode is fail-closed", result.stderr)

    def test_legacy_dry_run_remains_compatible(self) -> None:
        self._assert_no_artifacts(self.project)
        result = self._run(
            "--repo",
            str(self.repo),
            "--project",
            self.project,
            "--orchestrator",
            "both",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertNotIn("mirrored", plan)
        self.assertEqual(plan["orchestrator"], "both")
        self.assertIn("layout", plan)
        self.assertIn("commands", plan)
        self.assertFalse(plan["provider_api_keys_injected"])
        self._assert_no_artifacts(self.project)

    def test_snapshot_digest_changes_with_untracked_file_content(self) -> None:
        untracked = self.repo / "untracked.txt"
        untracked.write_text("first content\n", encoding="utf-8")
        first = self._dry_run("--task", "Fingerprint this repository.")
        untracked.write_text("second content\n", encoding="utf-8")
        second = self._dry_run("--task", "Fingerprint this repository.")

        first_snapshot = first["envelope"]["target_snapshot"]
        second_snapshot = second["envelope"]["target_snapshot"]
        self.assertEqual(first_snapshot["status_sha256"], second_snapshot["status_sha256"])
        self.assertNotEqual(
            first_snapshot["untracked_manifest_sha256"],
            second_snapshot["untracked_manifest_sha256"],
        )
        self.assertNotEqual(first_snapshot["snapshot_sha256"], second_snapshot["snapshot_sha256"])


if __name__ == "__main__":
    unittest.main()
