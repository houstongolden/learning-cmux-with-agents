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
import shlex
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

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
    return command(
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


def claude_cmd(model: str, effort: str, prompt: str, *, controller: bool) -> str:
    return command(
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


def validate_tools() -> dict[str, str]:
    versions: dict[str, str] = {}
    for binary in ("cmux", "codex", "claude", "git"):
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
        "__REVIEWER_COMMAND__": claude_cmd(args.claude_worker_model, "medium", prompts["reviewer"], controller=False),
        "__TESTER_COMMAND__": codex_cmd(
            args.codex_worker_model, "low", prompts["tester"], controller=False, repo=repo
        ),
        "__COMPARATOR_COMMAND__": claude_cmd(args.claude_worker_model, "medium", prompts["comparator"], controller=False),
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
    result_token: str | None = None,
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
            f"SEALED_RESULTS_TOKEN={shlex.quote(result_token or '')} "
            f"python3 {shlex.quote(str(ROOT / 'scripts' / 'sealed_results.py'))} submit "
            f"--root {shlex.quote(str(seal_root))} --team {shlex.quote(kind)} "
            f"--input {shlex.quote(f'/tmp/{project}-result.json')}\n"
            "The helper performs authenticated, atomic, one-way sealing. Do not write a result directly into its root."
        )
    if kind == "codex":
        launched = codex_cmd(
            args.codex_orchestrator_model, "high", prompt, controller=True, repo=repo
        )
    else:
        launched = claude_cmd(args.claude_orchestrator_model, "high", prompt, controller=True)
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


def main() -> None:
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
        run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{task_digest[:12]}"
        envelope_path = ROOT / ".team" / f"{project}.{run_id}.task-envelope.json"
        seal_root = ROOT / ".team" / "sealed-results" / project / run_id
        teams = ("codex", "claude")
        if args.dry_run:
            capabilities = {kind: f"<REDACTED-{kind.upper()}-CAPABILITY>" for kind in teams}
            contract = {
                "schema_version": sealed_results.SCHEMA_VERSION,
                "expected_teams": sorted(teams),
                "capability_sha256": {
                    kind: sha256(capabilities[kind]) for kind in sorted(teams)
                },
                "security_boundary": sealed_results.SECURITY_BOUNDARY,
            }
            contract_bytes = sealed_results.canonical_json(contract)
        else:
            initialized = sealed_results.initialize(seal_root, teams)
            capabilities = initialized["capabilities"]
            contract_bytes = (seal_root / "contract.json").read_bytes()
            contract = json.loads(contract_bytes)
        seal_metadata = {
            "root": str(seal_root),
            "security_boundary": sealed_results.SECURITY_BOUNDARY,
            "contract_sha256": sha256(contract_bytes),
            "capability_sha256": contract["capability_sha256"],
        }
        envelope = {
            "schema_version": 1,
            "immutable": True,
            "run_id": run_id,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
                result_token=capabilities[kind],
            )
        plan = {
            "mode": args.mode,
            "mirrored": True,
            "repo": str(repo),
            "project": project,
            "task_envelope": str(envelope_path),
            "task_envelope_sha256": envelope_sha256,
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
        }
        if args.dry_run:
            print(json.dumps(plan, indent=2))
            return

        exclusive_json(envelope_path, envelope)
        ensure_cmux()
        receipt: dict[str, object] = {
            "teams": {},
            "orchestrators": {},
            "project": project,
            "repo": str(repo),
            "mode": args.mode,
            "mirrored": True,
            "task_envelope": str(envelope_path),
            "task_envelope_sha256": envelope_sha256,
            "target_snapshot": snapshot,
            "sealed_results": seal_metadata,
        }
        for kind in teams:
            workspace = envelope["teams"][kind]
            receipt["teams"][kind] = create_workspace(workspace, repo, layouts[kind])
            receipt["orchestrators"][kind] = create_workspace(
                f"{project}-{kind}-orchestrator",
                repo,
                primary_layouts[kind],
            )
        receipt_path = ROOT / ".team" / f"{project}.{run_id}.subscription-spawn.json"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
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
