---
name: liq-cross-programme-tracing
description: >
  Use whenever a task asks whether one programme interacts with another,
  or with a shared merchant/partner, across a user's held-programme set,
  stacking, conflicting, overlapping, or explicitly not interacting. This
  covers Mode 2 (Review) gap analysis across held programmes, any partner-
  stacking claim (e.g. a discount or cashback that combines across two
  programmes at the same merchant), and any Audit check of a narration
  claim about a relationship between programmes. Trigger this before
  reporting that "no relationship was found" as if that were the same as
  "the source states no relationship exists", those are different findings
  and this skill exists to keep them separate.
---

# LoyaltyIQ Cross-Programme Tracing

## Core principle, stated once, not restated here

The underlying discipline this skill applies is already canonical in
`liq-engineering-conventions`, Three-way finding classification:
**absence of evidence is not a confirmed negative.** Read that section
before using this one, it is not repeated here, per the project's own
rule against stating the same principle twice with wording that can drift.
This skill is the applied version of that principle, specifically for
cross-programme and cross-merchant interaction checks, which is where it
has actually caused mistakes on this project (the Dis-Chem/Capitec
stacking case, see below).

## What makes cross-programme checks different from a single-programme fact check

A single-programme claim ("does Discovery Vitality have 5 tiers") has one
place to look. A cross-programme claim ("does the Capitec Dis-Chem
discount stack with Clicks ClubCard at the same till") has no natural
single record to check, the relationship, or its explicit absence, has to
be assembled from records keyed to a shared merchant, not to either
programme directly. That assembly step is where genuine absence gets
mistaken for a confirmed negative, there was nothing to find because
nobody looked in the right place, not because the source says no.

## Method

**1. Search by the shared merchant/partner name, not by either programme's
name.** Per `liq-engineering-conventions`'s Airtable conventions: records
describing a cross-programme interaction are usually keyed to the
merchant, not the held-programme. Searching "Capitec" and "Clicks
ClubCard" separately and finding no record connecting them is not the
same check as searching "Dis-Chem" (or whichever merchant is actually
shared) and reading what both programmes' records say about it.

**2. Check both the structured records and the `LLM context block`.**
Also per the existing KB precision rules, the two can drift
independently. A stacking relationship (or an explicit note that one
doesn't exist) might be captured in a Partners or Earn-rate record, in
the prose context block, in both, or drift so only one reflects the
current state.

**3. Enumerate what's actually present before concluding anything:**
- Does a record link the merchant to Programme A?
- Does a record link the merchant to Programme B?
- Does any record, structured or prose, make an explicit statement about
  whether the two interact (stack, conflict, or are unrelated)?

Only the third question can produce a genuine negative finding. The first
two, even if both come back empty, only establish that no relationship
was *found*, not that the source *states* there isn't one.

## The three-way outcome, applied to this specific check

- **CONFIRMED POSITIVE.** A record (structured or context block) explicitly
  documents the interaction, e.g. a stacking rate, a combined discount, a
  named partnership. Report the interaction as documented.
- **CONFIRMED STATED-NEGATIVE.** A record explicitly states the
  interaction does not exist, e.g. "does not stack with X" or an
  equivalent explicit exclusion. This is a real finding, not silence, and
  it's rare, most KB records don't bother stating a negative unless it
  was worth correcting a specific wrong assumption.
- **GENUINE ABSENCE.** No record either way, positive or explicitly
  negative. This is a coverage gap, not a confirmed lack of relationship.
  Report it as absence, don't upgrade it to a negative, and don't let a
  downstream narration state there's no interaction as if the source said
  so, since the source said nothing.

## Worked reference, documented this session, not independently
re-verified by this skill's author

The Dis-Chem/Capitec Boost case (Capitec Live Better's 15% Dis-Chem
instant-discount stacking) is the project's concrete instance of this
exact failure shape, a narration hallucination that got point-fixed
(`_check_dischem_capitec_boost_mention()`, per the ENG_6 handoff) after
being reproduced. The Phase 0 Audit pass this session independently
re-confirmed the underlying records exist as documented (Capitec's flat
1% base and the Dis-Chem 15% stacking both CONFIRMED against live
Airtable). Cited here as the standing example of why this enumeration
matters, not as a claim that this skill's author re-ran that specific
Airtable query, that verification is Audit's, reported in this session's
Phase 0 results, not reproduced independently for this skill.

## Before closing any cross-programme finding

1. Confirm the search was by merchant/partner name, not by either
   programme name alone.
2. Confirm both the structured records and the context block were
   checked, not just one.
3. State explicitly which of the three outcomes applies, and if it's
   genuine absence, say so as a coverage gap, not as "no interaction
   exists."
4. If reporting a CONFIRMED STATED-NEGATIVE, quote the actual explicit
   statement, don't paraphrase it into existence from an absence.
