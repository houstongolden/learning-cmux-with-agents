#!/usr/bin/env python3
"""Boot a subscription-authenticated Codex/Claude fleet in cmux.

The launcher intentionally uses declarative cmux layouts. It never reads an
.env file or injects provider API keys. Mutation mode is reserved until You.md
ships an atomic work-claim lease; the current agent bus is not a lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shlex
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import sealed_results
except ModuleNotFoundError:  # Direct execution adds scripts/, not the repository root.
    import sealed_results


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "houston" / "cmux" / "subscription-fleet.layout.json"
TOKENS = {
    "__LEAD_COMMAND__",
    "__EXPLORER_COMMAND__",
    "__REVIEWER_COMMAND__",
    "__TESTER_COMMAND__",
    "__COMPARATOR_COMMAND__",
}
SURFACE_READ_ONLY_BOUNDARY = (
    "macOS Seatbelt denies target-repository writes for each run-owned surface "
    "process tree; only this launcher's ROOT/.team control state is exempt when the "
    "lab itself is the target. This is a process boundary, not hostile isolation: "
    "same-user processes and independently launched raw-CMUX surfaces remain outside it."
)
READINESS_BOUNDARY = (
    "A readiness receipt proves provider-auth preflight plus short process liveness "
    "for one CMUX surface; it does not prove a completed model turn. It is not protection "
    "against a malicious same-user process."
)
READINESS_SCHEMA_VERSION = 1
READINESS_KEYS = {
    "schema_version",
    "ready",
    "run_id",
    "team",
    "role",
    "workspace_role",
    "workspace_ref",
    "surface_name",
    "surface_ref",
    "target_snapshot_sha256",
    "ready_at_utc",
    "security_boundary",
}
EXIT_MARKER_KEYS = {
    "schema_version",
    "run_id",
    "team",
    "role",
    "workspace_role",
    "workspace_ref",
    "surface_name",
    "surface_ref",
    "target_snapshot_sha256",
    "phase",
    "returncode",
    "exited_at_utc",
}


def die(message: str) -> None:
    raise SystemExit(f"error: {message}")


def run(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not normalized:
        die("project must contain a letter or number")
    return normalized


def command(parts: list[str]) -> str:
    return shlex.join(parts)


def sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def positive_minutes(value: str) -> int:
    try:
        minutes = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout minutes must be an integer") from exc
    if minutes <= 0:
        raise argparse.ArgumentTypeError("timeout minutes must be positive")
    return minutes


def positive_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("readiness timeout seconds must be an integer") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("readiness timeout seconds must be positive")
    return seconds


def positive_duration(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("readiness settle seconds must be numeric") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("readiness settle seconds must be positive")
    return seconds


def sandbox_profile(repo: Path) -> str:
    """Block target writes, except launcher state when target is this lab."""

    repo = repo.resolve()
    rules = [
        "(version 1)",
        "(allow default)",
        f"(deny file-write* (subpath {json.dumps(str(repo))}))",
    ]
    if repo == ROOT.resolve():
        rules.append(f"(allow file-write* (subpath {json.dumps(str(ROOT / '.team'))}))")
    return "\n".join(rules)


def surface_guard(value: str, repo: Path) -> str:
    """Apply an OS-enforced read-only boundary to one run-owned process tree."""

    return command(["sandbox-exec", "-p", sandbox_profile(repo), "/bin/sh", "-c", value])


def codex_cmd(
    model: str,
    effort: str,
    prompt: str,
    *,
    controller: bool,
    repo: Path,
) -> str:
    repo = repo.resolve()
    trust_override = f'projects.{json.dumps(str(repo))}.trust_level="trusted"'
    launched = command(
        [
            "codex",
            "-C",
            str(repo),
            "-c",
            trust_override,
            "--model",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--sandbox",
            "danger-full-access" if controller else "read-only",
            "--ask-for-approval",
            "never",
            prompt,
        ]
    )
    return surface_guard(launched, repo)


def claude_cmd(
    model: str,
    effort: str,
    prompt: str,
    *,
    controller: bool,
    repo: Path,
) -> str:
    launched = command(
        [
            "claude",
            "--model",
            model,
            "--effort",
            effort,
            "--permission-mode",
            "auto" if controller else "plan",
            prompt,
        ]
    )
    return surface_guard(launched, repo)


def validate_tools() -> dict[str, str]:
    versions: dict[str, str] = {}
    for binary in ("cmux", "codex", "claude", "git", "sandbox-exec"):
        path = shutil.which(binary)
        if not path:
            die(f"required binary not found: {binary}")
        versions[binary] = path

    data = json.loads(TEMPLATE.read_text())
    encoded = json.dumps(data)
    missing = sorted(token for token in TOKENS if token not in encoded)
    if missing:
        die(f"layout is missing tokens: {', '.join(missing)}")
    versions["layout"] = str(TEMPLATE)
    return versions


def ensure_cmux() -> None:
    try:
        healthy = run("cmux", "identify", "--json", timeout=5)
    except subprocess.TimeoutExpired:
        healthy = None
    if healthy and healthy.returncode == 0:
        return
    subprocess.run(["open", "-a", "cmux"], check=False)
    for _ in range(20):
        time.sleep(0.5)
        try:
            healthy = run("cmux", "identify", "--json", timeout=3)
        except subprocess.TimeoutExpired:
            continue
        if healthy.returncode == 0:
            return
    die("cmux is running but its socket is not responsive; restart cmux and check Automation settings")


def render_team(
    args: argparse.Namespace,
    repo: Path,
    project: str,
    *,
    envelope_path: Path | None = None,
    envelope_sha256: str | None = None,
) -> tuple[dict, dict[str, str]]:
    common = (
        f"Team {project}; repository {repo}. This is a read-only comparison run. "
        "Do not edit files, create commits, or change branches. Preserve evidence and report a concise sentinel."
    )
    if envelope_path and envelope_sha256:
        common += (
            f" Your assignment is bound to immutable task envelope {envelope_path} with SHA-256 "
            f"{envelope_sha256}; the primary orchestrator supplies the task, so you do not need to read that file."
        )
    prompts = {
        "lead": (
            f"You are the LEAD for {common} Find your four named sibling surfaces with cmux. "
            "Wait for the primary orchestrator, then decompose work, dispatch to explorer/reviewer/tester/comparator, "
            "compare evidence, retain disagreements, and report only to the orchestrator."
        ),
        "explorer": f"You are the EXPLORER for {common} Wait for the lead. End assignments with FLEET-DONE: explorer | <summary>.",
        "reviewer": f"You are the REVIEWER for {common} Wait for the lead. End assignments with FLEET-DONE: reviewer | <summary>.",
        "tester": f"You are the TESTER for {common} Wait for the lead. End assignments with FLEET-DONE: tester | <summary>.",
        "comparator": f"You are the COMPARATOR for {common} Wait for the lead. End assignments with FLEET-DONE: comparator | <summary>.",
    }
    commands = {
        "__LEAD_COMMAND__": codex_cmd(
            args.codex_lead_model, "medium", prompts["lead"], controller=True, repo=repo
        ),
        "__EXPLORER_COMMAND__": codex_cmd(
            args.codex_worker_model, "low", prompts["explorer"], controller=False, repo=repo
        ),
        "__REVIEWER_COMMAND__": claude_cmd(
            args.claude_worker_model, "medium", prompts["reviewer"], controller=False, repo=repo
        ),
        "__TESTER_COMMAND__": codex_cmd(
            args.codex_worker_model, "low", prompts["tester"], controller=False, repo=repo
        ),
        "__COMPARATOR_COMMAND__": claude_cmd(
            args.claude_worker_model, "medium", prompts["comparator"], controller=False, repo=repo
        ),
    }
    layout = json.loads(TEMPLATE.read_text())

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return commands.get(value, value) if isinstance(value, str) else value

    layout = replace(layout)
    return layout, commands


def orchestrator_layout(
    args: argparse.Namespace,
    repo: Path,
    project: str,
    kind: str,
    *,
    team_workspace: str | None = None,
    task: str | None = None,
    envelope_path: Path | None = None,
    envelope_sha256: str | None = None,
    seal_root: Path | None = None,
    result_token_file: Path | None = None,
) -> dict:
    workspace = team_workspace or f"{project}-team"
    prompt = (
        f"You are the PRIMARY ORCHESTRATOR for team {project} in repository {repo}. "
        f"The lead is in cmux workspace {workspace}. Talk only to the lead using cmux from inside this terminal. "
        "Do not edit the target repository. Judge worker evidence objectively; speed is telemetry, not correctness. "
    )
    if task is None:
        prompt += "Wait for Houston's task."
    else:
        prompt += (
            f"The immutable task is already assigned below; begin it immediately.\n\nTASK:\n{task}\n\n"
            f"Task envelope: {envelope_path} (SHA-256 {envelope_sha256}). "
            "Do not inspect the sibling orchestrator or the sealed-results directory. Prepare a JSON payload with "
            "verdict, summary, findings, evidence, disagreements, and elapsed_seconds at "
            f"/tmp/{project}-result.json, then submit it exactly once with this command:\n"
            f"python3 {shlex.quote(str(ROOT / 'scripts' / 'sealed_results.py'))} submit "
            f"--root {shlex.quote(str(seal_root))} --team {shlex.quote(kind)} "
            f"--token-file {shlex.quote(str(result_token_file))} "
            f"--input {shlex.quote(f'/tmp/{project}-result.json')}\n"
            "The helper performs authenticated, atomic, one-way sealing. Do not write a result directly into its root."
        )
    if kind == "codex":
        launched = codex_cmd(
            args.codex_orchestrator_model, "high", prompt, controller=True, repo=repo
        )
    else:
        launched = claude_cmd(
            args.claude_orchestrator_model, "high", prompt, controller=True, repo=repo
        )
    return {"pane": {"surfaces": [{"type": "terminal", "name": f"{kind}-orchestrator", "command": launched}]}}


def create_workspace(name: str, repo: Path, layout: dict) -> dict:
    try:
        result = run(
            "cmux",
            "workspace",
            "create",
            "--name",
            name,
            "--cwd",
            str(repo),
            "--layout",
            json.dumps(layout, separators=(",", ":")),
            "--json",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        die("cmux workspace creation timed out; restart cmux before retrying")
    if result.returncode != 0:
        die((result.stderr or result.stdout).strip() or "cmux workspace creation failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        die(f"cmux returned non-JSON workspace output: {result.stdout.strip()}")


class MirroredLaunchError(RuntimeError):
    """A mirrored fleet failed topology or post-release readiness validation."""


def command_provider(value: str) -> str:
    """Return the provider CLI hidden under an optional controller guard."""

    parts = shlex.split(value)
    if Path(parts[0]).name == "sandbox-exec":
        parts = shlex.split(parts[-1])
    provider = Path(parts[0]).name
    if provider not in {"codex", "claude"}:
        raise MirroredLaunchError(f"unsupported child provider command: {provider}")
    return provider


def supervised_command(
    value: str,
    gate_path: Path,
    readiness_root: Path,
    seal_root: Path,
    repo: Path,
    *,
    run_id: str,
    team: str,
    role: str,
    workspace_role: str,
    surface_name: str,
    snapshot_sha256: str,
    liveness_seconds: float,
) -> str:
    """Wait for release, then auth-check and supervise one provider CLI."""

    supervisor = command(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "child-supervise",
            "--gate",
            str(gate_path),
            "--readiness-root",
            str(readiness_root),
            "--seal-root",
            str(seal_root),
            "--run-id",
            run_id,
            "--team",
            team,
            "--role",
            role,
            "--workspace-role",
            workspace_role,
            "--surface-name",
            surface_name,
            "--snapshot-sha256",
            snapshot_sha256,
            "--provider",
            command_provider(value),
            "--liveness-seconds",
            str(liveness_seconds),
            "--agent-command",
            value,
        ]
    )
    supervisor = surface_guard(supervisor, repo)
    return command(
        [
            "/bin/sh",
            "-c",
            'while [ ! -f "$1" ]; do sleep 0.1; done; exec /bin/sh -c "$2"',
            "cmux-launch-barrier",
            str(gate_path),
            supervisor,
        ]
    )


def supervised_layout(
    layout: dict,
    gate_path: Path,
    readiness_root: Path,
    seal_root: Path,
    repo: Path,
    *,
    run_id: str,
    team: str,
    workspace_role: str,
    snapshot_sha256: str,
    liveness_seconds: float,
) -> dict:
    """Return a copy whose children publish authenticated liveness receipts."""

    def wrap(value):
        if isinstance(value, dict):
            result = {key: wrap(child) for key, child in value.items()}
            if result.get("type") == "terminal" and isinstance(result.get("command"), str):
                surface_name = result.get("name")
                if not isinstance(surface_name, str) or not surface_name:
                    raise MirroredLaunchError("every terminal surface must have a readiness name")
                role = "orchestrator" if workspace_role == "orchestrator" else surface_name
                result["command"] = supervised_command(
                    result["command"],
                    gate_path,
                    readiness_root,
                    seal_root,
                    repo,
                    run_id=run_id,
                    team=team,
                    role=role,
                    workspace_role=workspace_role,
                    surface_name=surface_name,
                    snapshot_sha256=snapshot_sha256,
                    liveness_seconds=liveness_seconds,
                )
            return result
        if isinstance(value, list):
            return [wrap(child) for child in value]
        return value

    return wrap(layout)


def terminal_names(layout: dict) -> list[str]:
    names: list[str] = []

    def collect(value) -> None:
        if isinstance(value, dict):
            if value.get("type") == "terminal" and isinstance(value.get("name"), str):
                names.append(value["name"])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(layout)
    return sorted(names)


def read_cmux_topology() -> dict:
    try:
        result = run("cmux", "tree", "--all", "--json", timeout=15)
    except subprocess.TimeoutExpired:
        die("cmux topology query timed out before launch-barrier release")
    if result.returncode != 0:
        die((result.stderr or result.stdout).strip() or "cmux topology query failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        die("cmux topology query returned invalid JSON")


def verify_topology(topology: dict, expected: list[dict[str, object]]) -> list[dict[str, str]]:
    actual: dict[str, dict] = {}
    for window in topology.get("windows", []):
        for workspace in window.get("workspaces", []):
            ref = workspace.get("ref")
            if isinstance(ref, str):
                actual[ref] = workspace

    problems: list[str] = []
    expected_children: list[dict[str, str]] = []
    for item in expected:
        ref = str(item["ref"])
        workspace = actual.get(ref)
        if workspace is None:
            problems.append(f"missing {ref}")
            continue
        if workspace.get("title") != item["name"]:
            problems.append(f"{ref} title mismatch")
        surfaces = [
            surface
            for pane in workspace.get("panes", [])
            for surface in pane.get("surfaces", [])
        ]
        names = sorted(
            surface.get("title") for surface in surfaces if isinstance(surface.get("title"), str)
        )
        if names != item["terminal_names"]:
            problems.append(f"{ref} terminal topology mismatch")
        if any(surface.get("type") != "terminal" for surface in surfaces):
            problems.append(f"{ref} contains a non-terminal surface")
        by_name: dict[str, list[dict]] = {}
        for surface in surfaces:
            name = surface.get("title")
            if isinstance(name, str):
                by_name.setdefault(name, []).append(surface)
        for surface_name in item["terminal_names"]:
            matches = by_name.get(str(surface_name), [])
            if len(matches) != 1:
                problems.append(f"{ref} surface {surface_name} is not uniquely identifiable")
                continue
            surface_ref = matches[0].get("ref")
            if not isinstance(surface_ref, str) or not surface_ref:
                problems.append(f"{ref} surface {surface_name} has no ref")
                continue
            workspace_role = str(item["workspace_role"])
            expected_children.append(
                {
                    "run_id": str(item["run_id"]),
                    "team": str(item["team"]),
                    "role": "orchestrator" if workspace_role == "orchestrator" else str(surface_name),
                    "workspace_role": workspace_role,
                    "workspace_ref": ref,
                    "surface_name": str(surface_name),
                    "surface_ref": surface_ref,
                    "target_snapshot_sha256": str(item["snapshot_sha256"]),
                }
            )
    if problems:
        raise MirroredLaunchError("expected CMUX topology is not ready: " + "; ".join(problems))
    return sorted(expected_children, key=lambda item: (item["team"], item["role"]))


def readiness_receipt_path(root: Path, expected: dict[str, str]) -> Path:
    identity = sha256(
        sealed_results.canonical_json(
            {
                key: expected[key]
                for key in (
                    "run_id",
                    "team",
                    "role",
                    "workspace_ref",
                    "surface_ref",
                    "target_snapshot_sha256",
                )
            }
        )
    )[:16]
    return Path(root) / f"{expected['team']}.{expected['role']}.{identity}.json"


def readiness_exit_path(root: Path, expected: dict[str, str]) -> Path:
    receipt = readiness_receipt_path(root, expected)
    return Path(root) / "exits" / receipt.name


def atomic_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish canonical JSON atomically without replacing an earlier receipt."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    try:
        with temporary.open("xb") as handle:
            handle.write(sealed_results.canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise MirroredLaunchError(f"refusing to replace readiness receipt {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def load_gate_child(
    gate_path: Path,
    *,
    run_id: str,
    team: str,
    role: str,
    workspace_role: str,
    surface_name: str,
    snapshot_sha256: str,
) -> dict[str, str]:
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MirroredLaunchError("released readiness gate is missing or invalid") from exc
    children = gate.get("expected_children")
    if gate.get("run_id") != run_id or not isinstance(children, list):
        raise MirroredLaunchError("readiness gate run binding is invalid")
    binding = {
        "run_id": run_id,
        "team": team,
        "role": role,
        "workspace_role": workspace_role,
        "surface_name": surface_name,
        "target_snapshot_sha256": snapshot_sha256,
    }
    matches = [
        child
        for child in children
        if isinstance(child, dict)
        and all(child.get(key) == value for key, value in binding.items())
    ]
    if len(matches) != 1:
        raise MirroredLaunchError("child does not have one exact readiness-gate binding")
    expected = matches[0]
    required = {
        *binding,
        "workspace_ref",
        "surface_ref",
    }
    if set(expected) != required or not all(isinstance(expected[key], str) for key in required):
        raise MirroredLaunchError("readiness-gate child binding is malformed")
    return expected


def cmux_caller_identity() -> dict[str, str]:
    try:
        result = run("cmux", "identify", "--json", timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise MirroredLaunchError("CMUX child identity query timed out") from exc
    if result.returncode != 0:
        raise MirroredLaunchError("CMUX child identity query failed")
    try:
        identity = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MirroredLaunchError("CMUX child identity query returned invalid JSON") from exc
    caller = identity.get("caller")
    if not isinstance(caller, dict):
        raise MirroredLaunchError("CMUX did not bind the readiness process to a caller surface")
    workspace_ref = caller.get("workspace_ref")
    surface_ref = caller.get("surface_ref")
    if not isinstance(workspace_ref, str) or not isinstance(surface_ref, str):
        raise MirroredLaunchError("CMUX caller identity lacks workspace or surface ref")
    return {"workspace_ref": workspace_ref, "surface_ref": surface_ref}


def publish_child_readiness(
    gate_path: Path,
    readiness_root: Path,
    *,
    run_id: str,
    team: str,
    role: str,
    workspace_role: str,
    surface_name: str,
    snapshot_sha256: str,
) -> dict[str, Any]:
    expected = load_gate_child(
        gate_path,
        run_id=run_id,
        team=team,
        role=role,
        workspace_role=workspace_role,
        surface_name=surface_name,
        snapshot_sha256=snapshot_sha256,
    )
    identity = cmux_caller_identity()
    if identity["workspace_ref"] != expected["workspace_ref"]:
        raise MirroredLaunchError("child readiness workspace ref does not match topology")
    if identity["surface_ref"] != expected["surface_ref"]:
        raise MirroredLaunchError("child readiness surface ref does not match topology")
    receipt: dict[str, Any] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "ready": True,
        **expected,
        "ready_at_utc": sealed_results.utc_timestamp(datetime.now(timezone.utc)),
        "security_boundary": READINESS_BOUNDARY,
    }
    if set(receipt) != READINESS_KEYS:
        raise MirroredLaunchError("internal readiness receipt schema mismatch")
    atomic_exclusive_json(readiness_receipt_path(readiness_root, expected), receipt)
    return receipt


def publish_child_exit(
    gate_path: Path,
    readiness_root: Path,
    *,
    run_id: str,
    team: str,
    role: str,
    workspace_role: str,
    surface_name: str,
    snapshot_sha256: str,
    phase: str,
    returncode: int,
) -> dict[str, Any]:
    expected = load_gate_child(
        gate_path,
        run_id=run_id,
        team=team,
        role=role,
        workspace_role=workspace_role,
        surface_name=surface_name,
        snapshot_sha256=snapshot_sha256,
    )
    marker: dict[str, Any] = {
        "schema_version": READINESS_SCHEMA_VERSION,
        **expected,
        "phase": phase,
        "returncode": returncode,
        "exited_at_utc": sealed_results.utc_timestamp(datetime.now(timezone.utc)),
    }
    if set(marker) != EXIT_MARKER_KEYS:
        raise MirroredLaunchError("internal child-exit marker schema mismatch")
    atomic_exclusive_json(readiness_exit_path(readiness_root, expected), marker)
    return marker


def provider_auth_preflight(provider: str) -> None:
    auth_command = ("codex", "login", "status") if provider == "codex" else ("claude", "auth", "status")
    try:
        result = run(*auth_command, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise MirroredLaunchError(f"{provider} authentication preflight timed out") from exc
    if result.returncode != 0:
        raise MirroredLaunchError(f"{provider} authentication preflight failed")


def terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    # supervise_child launches every provider in a fresh session, so its PID is
    # also the process-group ID. Signal that whole group; killing only the shell
    # would leave provider grandchildren alive after rollback.
    process_group = child.pid
    if process_group == os.getpgrp():
        raise MirroredLaunchError("refusing to terminate the launcher's process group")
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        child.wait()
        return
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
        return
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return
    os.killpg(process_group, signal.SIGKILL)


def supervise_child(args: argparse.Namespace) -> int:
    """Launch one authenticated CLI, prove short liveness, then wait for it."""

    provider_auth_preflight(args.provider)
    child = subprocess.Popen(
        ["/bin/sh", "-c", args.agent_command],
        start_new_session=True,
    )
    try:
        time.sleep(args.liveness_seconds)
        returncode = child.poll()
        if returncode is not None:
            publish_child_exit(
                args.gate,
                args.readiness_root,
                run_id=args.run_id,
                team=args.team,
                role=args.role,
                workspace_role=args.workspace_role,
                surface_name=args.surface_name,
                snapshot_sha256=args.snapshot_sha256,
                phase="before_readiness",
                returncode=returncode,
            )
            raise MirroredLaunchError(
                f"{args.provider} child exited before readiness with status {returncode}"
            )
        if sealed_results.invalidation_status(args.seal_root) is not None:
            terminate_child(child)
            return 75
        publish_child_readiness(
            args.gate,
            args.readiness_root,
            run_id=args.run_id,
            team=args.team,
            role=args.role,
            workspace_role=args.workspace_role,
            surface_name=args.surface_name,
            snapshot_sha256=args.snapshot_sha256,
        )
        while True:
            invalidation = sealed_results.invalidation_status(args.seal_root)
            if invalidation is not None:
                terminate_child(child)
                return 75
            returncode = child.poll()
            if returncode is not None:
                publish_child_exit(
                    args.gate,
                    args.readiness_root,
                    run_id=args.run_id,
                    team=args.team,
                    role=args.role,
                    workspace_role=args.workspace_role,
                    surface_name=args.surface_name,
                    snapshot_sha256=args.snapshot_sha256,
                    phase="after_readiness",
                    returncode=returncode,
                )
                return returncode
            time.sleep(0.2)
    except BaseException:
        terminate_child(child)
        raise


def validate_readiness_receipt(payload: Any, expected: dict[str, str]) -> None:
    if not isinstance(payload, dict) or set(payload) != READINESS_KEYS:
        raise MirroredLaunchError("child readiness receipt has an unexpected schema")
    if payload.get("schema_version") != READINESS_SCHEMA_VERSION or payload.get("ready") is not True:
        raise MirroredLaunchError("child readiness receipt does not declare readiness")
    for key, value in expected.items():
        if payload.get(key) != value:
            raise MirroredLaunchError(f"child readiness receipt has mismatched {key}")
    if payload.get("security_boundary") != READINESS_BOUNDARY:
        raise MirroredLaunchError("child readiness receipt has an invalid security boundary")
    ready_at = payload.get("ready_at_utc")
    if not isinstance(ready_at, str):
        raise MirroredLaunchError("child readiness receipt has no UTC timestamp")
    sealed_results.parse_utc_timestamp(ready_at)


def wait_for_child_readiness(
    expected_children: list[dict[str, str]],
    readiness_root: Path,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    """Wait a bounded interval until every expected child receipt validates."""

    deadline = time.monotonic() + timeout_seconds
    pending = {
        str(readiness_receipt_path(readiness_root, expected)): expected
        for expected in expected_children
    }
    receipts: list[dict[str, Any]] = []
    while pending:
        for path_value, expected in list(pending.items()):
            path = Path(path_value)
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MirroredLaunchError(f"invalid child readiness receipt {path.name}") from exc
            validate_readiness_receipt(payload, expected)
            receipts.append(payload)
            del pending[path_value]
        if not pending:
            break
        if time.monotonic() >= deadline:
            waiting = sorted(f"{item['team']}/{item['role']}" for item in pending.values())
            raise MirroredLaunchError(
                "child readiness timed out waiting for: " + ", ".join(waiting)
            )
        time.sleep(0.05)
    return sorted(receipts, key=lambda item: (item["team"], item["role"]))


def settle_child_readiness(
    expected_children: list[dict[str, str]],
    readiness_root: Path,
    settle_seconds: float,
) -> None:
    """Fail if any supervised child exits during the bounded settle window."""

    deadline = time.monotonic() + settle_seconds
    while True:
        for expected in expected_children:
            marker_path = readiness_exit_path(readiness_root, expected)
            if not marker_path.is_file():
                continue
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MirroredLaunchError(f"invalid child-exit marker {marker_path.name}") from exc
            if not isinstance(marker, dict) or set(marker) != EXIT_MARKER_KEYS:
                raise MirroredLaunchError(f"malformed child-exit marker {marker_path.name}")
            for key, value in expected.items():
                if marker.get(key) != value:
                    raise MirroredLaunchError(f"child-exit marker has mismatched {key}")
            raise MirroredLaunchError(
                f"child exited during readiness settle: {expected['team']}/{expected['role']}"
            )
        if time.monotonic() >= deadline:
            return
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def close_workspace(ref: str) -> None:
    try:
        result = run("cmux", "workspace", "close", ref, timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise MirroredLaunchError(f"rollback timed out closing {ref}") from exc
    if result.returncode != 0:
        raise MirroredLaunchError(
            (result.stderr or result.stdout).strip() or f"rollback failed closing {ref}"
        )


def release_barrier(gate_path: Path, payload: dict) -> None:
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = gate_path.with_name(f".{gate_path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(sealed_results.canonical_json(payload))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o444)
        try:
            os.link(temporary, gate_path)
        except FileExistsError as exc:
            raise MirroredLaunchError(f"refusing to replace released barrier: {gate_path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_receipt(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sealed_results.canonical_json(payload))


def public_workspace_response(response: dict) -> dict:
    """Keep receipts free of echoed layouts, prompts, and capability tokens."""

    allowed = ("workspace_ref", "window_ref", "surface_ref", "group_ref")
    return {key: response.get(key) for key in allowed if key in response}


def launch_mirrored_workspaces(
    *,
    repo: Path,
    project: str,
    team_layouts: dict[str, dict],
    orchestrator_layouts: dict[str, dict],
    workspace_names: dict[str, str],
    gate_path: Path,
    readiness_root: Path,
    seal_root: Path,
    receipt_path: Path,
    receipt: dict,
    run_id: str,
    snapshot_sha256: str,
    readiness_timeout_seconds: int,
    readiness_liveness_seconds: float = 0.75,
    readiness_settle_seconds: float = 1.0,
) -> dict:
    """Create, release, then validate every authenticated live child."""

    specs: list[dict[str, object]] = []
    for kind in ("codex", "claude"):
        specs.append(
            {
                "role": f"{kind}-team",
                "team": kind,
                "workspace_role": "team",
                "name": workspace_names[kind],
                "layout": team_layouts[kind],
            }
        )
    for kind in ("codex", "claude"):
        specs.append(
            {
                "role": f"{kind}-orchestrator",
                "team": kind,
                "workspace_role": "orchestrator",
                "name": f"{project}-{kind}-orchestrator",
                "layout": orchestrator_layouts[kind],
            }
        )

    created: list[dict[str, object]] = []
    expected_children: list[dict[str, str]] = []
    readiness_receipts: list[dict[str, Any]] = []
    barrier_released = False
    try:
        if gate_path.exists():
            raise MirroredLaunchError(f"refusing to reuse an already released barrier: {gate_path}")
        if readiness_root.exists():
            raise MirroredLaunchError(f"refusing to reuse child-readiness state: {readiness_root}")
        for spec in specs:
            try:
                response = create_workspace(
                    str(spec["name"]),
                    repo,
                    supervised_layout(
                        spec["layout"],
                        gate_path,
                        readiness_root,
                        seal_root,
                        repo,
                        run_id=run_id,
                        team=str(spec["team"]),
                        workspace_role=str(spec["workspace_role"]),
                        snapshot_sha256=snapshot_sha256,
                        liveness_seconds=readiness_liveness_seconds,
                    ),
                )
            except (Exception, SystemExit) as exc:
                # CMUX errors may echo the submitted layout. Never persist that
                # raw text because orchestrator layouts carry one capability.
                raise MirroredLaunchError(
                    f"workspace creation failed for {spec['name']}"
                ) from exc
            ref = response.get("workspace_ref")
            if not isinstance(ref, str) or not ref:
                raise MirroredLaunchError(
                    f"cmux create for {spec['name']} did not return a workspace_ref"
                )
            created.append(
                {
                    "role": spec["role"],
                    "team": spec["team"],
                    "workspace_role": spec["workspace_role"],
                    "name": spec["name"],
                    "ref": ref,
                    "run_id": run_id,
                    "snapshot_sha256": snapshot_sha256,
                    "terminal_names": terminal_names(spec["layout"]),
                    "response": public_workspace_response(response),
                }
            )

        topology = read_cmux_topology()
        expected_children = verify_topology(topology, created)
        release_barrier(
            gate_path,
            {
                "project": project,
                "run_id": run_id,
                "target_snapshot_sha256": snapshot_sha256,
                "workspace_refs": [item["ref"] for item in created],
                "topology_verified": True,
                "expected_children": expected_children,
            },
        )
        barrier_released = True
        readiness_receipts = wait_for_child_readiness(
            expected_children, readiness_root, readiness_timeout_seconds
        )
        settle_child_readiness(
            expected_children, readiness_root, readiness_settle_seconds
        )
    except (Exception, SystemExit) as exc:
        invalidation_reason = "post_release_failure" if barrier_released else "pre_release_failure"
        invalidation: dict[str, Any] = {
            "attempted": True,
            "completed": False,
            "reason_code": invalidation_reason,
        }
        try:
            sealed_results.invalidate(
                seal_root,
                invalidation_reason,
                "mirrored fleet failed closed before acceptance",
            )
            invalidation["completed"] = True
        except Exception as invalidation_error:
            invalidation["error"] = str(invalidation_error)
        rollback = {"attempted": [], "closed": [], "errors": {}}
        for item in reversed(created):
            ref = str(item["ref"])
            rollback["attempted"].append(ref)
            try:
                close_workspace(ref)
                rollback["closed"].append(ref)
            except Exception as close_error:
                rollback["errors"][ref] = str(close_error)
        failure = {
            **receipt,
            "valid": False,
            "status": "readiness_failed" if barrier_released else "launch_failed",
            "launch_barrier": {"path": str(gate_path), "released": barrier_released},
            "sealed_results_invalidation": invalidation,
            "child_readiness": {
                "root": str(readiness_root),
                "verified": False,
                "expected": len(expected_children),
            },
            "created_workspaces": [
                {key: item[key] for key in ("role", "name", "ref")} for item in created
            ],
            "rollback": rollback,
            "error": str(exc),
        }
        write_receipt(receipt_path, failure)
        raise MirroredLaunchError(str(exc)) from exc

    successful = {
        **receipt,
        "valid": True,
        "status": "running",
        "launch_barrier": {"path": str(gate_path), "released": True},
        "child_readiness": {
            "root": str(readiness_root),
            "verified": True,
            "timeout_seconds": readiness_timeout_seconds,
            "settle_seconds": readiness_settle_seconds,
            "expected": len(expected_children),
            "receipts": readiness_receipts,
            "security_boundary": READINESS_BOUNDARY,
        },
        "topology": {
            "verified": True,
            "workspaces": [
                {key: item[key] for key in ("role", "name", "ref")} for item in created
            ],
        },
        "teams": {
            kind: next(item["response"] for item in created if item["role"] == f"{kind}-team")
            for kind in ("codex", "claude")
        },
        "orchestrators": {
            kind: next(
                item["response"]
                for item in created
                if item["role"] == f"{kind}-orchestrator"
            )
            for kind in ("codex", "claude")
        },
    }
    write_receipt(receipt_path, successful)
    return successful


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path)
    p.add_argument("--project")
    p.add_argument("--mode", choices=("compare", "mutate"), default="compare")
    p.add_argument("--claim-id")
    p.add_argument("--orchestrator", choices=("codex", "claude", "both", "none"), default="codex")
    p.add_argument(
        "--mirrored",
        action="store_true",
        help="launch two identical teams with Codex and Claude primary orchestrators",
    )
    task = p.add_mutually_exclusive_group()
    task.add_argument("--task-file", type=Path, help="UTF-8 file containing the immutable comparison task")
    task.add_argument("--task", help="immutable comparison task (prefer --task-file for non-trivial prompts)")
    p.add_argument(
        "--timeout-minutes",
        type=positive_minutes,
        default=20,
        help="mirrored team deadline in minutes (default: 20)",
    )
    p.add_argument(
        "--readiness-timeout-seconds",
        type=positive_seconds,
        default=30,
        help="bounded wait for all post-release child receipts (default: 30)",
    )
    p.add_argument(
        "--readiness-settle-seconds",
        type=positive_duration,
        default=1.0,
        help="post-receipt early-exit settle window (default: 1.0)",
    )
    p.add_argument("--codex-orchestrator-model", default="gpt-5.6-sol")
    p.add_argument("--claude-orchestrator-model", default="claude-opus-4-8")
    p.add_argument("--codex-lead-model", default="gpt-5.6-sol")
    p.add_argument("--codex-worker-model", default="gpt-5.6-sol")
    p.add_argument("--claude-worker-model", default="sonnet")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def read_task(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if args.task_file:
        path = args.task_file.expanduser().resolve()
        try:
            return path.read_text(encoding="utf-8"), str(path)
        except (OSError, UnicodeError) as exc:
            die(f"cannot read --task-file {path}: {exc}")
    if args.task is not None:
        return args.task, "--task"
    return None, None


def repo_snapshot(repo: Path) -> dict[str, object]:
    head = run("git", "-C", str(repo), "rev-parse", "HEAD")
    if head.returncode != 0:
        die(f"target is not a readable git repository: {repo}")
    status = run("git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all", timeout=30)
    diff = run("git", "-C", str(repo), "diff", "--binary", "HEAD", timeout=60)
    untracked = run(
        "git", "-C", str(repo), "ls-files", "--others", "--exclude-standard", "-z", timeout=30
    )
    if status.returncode != 0 or diff.returncode != 0 or untracked.returncode != 0:
        die("failed to snapshot target repository dirty state")

    manifest: list[dict[str, object]] = []
    for relative in sorted(path for path in untracked.stdout.split("\0") if path):
        path = repo / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            die(f"failed to fingerprint untracked path {relative}: {exc}")
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            kind = "symlink"
            target = os.readlink(path)
            content_digest = sha256(os.fsencode(target))
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            try:
                content_digest = sha256(path.read_bytes())
            except OSError as exc:
                die(f"failed to hash untracked file {relative}: {exc}")
        else:
            kind = "other"
            content_digest = sha256(b"")
        manifest.append(
            {
                "path": relative,
                "type": kind,
                "mode": f"{mode:04o}",
                "content_or_target_sha256": content_digest,
            }
        )

    # The tracked binary diff covers both staged and unstaged changes relative
    # to HEAD. The canonical manifest binds every untracked path and its bytes.
    status_digest = sha256(status.stdout)
    diff_digest = sha256(diff.stdout)
    untracked_digest = sha256(sealed_results.canonical_json(manifest))
    head_value = head.stdout.strip()
    snapshot_digest = sha256(
        sealed_results.canonical_json(
            {
                "head": head_value,
                "tracked_diff_sha256": diff_digest,
                "untracked_manifest_sha256": untracked_digest,
            }
        )
    )
    return {
        "head": head_value,
        "dirty": bool(status.stdout),
        "status_sha256": status_digest,
        "tracked_diff_sha256": diff_digest,
        "untracked_count": len(manifest),
        "untracked_manifest_sha256": untracked_digest,
        "snapshot_sha256": snapshot_digest,
    }


def exclusive_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(sealed_results.canonical_json(payload))
    except FileExistsError:
        die(f"immutable task envelope already exists: {path}")
    path.chmod(0o444)


def write_capability_files(seal_root: Path, capabilities: dict[str, str]) -> dict[str, Path]:
    """Persist one-use capabilities privately so they never appear in argv."""

    capability_root = seal_root / "capabilities"
    capability_root.mkdir(mode=0o700, exist_ok=False)
    paths: dict[str, Path] = {}
    for team, capability in capabilities.items():
        path = capability_root / team
        with path.open("xb") as handle:
            handle.write((capability + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o400)
        paths[team] = path
    return paths


def child_supervisor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal CMUX child readiness supervisor")
    parser.add_argument("--gate", required=True, type=Path)
    parser.add_argument("--readiness-root", required=True, type=Path)
    parser.add_argument("--seal-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--team", required=True, choices=("codex", "claude"))
    parser.add_argument("--role", required=True)
    parser.add_argument("--workspace-role", required=True, choices=("team", "orchestrator"))
    parser.add_argument("--surface-name", required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--provider", required=True, choices=("codex", "claude"))
    parser.add_argument("--liveness-seconds", required=True, type=float)
    parser.add_argument("--agent-command", required=True)
    return parser


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "child-supervise":
        child_args = child_supervisor_parser().parse_args(sys.argv[2:])
        if child_args.liveness_seconds <= 0:
            die("child liveness seconds must be positive")
        try:
            raise SystemExit(supervise_child(child_args))
        except MirroredLaunchError as exc:
            die(f"child readiness failed: {exc}")

    args = parser().parse_args()
    versions = validate_tools()
    if args.validate:
        print(json.dumps({"ok": True, "tools": versions, "live_actions": False}, indent=2))
        return
    if not args.repo or not args.project:
        die("--repo and --project are required unless --validate is used")
    repo = args.repo.expanduser().resolve()
    if not repo.is_dir():
        die(f"repository directory not found: {repo}")
    project = slug(args.project)
    if args.mode == "mutate":
        die(
            "mutation mode is fail-closed: You.md does not yet ship the atomic work-claim lease. "
            "Use compare mode, or implement the contract in houston/ENDPOINTS.md first."
        )

    task, task_source = read_task(args)
    if args.mirrored and not task:
        die("--mirrored requires a non-empty --task-file or --task")

    if args.mirrored:
        snapshot = repo_snapshot(repo)
        task_digest = sha256(task or "")
        created_at = datetime.now(timezone.utc).replace(microsecond=0)
        deadline = created_at + timedelta(minutes=args.timeout_minutes)
        created_at_utc = sealed_results.utc_timestamp(created_at)
        deadline_utc = sealed_results.utc_timestamp(deadline)
        run_id = (
            f"{created_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{time.time_ns() % 1_000_000_000:09d}-{task_digest[:12]}"
        )
        envelope_path = ROOT / ".team" / f"{project}.{run_id}.task-envelope.json"
        seal_root = ROOT / ".team" / "sealed-results" / project / run_id
        gate_path = ROOT / ".team" / "launch-gates" / project / run_id / "released.json"
        readiness_root = ROOT / ".team" / "child-readiness" / project / run_id
        receipt_path = ROOT / ".team" / f"{project}.{run_id}.subscription-spawn.json"
        teams = ("codex", "claude")
        if args.dry_run:
            capabilities = {kind: f"<REDACTED-{kind.upper()}-CAPABILITY>" for kind in teams}
            capability_paths = {
                kind: seal_root / "capabilities" / kind for kind in teams
            }
            contract = {
                "schema_version": sealed_results.SCHEMA_VERSION,
                "expected_teams": sorted(teams),
                "capability_sha256": {
                    kind: sha256(capabilities[kind]) for kind in sorted(teams)
                },
                "deadline_utc": deadline_utc,
                "security_boundary": sealed_results.SECURITY_BOUNDARY,
            }
            contract_bytes = sealed_results.canonical_json(contract)
        else:
            initialized = sealed_results.initialize(
                seal_root,
                teams,
                deadline_utc=deadline_utc,
            )
            capabilities = initialized["capabilities"]
            capability_paths = write_capability_files(seal_root, capabilities)
            contract_bytes = (seal_root / "contract.json").read_bytes()
            contract = json.loads(contract_bytes)
        seal_metadata = {
            "root": str(seal_root),
            "security_boundary": sealed_results.SECURITY_BOUNDARY,
            "contract_sha256": sha256(contract_bytes),
            "capability_sha256": contract["capability_sha256"],
            "deadline_utc": contract["deadline_utc"],
        }
        envelope = {
            "schema_version": 1,
            "immutable": True,
            "run_id": run_id,
            "created_at_utc": created_at_utc,
            "deadline_utc": deadline_utc,
            "timeout_minutes": args.timeout_minutes,
            "mode": args.mode,
            "project": project,
            "repo": str(repo),
            "task": task,
            "task_sha256": task_digest,
            "task_source": task_source,
            "target_snapshot": snapshot,
            "teams": {
                "codex": f"{project}-codex-team",
                "claude": f"{project}-claude-team",
            },
            "sealed_results": seal_metadata,
            "comparison_rules": {
                "target_repo_read_only": True,
                "orchestrators_must_not_read_sibling_result": True,
                "speed_is_telemetry_not_correctness": True,
            },
        }
        envelope_sha256 = sha256(sealed_results.canonical_json(envelope))
        layouts: dict[str, dict] = {}
        team_commands: dict[str, dict[str, str]] = {}
        primary_layouts: dict[str, dict] = {}
        for kind in teams:
            layouts[kind], team_commands[kind] = render_team(
                args,
                repo,
                f"{project}-{kind}",
                envelope_path=envelope_path,
                envelope_sha256=envelope_sha256,
            )
            primary_layouts[kind] = orchestrator_layout(
                args,
                repo,
                f"{project}-{kind}",
                kind,
                team_workspace=envelope["teams"][kind],
                task=task,
                envelope_path=envelope_path,
                envelope_sha256=envelope_sha256,
                seal_root=seal_root,
                result_token_file=capability_paths[kind],
            )
        plan = {
            "mode": args.mode,
            "mirrored": True,
            "repo": str(repo),
            "project": project,
            "task_envelope": str(envelope_path),
            "task_envelope_sha256": envelope_sha256,
            "launch_barrier": {"path": str(gate_path), "released": False},
            "child_readiness": {
                "root": str(readiness_root),
                "timeout_seconds": args.readiness_timeout_seconds,
                "settle_seconds": args.readiness_settle_seconds,
                "verified": False,
                "security_boundary": READINESS_BOUNDARY,
            },
            "envelope": envelope,
            "team_workspaces": envelope["teams"],
            "orchestrators": {
                "codex": args.codex_orchestrator_model,
                "claude": args.claude_orchestrator_model,
            },
            "models": {
                "lead": args.codex_lead_model,
                "codex_workers": args.codex_worker_model,
                "claude_workers": args.claude_worker_model,
            },
            "layouts": layouts,
            "orchestrator_layouts": primary_layouts,
            "commands": team_commands,
            "provider_api_keys_injected": False,
            "surface_read_only_boundary": SURFACE_READ_ONLY_BOUNDARY,
        }
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return

        exclusive_json(envelope_path, envelope)
        ensure_cmux()
        receipt: dict[str, object] = {
            "project": project,
            "run_id": run_id,
            "repo": str(repo),
            "mode": args.mode,
            "mirrored": True,
            "task_envelope": str(envelope_path),
            "task_envelope_sha256": envelope_sha256,
            "target_snapshot": snapshot,
            "sealed_results": seal_metadata,
            "deadline_utc": deadline_utc,
            "timeout_minutes": args.timeout_minutes,
            "readiness_timeout_seconds": args.readiness_timeout_seconds,
            "readiness_settle_seconds": args.readiness_settle_seconds,
            "surface_read_only_boundary": SURFACE_READ_ONLY_BOUNDARY,
        }
        try:
            receipt = launch_mirrored_workspaces(
                repo=repo,
                project=project,
                team_layouts=layouts,
                orchestrator_layouts=primary_layouts,
                workspace_names=envelope["teams"],
                gate_path=gate_path,
                readiness_root=readiness_root,
                seal_root=seal_root,
                receipt_path=receipt_path,
                receipt=receipt,
                run_id=run_id,
                snapshot_sha256=str(snapshot["snapshot_sha256"]),
                readiness_timeout_seconds=args.readiness_timeout_seconds,
                readiness_settle_seconds=args.readiness_settle_seconds,
            )
        except MirroredLaunchError as exc:
            die(f"mirrored fleet launch failed closed: {exc}")
        print(json.dumps({"ok": True, "receipt": str(receipt_path), **receipt}, indent=2))
        return

    team_layout, commands = render_team(args, repo, project)
    plan = {
        "mode": args.mode,
        "repo": str(repo),
        "project": project,
        "team_workspace": f"{project}-team",
        "orchestrator": args.orchestrator,
        "models": {
            "orchestrator_codex": args.codex_orchestrator_model,
            "orchestrator_claude": args.claude_orchestrator_model,
            "lead": args.codex_lead_model,
            "codex_workers": args.codex_worker_model,
            "claude_workers": args.claude_worker_model,
        },
        "layout": team_layout,
        "commands": commands,
        "provider_api_keys_injected": False,
        "surface_read_only_boundary": SURFACE_READ_ONLY_BOUNDARY,
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return

    ensure_cmux()
    receipt = {"team": create_workspace(f"{project}-team", repo, team_layout), "orchestrators": {}}
    kinds = ("codex", "claude") if args.orchestrator == "both" else (args.orchestrator,)
    for kind in kinds:
        if kind != "none":
            receipt["orchestrators"][kind] = create_workspace(
                f"{project}-{kind}-orchestrator", repo, orchestrator_layout(args, repo, project, kind)
            )
    receipt.update({"project": project, "repo": str(repo), "mode": args.mode})
    receipt_path = ROOT / ".team" / f"{project}.subscription-spawn.json"
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"ok": True, "receipt": str(receipt_path), **receipt}, indent=2))


if __name__ == "__main__":
    main()
