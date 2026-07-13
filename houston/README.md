# Houston's subscription-first cmux lab

This additive package turns the upstream five-pane cmux pattern into a local
Codex + Claude Code lab that uses existing ChatGPT and Claude.ai logins. It does
not load `.env`, require provider API keys, or change upstream files.

The default topology is:

```text
primary orchestrator: Codex / gpt-5.6-sol / high (outside the team workspace)
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

The live command creates the lead + four-worker cmux workspace and starts the
primary Codex orchestrator in the invoking terminal. Use
`--orchestrator none` to create only the team, or `--orchestrator claude` to run
the comparison orchestrator with Claude Opus 4.8 at high effort.

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

## Attribution

The declarative 50/50 lead + 2x2 worker layout, stable-window discovery, named
surface routing, and `cmux send` / `send-key` / `read-screen` control loop are
adapted from IndyDevDan's MIT-licensed
[`disler/learning-cmux-with-agents`](https://github.com/disler/learning-cmux-with-agents).
This `houston/` package replaces the upstream API-backed `pi` defaults with
subscription-authenticated Codex and Claude Code sessions and adds collision
guardrails for You.md projects.
