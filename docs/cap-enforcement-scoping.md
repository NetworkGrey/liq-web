# Cap Enforcement + Shared-Cap Grouping: Final Scoping Record

From: Engineering (ENG_7, updated by Cowork Engineering)
Status: Schema live, resolver built, wired into `resolve_spend_routing()`,
live-verified. Cross-record pooling (`_apportion_cap_group()` /
`_resolve_grouped_compound_cap()`) remains standalone-verified only, no
live caller path, blocked on an input-surface decision (see final
section).
This document is the durable reference for how these decisions were
reached, superseding the chat history they came from.

## Evidence base, from Audit's blast-radius pull, unchanged except where noted

- 197 of 477 Earn rate records carry `Cap amount`. Two programmes only:
  UCount Rewards (192), MyDifference PLUS (5).
- Seven logical shapes:
  - A: lower-of-two, category-spend basis (85 records)
  - B: shared flat cap across named partners (40 records)
  - C: flat cap per period (30 records)
  - D: points-denominated, rand equivalent inline (21 records)
  - E: lower-of-two, total-card-spend basis (15 records)
  - F: rate substitution, not a stop-cap (5 records, MyDifference PLUS)
  - G: flat cap per transaction (1 record)
- **Correction, this session:** the originally stated cross-category
  sharing topology for UCount CYOR (Grocery, Fashion, Lifestyle sharing
  one pool) was found to contradict the primary Programme source, which
  states a member selects only ONE CYOR category at registration.
  Cross-category sharing within CYOR doesn't happen in reality. Replaced
  with a validated Category+Tier tagging scheme (see below). MyDifference
  PLUS's stated cross-category topology (All spend, Partner spend) is
  unaffected by this correction and remains handled as narration-only
  (shape B treatment, see below), not restructured.
- Cross-partner-within-category (UCount's LiquorShop + Petshop Science,
  40 records, shape B) unaffected by the above correction.
- `Conflict group` confirmed unusable for cap-sharing, different
  semantic, zero overlap, a record could need both tags at once.

## Decided: schema, live in Airtable, `tblMfVAmoPvbDCVKE`

| Field | ID | Type |
|---|---|---|
| `Cap type` | `fldeuvAkdxfVymkqt` | singleSelect, 6 options |
| `Cap value` | `fldecsYF8uGT40VsL` | number |
| `Cap percent value` | `fldARRu4HntVtMA8a` | number, 2 decimal places |
| `Cap period` | `fldnTqyGWmNLUaKPu` | singleSelect, 4 options |
| `Cap basis` | `fld0Eczj4Vw2kCE58` | singleSelect, 2 options |
| `Cap group` | `fldLzJw6538EaMeWC` | singleLineText |
| `Post-cap rate` | `fldvQmLja6yH4qU0l` | number |

`Cap type` options: Hard stop, fixed amount / Hard stop, percentage of
spend / Hard stop, lower of amount or percentage / Rate substitution /
Points-denominated / Shared across partners, narration only.

`Cap period` options: Fixed cycle / Calendar month / Transaction /
Invoice.

`Cap basis` options: Category spend / Total card spend.

`Post-cap rate` added after the initial field creation pass, holds shape
F's degraded rate, a genuinely separate number from `Cap value`'s
threshold, not reused.

`Cap percent value` added after `Cap percent value` was identified as
needed for shapes A/E (see "Resolved" section below), confirmed created
and live this session, spot-checked against a real Batch 2 record before
the backfill proceeded.

## Resolved: shapes A and E needed the second field, confirmed against source

The earlier open question, whether shapes A/E's compound "lower of a
flat rand amount or a percentage of spend" needed a seventh field or
could be represented some other way, is resolved: they needed the
second field. `Cap percent value` holds the percentage side, `Cap value`
the flat side, `Cap basis` says which base the percentage applies
against. All 100 shape A/E records (85 + 15) backfilled with this
structure (Batch 2, this session), independently re-verified via live
Airtable query after the write (100 tagged, 5 correctly left untouched,
no `Cap group` collision against 18 pre-existing tags).

## Decided: topology B gets no group structure

Confirmed directly against the live Review form: only six category-level
numeric inputs exist, no partner-level input anywhere. Topology B
(cross-partner sharing) therefore has no per-partner spend to apportion
against, no denominator, nothing to split. Handled as a narration-only
caveat on the ordinary category entry, same pattern as the general
unenforced-cap guard already shipped. `Cap type = Shared across
partners, narration only` marks these records, `Cap group` and the split
logic don't apply to them.

## Decided: Category+Tier tagging for UCount CYOR grouping (replaces cross-category)

Following the cross-category correction above, UCount CYOR groups are
tagged at the (Category, Tier) level only, e.g.
`ucount-cyor-grocery-tier2`, `ucount-cyor-grocery-tier5`. Live-confirmed
this session: `ucount-cyor-grocery-tier2` and `ucount-cyor-grocery-tier5`
each have 11 real members (11 retailer/card-type combinations per tier,
not the 3 originally assumed), and `Cap value` / `Cap percent value` /
`Cap basis` are identical across every member of both groups checked,
confirming the invariant the apportionment logic depends on.

At resolution time, the resolver's tier-gating (`_best_earn_match()`)
behaves differently depending on whether the programme is held: if not
held, it picks the single highest `Earn rate value` across ALL tiers for
the category, which can land in a different tier/group than a real
member sees; if held with an explicit tier, it correctly gates to that
tier's group. Both paths confirmed live this session. The compound cap's
correctness doesn't depend on which member wins, since `Cap value`/`Cap
percent value`/`Cap basis` are invariant across a group, but which
member's `Earn rate value` sets the pre-cap naive return does depend on
this, and the two paths can differ substantially (40% Tier 5 vs 6% Tier
2, confirmed live).

## Decided: proportional split for topology A

When combined naive return across a shared-pool group exceeds the pool,
each member's share of the pool is proportional to its own naive return:
`member_naive / combined_naive * pool_value`. Validated at two members
(R1,800/R1,000 naive, R2,500 pool, splits to R1,607/R893) and three
members (adding R700 naive, splits to R1,286/R714/R500). Re-verified
this session against real `ucount-cyor-grocery-tier2` member data (three
of that group's eleven real members, R60,000/R15,000/R10,000 spend at
6%/0.8%/3%, combined naive R4,020 against an R85,000 combined spend,
pool resolves to R2,500, apportioned R2,238.81/R74.63/R186.57, summing
to R2,500.01, one cent of independent-rounding drift from each share
rounding separately, expected and harmless, `_apportion_cap_group()`
doesn't promise otherwise), and against a passthrough case (same three
members, smaller spend, combined naive under the pool, unchanged). Both
confirmed against a real module import, not hand-copied logic.

## Decided: `rate` in the return contract is a display string

Forced by compound mechanics (Shell V+'s Rand-per-litre-plus-percentage,
Clicks ClubCard's segment-elevated rate) that a numeric field can't
represent. Costs nothing now, avoids a schema change later.

## Built: the resolver, now wired and live-verified

`app.py`, commit `b268a54` (`origin/main`, independently re-verified via
fresh clone, byte-identical). Four functions: `_apply_earn_cap()`
(per-record Hard stop / Rate substitution / narration-only handling,
now including the compound lower-of-amount-or-percentage branch),
`_apportion_cap_group()` (cross-record proportional split),
`_resolve_grouped_compound_cap()` (glue between the compound per-record
cap and the cross-record pool split, standalone-verified, no live
caller, see below), `_format_rate_display()` (display-string
formatting).

`resolve_spend_routing()` now calls `_apply_earn_cap()` directly for
Hard stop and Rate substitution cap types, confirmed via live production
`/analyse` calls against the deployed Railway endpoint (not just local
tests): a held, tier-gated UCount Tier 5 record correctly capped at
R2,500 (naive R20,000, lower of R2,500 flat or R10,000 at 20%), matching
the structured `routing` output's own cap-note string, not just LLM
narration referencing it.

## Open: cross-record pooling has no live caller path

`_apportion_cap_group()` / `_resolve_grouped_compound_cap()` are
standalone-verified against real group data (see above) but are not
called anywhere in `resolve_spend_routing()`, confirmed by inspection
this session. `user_spec["categories"]` carries one combined
monthly-spend figure per category, system-wide, with no per-retailer
sub-list breakdown, so there is no way today to hand multiple
`category_spend` values to multiple members of a group to apportion.
Wiring this live requires a Review-form input-surface redesign (accept
per-retailer sub-list spend within a category), a product/UX decision,
not a backend fix. Tracked as its own decision item, not resolved here.

## Sequencing, updated

1. Schema, done, including `Cap percent value`.
2. Resolver, built, standalone-verified, done.
3. KB backfill, done (100 shape A/E records, Batch 2), independently
   re-verified live.
4. Wiring into `resolve_spend_routing()`, done (`b268a54`), independently
   re-verified against a fresh clone.
5. Live verification, done, confirmed against the deployed Railway
   endpoint across held/not-held and tier-gated/aspirational paths.
6. Cross-record pooling, standalone-verified only, blocked on the
   input-surface decision above. Not the same item as the ~254-record
   Programme pool candidate triage (LP growth), a separate, unrelated,
   still-queued item, do not conflate the two.
