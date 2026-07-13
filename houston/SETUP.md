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

On an Intel/Homebrew or locked `/opt/homebrew/bin` installation, use a writable
directory already on `PATH`; `/usr/local/bin/cmux` with `sudo ln -sf ...` is a
fallback, not the preferred Apple Silicon path.

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

## 4. Start one read-only fleet

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

## 5. Run a mirrored A/B comparison

Put the exact task in a UTF-8 file, then let the launcher generate the immutable
envelope and both arms in one operation:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature-ab \
  --mirrored \
  --task-file /absolute/path/to/task.md
```

The envelope binds the task to repository HEAD, the tracked staged+unstaged
binary diff, and an ordered untracked path/type/mode/content-or-symlink-target
hash manifest. If the checkout changes after envelope creation, invalidate the
run.

Start two fresh fleet workspaces from that same envelope:

- arm A: Codex `gpt-5.6-sol`, high-effort orchestrator;
- arm B: Claude `claude-opus-4-8`, high-effort orchestrator;
- both: equivalent lead/worker prompt templates and identical models, tools,
  permissions, time budget, and acceptance commands.

The launcher embeds the full task in both orchestrator startup commands. The
lead and four workers receive the same envelope path/hash in their startup
prompts, then wait for their own orchestrator/lead to dispatch the task. Do not
depend on outside-terminal `send` for initial orchestrator delivery. Each
orchestrator prepares a JSON payload and submits it through
`scripts/sealed_results.py submit`; it must not write directly into the sealed
root. Plaintext capability tokens are injected only into their owning startup
commands. The shared task envelope and spawn receipt do not contain them.

Copy the nested `sealed_results.root` path printed by the launcher, then observe
without revealing payloads:

```bash
SEALED_ROOT=/absolute/path/printed/by/the/launcher
python3 scripts/sealed_results.py status --root "$SEALED_ROOT"
```

Reveal is fail-closed until both submissions exist:

```bash
python3 scripts/sealed_results.py reveal --root "$SEALED_ROOT"
```

The exact envelope, submission, and scoring contracts are in
[MIRRORED-AB-PROTOCOL.md](MIRRORED-AB-PROTOCOL.md).

## 6. Mutation is intentionally blocked today

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
team inside cmux. In-cmux wrappers and native `cmux codex-teams` /
`cmux claude-teams` are the safe fallback. Treat outside-terminal screen driving
as unverified until a cmux update and diagnostics pass prove it again.

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
