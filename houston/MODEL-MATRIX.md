# Model matrix

Defaults prioritize subscription access, judgment at the top, and cheaper
independent workers. Every model ID can be overridden on the launcher command
line because account-visible aliases evolve.

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

## Suggested experiments

1. Give both orchestrators the same saved spawn receipt and task, one at a time.
2. Keep the lead/workers, base SHA, claim scope, and acceptance test identical.
3. Record wall time, tool calls, worker disagreement, reproduced tests, diff
   size, and reviewer findings.
4. Have the non-producing orchestrator score the result blind where possible.
5. For science, score source fidelity, derivation validity, uncertainty, and
   preserved dissent; never reduce evaluation to speed or majority vote.
