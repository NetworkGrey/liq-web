---
name: liq-query-construction
description: >
  Use whenever building, reviewing, or trusting a batch of test queries
  against LIQ, for an Audit pass, a regression check, back-testing, or any
  task that assumes a query will route to a specific mode (1, 2, or 3).
  Trigger this before finalising any batch, not after results come back
  looking wrong. Also trigger if reasoning about why a query produced
  Mode 1 output when Mode 2 or 3 was expected, that's frequently a batch
  construction defect, not a product defect. `detect_mode()` routes on
  literal substring matching and payload shape, not on what a human would
  read the query as meaning, this skill is the standing check against
  assuming otherwise.
---

# LoyaltyIQ Query Construction

## Core principle

**`detect_mode()` does not understand intent, it matches literal
conditions.** A query batch built on "a human would read this as asking to
join a programme" will silently misroute if the query text doesn't happen
to contain one of a short, fixed list of substrings. The resulting output
then looks like a narration or coverage defect, wrong mode entirely, when
the actual defect is in the batch, not the product. This has been a
recurring source of mis-scoped batches on this project, checking the
literal logic before trusting a batch is the fix, not a code change,
`detect_mode()`'s substring matching is deliberate, simple, and documented
behaviour, not a bug.

## The current decision tree

Verified directly against the live repo (commit `5199553`, re-checked
before writing this, not reused from an earlier session per
`liq-source-tracing`'s standing instruction):

```python
def detect_mode(message: str, user_spec: dict | None) -> str:
    if user_spec and user_spec.get("categories"):
        return "2"
    msg_lower = message.lower()
    joining_signals = ["worth it", "should i join", "is it worth", "thinking of joining", "considering"]
    if any(s in msg_lower for s in joining_signals):
        return "3"
    return "1"
```

Three things follow directly from reading this, none of them optional
when constructing a batch:

1. **Mode 2 is checked first, and it is payload-triggered, not
   text-triggered.** See below, this is the less obvious of the two traps.
2. **Mode 3 is five literal lowercase substrings**, checked with plain
   `in`, no tokenisation, no word-boundary awareness, no synonym or intent
   matching.
3. **Everything else defaults to Mode 1.** There is no explicit Mode 1
   check, it's the fallthrough. A query that fails both the Mode 2 and
   Mode 3 conditions is Mode 1 regardless of what it's actually asking.

## Mode 2: payload-triggered, not text-triggered

Easy to get wrong in the opposite direction from Mode 3. Mode 2 does not
look at the message text at all. It fires only if `user_spec.get
("categories")` is truthy, that's a structured field in the request
payload, populated by the frontend when a user has entered a full spend
profile, not something a query's wording can trigger on its own.

A batch item like *"Please review my whole spend across all my
programmes"*, sent as plain text with no populated `categories` payload,
will **not** hit Mode 2. It falls through to the Mode 3 check (no match,
none of the five substrings are present), then defaults to Mode 1. A
batch intended to exercise Mode 2 needs the actual structured payload
attached to the request, review-sounding language alone does nothing.

## Mode 3: five literal substrings, not semantic intent

The current list, verbatim:
```
"worth it", "should i join", "is it worth", "thinking of joining", "considering"
```

Illustrative examples, constructed to show the gap, not drawn from a
specific logged incident, the failure shape has recurred more than once
per project history but the exact prior instances aren't reproduced here:

| Query | Reads as Mode 3 to a human? | Contains a listed substring? | Actual mode |
|---|---|---|---|
| "Is Discovery Vitality good for me?" | Yes | No | 1 |
| "Would Shell V+ be worth having?" | Yes | No, "worth having" ≠ "worth it" / "is it worth" | 1 |
| "I'm looking at signing up for FNB eBucks" | Yes | No, "signing up" isn't a listed phrase | 1 |
| "Is Capitec Live Better worth it for me?" | Yes | Yes, "worth it" | 3 |
| "I'm thinking of joining Discovery Vitality" | Yes | Yes, "thinking of joining" | 3 |

The first three are genuine Mode 3 intent that the current function will
not route as Mode 3. If a batch needs those scenarios covered, the query
text has to be rewritten to contain one of the five substrings, the
intent alone doesn't survive contact with the code.

## Before finalising any query batch

1. **Re-pull `detect_mode()` fresh**, don't reuse a cached substring list
   from a prior thread or an earlier point in this one, per
   `liq-source-tracing`. The list can change without this skill being
   updated in the same commit.
2. **For every batch item intended to hit Mode 3**, confirm it contains at
   least one of the current substrings, verbatim, lowercased. Word form
   matters, "considering" is listed, "consider" is not, and the check
   won't match a stem or synonym.
3. **For every batch item intended to hit Mode 2**, confirm the request
   carries a populated `categories` payload, not just review-sounding
   text. Wording cannot trigger Mode 2 on its own.
4. **Everything not deliberately constructed for Mode 2 or 3 is Mode 1.**
   If a batch item lands there unintentionally, that's a batch defect to
   fix before running it, not a finding to report against the product.
