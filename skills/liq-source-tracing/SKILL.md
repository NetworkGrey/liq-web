---
name: liq-source-tracing
description: >
  Use whenever a LIQ task involves asserting what the code, a function, or
  the repo currently does or contains, root-causing a bug, writing a Code
  instruction, or verifying a claim about `app.py`, `liq.html`,
  `liq-onboarding.html`, or `wallet/index.html`. Trigger this before stating
  what a function does, what triggers a mode, whether a file contains
  something, or whether a prior fix landed, even if the answer seems
  obvious or was true in an earlier session. A description of the code is
  not the code. This skill is the standing method for checking directly
  against `liq-web` (read-only clone or raw.githubusercontent.com) rather
  than reasoning from memory, a handoff doc, or what "should" be there.
---

# LoyaltyIQ Source Tracing

## Core principle

**Check the code before asserting a root cause.** This project has been
bitten by the gap between "should be true" and "is true" repeatedly this
session alone: A1 described as final before the bytes existed anywhere
reachable, an Exec Summary POC list that didn't match live Airtable, a
Shell V+ member count the KB itself declined to state. Source-tracing is
the standing countermeasure: pull the actual file, actual function, actual
line, before writing anything down as fact.

This applies even when the claim feels safe. "The handoff says X" and "I
checked and X" are different strengths of evidence, and only the second
belongs in a Code instruction, an Audit finding, or a document correction.

## Method

- **Read-only clone, no credentials needed:**
  `git clone --depth 1 https://github.com/NetworkGrey/liq-web.git`. Read
  access works without auth. Push does not, confirmed empirically this
  session (a push attempt failed immediately with no credential prompt
  available, this is expected and correct, this thread should never hold
  write credentials).
- **`--depth 1` caveat:** a shallow clone only shows the single latest
  commit in `git log`. If a task needs commit history for a specific file
  (when was this last touched, by which commit), drop `--depth 1` or
  `git fetch --unshallow`. Don't report "no other commits touch this file"
  from a shallow clone, that's an artifact of the clone depth, not a fact
  about the repo.
- **Byte-identical verification via raw URL:**
  `curl -sS -o local.file -w "HTTP %{http_code}\n"
  https://raw.githubusercontent.com/NetworkGrey/liq-web/main/<path>`,
  then `diff` against the expected content. This is the standard for
  closing out any Code-reported commit, don't mark a commit verified from
  Code's report alone, pull the raw URL and diff it yourself.
- **When the raw URL doesn't match but the push is otherwise confirmed
  good** (`git show origin/main:<path>` on Code's side matches): this can
  be Fastly CDN edge-cache lag on `raw.githubusercontent.com` specifically,
  not a failed or reverted push, confirmed live this session. Don't poll
  the same cached URL repeatedly. Instead, pull an independent path:
  `curl -sSL https://codeload.github.com/NetworkGrey/liq-web/tar.gz/refs/heads/main
  -o main.tar.gz`, extract the specific file, and diff that. It's a
  different serving path from the raw CDN and confirms the actual git
  content directly.
- **Grep for existence and absence, not just content.** A zero-result grep
  is itself a finding, not a null result to discard, see the trust-section
  example below.

## Key functions in `app.py`

Verified directly against the live repo, not copied from a prior
description (line numbers as of commit `caa2e3b`, will drift, re-check
before citing a line number in anything written down):

| Function | Line | Role |
|---|---|---|
| `build_system_prompt` | 154 | Assembles the LLM's system prompt from KB context |
| `_held_programmes_display` | 358 | Formats the user's held-programme list for narration |
| `_detect_mentioned_partners` | 375 | Matches partner/merchant names mentioned in a query |
| `_best_bank_match` | 656 | Resolves the best-fit bank programme for a query |
| `resolve_spend_routing` | 752 | The deterministic engine, computes routing, never narrates |
| `detect_mode` | 962 | Classifies a query into Mode 1, 2, or 3 |

If a task references one of these functions, pull the current body before
describing its behaviour, the line numbers above are a starting point for
navigation, not a substitute for reading the current code.

## Worked examples, from this session, not hypothetical

**1. A repo-structure check before a path decision.** Before instructing
Code where to commit A1's `SKILL.md`, cloned the repo and listed the root
rather than guessing a conventional path. Found no `skills/` or `docs/`
directory existed at all, so "confirm the path with Code" wasn't a
formality, there was genuinely no established convention to slot into.
Passing that finding to Code changed the instruction from "commit to the
usual place" to "you're choosing the convention, not picking from one."

**2. An absence-grep that surfaced a bigger gap than the task in hand.**
While backfilling `.trust-dark`/`.trust-light` CSS to `liq.html`, grepped
the file for any mention of "trust" at all, markup included, expecting to
find the existing elements the CSS would attach to. Zero matches. The
whole trust section, markup and styling both, existed only in WordPress,
never in git. The original task (tokenise two CSS rules) stayed scoped to
exactly that, but the larger finding got written down and flagged
separately rather than silently absorbed or silently dropped.

**3. Byte-identical raw-URL verification, used on every commit this
session.** Never accepted "committed and pushed" as closed on Code's
report alone. Every commit (A1's `SKILL.md`, the trust-band backfill) was
independently pulled via `raw.githubusercontent.com` and diffed against
the expected content before being marked done. This caught nothing wrong
in either case, but the check is what makes "confirmed" mean something
rather than "reported."

**4. A cascade-collision check run before publish, not after.** Before
signing off the trust-band tokenisation for Elouise to publish, grepped
`liq.html`'s embedded `:root` block for the same two selectors to confirm
no page-local override would collide with the new sitewide tokens. Clean,
zero matches, so the change was safe to publish as scoped. Had it not been
clean, the spec would have forked, per A5's collision-check rule, rather
than publishing on an unverified assumption.

**5. Verifying a trigger list rather than assuming semantic equivalence.**
`detect_mode()`'s actual Mode 3 trigger check, current as of this write-up:

```python
joining_signals = ["worth it", "should i join", "is it worth", "thinking of joining", "considering"]
if any(s in msg_lower for s in joining_signals):
    return "3"
```

Five literal substrings, checked with plain `in`, not an intent
classifier. A query that means "should I join" but doesn't contain one of
these five strings will not route to Mode 3, regardless of how clearly a
human would read the intent. This is the exact failure B3
(`liq-query-construction`) exists to prevent, verified here directly
against the current function body rather than cited from memory of what
the function probably does.

## Standing instruction

Don't write a Code instruction, an Audit finding, or a document correction
that asserts what a file currently contains, without having pulled and
read it in the current working session. A read from an earlier session,
or from a handoff doc describing the code, is not current evidence, the
code moves, handoffs don't always catch up. Re-check, even if it feels
redundant.
