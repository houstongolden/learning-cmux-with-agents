# Architecture

## Runtime hierarchy

The lab deliberately keeps three distinct levels:

1. **Primary orchestrator** — a high-judgment Codex or Claude Code session
   booted inside cmux. It talks only to the lead.
2. **Lead** — a Codex session in the left half of the cmux workspace. It owns
   decomposition, worker routing, result comparison, and the final report.
3. **Workers** — four named panes in a 2x2 grid. They explore, review, test, and
   independently compare solutions.

cmux is the observable transport. Each agent is a real terminal surface. The
normal control loop is `send` text, `send-key enter`, wait for a notification
event, then `read-screen` to verify the actual result. On this macOS 26.5 host,
cmux 0.64.17 outside-terminal `send`/`read-screen` repeatedly timed out even
though workspace creation succeeded. The current safe path is declarative
startup plus in-cmux control. The spawn receipt records the stable window UUID
and workspace name; agents rediscover positional surface refs immediately
before use because refs can renumber.

Codex process bootstrap also binds the exact repository twice: `-C <repo>` sets
the working root and a per-invocation
`projects."<repo>".trust_level="trusted"` override clears the interactive
trust gate. Exact-project trust was not inherited reliably from a trusted
parent. Keeping this on the generated command avoids global config mutation and
keeps both mirrored arms reproducible.

## Read-only is the default

`compare` mode is designed for multi-model evaluation and science/review work:

- Codex workers launch with the `read-only` sandbox and approval policy `never`.
- Claude workers launch in `plan` permission mode.
- Every run-owned surface command, including leads and primary orchestrators,
  runs under a macOS Seatbelt profile that denies writes below the target
  repository for that entire process tree. When this lab is itself the target,
  only its `.team` control state is exempted.
- No `.env` file or provider key is injected into any workspace.

This process boundary does not cover raw CMUX commands or other processes
launched separately by the same macOS user. It prevents accidental target
mutation by the supervised fleet; it is not hostile-model isolation or a
run-scoped CMUX authorization broker.

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

## Mirrored orchestrator A/B

A fair orchestrator comparison uses two clean-room arms. Arm A and arm B receive
the same immutable task envelope and equivalent fresh lead/worker sessions. The
lead/worker model routes, prompts, permissions, tools, task text, base SHA,
dirty-state digest, acceptance commands, and time budget stay fixed. Only the
orchestrator identity changes.

The same full task is embedded in both orchestrator startup commands,
eliminating delivery timing and external socket health as confounders. Team
commands receive the shared envelope path/hash and wait for role-specific
dispatch; they do not receive the full task at startup. Each arm submits a
hashed result payload through the sealing helper. The adjudicator waits for both
submissions, then reveals and scores the results together. The shared envelope
and spawn receipt contain no plaintext capability token. One arm is
procedurally forbidden from inspecting the other's plan, transcript, status
detail, payload, elapsed progress, or preliminary score before it seals its own
result.

### Transactional topology release

Both teams and both orchestrators are created with every terminal command
waiting behind one run-specific gate. The launcher verifies the four-workspace
cmux topology—run-owned refs, expected titles, and expected terminal surfaces—
before publishing the gate with atomic no-replace semantics. Gate collisions
fail closed instead of replacing another run's release record.

Readiness has two fail-closed phases. Before CMUX creates any workspace, the
launcher runs one subscription-authenticated, completed model turn for every
unique provider/model/effort route in the planned fleet. Codex probes use an
ephemeral, ignored-user-config/rules, read-only invocation; Claude probes use
plan mode, no tools, safe mode, and no session persistence. Provider API-key,
alternate-base-URL, Bedrock/Vertex/Foundry, and related cloud credential/routing
variables are removed from each probe environment while subscription OAuth and
keychain state are preserved. The probes therefore exercise the existing
`codex login` and Claude subscription sessions. Each provider CLI is resolved
to its absolute executable path before launch; its content digest is verified
after the turn and bound into the receipt. Each route has
`--route-probe-timeout-seconds` (default `60`) to return its exact run-bound
sentinel as bare text, sentinel plus one LF, or sentinel plus one CRLF. The
response is deleted; an immutable receipt retains only hashes of the run, route fields, repository snapshot,
command, executable content, and sentinel plus its UTC completion time.
Duplicate routes are probed once.

Only after every route receipt validates does CMUX creation begin. After the
topology gate releases, one supervisor per expected surface performs
provider-auth preflight, starts the provider in a dedicated process group,
verifies short liveness, and publishes a second immutable receipt bound to run,
team, role, workspace ref, surface ref, and repository snapshot. The launcher
waits up to `--readiness-timeout-seconds` (default `30`) for every surface
receipt, then watches a `--readiness-settle-seconds` window (default `1.0`) for
immediate exit markers. Together these phases prove a completed turn and quota
availability for each unique planned route at launch time, plus authentication
and initial liveness for each interactive surface. They do **not** prove that
every interactive surface completes its own turn or that quota remains
available later in the run.

On any route-probe, launch, or surface-readiness failure, the coordinator
atomically invalidates the sealed-results contract before rollback. Route-probe
failure occurs before CMUX creation and therefore leaves zero workspaces.
Invalidated runs cannot submit,
expire, or reveal results, even if a late writer races publication. Supervisors
observe invalidation and send TERM, then KILL if necessary, to their entire
provider process group. Run-owned workspaces are closed in reverse order;
failed closes remain explicit in the invalid receipt. The focused suite is
currently `37/37` passing.

### Deadline adjudication

New mirrored contracts store a UTC deadline derived from `--timeout-minutes`
(default `20`). A normal model submission is accepted only before that deadline.
Afterward, the coordinator may use `sealed_results.py expire` to atomically fill
one still-empty arm with a typed infrastructure-failure result. Expiration is
rejected before the deadline, for legacy contracts without a deadline, or when
the arm already submitted. Once the pair is complete, reveal returns the real
model result and infrastructure record together without turning provider
availability into a model-quality verdict.

Each orchestrator receives its capability through its own mode-`0400` token
file, not an argv value. The envelope, receipt, and process listing therefore do
not expose plaintext tokens. This same-user “capability sealing” is procedural,
not an adversarial operating system boundary. Result paths and token files are
not private from another process owned by the same user.
Sessions owned by the same macOS user can potentially read the other arm's
files, processes, cmux surfaces, or You.md messages if prompted to do so. The
protocol prevents accidental leakage and produces an auditable run;
hostile-model isolation requires separate OS users, VMs/containers with enforced
mount/network policy, or a server-side blind evaluation service.

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
