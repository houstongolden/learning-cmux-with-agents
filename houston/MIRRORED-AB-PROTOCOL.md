# Mirrored orchestrator A/B protocol

## Purpose

Compare Codex `gpt-5.6-sol` high against Claude `claude-opus-4-8` high without
letting different workers, repository state, prompt delivery, or early result
visibility decide the outcome. This protocol is read-only by default and uses
existing ChatGPT and Claude.ai subscription logins only.

## 1. Freeze one immutable task envelope

The launcher creates the envelope before either arm launches. Its current field
shape is:

```json
{
  "schema_version": 1,
  "immutable": true,
  "run_id": "20260713T000000Z-<task-hash-prefix>",
  "created_at_utc": "ISO-8601",
  "deadline_utc": "ISO-8601 UTC",
  "timeout_minutes": 20,
  "mode": "compare",
  "project": "bigbounce-p3-review",
  "repo": "/absolute/path/to/repo",
  "task": "exact task text, including scope, acceptance checks, and limits",
  "task_sha256": "<sha256>",
  "task_source": "/absolute/path/to/task.md",
  "target_snapshot": {
    "head": "<git rev-parse HEAD>",
    "dirty": false,
    "status_sha256": "<sha256>",
    "tracked_diff_sha256": "<sha256 of git diff --binary HEAD>",
    "untracked_count": 0,
    "untracked_manifest_sha256": "<sha256>",
    "snapshot_sha256": "<sha256>"
  },
  "teams": {
    "codex": "bigbounce-p3-review-codex-team",
    "claude": "bigbounce-p3-review-claude-team"
  },
  "sealed_results": {
    "root": "/absolute/path/to/.team/sealed-results/<project>/<run-id>",
    "security_boundary": "<procedural isolation warning>",
    "contract_sha256": "<sha256>",
    "capability_sha256": {"codex": "<sha256>", "claude": "<sha256>"},
    "deadline_utc": "ISO-8601 UTC"
  },
  "comparison_rules": {
    "target_repo_read_only": true,
    "orchestrators_must_not_read_sibling_result": true,
    "speed_is_telemetry_not_correctness": true
  }
}
```

The launcher snapshot binds `HEAD`; one tracked `git diff --binary HEAD` digest
covering staged and unstaged tracked changes; and a canonical, ordered untracked
manifest. Every untracked entry records path, file type, mode, and either file
content SHA-256 or symlink-target SHA-256 (`other` types use the defined empty
content hash). Canonicalize and hash the whole envelope. Any HEAD, dirty
digest or task change creates a new run; it cannot silently amend an active
comparison. Scope, acceptance checks, and time limits belong in the immutable
task text because the current launcher does not emit separate fields for them.

Mutation remains fail-closed until You.md has atomic work claims and every
writer has its own worktree/branch. A dirty envelope is acceptable for a
read-only comparison only when both arms mount the exact same state.

## 2. Mirror everything except the orchestrator

Create fresh arm A and arm B workspaces. Both get:

- the same lead model and effort;
- the same four worker models, roles, prompts, tools, and permissions;
- the same repository snapshot and read-only policy;
- the same task envelope, acceptance checks, wall-clock limit, and output
  schema;
- a procedural prohibition on inspecting the other arm's transcript, pane
  output, result path, or status details.

Arm A uses the Codex orchestrator. Arm B uses the Claude orchestrator. Do not
reuse one arm's already-influenced lead/workers for the other arm.

## 3. Inject both orchestrator tasks at startup

The launcher embeds the full task in each orchestrator's declarative startup
command. Every lead/worker startup command receives the same envelope path and
hash, but not the full task; those roles wait for their own orchestrator/lead to
dispatch it. This avoids external task-injection timing differences and the
verified cmux 0.64.17/macOS 26.5 outside-socket deadlock.

The current launcher computes and records the envelope/repository digests, but
the sessions do not independently recompute or enforce them before working.
Until that hardening exists, the operator must keep the checkout frozen, inspect
the emitted `task_envelope_sha256` and `target_snapshot`, and invalidate the run
if HEAD or dirty state changes. A future release should make every session
recompute the envelope and repository snapshot, emit `CMUX_RESULT_INVALID` on a
mismatch, and stop before work. Workers report only to their own lead; each
orchestrator talks only to its own lead.

Every run-owned surface command is wrapped in a macOS Seatbelt profile that
denies target-repository writes for its complete process tree. Raw CMUX surfaces
and separately launched same-user processes are outside this boundary.

After the no-replace topology gate releases, supervisors perform provider-auth
preflight and short liveness checks. Each immutable readiness receipt binds the
run, team, role, workspace ref, surface ref, and target snapshot. All receipts
must arrive within `--readiness-timeout-seconds 30`, then survive the
`--readiness-settle-seconds 1.0` early-exit window. This does not prove a
completed model turn or available provider quota.

## 4. Seal results before reveal

Each orchestrator writes a canonical JSON payload outside the sealed root, then
submits it exactly once through the helper. The injected startup instruction is
equivalent to:

```bash
python3 scripts/sealed_results.py submit \
  --root "$SEALED_ROOT" --team codex \
  --token-file "$SEALED_ROOT/capabilities/codex" \
  --input /tmp/codex-result.json
```

The Claude arm substitutes `claude` in the team and token-file path. Plaintext
capabilities live only in per-team mode-`0400` files, never argv. The task
envelope and spawn receipt contain no plaintext tokens, and the helper contract
persists only capability hashes.

The coordinator checks receipts without payloads:

```bash
python3 scripts/sealed_results.py status --root "$SEALED_ROOT"
```

When and only when both valid submissions exist, it reveals both together:

```bash
python3 scripts/sealed_results.py reveal --root "$SEALED_ROOT"
```

Before that point, summaries, progress comparisons, and scores remain withheld.
If launch or readiness fails, the coordinator atomically invalidates the seal
before rollback. Invalidation dominates late submission races: status remains
`valid: false`, while submit, expire, and reveal fail closed. Supervisors observe
the marker and TERM/KILL their whole provider process group; failed workspace
closes remain in the invalid receipt.

For new contracts, `--timeout-minutes` defaults to `20`. At or after the stored
deadline, expire one missing arm with a typed infrastructure result:

```bash
python3 scripts/sealed_results.py expire \
  --root "$SEALED_ROOT" \
  --team claude \
  --reason-code provider_subscription_limit \
  --message "Provider limit prevented completion before deadline"
```

Expiration is impossible before the deadline, on a contract without a deadline,
or over an existing submission. A model cannot submit at or after its deadline.
The expiration marker completes the pair so the coordinator can reveal both
records together; it is infrastructure evidence, not a model-quality loss.

Required payload fields: final answer or patch reference; evidence and source
provenance; commands and results; elapsed time; actual model route; worker
agreements and dissents; limitations; envelope/result hashes.

## 5. Adjudicate objectively

Use a predeclared rubric. For coding, score reproduced acceptance tests,
regressions, correctness, security, minimality, maintainability, evidence, and
unnecessary churn before wall time. For science, score source fidelity,
derivation and dimensional validity, statistical checks, uncertainty
calibration, reproducibility, claim consistency, and preserved disagreement.

The adjudicator returns per-metric evidence, a weighted total, and one of
`A`, `B`, `tie`, or `no-decision`. It must distinguish model failure from
infrastructure failure and may not award correctness for confidence, verbosity,
majority vote, or speed alone.

## Capability-sealing limit

This protocol's sealing is procedural. Result files are write-once/read-only
and capabilities are mode-`0400`, but their paths are not private from processes
owned by the same macOS user. Such sessions may technically inspect sibling
files, processes, cmux surfaces, or You.md messages. Seatbelt constrains the
launcher-owned surface trees; it does not govern raw CMUX or other same-user
processes. For hostile or publish-grade blind evaluation, use a run-scoped CMUX
broker plus separate OS accounts or VMs/containers with enforced mount/network
rules, or a server-side blind evaluation service.
