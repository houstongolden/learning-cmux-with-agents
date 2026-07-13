# Model matrix

Defaults prioritize subscription access, judgment at the top, and cheaper
independent workers. Every model ID can be overridden on the launcher command
line because account-visible aliases evolve.

Direct subscription smokes on 2026-07-13 verified `gpt-5.6-sol` at high effort
through Codex and `claude-opus-4-8` at high effort through Claude Max. Worker
models remain configuration choices; the default Codex worker uses the verified
`gpt-5.6-sol` family at low effort rather than assuming `gpt-5.4-mini` access.

| Role | CLI | Default model | Effort | Default permissions | Why |
|---|---|---|---|---|---|
| Primary orchestrator | Codex | `gpt-5.6-sol` | high | cmux control; no repo edits by contract | final routing and synthesis |
| Comparison orchestrator | Claude Code | `claude-opus-4-8` | high | cmux control; no repo edits by contract | independent Anthropic benchmark |
| Lead | Codex | `gpt-5.6-sol` | medium | cmux control; no repo edits by contract | decompose, dispatch, reconcile |
| Explorer | Codex | `gpt-5.6-sol` | low | read-only / isolated worktree in future mutate mode | fast code/data mapping |
| Reviewer | Claude Code | `sonnet` | high | plan / isolated worktree in mutate mode | adversarial review |
| Tester | Codex | `gpt-5.6-sol` | low | read-only / isolated worktree in future mutate mode | reproduction and proof |
| Comparator | Claude Code | `sonnet` | high | plan / isolated worktree in mutate mode | independent candidate |

The launcher does not claim that a model exists on every subscription. Validate
the model ID exposed to the signed-in account, then override only that flag:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature \
  --codex-orchestrator-model gpt-5.6-sol \
  --codex-lead-model gpt-5.6-sol \
  --codex-worker-model gpt-5.6-sol \
  --claude-orchestrator-model claude-opus-4-8 \
  --claude-worker-model sonnet
```

## Mirrored experiment matrix

| Variable | Arm A | Arm B |
|---|---|---|
| Orchestrator | Codex `gpt-5.6-sol`, high | Claude `claude-opus-4-8`, high |
| Lead/workers | fixed shared matrix | identical fixed shared matrix |
| Task envelope / HEAD / dirty digest | identical | identical |
| Startup injection / permissions / tools | identical | identical |
| Result visibility before seal | own arm only | own arm only |

Score both arms only after the two result tokens are sealed. Coding metrics are
acceptance-test pass rate, regression failures, correctness findings, security
findings, diff surface, evidence reproducibility, unnecessary churn, elapsed
time, and subscription usage if observable. Science metrics are source fidelity,
derivation validity, dimensional/statistical checks, uncertainty calibration,
reproducibility, anomaly/claim consistency, and preserved dissent. Speed is a
tie-breaker after correctness, never the primary score.
