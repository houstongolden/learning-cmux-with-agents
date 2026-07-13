#!/usr/bin/env python3
"""Boot a subscription-authenticated Codex/Claude fleet in cmux.

The launcher intentionally uses declarative cmux layouts. It never reads an
.env file or injects provider API keys. Mutation mode is reserved until You.md
ships an atomic work-claim lease; the current agent bus is not a lock.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


def codex_cmd(model: str, effort: str, prompt: str, *, controller: bool) -> str:
    return command(
        [
            "codex",
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


def render_team(args: argparse.Namespace, repo: Path, project: str) -> tuple[dict, dict[str, str]]:
    common = (
        f"Team {project}; repository {repo}. This is a read-only comparison run. "
        "Do not edit files, create commits, or change branches. Preserve evidence and report a concise sentinel."
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
        "__LEAD_COMMAND__": codex_cmd(args.codex_lead_model, "medium", prompts["lead"], controller=True),
        "__EXPLORER_COMMAND__": codex_cmd(args.codex_worker_model, "low", prompts["explorer"], controller=False),
        "__REVIEWER_COMMAND__": claude_cmd(args.claude_worker_model, "medium", prompts["reviewer"], controller=False),
        "__TESTER_COMMAND__": codex_cmd(args.codex_worker_model, "low", prompts["tester"], controller=False),
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


def orchestrator_layout(args: argparse.Namespace, repo: Path, project: str, kind: str) -> dict:
    prompt = (
        f"You are the PRIMARY ORCHESTRATOR for team {project} in repository {repo}. "
        f"The lead is in cmux workspace {project}-team. Talk only to the lead using cmux from inside this terminal. "
        "Do not edit the repository. Judge worker evidence objectively; speed is telemetry, not correctness. "
        "Wait for Houston's task."
    )
    if kind == "codex":
        launched = codex_cmd(args.codex_orchestrator_model, "high", prompt, controller=True)
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
    p.add_argument("--codex-orchestrator-model", default="gpt-5.6-sol")
    p.add_argument("--claude-orchestrator-model", default="claude-opus-4-8")
    p.add_argument("--codex-lead-model", default="gpt-5.6-sol")
    p.add_argument("--codex-worker-model", default="gpt-5.6-sol")
    p.add_argument("--claude-worker-model", default="sonnet")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


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
