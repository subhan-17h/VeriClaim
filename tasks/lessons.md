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
`git -C <ref-repo> status --porcelain` must be empty for both repos.
