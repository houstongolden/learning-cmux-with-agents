from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest import mock

from scripts import spawn_subscription_fleet as fleet


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "spawn_subscription_fleet.py"


class SpawnSubscriptionFleetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.repo = self.base / "target repository"
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

    def _provider_parts(self, value: str) -> list[str]:
        parts = shlex.split(value)
        if Path(parts[0]).name == "sandbox-exec":
            self.assertEqual(parts[1], "-p")
            self.assertIn(str(self.repo.resolve()), parts[2])
            parts = shlex.split(parts[-1])
        if Path(parts[0]).name == "env":
            index = 1
            while index < len(parts) and parts[index] == "-u":
                index += 2
            parts = parts[index:]
        return parts

    def _assert_api_credentials_scrubbed(self, value: str) -> None:
        parts = shlex.split(value)
        if Path(parts[0]).name == "sandbox-exec":
            parts = shlex.split(parts[-1])
        self.assertEqual(parts[0], "/usr/bin/env")
        unset: set[str] = set()
        index = 1
        while index < len(parts) and parts[index] == "-u":
            unset.add(parts[index + 1])
            index += 2
        self.assertEqual(unset, fleet.PROBE_ENV_DENYLIST)

    def _assert_surface_guarded(self, value: str) -> None:
        parts = shlex.split(value)
        self.assertEqual(Path(parts[0]).name, "sandbox-exec")
        self.assertIn(str(self.repo.resolve()), parts[2])

    def _command_topology(self, value: str) -> tuple[str, str, str, str]:
        parts = self._provider_parts(value)
        binary = Path(parts[0]).name
        model = parts[parts.index("--model") + 1]
        if binary == "codex":
            effort = next(
                parts[index + 1]
                for index, part in enumerate(parts[:-1])
                if part == "-c" and parts[index + 1].startswith("model_reasoning_effort=")
            )
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

    def _assert_codex_repo_binding(self, value: str) -> None:
        parts = self._provider_parts(value)
        self.assertEqual(Path(parts[0]).name, "codex")
        self.assertEqual(parts[parts.index("-C") + 1], str(self.repo.resolve()))
        config_values = [parts[index + 1] for index, part in enumerate(parts[:-1]) if part == "-c"]
        expected = f'projects.{json.dumps(str(self.repo.resolve()))}.trust_level="trusted"'
        self.assertIn(expected, config_values)
        self._assert_api_credentials_scrubbed(value)

    def _assert_claude_unchanged(self, value: str) -> None:
        parts = self._provider_parts(value)
        self.assertEqual(Path(parts[0]).name, "claude")
        self.assertNotIn("-C", parts)
        self.assertFalse(any("trust_level" in part for part in parts))
        self._assert_api_credentials_scrubbed(value)

    @staticmethod
    def _launch_layout(*names: str) -> dict:
        return {
            "pane": {
                "surfaces": [
                    {"type": "terminal", "name": name, "command": "codex --model test"}
                    for name in names
                ]
            }
        }

    @staticmethod
    def _assert_barriered(layout: dict, gate: Path) -> None:
        commands: list[str] = []

        def collect(value) -> None:
            if isinstance(value, dict):
                if value.get("type") == "terminal":
                    commands.append(value["command"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(layout)
        assert commands
        for value in commands:
            parts = shlex.split(value)
            assert parts[0] == "/bin/sh"
            assert str(gate) in parts
            assert "while [ ! -f" in parts[2]
            assert Path(shlex.split(parts[-1])[0]).name == "sandbox-exec"

    @staticmethod
    def _mock_topology(include_claude_orchestrator: bool = True) -> dict:
        specs = [
            ("workspace:101", "barrier-test-codex-team", ("lead", "worker")),
            ("workspace:102", "barrier-test-claude-team", ("lead", "worker")),
            ("workspace:103", "barrier-test-codex-orchestrator", ("codex-orchestrator",)),
            ("workspace:104", "barrier-test-claude-orchestrator", ("claude-orchestrator",)),
        ]
        if not include_claude_orchestrator:
            specs.pop()
        return {
            "windows": [
                {
                    "workspaces": [
                        {
                            "ref": ref,
                            "title": title,
                            "panes": [
                                {
                                    "surfaces": [
                                        {
                                            "title": name,
                                            "type": "terminal",
                                            "ref": f"surface:{ref.split(':')[1]}-{index}",
                                        }
                                        for index, name in enumerate(names, start=1)
                                    ]
                                }
                            ],
                        }
                        for ref, title, names in specs
                    ]
                }
            ]
        }

    def _launch_inputs(self) -> dict:
        seal_root = self.base / "seal"
        fleet.sealed_results.initialize(seal_root, ("codex", "claude"))
        return {
            "repo": self.repo,
            "project": "barrier-test",
            "team_layouts": {
                "codex": self._launch_layout("lead", "worker"),
                "claude": self._launch_layout("lead", "worker"),
            },
            "orchestrator_layouts": {
                "codex": self._launch_layout("codex-orchestrator"),
                "claude": self._launch_layout("claude-orchestrator"),
            },
            "workspace_names": {
                "codex": "barrier-test-codex-team",
                "claude": "barrier-test-claude-team",
            },
            "gate_path": self.base / "gate" / "released.json",
            "readiness_root": self.base / "readiness",
            "seal_root": seal_root,
            "receipt_path": self.base / "launch-receipt.json",
            "run_id": "run-123",
            "snapshot_sha256": "b" * 64,
            "readiness_timeout_seconds": 1,
            "readiness_settle_seconds": 0.01,
            "receipt": {
                "project": "barrier-test",
                "sealed_results": {"contract_sha256": "a" * 64},
            },
        }

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
                self.assertEqual(plan["envelope"]["timeout_minutes"], 20)
                self.assertEqual(plan["child_readiness"]["timeout_seconds"], 30)
                self.assertEqual(
                    plan["envelope"]["deadline_utc"],
                    plan["envelope"]["sealed_results"]["deadline_utc"],
                )
                self.assertFalse(plan["provider_api_keys_injected"])
                self.assertEqual(
                    plan["provider_authentication"],
                    fleet.provider_authentication_receipt(),
                )
                self.assertEqual(plan["provider_authentication"]["mode"], "cli_subscription")
                self.assertFalse(
                    plan["provider_authentication"][
                        "direct_usage_billed_api_requests_by_launcher"
                    ]
                )

                codex_orchestrator = plan["orchestrator_layouts"]["codex"]["pane"]["surfaces"][0]["command"]
                claude_orchestrator = plan["orchestrator_layouts"]["claude"]["pane"]["surfaces"][0]["command"]
                self.assertEqual(self._command_topology(codex_orchestrator)[0], "codex")
                self.assertEqual(self._command_topology(claude_orchestrator)[0], "claude")
                self.assertIn(expected_task, codex_orchestrator)
                self.assertIn(expected_task, claude_orchestrator)
                self.assertIn("--token-file", codex_orchestrator)
                self.assertNotIn("SEALED_RESULTS_TOKEN", json.dumps(plan))
                self.assertNotIn("REDACTED", json.dumps(plan))
                self._assert_codex_repo_binding(codex_orchestrator)
                self._assert_claude_unchanged(claude_orchestrator)
                self._assert_surface_guarded(codex_orchestrator)
                self._assert_surface_guarded(claude_orchestrator)

                self.assertEqual(set(plan["commands"]["codex"]), set(plan["commands"]["claude"]))
                for role in plan["commands"]["codex"]:
                    self.assertEqual(
                        self._command_topology(plan["commands"]["codex"][role]),
                        self._command_topology(plan["commands"]["claude"][role]),
                    )
                for team_commands in plan["commands"].values():
                    for command_value in team_commands.values():
                        self._assert_surface_guarded(command_value)
                        if Path(self._provider_parts(command_value)[0]).name == "codex":
                            self._assert_codex_repo_binding(command_value)
                        else:
                            self._assert_claude_unchanged(command_value)

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

    def test_mirrored_timeout_is_positive_and_recorded_as_utc_deadline(self) -> None:
        plan = self._dry_run(
            "--task",
            "Bound this comparison.",
            "--timeout-minutes",
            "7",
        )
        envelope = plan["envelope"]
        created = datetime.fromisoformat(envelope["created_at_utc"].replace("Z", "+00:00"))
        deadline = datetime.fromisoformat(envelope["deadline_utc"].replace("Z", "+00:00"))
        self.assertEqual((deadline - created).total_seconds(), 7 * 60)
        self.assertEqual(envelope["timeout_minutes"], 7)
        self.assertEqual(envelope["sealed_results"]["deadline_utc"], envelope["deadline_utc"])

        for invalid in ("0", "-1", "not-a-number"):
            with self.subTest(invalid=invalid):
                result = self._run(
                    "--repo",
                    str(self.repo),
                    "--project",
                    self.project,
                    "--mirrored",
                    "--task",
                    "Bound this comparison.",
                    "--timeout-minutes",
                    invalid,
                    "--dry-run",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("timeout minutes", result.stderr)

    def test_dry_run_exposes_deterministic_deduped_provider_routes(self) -> None:
        plan = self._dry_run("--task", "Probe every route before launch.")
        routes = plan["provider_route_readiness"]["planned_routes"]
        self.assertEqual(routes, fleet.provider_routes(fleet.parser().parse_args([])))
        self.assertEqual(len(routes), len({tuple(route.values()) for route in routes}))
        self.assertEqual(
            routes,
            [
                {"provider": "codex", "model": "gpt-5.6-terra", "effort": "medium"},
                {"provider": "codex", "model": "gpt-5.6-luna", "effort": "medium"},
                {"provider": "codex", "model": "gpt-5.3-codex-spark", "effort": "low"},
                {"provider": "codex", "model": "gpt-5.6-sol", "effort": "high"},
                {"provider": "claude", "model": "claude-fable-5", "effort": "high"},
            ],
        )
        self.assertEqual(plan["provider_route_readiness"]["timeout_seconds"], 60)

    def test_default_role_routing_is_subscription_first_and_tiered(self) -> None:
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
        expected = {
            "__LEAD_COMMAND__": (
                "codex",
                "gpt-5.6-terra",
                'model_reasoning_effort="medium"',
                "danger-full-access",
            ),
            "__EXPLORER_COMMAND__": (
                "codex",
                "gpt-5.6-luna",
                'model_reasoning_effort="medium"',
                "read-only",
            ),
            "__REVIEWER_COMMAND__": (
                "codex",
                "gpt-5.6-luna",
                'model_reasoning_effort="medium"',
                "read-only",
            ),
            "__TESTER_COMMAND__": (
                "codex",
                "gpt-5.3-codex-spark",
                'model_reasoning_effort="low"',
                "read-only",
            ),
            "__COMPARATOR_COMMAND__": (
                "codex",
                "gpt-5.6-luna",
                'model_reasoning_effort="medium"',
                "read-only",
            ),
        }
        self.assertEqual(
            {role: self._command_topology(value) for role, value in plan["commands"].items()},
            expected,
        )

        args = fleet.parser().parse_args([])
        codex_director = fleet.orchestrator_layout(
            args, self.repo, self.project, "codex"
        )["pane"]["surfaces"][0]["command"]
        claude_director = fleet.orchestrator_layout(
            args, self.repo, self.project, "claude"
        )["pane"]["surfaces"][0]["command"]
        self.assertEqual(
            self._command_topology(codex_director),
            (
                "codex",
                "gpt-5.6-sol",
                'model_reasoning_effort="high"',
                "danger-full-access",
            ),
        )
        self.assertEqual(
            self._command_topology(claude_director),
            ("claude", "claude-fable-5", "high", "auto"),
        )
        for value in plan["commands"].values():
            self._assert_api_credentials_scrubbed(value)

    def test_provider_probe_commands_are_isolated_and_disable_api_key_env(self) -> None:
        cwd = self.base / "empty"
        cwd.mkdir()
        output = self.base / "last-message"
        codex_route = {"provider": "codex", "model": "codex-model", "effort": "low"}
        claude_route = {"provider": "claude", "model": "claude-model", "effort": "medium"}
        sentinel = "CMUX-ROUTE-READY:test"
        observed_envs: list[dict[str, str]] = []

        def fake_run(argv, **kwargs):
            observed_envs.append(kwargs["env"])
            self.assertEqual(kwargs["cwd"], cwd)
            if len(argv) > 1 and argv[1] == "exec":
                output.write_text(sentinel + "\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "ignored stdout", "")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"is_error": False, "result": sentinel}), ""
            )

        forbidden_environment = {name: "forbidden" for name in fleet.PROBE_ENV_DENYLIST}
        forbidden_environment["CLAUDE_CODE_OAUTH_TOKEN"] = "retained-subscription-oauth"
        with mock.patch.dict(os.environ, forbidden_environment), mock.patch.object(
            fleet.subprocess, "run", side_effect=fake_run
        ):
            codex_argv, codex_result, codex_executable = fleet.execute_route_probe(
                codex_route,
                cwd=cwd,
                output_path=output,
                sentinel=sentinel,
                timeout_seconds=3,
            )
            claude_argv, claude_result, claude_executable = fleet.execute_route_probe(
                claude_route,
                cwd=cwd,
                output_path=output,
                sentinel=sentinel,
                timeout_seconds=3,
            )

        self.assertEqual(codex_result.strip(), sentinel)
        self.assertEqual(claude_result, sentinel)
        for forbidden in fleet.PROBE_ENV_DENYLIST:
            self.assertTrue(all(forbidden not in env for env in observed_envs))
        self.assertTrue(all(env["CLAUDE_CODE_OAUTH_TOKEN"] == "retained-subscription-oauth" for env in observed_envs))
        self.assertTrue(Path(codex_argv[0]).is_absolute())
        self.assertEqual(codex_argv[1], "exec")
        self.assertEqual(codex_executable, fleet.sha256(Path(codex_argv[0]).read_bytes()))
        self.assertEqual(claude_executable, fleet.sha256(Path(claude_argv[0]).read_bytes()))
        for flag in (
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "--output-last-message",
        ):
            self.assertIn(flag, codex_argv)
        self.assertEqual(codex_argv[codex_argv.index("--sandbox") + 1], "read-only")
        for flag in (
            "-p",
            "--output-format",
            "--tools",
            "--permission-mode",
            "--no-session-persistence",
            "--safe-mode",
        ):
            self.assertIn(flag, claude_argv)
        self.assertEqual(claude_argv[claude_argv.index("--tools") + 1], "")
        self.assertEqual(claude_argv[claude_argv.index("--permission-mode") + 1], "plan")

    def test_route_probe_receipts_are_hash_only_immutable_and_deduped(self) -> None:
        route = {"provider": "codex", "model": "secret-model-name", "effort": "high"}
        root = self.base / "route-probes"
        calls: list[str] = []

        def fake_execute(_route, **kwargs):
            calls.append(kwargs["sentinel"])
            return ["/absolute/codex", "exec", "redacted-prompt"], kwargs["sentinel"], "1" * 64

        with mock.patch.object(fleet, "execute_route_probe", side_effect=fake_execute):
            receipts = fleet.run_route_probes(
                [route, dict(route)],
                root=root,
                run_id="run-secret",
                snapshot_sha256="e" * 64,
                timeout_seconds=5,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        serialized = json.dumps(receipt)
        for raw in ("codex", "secret-model-name", "high", "run-secret", calls[0], "redacted-prompt"):
            self.assertNotIn(raw, serialized)
        path = fleet.route_probe_receipt_path(root, route)
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        with self.assertRaises(fleet.MirroredLaunchError):
            fleet.atomic_exclusive_json(path, receipt)

        tampered = dict(receipt, model_sha256="0" * 64)
        with self.assertRaisesRegex(fleet.MirroredLaunchError, "mismatched model_sha256"):
            fleet.validate_route_probe_receipt(
                tampered,
                route=route,
                run_id="run-secret",
                snapshot_sha256="e" * 64,
                command_sha256=receipt["command_sha256"],
                executable_sha256=receipt["executable_sha256"],
                sentinel_sha256=receipt["sentinel_sha256"],
            )
        malformed = dict(receipt, extra=True)
        with self.assertRaisesRegex(fleet.MirroredLaunchError, "unexpected schema"):
            fleet.validate_route_probe_receipt(
                malformed,
                route=route,
                run_id="run-secret",
                snapshot_sha256="e" * 64,
                command_sha256=receipt["command_sha256"],
                executable_sha256=receipt["executable_sha256"],
                sentinel_sha256=receipt["sentinel_sha256"],
            )

    def test_route_probe_failure_classes_are_typed_and_never_retain_output(self) -> None:
        route = {"provider": "claude", "model": "model", "effort": "medium"}
        cwd = self.base / "probe-cwd"
        cwd.mkdir()
        output = self.base / "output"
        cases = (
            (subprocess.TimeoutExpired(["claude"], 1, output="sensitive"), "timeout"),
            (OSError("sensitive spawn failure"), "spawn_failed"),
            (subprocess.CompletedProcess(["claude"], 7, "sensitive", "sensitive"), "nonzero_exit"),
            (
                subprocess.CompletedProcess(
                    ["claude"], 0, json.dumps({"is_error": True, "result": "sensitive"}), ""
                ),
                "provider_error",
            ),
            (subprocess.CompletedProcess(["claude"], 0, "not-json-sensitive", ""), "provider_output_invalid"),
        )
        for result, code in cases:
            with self.subTest(code=code), mock.patch.object(
                fleet.subprocess, "run", side_effect=result if isinstance(result, Exception) else None,
                return_value=None if isinstance(result, Exception) else result,
            ):
                with self.assertRaises(fleet.RouteProbeError) as raised:
                    fleet.execute_route_probe(
                        route,
                        cwd=cwd,
                        output_path=output,
                        sentinel="READY",
                        timeout_seconds=1,
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn("sensitive", str(raised.exception))

        with mock.patch.object(
            fleet,
            "execute_route_probe",
            return_value=(["/absolute/claude", "-p", "prompt"], "WRONG", "2" * 64),
        ):
            with self.assertRaises(fleet.RouteProbeError) as raised:
                fleet.run_route_probes(
                    [route],
                    root=self.base / "mismatch",
                    run_id="run",
                    snapshot_sha256="f" * 64,
                    timeout_seconds=1,
                )
        self.assertEqual(raised.exception.code, "sentinel_mismatch")

        transforms = (
            lambda sentinel: " " + sentinel,
            lambda sentinel: sentinel + " ",
            lambda sentinel: sentinel + " extra",
            lambda sentinel: sentinel + "\n\n",
            lambda sentinel: sentinel + "\r",
            lambda sentinel: sentinel + "\r\n\n",
            lambda sentinel: sentinel + "\n\r\n",
        )
        for index, transform in enumerate(transforms):
            with self.subTest(index=index), mock.patch.object(
                fleet,
                "execute_route_probe",
                side_effect=lambda _route, **kwargs: (
                    ["/absolute/claude", "-p", "prompt"],
                    transform(kwargs["sentinel"]),
                    "2" * 64,
                ),
            ):
                with self.assertRaises(fleet.RouteProbeError) as raised:
                    fleet.run_route_probes(
                        [route],
                        root=self.base / f"whitespace-{index}",
                        run_id="run",
                        snapshot_sha256="f" * 64,
                        timeout_seconds=1,
                    )
            self.assertEqual(raised.exception.code, "sentinel_mismatch")

        accepted_endings = ("", "\n", "\r\n")
        for index, ending in enumerate(accepted_endings):
            root = self.base / f"accepted-ending-{index}"
            with self.subTest(ending=repr(ending)), mock.patch.object(
                fleet,
                "execute_route_probe",
                side_effect=lambda _route, **kwargs: (
                    ["/absolute/claude", "-p", "prompt"],
                    kwargs["sentinel"] + ending,
                    "2" * 64,
                ),
            ):
                receipts = fleet.run_route_probes(
                    [route],
                    root=root,
                    run_id="run",
                    snapshot_sha256="f" * 64,
                    timeout_seconds=1,
                )
            self.assertEqual(len(receipts), 1)

    def test_route_probe_timeout_and_nonzero_remove_codex_output_residue(self) -> None:
        route = {"provider": "codex", "model": "model", "effort": "low"}

        def result_with_residue(kind):
            def side_effect(argv, **_kwargs):
                output = Path(argv[argv.index("--output-last-message") + 1])
                output.write_text("sensitive partial provider output", encoding="utf-8")
                if kind == "timeout":
                    raise subprocess.TimeoutExpired(argv, 1, output="sensitive")
                return subprocess.CompletedProcess(argv, 9, "sensitive", "sensitive")

            return side_effect

        for kind, code in (("timeout", "timeout"), ("nonzero", "nonzero_exit")):
            root = self.base / f"cleanup-{kind}"
            output = root / "transient" / fleet.route_probe_identity(route)
            with self.subTest(kind=kind), mock.patch.object(
                fleet.subprocess, "run", side_effect=result_with_residue(kind)
            ):
                with self.assertRaises(fleet.RouteProbeError) as raised:
                    fleet.run_route_probes(
                        [route],
                        root=root,
                        run_id="run",
                        snapshot_sha256="f" * 64,
                        timeout_seconds=1,
                    )
            self.assertEqual(raised.exception.code, code)
            self.assertFalse(output.exists())

    def test_route_probe_fails_when_provider_binary_cannot_be_resolved(self) -> None:
        route = {"provider": "codex", "model": "model", "effort": "low"}
        with mock.patch.object(fleet.shutil, "which", return_value=None):
            with self.assertRaises(fleet.RouteProbeError) as raised:
                fleet.route_probe_command(
                    route,
                    cwd=self.base,
                    codex_output=self.base / "output",
                    sentinel="READY",
                )
        self.assertEqual(raised.exception.code, "provider_binary_missing")

    def test_route_probe_detects_provider_binary_change_during_execution(self) -> None:
        route = {"provider": "codex", "model": "model", "effort": "low"}
        executable = self.base / "fake-codex"
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)

        def mutate_binary(argv, **_kwargs):
            executable.write_bytes(b"#!/bin/sh\nexit 1\n")
            executable.chmod(0o755)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with (
            mock.patch.object(fleet, "provider_binary_path", return_value=executable),
            mock.patch.object(fleet.subprocess, "run", side_effect=mutate_binary),
        ):
            with self.assertRaises(fleet.RouteProbeError) as raised:
                fleet.execute_route_probe(
                    route,
                    cwd=self.base,
                    output_path=self.base / "output",
                    sentinel="READY",
                    timeout_seconds=1,
                )
        self.assertEqual(raised.exception.code, "provider_binary_changed")

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
        self.assertEqual(plan["provider_authentication"], fleet.provider_authentication_receipt())
        for value in plan["commands"].values():
            self._assert_surface_guarded(value)
            if Path(self._provider_parts(value)[0]).name == "codex":
                self._assert_codex_repo_binding(value)
            else:
                self._assert_claude_unchanged(value)

        args = fleet.parser().parse_args([])
        codex_orchestrator = fleet.orchestrator_layout(
            args, self.repo, self.project, "codex"
        )["pane"]["surfaces"][0]["command"]
        claude_orchestrator = fleet.orchestrator_layout(
            args, self.repo, self.project, "claude"
        )["pane"]["surfaces"][0]["command"]
        self._assert_codex_repo_binding(codex_orchestrator)
        self._assert_claude_unchanged(claude_orchestrator)
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

    def test_controller_seatbelt_denies_target_writes_and_allows_external_control_state(self) -> None:
        target_team = self.repo / ".team"
        target_team.mkdir()
        blocked = target_team / "blocked.txt"
        blocked_command = fleet.surface_guard(f"touch {shlex.quote(str(blocked))}", self.repo)
        result = subprocess.run(shlex.split(blocked_command), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(blocked.exists())

        control = ROOT / ".team" / f"seatbelt-test-{uuid.uuid4().hex}"
        allowed_command = fleet.surface_guard(f"touch {shlex.quote(str(control))}", self.repo)
        try:
            result = subprocess.run(shlex.split(allowed_command), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(control.exists())
        finally:
            control.unlink(missing_ok=True)

    def test_child_readiness_receipt_is_exactly_bound_and_immutable(self) -> None:
        expected = {
            "run_id": "run-ready",
            "team": "codex",
            "role": "lead",
            "workspace_role": "team",
            "workspace_ref": "workspace:7",
            "surface_name": "lead",
            "surface_ref": "surface:9",
            "target_snapshot_sha256": "c" * 64,
        }
        gate = self.base / "gate-ready.json"
        gate.write_bytes(
            fleet.sealed_results.canonical_json(
                {"run_id": "run-ready", "expected_children": [expected]}
            )
        )
        readiness_root = self.base / "ready"
        with mock.patch.object(
            fleet,
            "cmux_caller_identity",
            return_value={"workspace_ref": "workspace:7", "surface_ref": "surface:9"},
        ):
            receipt = fleet.publish_child_readiness(
                gate,
                readiness_root,
                run_id="run-ready",
                team="codex",
                role="lead",
                workspace_role="team",
                surface_name="lead",
                snapshot_sha256="c" * 64,
            )
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.publish_child_readiness(
                    gate,
                    readiness_root,
                    run_id="run-ready",
                    team="codex",
                    role="lead",
                    workspace_role="team",
                    surface_name="lead",
                    snapshot_sha256="c" * 64,
                )
        self.assertEqual(set(receipt), fleet.READINESS_KEYS)
        self.assertNotIn("token", json.dumps(receipt).lower())
        fleet.validate_readiness_receipt(receipt, expected)
        with self.assertRaises(fleet.MirroredLaunchError):
            fleet.wait_for_child_readiness([expected], self.base / "missing-ready", 0)

        fleet.publish_child_exit(
            gate,
            readiness_root,
            run_id="run-ready",
            team="codex",
            role="lead",
            workspace_role="team",
            surface_name="lead",
            snapshot_sha256="c" * 64,
            phase="after_readiness",
            returncode=1,
        )
        with self.assertRaisesRegex(fleet.MirroredLaunchError, "readiness settle"):
            fleet.settle_child_readiness([expected], readiness_root, 0.01)

    def test_gate_publication_is_atomic_no_replace(self) -> None:
        gate = self.base / "gate" / "released.json"
        gate.parent.mkdir()
        gate.write_text('{"original":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(fleet.MirroredLaunchError, "refusing to replace"):
            fleet.release_barrier(gate, {"replacement": True})
        self.assertEqual(json.loads(gate.read_text(encoding="utf-8")), {"original": True})

    def test_supervisor_authenticates_then_terminates_on_seal_invalidation(self) -> None:
        events: list[object] = []
        invalidations = iter((None, {"result_type": "run_invalidation"}))

        class FakeChild:
            pid = 43210

            def poll(self):
                events.append("poll")
                return None

            def wait(self, timeout=None):
                events.append("wait")
                return 0

            def terminate(self):
                events.append("terminate")

            def kill(self):
                events.append("kill")

        args = mock.Mock(
            provider="codex",
            agent_command="codex test",
            liveness_seconds=0.1,
            gate=self.base / "gate",
            readiness_root=self.base / "ready",
            seal_root=self.base / "seal",
            run_id="run",
            team="codex",
            role="lead",
            workspace_role="team",
            surface_name="lead",
            snapshot_sha256="d" * 64,
        )
        with (
            mock.patch.object(fleet, "provider_auth_preflight", side_effect=lambda _: events.append("auth")),
            mock.patch.object(
                fleet.subprocess,
                "Popen",
                side_effect=lambda *_a, **kwargs: (
                    self.assertTrue(kwargs["start_new_session"]),
                    events.append("launch"),
                    FakeChild(),
                )[2],
            ),
            mock.patch.object(fleet.time, "sleep", side_effect=lambda _: events.append("liveness")),
            mock.patch.object(fleet, "publish_child_readiness", side_effect=lambda *_a, **_k: events.append("receipt")),
            mock.patch.object(
                fleet.sealed_results,
                "invalidation_status",
                side_effect=lambda _root: (events.append("invalidation"), next(invalidations))[1],
            ),
            mock.patch.object(fleet.os, "getpgrp", return_value=999),
            mock.patch.object(
                fleet.os,
                "killpg",
                side_effect=lambda group, sent_signal: events.append(
                    ("killpg", group, sent_signal)
                ),
            ),
        ):
            self.assertEqual(fleet.supervise_child(args), 75)
        self.assertEqual(
            events[:6],
            ["auth", "launch", "liveness", "poll", "invalidation", "receipt"],
        )
        self.assertIn("invalidation", events)
        self.assertIn(("killpg", 43210, fleet.signal.SIGTERM), events)
        self.assertIn(("killpg", 43210, fleet.signal.SIGKILL), events)
        self.assertNotIn("terminate", events)

    def test_create_failure_keeps_agents_behind_barrier_and_rolls_back_owned_ref(self) -> None:
        inputs = self._launch_inputs()
        events: list[str] = []
        real_invalidate = fleet.sealed_results.invalidate
        responses = (
            {"workspace_ref": "workspace:101", "echoed_layout": "SEALED_RESULTS_TOKEN=secret"},
            SystemExit("create failed SEALED_RESULTS_TOKEN=secret"),
        )

        with (
            mock.patch.object(fleet, "create_workspace", side_effect=responses) as create,
            mock.patch.object(
                fleet,
                "close_workspace",
                side_effect=lambda _ref: events.append("close"),
            ) as close,
            mock.patch.object(fleet, "read_cmux_topology") as topology,
            mock.patch.object(
                fleet.sealed_results,
                "invalidate",
                side_effect=lambda *args: (
                    events.append("invalidate"),
                    real_invalidate(*args),
                )[1],
            ),
        ):
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.launch_mirrored_workspaces(**inputs)

        self.assertEqual(create.call_count, 2)
        for call in create.call_args_list:
            self._assert_barriered(call.args[2], inputs["gate_path"])
        topology.assert_not_called()
        close.assert_called_once_with("workspace:101")
        self.assertEqual(events, ["invalidate", "close"])
        self.assertFalse(inputs["gate_path"].exists())

        failure = json.loads(inputs["receipt_path"].read_text(encoding="utf-8"))
        self.assertFalse(failure["valid"])
        self.assertEqual(failure["status"], "launch_failed")
        self.assertFalse(failure["launch_barrier"]["released"])
        self.assertEqual(failure["rollback"]["attempted"], ["workspace:101"])
        self.assertEqual(failure["rollback"]["closed"], ["workspace:101"])
        self.assertTrue(failure["sealed_results_invalidation"]["completed"])
        self.assertNotIn("TOKEN", json.dumps(failure))

    def test_route_probe_failure_invalidates_pre_release_and_creates_zero_workspaces(self) -> None:
        inputs = self._launch_inputs()
        route = {"provider": "codex", "model": "model", "effort": "high"}
        inputs.update(
            route_probes=[route],
            route_probe_root=self.base / "probe-root",
            route_probe_timeout_seconds=2,
        )
        route_sha256 = fleet.route_probe_identity(route)
        with (
            mock.patch.object(
                fleet,
                "run_route_probes",
                side_effect=fleet.RouteProbeError("sentinel_mismatch", route_sha256),
            ) as probes,
            mock.patch.object(fleet, "create_workspace") as create,
        ):
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.launch_mirrored_workspaces(**inputs)

        probes.assert_called_once()
        create.assert_not_called()
        self.assertFalse(inputs["gate_path"].exists())
        failure = json.loads(inputs["receipt_path"].read_text(encoding="utf-8"))
        self.assertFalse(failure["valid"])
        self.assertEqual(failure["status"], "launch_failed")
        self.assertEqual(failure["created_workspaces"], [])
        self.assertEqual(
            failure["failure"],
            {
                "phase": "provider_route_readiness",
                "code": "sentinel_mismatch",
                "route_sha256": route_sha256,
            },
        )
        self.assertTrue(failure["sealed_results_invalidation"]["completed"])
        self.assertEqual(
            fleet.sealed_results.invalidation_status(inputs["seal_root"])["reason_code"],
            "pre_release_failure",
        )

    def test_successful_route_probes_finish_before_first_workspace_creation(self) -> None:
        inputs = self._launch_inputs()
        route = {"provider": "codex", "model": "model", "effort": "high"}
        inputs.update(
            route_probes=[route],
            route_probe_root=self.base / "probe-root",
            route_probe_timeout_seconds=2,
        )
        events: list[str] = []
        refs = iter(f"workspace:{number}" for number in range(101, 105))
        probe_receipt = {"route_sha256": fleet.route_probe_identity(route)}

        with (
            mock.patch.object(
                fleet,
                "run_route_probes",
                side_effect=lambda *_args, **_kwargs: (events.append("probe"), [probe_receipt])[1],
            ),
            mock.patch.object(
                fleet,
                "create_workspace",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("create"),
                    {"workspace_ref": next(refs)},
                )[1],
            ),
            mock.patch.object(fleet, "read_cmux_topology", return_value=self._mock_topology()),
            mock.patch.object(fleet, "wait_for_child_readiness", return_value=[]),
        ):
            receipt = fleet.launch_mirrored_workspaces(**inputs)

        self.assertEqual(events[0], "probe")
        self.assertEqual(events.count("create"), 4)
        self.assertTrue(receipt["provider_route_readiness"]["verified"])
        self.assertEqual(receipt["provider_route_readiness"]["expected"], 1)
        self.assertEqual(receipt["provider_route_readiness"]["receipts"], [probe_receipt])

    def test_topology_failure_rolls_back_only_four_invocation_owned_refs(self) -> None:
        inputs = self._launch_inputs()
        refs = [f"workspace:{number}" for number in range(101, 105)]

        def create_side_effect(_name, _repo, layout):
            self.assertFalse(inputs["gate_path"].exists())
            self._assert_barriered(layout, inputs["gate_path"])
            return {"workspace_ref": refs.pop(0)}

        with (
            mock.patch.object(fleet, "create_workspace", side_effect=create_side_effect) as create,
            mock.patch.object(
                fleet, "read_cmux_topology", return_value=self._mock_topology(False)
            ),
            mock.patch.object(fleet, "close_workspace") as close,
        ):
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.launch_mirrored_workspaces(**inputs)

        self.assertEqual(create.call_count, 4)
        self.assertEqual(
            [call.args[0] for call in close.call_args_list],
            ["workspace:104", "workspace:103", "workspace:102", "workspace:101"],
        )
        self.assertFalse(inputs["gate_path"].exists())
        failure = json.loads(inputs["receipt_path"].read_text(encoding="utf-8"))
        self.assertFalse(failure["valid"])
        self.assertIn("missing workspace:104", failure["error"])
        self.assertEqual(len(failure["created_workspaces"]), 4)
        self.assertNotIn("TOKEN", json.dumps(failure))

    def test_barrier_releases_only_after_complete_topology_is_observed(self) -> None:
        inputs = self._launch_inputs()
        refs = iter(f"workspace:{number}" for number in range(101, 105))

        def create_side_effect(_name, _repo, layout):
            self.assertFalse(inputs["gate_path"].exists())
            self._assert_barriered(layout, inputs["gate_path"])
            return {
                "workspace_ref": next(refs),
                "echoed_layout": "SEALED_RESULTS_TOKEN=secret",
            }

        def topology_side_effect():
            self.assertFalse(inputs["gate_path"].exists())
            return self._mock_topology()

        with (
            mock.patch.object(fleet, "create_workspace", side_effect=create_side_effect) as create,
            mock.patch.object(fleet, "read_cmux_topology", side_effect=topology_side_effect),
            mock.patch.object(fleet, "close_workspace") as close,
            mock.patch.object(
                fleet, "wait_for_child_readiness", return_value=[]
            ) as readiness,
        ):
            receipt = fleet.launch_mirrored_workspaces(**inputs)

        self.assertEqual(create.call_count, 4)
        close.assert_not_called()
        self.assertTrue(inputs["gate_path"].is_file())
        self.assertTrue(receipt["valid"])
        self.assertTrue(receipt["launch_barrier"]["released"])
        self.assertTrue(receipt["topology"]["verified"])
        self.assertTrue(receipt["child_readiness"]["verified"])
        self.assertEqual(readiness.call_args.args[2], 1)
        self.assertTrue(inputs["gate_path"].is_file())
        self.assertNotIn("TOKEN", json.dumps(receipt))

    def test_post_release_readiness_failure_rolls_back_and_never_claims_ready(self) -> None:
        inputs = self._launch_inputs()
        refs = iter(f"workspace:{number}" for number in range(101, 105))
        with (
            mock.patch.object(
                fleet,
                "create_workspace",
                side_effect=lambda _name, _repo, _layout: {"workspace_ref": next(refs)},
            ),
            mock.patch.object(fleet, "read_cmux_topology", return_value=self._mock_topology()),
            mock.patch.object(
                fleet,
                "wait_for_child_readiness",
                side_effect=fleet.MirroredLaunchError("readiness timeout"),
            ),
            mock.patch.object(fleet, "close_workspace") as close,
        ):
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.launch_mirrored_workspaces(**inputs)

        self.assertEqual(close.call_count, 4)
        failure = json.loads(inputs["receipt_path"].read_text(encoding="utf-8"))
        self.assertFalse(failure["valid"])
        self.assertEqual(failure["status"], "readiness_failed")
        self.assertTrue(failure["launch_barrier"]["released"])
        self.assertFalse(failure["child_readiness"]["verified"])
        self.assertTrue(failure["sealed_results_invalidation"]["completed"])
        self.assertIsNotNone(fleet.sealed_results.invalidation_status(inputs["seal_root"]))

    def test_failed_close_is_preserved_in_failure_receipt_after_invalidation(self) -> None:
        inputs = self._launch_inputs()
        with (
            mock.patch.object(
                fleet,
                "create_workspace",
                side_effect=(
                    {"workspace_ref": "workspace:101"},
                    SystemExit("create failed"),
                ),
            ),
            mock.patch.object(
                fleet,
                "close_workspace",
                side_effect=fleet.MirroredLaunchError("close denied"),
            ),
        ):
            with self.assertRaises(fleet.MirroredLaunchError):
                fleet.launch_mirrored_workspaces(**inputs)

        failure = json.loads(inputs["receipt_path"].read_text(encoding="utf-8"))
        self.assertTrue(failure["sealed_results_invalidation"]["completed"])
        self.assertEqual(failure["rollback"]["attempted"], ["workspace:101"])
        self.assertEqual(failure["rollback"]["closed"], [])
        self.assertIn("close denied", failure["rollback"]["errors"]["workspace:101"])


if __name__ == "__main__":
    unittest.main()
