---
name: liq-engineering-conventions
description: >
  Standing engineering, audit, and KB-sourcing conventions for the LoyaltyIQ
  (LIQ) project. Consult this whenever working on LIQ, whether the task is
  Airtable schema/data changes, backend (app.py) changes, characterizing a
  bug or narration error, sourcing or verifying loyalty-programme data, or
  coordinating work between Engineering, Audit, Design, Code, Business
  Analyst, Marketing, or any per-programme sourcing sub-agent. Trigger this
  proactively for LIQ work even if the request doesn't explicitly ask for
  "conventions" or "process", e.g. "add a field to Airtable", "is this a
  bug or a data problem", "write the Code instruction for X", "check this
  KB record", "should this go live" all depend on the rules here. Do not
  skip this and rely on general Airtable/engineering knowledge instead.
  Several of LIQ's conventions (e.g. create_field being unreachable via
  MCP, the three-way audit classification) contradict what would otherwise
  be a reasonable default approach.
---

# LoyaltyIQ (LIQ) Engineering Conventions

LIQ is a South African loyalty-programme optimisation engine (Network Grey /
Gustav). Deterministic Python engine computes, LLM narrates, never the
reverse. This skill encodes the standing conventions that keep Engineering,
Audit, Design, Code, and per-programme sourcing sub-agents working from the
same rules instead of re-deriving or contradicting them each thread.

## Multi-agent architecture

- **Engineering is the coordination point.** No sub-agent instructs Code
  directly. No Airtable write happens without an Engineering-authored
  instruction, even if a sub-agent drafts one first, Engineering reviews
  and re-issues it, never relays a sub-agent's draft verbatim as-is.
- Sub-agents (Audit, Design, Business Analyst, Marketing, per-LP sourcing
  threads, Code) don't talk to each other directly. A human relays messages
  between threads. Don't assume a fact "known" in one thread is visible in
  another unless it's been relayed or written to shared KB/memory.
- **Audit's mandate is read-only.** It detects, traces, and characterizes.
  It does not fix, and does not write to Airtable.
- When Code reports back, treat its capture as verbatim data, not something
  to paraphrase before forwarding to Audit, relay the actual text.

## Stage-gate protocol (KB/schema changes)

1. **Stage 1: source.** Primary source only. No fabrication. Single-source
   claims get tagged `[hypothesis]` or `[unverified]`, not stated as fact.
2. **Stage 2: structured snapshot.** Lay out exactly what will be written
   (schema, records, values) before writing anything.
3. **Stage 3: mandatory sign-off.** No Airtable write before explicit
   human confirmation of the Stage 2 snapshot. This is not optional or
   implied by "proceed" on an earlier, different question.
4. **Stage 4: Code instruction.** Authored by Engineering directly, never
   accepted pre-drafted from a sub-agent thread and passed through unedited.

## Three-way finding classification

Every narration-vs-source discrepancy gets classified as exactly one of:

1. **Wrong narration**: the model said something wrong given correct
   input. Model/Engineering/Code owns the fix.
2. **Faithful reproduction of wrong source**: the model correctly
   repeated something wrong. KB/Audit owns the fix, at the source, not in
   code.
3. **Correct silence on an absent source**: the model correctly had
   nothing to say because the source had nothing there. This is a coverage
   gap, not a defect. Sourcing owns it, and it should not be logged as a
   fabrication or narration bug.

Don't collapse this back to two categories. A model confidently denying a
fact that's absent from source is shape 3 (correct); a model confidently
denying a fact that IS present in its context is shape 1, a distinct and
more serious failure signature. Treat repeats of that specific pattern as
their own line of evidence, not folded into general "hallucination."

**Absence of evidence ≠ confirmed negative.** "I searched the tables and
found nothing" is not the same as "the source explicitly states no
relationship exists." Only the latter is a clean shape-3 case. Hold the
former separately and close it with a targeted grep of the actual prose
`LLM context block` before treating it as equivalent.

This section, together with Evidence discipline below, is the canonical
audit-methodology reference for this project. Any Audit-specific skill
should link to it, not restate it, this supersedes the separately
proposed `liq-audit-methodology`.

## Evidence discipline

- No prompt-only patch is trusted for a problem class that has already
  failed to close under a prompt-only fix once. Escalate to deterministic
  code-level handling instead of retrying the same category of fix.
- A finding needs reproduction before being escalated in severity or used
  to justify bigger architectural work. One instance can be noise; rerun
  before concluding.
- **Point-fix vs. general mechanism:** default to the smallest fix that
  closes the actual instance. Escalate to scoping a general mechanism only
  once independent instances of the *same shape* (not just superficially
  similar bugs) cross roughly three confirmed occurrences, and even then,
  rerun the newest instance before committing, since over-building on thin
  evidence has already cost real time on this project once (a broad
  architecture was scoped on two assumed cases; five of six turned out to
  be ordinary KB-completeness gaps).
- Fixes get verified live/standalone before being marked closed. A
  standalone throwaway venv with real project deps (Python 3.12, per
  `runtime.txt`) importing the actual module, not a hand-copied function,
  is the standard for pre-wiring verification.

## Airtable conventions

- **`create_field` is not reachable via the MCP connection.** New fields
  must be created manually in the Airtable UI (draft exact name/type/
  options for the human to enter), then retrieve the field IDs via
  `get_table_schema` before any programmatic write. Don't attempt
  `create_field` and assume it'll work this time.
- `typecast: true` is required on update calls that need to create new
  single-select options on the fly.
- Batch writes in groups of ~8 records per call, not one record at a time
  and not the full set in a single oversized call.
- `search_records` with `fields: ALL_SEARCHABLE_FIELDS` returns record IDs
  only, not field content. Always follow up with `list_records_for_table`
  passing explicit `recordIds` and `fieldIds` arrays to get actual values.
- The MCP tool returns singleSelect fields as objects
  (`{"id": "sel...", "name": "..."}`); the REST API returns bare strings.
  Any code or test harness built against MCP-returned data must account
  for this or category lookups will silently fail.
- For cross-programme interaction lookups (e.g. "does Programme A earn at
  Programme B's category"), search for the specific partner/merchant name
  directly rather than the held-programme name, the records are usually
  keyed that way.
- When two records independently describe what looks like the same
  real-world fact (same date, same mechanism, different composition or
  provenance), don't silently merge or silently ignore the duplication.
  Add an explicit cross-reference note on both pointing at the other,
  tagged `[cross-reference]` and `[hypothesis]` unless independently
  re-verified against primary source, so neither drifts out of sync and no
  downstream logic sums both as if additive.

This section is the canonical Airtable-conventions reference for this
project. Any Audit-specific skill should link to it, not restate it, this supersedes the separately proposed `liq-airtable-verification`.

## KB precision rules

- The `LLM context block` field on Programme records is the actual prose
  fed to the model; it is the ground-truth target for narration-accuracy
  findings, not the structured Earn-rate records alone. The two can drift
  independently; check both.
- Conflicting source data is preserved with both dated value sets and
  tagged inline, never silently resolved to one figure.
- No fabricated KB data under any circumstances. Every claim traces to a
  primary source. Hypothesis-level findings carry the exact tag text
  (`[hypothesis]`, `[unverified]`, `[hypothesis, source-corroborated]`,
  `[source ambiguity]`, `[cross-reference]`) inline, not as a separate
  disclaimer elsewhere.
- Source notation gets written through literally even when it's internally
  odd (e.g. a stated band boundary that overlaps the previous band's upper
  bound by a small margin), don't silently "correct" the source's own
  numbers. Flag the oddity inline instead.
- For purchase categories intended to be routable by
  `resolve_spend_routing()`, `Spend category` must match a working
  `CATEGORY_ALIASES` string exactly, purchase-type language only.
  Categories deliberately outside the alias map (e.g. `Bank / non-partner`,
  consumed by `_best_bank_match()`; `All spend`; `Partner spend`) are not
  violations of this rule, but a category intended to be routable that
  doesn't match a working alias string is, whether by near-miss ("Grocer"
  for "Grocery") or by a missing alias key ("Lifestyle"). Merchant or
  retailer type belongs on the Partners table's `Partner sector` field,
  never on `Spend category`. A near-miss or missing-alias category string
  is not cosmetic, it makes every record carrying it silently unreachable
  to `resolve_spend_routing()`.

## Frontend / WordPress conventions

- **Pages render from `_elementor_data`** (the in-browser
  `elementor.elements` tree, via a `text-editor` widget), never from
  `post_content`.
  A direct REST write to `post_content` succeeds silently but never
  reaches the live page. Raw Backbone `.set()` on the model doesn't
  register a publishable change either. The only path that actually
  works: Elementor's own `$e.run('document/elements/settings', ...)`
  command API, followed by the native Publish button.
- **Mandatory pre-publish checks**, both required on every publish:
  - Grep the widget's script content for literal `&&` before publishing.
    A confirmed, non-deterministic entity-corruption bug turns `&&` into
    `&#038;&#038;` on this install, silently breaking logical operators.
    `||` is unaffected. Leave it alone.
  - Check computed heading colour after publish. Both pages carry a
    latent per-widget "Text Color" override in `post-{ID}.css` that can
    resurface once other explicit colour rules are removed or a page
    starts relying on inheritance instead.
- **CSS-collision-check methodology.** Both pages share one sitewide
  Additional CSS field. Before consolidating into it or adding to it,
  diff duplicate selectors and `:root` variables as full raw-text blocks
  between the two pages, not as a merged, last-one-wins map. A
  same-named selector or variable can genuinely carry different values
  per page, and a map comparison will silently hide that. Where values
  are confirmed identical or safely mergeable, consolidate globally;
  where a difference is genuinely page-purpose-driven, scope it via
  WordPress's own auto-generated `body.page-id-N` class rather than
  renaming variables.
- All CSS, including `:root` custom property declarations, must live in
  WordPress's Additional CSS field. Elementor's Custom HTML widget strips
  embedded `<style>` blocks on publish. Inline style blocks will silently
  not render.
- GitHub is the source of truth for frontend files; WordPress is
  maintained by manual re-paste into Elementor's Custom HTML widget, not
  synced automatically. This has failed silently before: a fix landed
  only in WordPress and was never backported to the committed source, then
  regressed. Treat "committed to git" and "live on WordPress" as two
  separate claims, each needing its own verification, not one implying
  the other.
- **Git hunk-splitting**, for isolating one fix per commit out of a larger
  local diff: `git diff --unified=3`, parse on `@@` boundaries, `git apply
  --cached` per hunk/commit, and re-diff fresh between extractions rather
  than trusting a previous diff's line numbers. Watch for `git stash pop`
  triggering a 3-way merge that can silently resurrect stashed content.
- Moving a token block from page-local to sitewide (e.g. Additional CSS)
  changes cascade behaviour for every other page that still has its own
  local copy of the same variable names; check for that collision
  whenever a page with its own embedded tokens is next touched.

## Product boundaries

- LIQ recommends optimal spend routing across programmes the user already
  holds or is evaluating. It does not give comparative financial advice
  (e.g. which bank has the best interest rate). Tier-level differences in
  reward rates within a single programme are KB data, not advice, and are
  in scope.
- Advertising is not a revenue model for this product (rejected explicitly,
  Mint cited as the cautionary case).
