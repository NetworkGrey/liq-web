# Cap Enforcement + Shared-Cap Grouping: Final Scoping Record

From: Engineering (ENG_7)
Status: Schema live, resolver built and standalone-verified, unwired.
This document is the durable reference for how these decisions were
reached, superseding the chat history they came from.

## Evidence base, from Audit's blast-radius pull, unchanged

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
- Two sharing topologies: cross-category (UCount CYOR: Grocery, Fashion,
  Lifestyle; MyDifference PLUS: All spend, Partner spend) and
  cross-partner-within-category (UCount's LiquorShop + Petshop Science,
  40 records, shape B).
- `Conflict group` confirmed unusable for cap-sharing, different
  semantic, zero overlap, a record could need both tags at once.

## Decided: schema, live in Airtable, `tblMfVAmoPvbDCVKE`

| Field | ID | Type |
|---|---|---|
| `Cap type` | `fldeuvAkdxfVymkqt` | singleSelect, 6 options |
| `Cap value` | `fldecsYF8uGT40VsL` | number |
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

## Decided: topology B gets no group structure

Confirmed directly against the live Review form (`liq.html`): only six
category-level numeric inputs exist, no partner-level input anywhere.
Topology B (cross-partner sharing) therefore has no per-partner spend to
apportion against, no denominator, nothing to split. Handled as a
narration-only caveat on the ordinary category entry (`partner_cap_note`
in the return contract), same pattern as the general unenforced-cap
guard already shipped. `Cap type = Shared across partners, narration
only` marks these records, `Cap group` and the split logic don't apply
to them.

## Decided: proportional split for topology A

When combined naive return across a shared-pool group exceeds the pool,
each member's share of the pool is proportional to its own naive return:
`member_naive / combined_naive * pool_value`. Validated at two members
(R1,800/R1,000 naive, R2,500 pool, splits to R1,607/R893) and three
members (adding R700 naive, splits to R1,286/R714/R500), both exact,
both confirmed against real arithmetic, not approximated.

## Decided: `rate` in the return contract is a display string

Forced by compound mechanics (Shell V+'s Rand-per-litre-plus-percentage,
Clicks ClubCard's segment-elevated rate) that a numeric field can't
represent. Costs nothing now, avoids a schema change later.

## Built: the resolver, standalone-verified, deliberately unwired

`app.py`, commit `0d08f42`. Three functions: `_apply_earn_cap()` (per-
record Hard stop / Rate substitution / narration-only handling),
`_apportion_cap_group()` (cross-record proportional split),
`_format_rate_display()` (the display-string formatting). Verified
against both worked cases exactly, the narration-only passthrough, an
ordinary uncapped record, the Rate substitution case, and an explicit
`Cap basis` stress test (same `Cap value`, different basis, genuinely
different ceilings, confirmed not collapsed). `resolve_spend_routing()`
itself untouched, confirmed via diff before commit, single file, 168
insertions, zero deletions.

## Open, surfaced while planning the backfill, not resolved here

**Shapes A and E may not be representable with a single `Cap value`
field.** Both are compound, "lower of a flat rand amount or a percentage
of spend", which needs two numbers, not one. Whether the flat amount is
better understood as the group-level pool ceiling (stored once via `Cap
group` + `Cap value`) with the percentage as a separate per-record
consideration, or whether these 100 records (85 + 15) need a seventh
field, isn't decided. This needs resolving against the actual source
terms during Stage 1 of the backfill, not assumed here. See the backfill
brief for how this affects sequencing.

## Sequencing, updated

1. Schema, done.
2. Resolver, built and standalone-verified, done.
3. **KB backfill**, in progress, staged around the open question above.
4. Wiring into `resolve_spend_routing()`, blocked on backfill completing.
5. Live verification, same standard as every fix this session.
