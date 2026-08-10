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

---

## LESSON-6 — A model appearing in `models.list()` does not mean you can call it

**Pattern:** `config.yaml` routed three of four tiers to `gemini-2.5-flash`, chosen from
the published model list. Every call returned **404 "no longer available to new users"**.
The model is listed by the API and documented on the web; it simply cannot be called with
a newly issued key. `gemini-2.5-pro` was listed too and returned 429 immediately — its
free allowance is far below this workload.

**Rule:** Verify model names with a real call before routing anything to them, and keep
that check runnable (`scripts/verify_providers.py`). Documentation and `models.list()` are
both hints, not contracts. Record the verification date beside the config entry.

---

## LESSON-7 — Thinking tokens are billed against `max_output_tokens`

**Pattern:** A smoke test capped `max_output_tokens` at 16 and every Gemini 3.x *flash*
model returned an **empty string with zero output tokens**. It looked like a broken model
or a bad request. In fact the model spent 128 tokens reasoning and had no budget left to
answer: with 512 tokens the same call returned `'ok'` after 128 thought tokens.

**Rule:** For reasoning models, `max_output_tokens` must cover thinking *plus* the answer.
Budget generously — an empty reply is the failure mode, and it is silent. `thinking_budget=0`
is not a portable escape: it works on `gemini-3.5-flash` but is rejected with 400 by
`gemini-3.6-flash` and `gemini-3.5-flash-lite`.

---

## LESSON-8 — Secrets in `.env` must reach the code that reads `os.environ`

**Pattern:** Keys were correctly placed in `.env` and `Settings` loaded them, but the
provider adapters and the tracing wrapper read `os.environ` directly (deliberately, to keep
secrets out of a settings object that gets logged and `repr`'d). Nothing bridged the two, so
every key read as absent and every provider reported "not set".

**Rule:** If any component reads `os.environ` for configuration, load `.env` into the real
process environment once at import — `load_dotenv(..., override=False)` so a genuine
environment variable still wins. Verify with a check that prints key *presence*, never the
value.
