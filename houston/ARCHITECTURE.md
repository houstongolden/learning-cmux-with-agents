# Architecture

## Runtime hierarchy

The lab deliberately keeps three distinct levels:

1. **Primary orchestrator** — a high-judgment Codex or Claude Code session in
   the invoking terminal. It talks only to the lead.
2. **Lead** — a Codex session in the left half of the cmux workspace. It owns
   decomposition, worker routing, result comparison, and the final report.
3. **Workers** — four named panes in a 2x2 grid. They explore, review, test, and
   independently compare solutions.

cmux is the observable transport. Each agent is a real terminal surface. The
control loop is `send` text, `send-key enter`, wait for a notification event,
then `read-screen` to verify the actual result. The spawn receipt records the
stable window UUID and workspace name; agents rediscover positional surface
refs immediately before use because refs can renumber.

## Read-only is the default

`compare` mode is designed for multi-model evaluation and science/review work:

- Codex workers launch with the `read-only` sandbox and approval policy `never`.
- Claude workers launch in `plan` permission mode.
- The lead and primary orchestrator are told not to edit the checkout. They need
  cmux socket access so their boundary is a role contract, while the worker
  boundary is enforced by each CLI.
- No `.env` file or provider key is injected into any workspace.

This is the right first mode for BigBounce truth audits: independent derivation,
literature/context inspection, adversarial review, and synthesis can happen in
parallel without multiple agents rewriting the paper.

## Planned mutation mode

Mutation mode must add two separate collision controls before this launcher will enable it:

1. **Intent claim:** a cooperative You.md claim for the project + scoped work ID
   is checked before any write-enabled sessions or worktrees are created.
2. **Filesystem isolation:** every worker gets a sibling git worktree and a
   unique `codex/cmux/<project>-<run>-<role>` branch.

The lead still does not write. It assigns non-overlapping files/contracts, asks
workers to commit on their own branches, compares results, and tells the primary
orchestrator which branch should be reviewed or integrated. There is no shared
writable checkout.

The root checkout must be clean before mutation mode. This prevents the four
worktrees from silently starting from a commit that does not include important
local changes.

## You.md coordination layers

The desired architecture is layered rather than one giant daemon:

| Layer | Purpose | Current mechanism |
|---|---|---|
| Local process | Observe and drive panes | cmux Unix socket + events |
| Local repository | Prevent file/index collision | git worktree + branch per worker |
| Same machine | Publish task intent/status | You.md agent bus |
| Cross machine | Route messages and workers | You.md realtime daemon + `youmd orchestrate` |
| Git host | Durable integration state | branches, commits, PRs, CI |
| Future lease rail | Atomic ownership/renew/release | proposed Work Claims API |

Agents should not merely ask “is another process alive?” They should compare
normalized work scope: project slug, repository identity, base commit, task or
issue ID, path globs, intended mutation, claim owner, expiry, branch, and PR.
That allows useful parallelism on the same project while rejecting true overlap.

## Result comparison

The four worker roles are intentionally different:

- **Explorer:** maps the code/paper/data and finds likely root causes.
- **Reviewer:** challenges assumptions, safety, truth, and missing cases.
- **Tester:** identifies reproducible checks and evaluates candidate fixes.
- **Comparator:** creates an independent solution and scores trade-offs.

For science, the lead should retain disagreement and evidence provenance rather
than vote by majority. For coding, it can rank candidates by reproduced tests,
minimal diff, correctness, and maintainability. “Fastest answer” is telemetry,
not a correctness criterion.

## Cross-computer evolution

cmux should remain the local visual control plane. You.md should become the
durable coordination plane across cmux, Codex desktop, Claude Code, Cursor, and
other machines:

1. acquire/renew a work claim;
2. create an isolated checkout;
3. publish plan, base SHA, path scope, branch, and expected proof;
4. stream status and completion receipts;
5. attach commit/PR/CI evidence;
6. release the claim;
7. wake waiting agents whose dependency is now available.

That separation lets local cmux sessions stay fast and visible while You.md
answers the portfolio-level question: “who is working on what, where, and what
must finish before I begin?”
