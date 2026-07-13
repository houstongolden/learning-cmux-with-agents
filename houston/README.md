# Houston's subscription-first cmux lab

This additive package turns the upstream five-pane cmux pattern into a local
Codex + Claude Code lab that uses existing ChatGPT and Claude.ai logins. It does
not load `.env`, require provider API keys, or change upstream files.

The default single-arm topology is:

```text
primary orchestrator: Codex / gpt-5.6-sol / high (inside cmux)
                                |
                                v
+-------------------------------+-------------------------------+
| lead: Codex / gpt-5.6-sol     | explorer | reviewer          |
|                               |----------+-------------------|
| drives the four named panes   | tester   | comparator        |
+-------------------------------+-------------------------------+
```

The lead is the only team surface the primary orchestrator talks to. The lead
fans work out to four workers and reconciles their answers. In the default
`compare` mode every worker is read-only. The lead and primary orchestrator are
instructed to coordinate only; they must not edit the target checkout.

## Start here

```bash
python3 scripts/spawn_subscription_fleet.py --validate

python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-or-bug-name \
  --dry-run

python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-or-bug-name
```

The live command boots commands declaratively inside cmux. Use
`--orchestrator none` to create only the team, or `--orchestrator claude` to run
the comparison orchestrator with Claude Opus 4.8 at high effort.

For a real Codex-versus-Claude comparison, do not point two orchestrators at one
already-active lead. Create mirrored A and B arms from one immutable task
envelope. The arms use identical lead/worker models, prompts, permissions,
repository state, and acceptance checks; only the orchestrator differs. Each
arm submits through the sealed-results helper, and the adjudicator reveals
either payload only after both submission receipts exist. See
[MIRRORED-AB-PROTOCOL.md](MIRRORED-AB-PROTOCOL.md).

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-ab \
  --mirrored \
  --task-file /absolute/path/to/task.md
```

The launcher prints the task-envelope and spawn-receipt paths; the sealed root
is the nested `sealed_results.root` field. The shared envelope and receipt
contain capability hashes/references, not plaintext submission tokens.

Codex trust is also pinned per process. Trust for the exact target checkout was
not inherited reliably from a trusted parent directory, so every generated
Codex command includes both `-C <absolute-repo>` and
`-c 'projects."<absolute-repo>".trust_level="trusted"'`. This avoids an
interactive trust prompt without changing global Codex configuration.

The first mirrored attempt reached this trust prompt at the 20-minute mark and
is diagnostic-only. It did not produce a clean Codex-versus-Claude comparison;
rerun both fresh arms with the per-invocation trust fix before drawing model
conclusions.

Mutation is currently fail-closed:

```bash
python3 scripts/spawn_subscription_fleet.py --repo /absolute/path/to/repo \
  --project scoped-feature --mode mutate
```

This refuses to launch because the current You.md agent bus is not an atomic
lease. The required claim API and isolated-worktree rollout are specified in
[ENDPOINTS.md](ENDPOINTS.md). Read [SETUP.md](SETUP.md) before the first live run.

## Documents

- [SETUP.md](SETUP.md) — install, login, hooks, dry-run, and live commands.
- [ARCHITECTURE.md](ARCHITECTURE.md) — local hierarchy, collision policy, and
  cross-computer evolution.
- [ENDPOINTS.md](ENDPOINTS.md) — cmux and You.md control surfaces, including
  what exists today versus the atomic lease API still needed.
- [MODEL-MATRIX.md](MODEL-MATRIX.md) — model/effort/permission defaults and
  override examples.
- [MIRRORED-AB-PROTOCOL.md](MIRRORED-AB-PROTOCOL.md) — clean-room task
  envelopes, startup injection, sealed submissions, and adjudication.
- [runs/2026-07-13-bigbounce-readonly-review.md](runs/2026-07-13-bigbounce-readonly-review.md)
  — earlier CMUX dogfood diagnostics and the motivation for the corrected
  mirrored-A/B protocol; not a clean orchestrator comparison.

## Attribution

The declarative 50/50 lead + 2x2 worker layout, stable-window discovery, named
surface routing, and `cmux send` / `send-key` / `read-screen` control loop are
adapted from IndyDevDan's MIT-licensed
[`disler/learning-cmux-with-agents`](https://github.com/disler/learning-cmux-with-agents).
This `houston/` package replaces the upstream API-backed `pi` defaults with
subscription-authenticated Codex and Claude Code sessions and adds collision
guardrails for You.md projects.
