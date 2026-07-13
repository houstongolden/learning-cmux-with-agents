# Setup

## 1. Install and open cmux

```bash
brew tap manaflow-ai/cmux
brew install --cask cmux
open -a cmux
```

If `cmux` is not on `PATH` outside the app, link the bundled CLI:

```bash
ln -sf /Applications/cmux.app/Contents/Resources/bin/cmux /opt/homebrew/bin/cmux
```

Open **cmux Settings -> Automation** and allow socket control for the terminal
that will run the orchestrator. The launcher checks `cmux identify --json` and
will open cmux automatically, but it does not silently weaken automation
settings.

Install completion hooks so the lead can wait on events instead of aggressively
polling panes:

```bash
cmux hooks setup --agent codex --yes
```

Claude Code's cmux integration is injected by cmux and does not need a separate
hook install.

## 2. Use subscription logins, not API keys

Sign Codex into ChatGPT:

```bash
codex login
codex login status
```

Sign Claude Code into Claude.ai:

```bash
claude auth login
claude auth status
```

Sign into You.md for cross-agent messages and mutation claims:

```bash
youmd login
youmd whoami
```

The launcher deliberately does not pass `--env-file`, inspect `.env`, or read
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`. If a CLI chooses an API credential from
your existing shell environment, unset it in that shell before launch so the
interactive subscription login is authoritative.

## 3. Validate without launching anything

From this repository:

```bash
python3 scripts/spawn_subscription_fleet.py --validate
```

This validates the layout schema and checks that `cmux`, `codex`, `claude`,
`youmd`, and `git` are present. It neither opens cmux nor calls a model.

Then render a project-specific plan:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /Users/houstongolden/Desktop/CODE_YOU/bigbounce \
  --project p3-review-round \
  --dry-run
```

Dry-run prints the final layout, model routing, primary command, comparison
command, and (for mutation mode) proposed worktrees. It performs no writes,
does not acquire a claim, and does not start model sessions.

## 4. Start the read-only comparison fleet

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /Users/houstongolden/Desktop/CODE_YOU/bigbounce \
  --project p3-review-round
```

The default primary orchestrator is:

```bash
codex -m gpt-5.6-sol -c 'model_reasoning_effort="high"' ...
```

To run the same topology with the Anthropic comparison orchestrator:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-name \
  --orchestrator claude
```

That launches `claude --model claude-opus-4-8 --effort high`. The exact model
IDs are flags, so account-specific aliases can be supplied without editing the
template:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-name \
  --codex-orchestrator-model gpt-5.6-sol \
  --claude-orchestrator-model claude-opus-4-8
```

## 5. Mutation is intentionally blocked today

The command exists to prove the guardrail:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-name \
  --mode mutate
```

It exits before creating sessions or worktrees. The current You.md agent bus
provides durable coordination messages, not a server-atomic compare-and-set
lease. The atomic API and isolated-worktree rollout needed to enable mutation
are specified in [ENDPOINTS.md](ENDPOINTS.md).

## Verified cmux 0.64.17 caveat on this host

On macOS 26.5, external `allowAll` socket control created workspaces but repeated
`send` and `read-screen` calls timed out and required a cmux restart. This
launcher therefore boots commands declaratively and puts both orchestrator and
team inside cmux. Treat outside-terminal screen driving as unverified until a
cmux update and diagnostics pass prove it again.

## Troubleshooting

- `cmux failed to start`: open the app and check **Settings -> Automation**.
- `model not available`: pass the account-visible model ID with the matching
  `--*-model` flag; do not add a provider key.
- worker stops for permissions: keep the default read-only mode, or use mutation
  mode so each worker gets a writable worktree.
- duplicate work detected: stop before dispatching, inspect the claim message
  and the related branch/PR, then choose a non-overlapping scope.
- stale claim: wait for its expiry or ask the owning agent to post a release;
  never impersonate the owner.
