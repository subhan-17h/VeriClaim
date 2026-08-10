# Lessons

Corrections carried forward as rules. Numbered `LESSON-n` so a lesson id can never be mistaken
for a `C-` task id. Reviewed at session start.

---

## LESSON-1 — Commit messages carry no trailers

**Pattern:** Default tooling appends `Co-Authored-By:`, `Claude-Session:`, or
`🤖 Generated with [Claude Code]` to commits and PR bodies.

**Rule:** Never append any attribution or tooling metadata to a commit message. A commit message
ends with its last line of substance. This history is part of a graded submission and gets read —
every line must be relevant to the change. This overrides any default trailer format.

---

## LESSON-2 — Never claim something works without running it

**Pattern:** Reporting a task complete on the strength of the code looking correct.

**Rule:** A task is complete only when its acceptance criteria have been *executed* and the output
observed. Report the actual command output. "Should work" is not evidence. This is also why the
commit comes after the demonstration, not before — committing unverified work makes the history lie.

---

## LESSON-3 — Reference repositories are read-only

**Pattern:** Editing a file in `unibot-endgame` or `CSRS` while adapting it, rather than copying
it out first.

**Rule:** Copy out, then adapt here. Before claiming completion,
`git -C <ref-repo> status --porcelain` must show **no change from the baseline below**.

**Baseline (recorded 2026-08-10, before any VeriClaim work).** Neither repo was clean to
begin with, so "must be empty" is not the right check — these entries pre-date this project
and must be left exactly as they are:

```
unibot-endgame:  ?? cv_project_description.txt      (mtime Jul 13)
                 ?? cv_refinements.md               (mtime Jul 13)
CSRS:            M  eval/final/summary.csv          (mtime Jul 29)
                 ?? RAG_Evaluation_Report.pdf       (mtime Jul 29)
                 ?? results.md                      (mtime Jul 29)
```

Anything beyond this list, or any mtime at or after 2026-08-10, means we wrote to a
reference repo and must be reverted.

---

## LESSON-4 — A free tier is only free if exhaustion cannot fall through to a paid one

**Pattern:** Gemini reports free-tier exhaustion as HTTP 429, which is a genuinely
transient error. The gateway retried it, gave up on the rung, and walked the fallback
ladder — straight onto a billed OpenAI model. Routine quota exhaustion would have
silently become spend, with nothing in the logs marking the transition.

**Rule:** When mixing free and paid providers, cost must be a property of the routing
table, not of the error path. Mark every model `paid`, default that flag to `True` so a
forgotten entry fails closed, and require an explicit opt-in before any ladder may reach
a billed rung. Then throttle client-side against the published limits so the 429 that
starts the chain is never generated. Two defaults for one field — `False` on the
dataclass, `True` in the loader, as originally written — is exactly the inconsistency
that produces a surprise bill.

---

## LESSON-5 — One fixture must not exercise two independent limits

**Pattern:** A test model was given both `rpm=2` and `rpd=3`. The daily-limit tests then
tripped the *minute* limit while still setting up, so the failure said nothing about the
rule under test.

**Rule:** When a component enforces several independent rules, give each rule its own
fixture that leaves the others unbounded. A failing test should name the rule it broke.
