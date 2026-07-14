# BigBounce read-only CMUX dogfood review

Date: 2026-07-13 (America/Los_Angeles)

## Outcome

The first subscription-authenticated CMUX dogfood run completed without any
worker or adjudicator changing the BigBounce review inputs. The controller
separately appended Houston's `continue` command to the required project prompt
history. The Codex-led fleet and the independent Claude Opus adjudicator both
rejected the current local skill bundle.

The run proved that CMUX can host the intended visible hierarchy with ChatGPT
and Claude.ai subscription logins. It also exposed a dispatch-design gap: the
Codex orchestrator received the task and drove the five-pane team, while the
original Opus orchestrator remained at its startup wait prompt. A second
CMUX-native Opus workspace was therefore launched with the completed sentinel
as its bounded adjudication input.

This is useful pipeline-plus-adjudicator evidence, but it is not yet a fair
mirrored orchestrator A/B.

## Topology exercised

```text
GPT-5.6 Sol high primary orchestrator
                 |
GPT-5.6 Sol medium lead
                 |
  +--------------+--------------+
  |              |              |
Codex low   Claude Sonnet   Codex low   Claude Sonnet
explorer       reviewer       tester      comparator

Claude Opus 4.8 high independent adjudicator
```

All model surfaces were instructed to remain read-only and made zero changes.
The pre-existing manifest/skill working tree was preserved; only the
controller's append-only prompt-history entry was added outside those surfaces.

## Timing

- Codex fleet boot: 22:16:57 UTC.
- Review task routed: approximately 22:18 UTC.
- Lead sentinel closed: 22:26:55 UTC.
- Opus adjudication started: 22:42:36 UTC.
- Opus adjudication completed: 22:47:45 UTC.

The durations are not directly comparable. Codex orchestrated four independent
worker lanes and synthesized the initial review; Opus received that sentinel
and independently verified it without creating a second worker team.

## Converged findings

Both paths agreed on the following:

- The five project-local `bigbounce-*` skills violate the repository rule that
  SciStack owns the canonical global science skills.
- `bigbounce-site-sync` is stale and omits current Convex writes, three-way PDF
  hash hygiene, and browser-QA gates.
- `bigbounce-version-bump` omits the pattern-047 provenance/artifact update
  gate.
- The generic `arxiv/${PAPER}.pdf` verifier in `bigbounce-paper-pdf-mirror` is
  invalid for P2 through P5, whose PDFs live outside `arxiv/`.
- Manifest rows 311 through 314 are syntactically and chronologically valid,
  unique additions.
- M43 is incomplete: it contains only four failed-dead P5/P2 ChatGPT/Grok
  submission legs and no Gemini legs.
- Existing duplicate groups were not introduced by the four new manifest rows.

## Disagreements and corrections

Opus rejected one literal statement in the Codex sentinel:

- `bigbounce-paper-pdf-mirror/SKILL.md` and
  `bigbounce-revision-tracker/SKILL.md` are not byte-identical to each other.
  They are different skills with different lengths and content. The intended
  claim may have been that each matched its own canonical SciStack counterpart,
  but Opus could not verify paths outside its allowed read boundary.

Opus also found that the sentinel under-counted stale source references:

- Stale `AGENTS.md` section references occur in four of the five local skills,
  not only claims-table and version-bump.
- `bigbounce-version-bump` also misses the current Convex
  `paperVersions:bump` and three-way PDF-hash gate.
- All five local copies are versioned `0.1.0`, indicating systemic age rather
  than isolated drift.

## Orchestration lessons

1. A comparison task must be included at launch or placed in a durable task
   inbox. Starting two orchestrators with `Wait for Houston's task` does not
   guarantee both receive a later interactive dispatch.
2. Completion needs an explicit sentinel and durable transcript harvest.
   CMUX notifications are useful status signals but are too short to serve as
   the evidence record.
3. The orchestrator comparison must distinguish correctness from speed. This
   run showed that a slower independent adjudicator can add value by rejecting
   an overstatement and widening an under-scoped finding.
4. Read boundaries must be identical. Opus could not inspect canonical
   `~/.claude/scistack` paths in this run, preventing one exact comparison.
5. Declarative layouts and `respawn-pane --command` worked. The one-off
   `workspace create --command` attempt created a shell but did not launch its
   requested process on CMUX 0.64.17.

## Required protocol for the next A/B

The next experiment should create two isolated, structurally identical teams:

```text
Team A: GPT-5.6 Sol high orchestrator -> lead -> four workers
Team B: Claude Opus 4.8 high orchestrator -> lead -> four workers
```

Both teams must receive the same immutable task envelope containing:

- repository commit and dirty-state snapshot;
- explicit read/write policy;
- identical scope and acceptance criteria;
- identical allowed filesystem roots;
- deadline and completion sentinel schema;
- artifact path for the final evidence bundle.

The teams should not see each other's findings until both sentinels are sealed.
A third adjudication pass can then compare factual correctness, severity,
coverage, unsupported claims, elapsed time, and token/session cost. This is the
coding analogue of BigBounce's blind multi-lab science protocol.
