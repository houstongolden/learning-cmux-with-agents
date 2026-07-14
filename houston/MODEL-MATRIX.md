# Model matrix

Defaults prioritize subscription access, frontier judgment at the top, and
lower-cost/faster workers. Every model ID can be overridden on the launcher
command line because account-visible aliases evolve. The launcher invokes local
subscription CLIs only: it does not load `.env`, inject provider keys, or make
direct usage-billed OpenAI/Anthropic API requests.

The local Codex model cache exposes Sol, Terra, Luna, and Codex Spark. Claude
Fable 5 is reserved for an independent checkpoint or sealed A/B director when
the Claude subscription has quota; it is not a routine worker dependency.

| Role | CLI | Default model | Effort | Default permissions | Why |
|---|---|---|---|---|---|
| Primary orchestrator | Codex | `gpt-5.6-sol` | high | cmux control; no repo edits by contract | final routing and synthesis |
| Comparison/checkpoint orchestrator | Claude Code | `claude-fable-5` | high | cmux control; no repo edits by contract | independent Anthropic judgment when available |
| Lead | Codex | `gpt-5.6-terra` | medium | cmux control; no repo edits by contract | decompose, dispatch, reconcile |
| Explorer | Codex | `gpt-5.6-luna` | medium | read-only / isolated worktree in future mutate mode | fast code/data mapping |
| Reviewer | Codex | `gpt-5.6-luna` | medium | read-only / isolated worktree in future mutate mode | adversarial review |
| Tester/poller | Codex | `gpt-5.3-codex-spark` | low | read-only / isolated worktree in future mutate mode | reproduction, checks, and polling |
| Comparator | Codex | `gpt-5.6-luna` | medium | read-only / isolated worktree in future mutate mode | independent candidate |

The launcher does not claim that a model exists on every subscription. Validate
the model ID exposed to the signed-in account, then override only that flag:

```bash
python3 scripts/spawn_subscription_fleet.py \
  --repo /absolute/path/to/repo \
  --project feature \
  --codex-orchestrator-model gpt-5.6-sol \
  --codex-lead-model gpt-5.6-terra \
  --codex-worker-model gpt-5.6-luna \
  --codex-tester-model gpt-5.3-codex-spark \
  --claude-orchestrator-model claude-fable-5
```

## Mirrored experiment matrix

| Variable | Arm A | Arm B |
|---|---|---|
| Orchestrator | Codex `gpt-5.6-sol`, high | Claude `claude-fable-5`, high |
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
