# Mirrored launcher acceptance — diagnostic run

Date: 2026-07-13 (America/Los_Angeles)
Run ID: `20260713T231542Z-8b6bc0c305ff`
Project: `cmux-mirror-acceptance-20260713`

## Verdict

This completed run is **diagnostic-only**. It is not a clean Codex-versus-Claude
A/B comparison and establishes no model winner.

Claude completed and sealed a `PASS` result against the clean launch snapshot in
about 162 seconds. Codex was blocked at the exact-project trust prompt until the
20-minute timeout. The operator accepted the trust prompts only to salvage
diagnostic evidence. During that salvage window, subsequent launcher fixes made
the repository snapshot dirty. Codex then correctly returned
`CMUX_RESULT_INVALID` after about 430 seconds of post-salvage work because its
live repository no longer matched the immutable clean envelope.

The Claude payload remained sealed while Codex was blocked. No result payload
was revealed early; both submissions existed before the paired reveal.

## Bound inputs and artifacts

- Repository: `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents`
- Launch HEAD: `c24b2d6cc5d81b1663d31d3cc8c2a7578d2e38e2`
- Launch state: clean
- Snapshot SHA-256:
  `6283a2482d689a1127d7e71c02e0a4b2cae5c03d6635131011e755174bc6f74c`
- Task SHA-256:
  `8b6bc0c305ffec4b2a13079ee49495e5f5c328ea6cd5062f1d6320fab0fccfe9`
- Envelope SHA-256:
  `b758ee4970dee9c3ad8c5956b831bf8ac2a5fc54783407f69c3408dc275e6dd3`
- Seal contract SHA-256:
  `31b49c9d5c812152b4081755d5d3805182668f862d5da01145a4af05be3ceac0`
- Receipt:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/cmux-mirror-acceptance-20260713.20260713T231542Z-8b6bc0c305ff.subscription-spawn.json`
- Envelope:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/cmux-mirror-acceptance-20260713.20260713T231542Z-8b6bc0c305ff.task-envelope.json`
- Sealed-results root:
  `/Users/houstongolden/Desktop/CODE_YOU/learning-cmux-with-agents/.team/sealed-results/cmux-mirror-acceptance-20260713/20260713T231542Z-8b6bc0c305ff`

No capability value, provider credential, or raw secret is recorded here.

## Models and authentication

| Role | Model / effort |
|---|---|
| Codex orchestrator | `gpt-5.6-sol`, high |
| Claude orchestrator | `claude-opus-4-8`, high |
| Both leads | `gpt-5.6-sol`, medium |
| Explorer + tester | `gpt-5.6-sol`, low |
| Reviewer + comparator | Claude `sonnet`, medium |

All model sessions used existing ChatGPT and Claude.ai/Claude Max subscription
authentication. The launcher injected no provider API keys.

## Sealed submissions

| Arm | Outcome | Timing | Bytes | Sealed SHA-256 |
|---|---|---:|---:|---|
| Claude | `PASS` on the clean bound snapshot | ~162s | 7,597 | `889bbd8c5fefbd1db5b4c00dab95c2893a9d97b9f51e9b981ea42cdc6578c18e` |
| Codex | `CMUX_RESULT_INVALID` after snapshot drift | ~430s post-salvage | 9,813 | `ecfb1c5b4e049687db7cb81074ec6f4c8584228fea57a0f0131979b57fdf57aa` |

The sealing boundary was procedural/capability isolation only. It protected the
normal helper workflow from accidental early reveal; it did not protect against
a malicious process running as the same macOS user.

## Finding overlap and disagreement

Both orchestrators identified the same two core launcher risks:

1. sealed-result/contract publication was not atomic; and
2. multi-workspace launch was nontransactional and lacked a readiness barrier.

They disagreed on severity. Codex rated both issues `HIGH`; Claude rated atomic
publication `MEDIUM` and nontransactional launch `LOW-MEDIUM`. Codex additionally
flagged the absence of an explicit readiness gate and the fact that read-only
behavior for controller roles was prompt-enforced rather than a hard sandbox
boundary.

Those differences are useful diagnostic evidence, but they cannot be used to
rank the orchestrators because the trust stall and later snapshot drift broke
the mirrored conditions.

## Remediation status

- Exact-project Codex trust fix: committed as `e3c5237`.
- Trust/diagnostic documentation: committed as `db5ad83`.
- Atomic sealed-result/contract publication: fixed in `29d64b6`. This was not a
  task-envelope publication defect.
- Transactional four-workspace topology barrier and run-owned rollback: fixed
  in `0e3b0d4`.
- Focused remediation suite: `15/15` tests passing.

The barrier holds all four workspaces until cmux topology verification succeeds.
Failure keeps the gate closed, rolls back only refs created by that run, and
records `valid: false` plus error/rollback evidence in the receipt. This proves
topology readiness only; it does not prove every model child completed
onboarding or started reasoning.

Remaining blockers are post-release child readiness, a hard boundary replacing
prompt-only controller read-only behavior, deadline/timeout adjudication, and a
fresh clean mirrored rerun.

A valid acceptance rerun must start both arms fresh from one unchanged snapshot,
clear Codex trust without interaction, hold results sealed until both submit,
and avoid operator salvage actions.
