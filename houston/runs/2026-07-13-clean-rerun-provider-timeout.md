# Clean mirrored rerun — provider timeout

Date: 2026-07-13 (America/Los_Angeles)
Project: `cmux-mirror-clean-20260713`
Run ID: `20260714T000136Z-024658000-bfee86cfcc83`

## Verdict

Launcher topology, repository binding, and noninteractive Codex trust acceptance
passed on a clean snapshot. The model comparison did not complete because the
Claude arm hit its weekly subscription limit. There was no reveal and there is
no winner.

This run predates the deadline-aware sealed-results contract. Its contract has
no deadline, so the missing Claude slot cannot be retroactively expired. The
8,506-byte Codex result remains sealed until the pair can be completed; it was
not read early.

## Bound inputs and artifacts

- Repository: `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents`
- Launch HEAD: `ce3840759f2756c4b84c7cf3bc5a1c0e939ec6fc`
- Launch state: clean
- Snapshot SHA-256:
  `767b9b0f2e2c53e83d48c95b9c9e8e363c413422553ef9bf2eae7a3664fa7d2f`
- Task SHA-256:
  `bfee86cfcc8372f4f8e16529204c8693ba6b3432e6209a6c019b98dd1563c4fc`
- Envelope SHA-256:
  `44748dfe2072f0cba3b1e20c3a63c46b2667c2a212f525a5e7081c635c88aefd`
- Seal contract SHA-256:
  `2e1afe888b99fc485c3e94c4f78cec2620fec25a6322dce0f2e92b6103a7fafb`
- Receipt:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/cmux-mirror-clean-20260713.20260714T000136Z-024658000-bfee86cfcc83.subscription-spawn.json`
- Envelope:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/cmux-mirror-clean-20260713.20260714T000136Z-024658000-bfee86cfcc83.task-envelope.json`
- Sealed-results root:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/sealed-results/cmux-mirror-clean-20260713/20260714T000136Z-024658000-bfee86cfcc83`

No capability value, API key, provider credential, or raw secret is recorded.

## Topology and trust acceptance

The shared gate was released only after the launcher verified all four expected
workspaces:

| Ref | Workspace |
|---|---|
| `workspace:12` | Codex team |
| `workspace:13` | Claude team |
| `workspace:14` | Codex orchestrator |
| `workspace:15` | Claude orchestrator |

All seven Codex surfaces reached `TRUST_OK` without interaction: lead, explorer,
and tester in each of the two team workspaces, plus the Codex orchestrator. This
accepts the exact-project per-invocation trust fix; it does not establish general
post-release child readiness for every provider process.

## Models and subscription authentication

| Role | Model / effort |
|---|---|
| Codex orchestrator | `gpt-5.6-sol`, high |
| Claude orchestrator | `claude-opus-4-8`, high |
| Both leads | `gpt-5.6-sol`, medium |
| Explorer + tester | `gpt-5.6-sol`, low |
| Reviewer + comparator | Claude `sonnet`, medium |

All sessions used existing ChatGPT and Claude.ai/Claude Max subscription
authentication. The launcher injected no provider API keys.

## Sealed-result status

- Codex submitted `8,506` bytes with SHA-256
  `c1cbc6695d73343e055a60585362489187f4b6926318915d88cc6cd5338a08ac`.
- Claude did not submit within 15 minutes. Claude Code reported the weekly
  subscription limit, resetting July 15 at 7:00 AM
  `America/Los_Angeles`.
- `status` therefore remained `ready: false`, missing `claude`.
- `reveal` was not performed, and no model result was exposed.

Provider exhaustion is an infrastructure outcome, not evidence that Codex
outperformed Claude.

## Deadline remediation added after this run

New mirrored launches accept `--timeout-minutes` with a default of `20` and bind
the resulting UTC deadline into the envelope and seal contract. After that
deadline, the coordinator can fill one missing slot exactly once:

```bash
python3 scripts/sealed_results.py expire \
  --root "$SEALED_ROOT" \
  --team claude \
  --reason-code provider_subscription_limit \
  --message "Weekly subscription limit prevented completion before deadline"
```

The helper rejects early expiration, expiration without a contract deadline,
replacement of an existing result, and late model submission. Expiration seals
a typed `infrastructure_failure`, allowing paired reveal without declaring a
model loss. Deadline-focused coverage reached `19/19` at that remediation
checkpoint.

## Launch hardening added after this run

Subsequent commits `e2e2496` and `ec6f48e` add fail-closed launch supervision;
they do not alter this historical run or reveal its Codex result. Every
run-owned surface process tree is now Seatbelt-denied target writes, while raw
CMUX and separately launched same-user processes remain outside the boundary.
The topology gate uses atomic no-replace publication.

Supervisors perform provider-auth preflight, short liveness validation, and a
one-second early-exit settle. Their immutable receipts bind run, team, role,
workspace, surface, and snapshot. This still does not prove a completed model
turn or provider quota availability. Failed launch/readiness atomically
invalidates the sealed contract before rollback; supervisors then TERM/KILL the
full process group, and receipts retain any workspace-close failures.
Capabilities now use per-team mode-`0400` token files instead of argv values.
The combined focused suite is `28/28` passing.

## Remaining blockers

- provider completed-turn readiness, including quota availability;
- a run-scoped CMUX broker or stronger hostile-model isolation;
- a fresh clean mirrored rerun after the Claude subscription reset.

The security boundary remains procedural/capability isolation only. It prevents
accidental early reveal through the helper interface, not inspection by a
malicious process running as the same macOS user.
