# Control surfaces and endpoints

This document separates commands that exist in the installed tools from the
atomic claim contract that You.md still needs. It is intentionally explicit so
docs do not imply a safety guarantee that has not shipped.

## cmux local control plane (available)

cmux exposes a local Unix socket, normally
`~/.local/state/cmux/cmux.sock`. The launcher uses these CLI/RPC surfaces:

| Purpose | CLI surface |
|---|---|
| Health / caller context | `cmux identify --json` |
| Windows | `cmux list-windows --json`, `cmux new-window` |
| Workspace creation | `cmux workspace create --window ... --cwd ... --layout ... --json` |
| Workspace discovery | `cmux workspace list --window ... --json` |
| Named surfaces | `cmux list-pane-surfaces --workspace ...` |
| Send and submit | `cmux send --surface ...`, `cmux send-key --surface ... enter` |
| Verify result | `cmux read-screen --surface ... --scrollback` |
| Completion stream | `cmux events --name agent.hook --name notification.created --reconnect` |
| Identity / status | `cmux rename-tab`, `cmux set-status`, `cmux workspace-action set-color` |
| Scoped teardown | `cmux close-surface`, `cmux workspace close` |

The layout API maps to cmux RPC methods such as `workspace.create`,
`surface.send_text`, `surface.send_key`, `surface.read_text`, and
`notification.create`. Positional refs are rediscovered; the spawn receipt keeps
the stable window UUID.

## Codex and Claude subscription surfaces (available)

| Provider | Login | Session launch |
|---|---|---|
| OpenAI / ChatGPT | `codex login`, `codex login status` | `codex --model ... --config model_reasoning_effort=...` |
| Anthropic / Claude.ai | `claude auth login`, `claude auth status` | `claude --model ... --effort ...` |

No provider REST endpoint is called by this package and no provider API key is
loaded by the launcher.

## You.md CLI and API surfaces (available in v0.10.0)

| Purpose | CLI | HTTP/API |
|---|---|---|
| Authenticate | `youmd login`, `youmd whoami` | existing You.md auth/session rail |
| Send coordination message | `youmd agent send ... --channel work-claims --entity-type work-claim --entity-id <id>` | `POST /api/v1/me/agent-bus/messages` |
| Read coordination messages | `youmd agent inbox --channel work-claims --limit 100 --json` | `GET /api/v1/me/agent-bus/messages?channel=work-claims&limit=100` |
| Realtime session | `youmd sync --live --daemon` | `POST /api/v1/me/realtime-sync/session` |
| Inspect connected agents | `youmd agents` | agent/realtime status rail |
| Spawn/watch workers | `youmd orchestrate spawn|list|logs|stop|watch|run` | local supervisor plus agent-bus/remote-command rails |
| Cross-machine message | `youmd agent send --target-host ...` | same agent-bus endpoint with target metadata |

The launcher uses only the agent-bus GET/POST pair for cooperative claims. A
claim body includes schema, claim ID, owner, repo, project, base SHA, state, and
expiry. It reads before posting and reads back after posting; the oldest active
claim wins.

## Work Claims API (required, not yet available)

The agent bus is durable messaging, not an atomic compare-and-set lease. The
production-grade contract should add:

| Method | Endpoint | Semantics |
|---|---|---|
| `POST` | `/api/v1/me/work-claims/acquire` | Atomically acquire normalized scope or return the conflicting owner/claim |
| `GET` | `/api/v1/me/work-claims` | List active/recent claims by project, repo, host, agent, branch, or PR |
| `GET` | `/api/v1/me/work-claims/{claimId}` | Read one claim, owner, scope, lease, heartbeat, branch, and proof |
| `POST` | `/api/v1/me/work-claims/{claimId}/renew` | Owner-only lease extension with expected-version compare-and-set |
| `POST` | `/api/v1/me/work-claims/{claimId}/release` | Owner-only release with commit/PR/result receipt |
| `POST` | `/api/v1/me/work-claims/check` | Read-only overlap analysis for a proposed normalized scope |
| `GET` | `/api/v1/me/work-claims/events` | Reconnectable claim acquired/renewed/released/expired stream |

Required acquire input:

```json
{
  "project": "bigbounce",
  "repo": "github.com/owner/repo",
  "baseSha": "<git sha>",
  "taskId": "paper3:review-round",
  "intent": "read|mutate",
  "paths": ["pipelines/p3_anomaly_engine/**"],
  "owner": {"host": "...", "agent": "...", "session": "..."},
  "leaseSeconds": 7200,
  "branch": "codex/cmux/..."
}
```

The server must normalize equivalent repo URLs and path globs, reject overlapping
mutation claims atomically, permit compatible read claims, expire abandoned
leases, and never expose credentials in claim metadata. A waiting agent should
subscribe to events and re-check scope when the conflicting claim attaches a
commit or PR, rather than busy-polling or duplicating the work.

## MCP surface (required parity)

When Work Claims ships, the You.md MCP should expose the same contract as tools:

- `check_work_overlap`
- `acquire_work_claim`
- `renew_work_claim`
- `release_work_claim`
- `list_work_claims`
- `watch_work_claims`

CLI, MCP, HTTP, daemon, and web UI must share one backend claim record and one
schema. Do not implement separate “cmux claims” and “You.md claims.”
