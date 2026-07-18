"""
LoyaltyIQ Web App
SA Loyalty Programme Optimiser
Built by Network Grey | Powered by Anthropic Claude
"""

import os
import re
import json
import uuid
import html
import anthropic
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ─── Configuration ────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
AIRTABLE_API_KEY  = os.environ["AIRTABLE_API_KEY"]

CLAUDE_MODEL    = "claude-sonnet-4-6"
MAX_TOKENS      = 2048
TEMPERATURE     = 0.3
MAX_INPUT_LEN   = 8000
CONTEXT_TURNS   = 10       # message pairs kept per session
SESSION_TTL     = 86400    # 24 hours in seconds — TEMPORARY, revert to 1800 (30 min) before public launch
RATE_LIMIT      = 100      # messages per day per session — TEMPORARY, raised for testing phase, revert to 10 before public launch
FRICTION_PENALTY = 50      # rand penalty per friction point in optimiser

ALLOWED_ORIGINS = [
    "https://liq-web-production.up.railway.app",
    "https://networkgrey.co.za",
    "https://www.networkgrey.co.za",
    "https://liq.networkgrey.co.za",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]

# ─── Airtable KB ─────────────────────────────────────────────────────────────
# KB is fetched from Airtable at server start and cached in memory.
# Re-fetch on a short TTL (see kb_last_fetched) so the KB stays live
# without requiring redeployment.
#
# Base and table IDs — LoyaltyIQ KB
AT_BASE_ID              = "appOHcS0fhY2jLyJJ"
AT_PROGRAMMES_TABLE     = "tblgGrH8qRkU7cCJa"
AT_TIERS_TABLE          = "tblMT25Isfe57f5gI"
AT_EARN_RATES_TABLE     = "tblMfVAmoPvbDCVKE"
AT_REDEMPTION_TABLE     = "tblduburC9DUJSUfn"
AT_PARTNERS_TABLE       = "tbl5AWBawrAa8sJYH"

AT_API_BASE = "https://api.airtable.com/v0"
KB_TTL = 3600  # re-fetch KB every 60 minutes

kb_cache: dict = {}
kb_last_fetched: datetime | None = None


def fetch_airtable_table(table_id: str) -> list[dict]:
    """Fetch all records from an Airtable table. Returns list of field dicts."""
    import urllib.request
    headers = {
        "Authorization": f"Bearer {AIRTABLE_API_KEY}",
        "Content-Type": "application/json",
    }
    records = []
    offset = None
    while True:
        url = f"{AT_API_BASE}/{AT_BASE_ID}/{table_id}?pageSize=100"
        if offset:
            url += f"&offset={offset}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return [{**r["fields"], "_id": r["id"]} for r in records]


def load_kb() -> dict:
    """Fetch all LIQ KB tables from Airtable and return as a structured dict."""
    return {
        "programmes":   fetch_airtable_table(AT_PROGRAMMES_TABLE),
        "tiers":        fetch_airtable_table(AT_TIERS_TABLE),
        "earn_rates":   fetch_airtable_table(AT_EARN_RATES_TABLE),
        "redemptions":  fetch_airtable_table(AT_REDEMPTION_TABLE),
        "partners":     fetch_airtable_table(AT_PARTNERS_TABLE),
    }


def get_kb() -> dict:
    """Return cached KB, refreshing if stale."""
    global kb_cache, kb_last_fetched
    now = datetime.utcnow()
    if not kb_cache or not kb_last_fetched or (now - kb_last_fetched).seconds > KB_TTL:
        kb_cache = load_kb()
        kb_last_fetched = now
    return kb_cache


# ─── System Prompt ────────────────────────────────────────────────────────────
# Populated at query time with:
#   1. The LIQ adviser persona and rules
#   2. The relevant programme LLM context block(s) from the KB
#   3. The pre-computed routing output from resolve_spend_routing()
# The LLM narrates verified output — it never computes earn rates or ZAR values.

LIQ_SYSTEM_PROMPT_BASE = """You are LIQ (LoyaltyIQ Adviser), a personalised loyalty programme optimiser for South African consumers, built by Network Grey and powered by Anthropic Claude.

## IDENTITY
You give clear, accurate, spend-specific advice on which loyalty programmes to use, where to swipe, and how to route monthly spend for maximum rand return. You are evidence-based, direct, and treat every user's spend profile as unique.

## KNOWLEDGE DISCIPLINE — CRITICAL
- Your ONLY source for programme facts, earn rates, redemption values, and partner lists is the verified KB data injected into this prompt
- Do not draw on training knowledge for specific earn rates, cashback percentages, tier thresholds, or partner details
- Never state that a specific merchant is a partner of a programme unless that merchant's name appears verbatim in the VERIFIED PROGRAMME KB DATA block for that specific programme, in this prompt, right now. This applies even if the merchant seems like an obvious or well-known partner. A merchant's absence from the injected block is a definitive negative, not an unknown, treat it as confirmed non-partnership, not as "not sure"
- This rule applies regardless of how the question is phrased, including yes/no questions, "confirm that...", "right?", or any framing that presupposes the partnership already exists. Do not answer from the premise of the question, check the injected block first, every time, regardless of phrasing. If the user's question assumes a partnership that isn't in the injected data, correct the premise, don't confirm it, and never state or imply that an absent merchant "appears", "is listed", or "is confirmed" in your verified data when it is not
- If asked for data not in the injected KB, say: "That detail is not in my current verified data — check the programme's website directly"
- Never speculate on earn rates or ZAR values. Never use "probably" or "likely" for programme facts
- The spend routing output provided in this prompt is pre-computed and verified — narrate it, do not recompute it
- When a programme is in your verified KB but a specific sub-detail is requested (e.g. a threshold for a specific household size, a rate for a specific tier), check the exact verified value before stating it. If the precise figure isn't explicitly present in your context, say so — do not infer, average, or extrapolate a plausible-sounding number from related data. A wrong specific number is worse than admitting the detail isn't available

## RESPONSE MODES
You operate in three modes depending on the query:

**Mode 1 — Choice:** User is about to spend. Give one clear recommendation and expected return. Maximum 2 sentences.

**Mode 2 — Review:** User wants a full audit. Return a per-category breakdown, total monthly uplift, and friction labels. Use a structured list.

**Mode 3 — Joining:** User is evaluating a specific programme. Return estimated monthly return for their spend, friction assessment, what's required to join, and a breakeven point if there's a cost.

## RESPONSE FORMAT
- No preamble, no padding, no rephrasing the question
- Lead with the number (rand return) before the explanation
- Bold programme names
- For Mode 2, always end with total monthly uplift if all recommendations are followed
- Never end with a question or prompt for further engagement
- Flag high-friction recommendations clearly — a recommendation that requires a new bank account or insurance policy must always carry that caveat
- Default to bullet points for any list-shaped content (partners, benefits, category breakdowns)
- Keep responses under 100 words for Mode 1, under 180 words for Mode 2/3 unless the user asks for detail
- State the "check the app/website for your exact rate" caveat at most once per response, not per claim

## WHAT LIQ DOES NOT DO
- Advise on financial products beyond their loyalty programme benefit
- State earn rates or ZAR values not present in the injected KB data
- Make programme comparisons outside the programmes present in the injected VERIFIED PROGRAMME KB DATA for this query
- Give personalised investment or financial advice
"""


def build_system_prompt(
    context_blocks: list[str],
    routing_output: dict | None,
    held_programmes: list[dict] | None = None,
    merchant_facts: list[dict] | None = None,
    conflict_facts: list[str] | None = None,
) -> str:
    """
    Assemble the full system prompt for a query:
    - Base persona and rules
    - Relevant programme LLM context blocks
    - User's stated held programmes (fact, every mode, independent of routing)
    - Deterministic merchant/programme partnership verification (Mode 1 only)
    - Known unresolved data conflicts for the evaluated programme (Mode 3 only)
    - Pre-computed routing output (Mode 2 only, when the deterministic engine ran)
    """
    prompt = LIQ_SYSTEM_PROMPT_BASE

    if context_blocks:
        prompt += "\n\n## VERIFIED PROGRAMME KB DATA\n"
        for block in context_blocks:
            prompt += f"\n{block}\n"

    if held_programmes:
        prompt += "\n\n## USER'S HELD PROGRAMMES (stated by user, treat as fact)\n"
        for h in held_programmes:
            tier_note = f" (tier: {h['tier']})" if h["tier"] else ""
            prompt += f"- {h['name']}{tier_note}\n"
        prompt += (
            "\nRoute recommendations among these held programmes first. "
            "Only discuss programmes the user doesn't hold if they explicitly "
            "ask about joining something new or ask for a comparison."
        )

    if merchant_facts:
        prompt += "\n\n## MERCHANT VERIFICATION (verified against KB, settled fact, do not override)\n"
        for f in merchant_facts:
            status = "confirmed partner" if f["confirmed"] else "NOT a confirmed partner"
            prompt += f"- {f['merchant']} / {f['programme']}: {status}\n"

    if conflict_facts:
        prompt += "\n\n## KNOWN UNRESOLVED CONFLICTS (state both values, do not silently pick one)\n"
        for fact in conflict_facts:
            prompt += f"- {fact}\n"

    if routing_output:
        prompt += (
            "\n\n## PRE-COMPUTED SPEND ROUTING (verified, do not recompute)\n"
            + json.dumps(routing_output, ensure_ascii=False, indent=2)
        )

    return prompt


# ─── Spend Routing Engine ─────────────────────────────────────────────────────
# Verified against live KB structure 1 July 2026:
#   - Earn rates: Spend category (24 options) + Earn rate unit (31 free-form
#     options) is NOT reliably comparable across programmes. ZAR return rate is
#     only populated on a handful of records (mostly 0 or unset).
#   - Redemption options: Return value % IS reliably populated for Discovery
#     Vitality (25% HealthyFood, 25% HealthyCare, etc.) and Clicks ClubCard
#     (2-4% cashback), using a DIFFERENT category vocabulary than Earn rates
#     (Groceries/Health / gym/Travel/... vs Grocery/Fuel/Dining/...).
#   - Shell V+ and Clicks ClubCard put their real rand-comparable rate directly
#     on Earn rates (R/litre, %) — these resolve without needing the fallback.
#
# Design: for each user spend category, try Earn rates first (only records
# whose Earn rate unit is directly rand-comparable — a %, or a R/litre-style
# flat rand rate); if no rand-comparable Earn rates record matches, fall back
# to Redemption options Return value % using the category-alias map below.
# Points-based mechanics (Points per rand, Miles per rand, Points per activity,
# etc.) are NOT converted to Rand — there is no reliable, verified exchange
# rate for eBucks/Vitality points on a per-category basis, and guessing one
# would violate the anti-fabrication principle. These are surfaced to the LLM
# as "not directly comparable" rather than silently omitted or estimated.

CATEGORY_ALIASES: dict[str, dict[str, list[str]]] = {
    "groceries": {
        "earn_rates":  ["Grocery"],
        "redemptions": ["Groceries"],
    },
    "fuel": {
        "earn_rates":  ["Fuel", "Petrol/diesel at participating Engen stations"],
        "redemptions": [],
    },
    "pharmacy": {
        "earn_rates":  ["Pharmacy (RSA/eSwatini)"],
        "redemptions": [],
    },
    "dining": {
        "earn_rates":  ["Dining"],
        "redemptions": [],
    },
    "clothing": {
        "earn_rates":  ["Clothing"],
        "redemptions": [],
    },
    "travel": {
        "earn_rates":  ["Travel", "Flights"],
        "redemptions": ["Travel"],
    },
    "online_shopping": {
        "earn_rates":  ["Online"],
        "redemptions": ["Shopping voucher"],
    },
    "baby": {
        "earn_rates":  ["Baby products (excl. legislated products)"],
        "redemptions": [],
    },
}

PARTNER_ALIASES = {
    # Hand-curated common SA colloquial names -> canonical Partner name as it
    # appears in Airtable. Deliberately small and conservative. Only add an
    # alias here if it maps unambiguously to exactly one real-world merchant.
    # Ambiguous short forms ("Virgin" -> Atlantic/Active/Australia, "BA") are
    # deliberately excluded, guessing among them reintroduces the exact
    # fabrication risk this feature exists to close. Extend by hand, not
    # automatically.
    "woolies": "Woolworths",
    "dischem": "Dis-Chem",
    "dis chem": "Dis-Chem",
    "pnp": "Pick n Pay",
}

PROGRAMME_ALIASES = {
    # Hand-curated common shorthand -> canonical Programme name as it appears
    # in Airtable. Small and conservative, same discipline as PARTNER_ALIASES.
    "ebucks": "FNB eBucks",
    "vitality": "Discovery Vitality",
    "live better": "Capitec Live Better",
    "voyager": "SAA Voyager",
}

RAND_COMPARABLE_UNITS = {
    "% cashback",
    "%",
    "R/litre",
    "R/litre (max)",
    "Rand per litre (max)",
    "ZAR per litre",
    "Rand back per month (max R150)",
}

PERCENT_UNITS = {"% cashback", "%"}
PER_LITRE_UNITS = {
    "R/litre", "R/litre (max)", "Rand per litre (max)", "ZAR per litre",
}


def _programme_index(kb: dict) -> dict:
    programmes = kb.get("programmes", [])
    programmes_by_name = {p.get("Programme name"): p for p in programmes}
    # REST API returns linked fields as record ID arrays; build id→name for resolution.
    id_to_name = {p.get("_id"): p.get("Programme name") for p in programmes if p.get("_id")}

    def _linked_names(field_value) -> list[str]:
        """Resolve Programme linked field — handles both REST (str IDs) and MCP (dicts)."""
        names = []
        for link in field_value or []:
            if isinstance(link, dict):
                names.append(link.get("name"))
            elif isinstance(link, str):
                names.append(id_to_name.get(link))
        return [n for n in names if n]

    earn_by_programme: dict[str, list[dict]] = {}
    for rate in kb.get("earn_rates", []):
        for name in _linked_names(rate.get("Programme")):
            earn_by_programme.setdefault(name, []).append(rate)

    redemptions_by_programme: dict[str, list[dict]] = {}
    for redemption in kb.get("redemptions", []):
        for name in _linked_names(redemption.get("Programme")):
            redemptions_by_programme.setdefault(name, []).append(redemption)

    return {
        "programmes": programmes_by_name,
        "earn_rates": earn_by_programme,
        "redemptions": redemptions_by_programme,
    }


def _select_name(field_value) -> str | None:
    """Airtable singleSelect fields: REST API returns a plain string; MCP returns a dict."""
    if isinstance(field_value, dict):
        return field_value.get("name")
    if isinstance(field_value, str):
        return field_value
    return None


def _tier_index(kb: dict) -> dict:
    """Map Tier record _id -> Tier name, for resolving Earn rates' Tier link field."""
    return {t.get("_id"): t.get("Tier name") for t in kb.get("tiers", []) if t.get("_id")}


def _norm_name(name: str | None) -> str:
    """Case/whitespace-insensitive key for matching user-submitted programme
    names against Airtable's canonical Programme name. Comparison-only,
    never used for display or as a KB lookup key elsewhere."""
    return (name or "").strip().lower()


def _held_programmes_display(user_spec: dict) -> list[dict]:
    """
    Extract user-stated held programmes for the system prompt, independent of
    resolve_spend_routing()'s internal matching logic. Original names
    preserved, not normalised, since this is shown to the LLM verbatim, not
    used for KB lookups. Returns [] if none provided.
    """
    raw_held = user_spec.get("programmes_held") or []
    held = []
    for entry in raw_held:
        if isinstance(entry, str) and entry.strip():
            held.append({"name": entry.strip(), "tier": None})
        elif isinstance(entry, dict) and entry.get("name"):
            held.append({"name": entry["name"], "tier": entry.get("tier") or None})
    return held


def _detect_mentioned_partners(
    message: str, held_programme_names: list[str], kb: dict
) -> list[dict]:
    """
    Deterministic, non-LLM detection of merchant mentions in free text,
    checked against the user's held programmes' actual Partner records.
    Returns [{"merchant": str, "programme": str, "confirmed": bool}, ...]
    for each detected (merchant, held programme) pair. Empty list means no
    confident match, callers must inject nothing in that case, silence is
    correct, not a positive or negative claim.

    Conservative by design: exact/word-boundary matching against real Partner
    records, plus the small PARTNER_ALIASES table. No fuzzy matching, no edit
    distance. An unmatched mention falls back to the general KNOWLEDGE
    DISCIPLINE prompt rules.
    """
    # (?<!\w)...(?!\w) rather than \b...\b: real Partner names end in
    # punctuation (e.g. "Pick n Pay asap!"), and a trailing \b never matches
    # after a non-word character, so \b would silently never fire for those.
    def _boundary(term: str) -> str:
        return rf"(?<!\w){re.escape(term)}(?!\w)"

    lowered = " " + message.lower() + " "

    for alias, canonical in PARTNER_ALIASES.items():
        if re.search(_boundary(alias), lowered):
            lowered += f" {canonical.lower()} "

    partner_names = sorted(
        {
            p.get("Partner name", "").strip()
            for p in kb.get("partners", [])
            if p.get("Partner name")
        },
        key=len,
        reverse=True,  # longest first, so "Uber Eats" is consumed before "Uber"
    )

    detected_names = []
    for name in partner_names:
        pattern = _boundary(name.lower())
        if re.search(pattern, lowered):
            detected_names.append(name)
            lowered = re.sub(pattern, " ", lowered)  # consume so shorter overlapping names don't also fire

    if not detected_names:
        return []

    # Partner -> Programme link field is "Programmes". REST returns linked
    # fields as bare record ID strings, MCP returns {id, name} dicts — same
    # dual-shape handling as _programme_index().
    programme_name_by_id = {
        p.get("_id"): p.get("Programme name", "")
        for p in kb.get("programmes", [])
        if p.get("_id")
    }
    canonical_programmes = {
        _norm_name(p.get("Programme name"))
        for p in kb.get("programmes", [])
        if p.get("Programme name")
    }

    def _linked_programme_names(field_value) -> set[str]:
        names = set()
        for link in field_value or []:
            if isinstance(link, dict):
                pname = link.get("name") or programme_name_by_id.get(link.get("id"), "")
            else:
                pname = programme_name_by_id.get(link, "")
            if pname:
                names.add(_norm_name(pname))
        return names

    results = []
    for name in detected_names:
        records = [
            p for p in kb.get("partners", [])
            if p.get("Partner name", "").strip() == name
        ]
        linked_programmes = set()
        for r in records:
            linked_programmes |= _linked_programme_names(r.get("Programmes"))

        for held in held_programme_names:
            # Only assert a fact for a held programme that resolves to a real KB
            # Programme record. An unrecognised name cannot be verified either
            # way — stay silent rather than emit a false "NOT a partner".
            if _norm_name(held) not in canonical_programmes:
                continue
            results.append({
                "merchant": name,
                "programme": held,
                "confirmed": _norm_name(held) in linked_programmes,
            })

    return results


def _detect_evaluated_programme(message: str, kb: dict) -> str | None:
    """
    Deterministic, non-LLM detection of which programme a Mode 3 query is
    evaluating. Returns the canonical Programme name (matches
    _programme_index()'s keys) or None if detection isn't confident.

    Conservative by design, same principle as _detect_mentioned_partners():
    exact/word-boundary matching against real Programme names plus the small
    curated alias table above, no fuzzy matching. If the message names zero
    programmes or more than one, returns None. An unconfident grounding
    target is worse than none, silence is correct here, not a failure.
    """
    lowered = " " + message.lower() + " "

    for alias, canonical in PROGRAMME_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            lowered += f" {canonical.lower()} "

    programme_names = sorted(
        {p.get("Programme name", "").strip() for p in kb["programmes"] if p.get("Programme name")},
        key=len,
        reverse=True,  # longest first: "MyDifference PLUS" before "MyDifference"
    )

    matched = []
    for name in programme_names:
        pattern = rf"\b{re.escape(name.lower())}\b"
        if re.search(pattern, lowered):
            matched.append(name)
            lowered = re.sub(pattern, " ", lowered)  # consume, avoid overlap double-count

    if len(matched) != 1:
        return None

    return matched[0]


def _detect_conflict_facts(programme_name: str, kb: dict) -> list[str]:
    """
    For a Mode 3-evaluated programme, find any Earn rate records flagged with
    a non-blank Conflict group (records sharing an identical Conflict group
    value are deliberately co-existing, unresolved facts per the field's own
    description, not a superseded pair). Returns each unique conflict's
    Conditions / notes text verbatim, deduplicated by Conflict group value,
    for injection as a fact the model must state rather than silently resolve.

    Reuses _programme_index()'s already-resolved earn_rates lookup, same
    infrastructure resolve_spend_routing() runs on for Mode 2, not new
    plumbing. Returns [] if the programme has no tagged conflicts, the normal
    case for nearly every programme and every query, silence is correct here.
    """
    index = _programme_index(kb)
    records = index.get("earn_rates", {}).get(programme_name, [])

    seen_groups = set()
    facts = []
    for r in records:
        group = (r.get("Conflict group") or "").strip()
        if not group or group in seen_groups:
            continue
        seen_groups.add(group)
        note = (r.get("Conditions / notes") or "").strip()
        if note:
            facts.append(note)

    return facts


def _check_partial_conflict_mention(reply: str, conflict_facts: list[str]) -> str:
    """
    Deterministic post-generation check, no LLM call. If a Mode 3 response
    mentions SOME but not ALL of the Rand values in a known conflict fact,
    that's the unhedged-fabrication pattern, a flat confident figure
    standing in for a disputed one, not omission. Appends a plain correction
    in that case only.

    Deliberately does not append the raw Conditions/notes text verbatim,
    that field carries internal KB-authoring metadata ("[unverified:
    conflicting sources]", capture dates) not meant for user-facing copy.
    Builds a generic correction from the extracted values instead, works
    for any future tagged conflict without depending on each note reading
    cleanly as user-facing prose.

    Two matching variants checked per value (with/without comma) to reduce
    false negatives from minor formatting drift. Not exhaustive, fuzzy
    phrasing beyond that isn't handled, accepted limitation, flag if it
    proves to matter in practice.

    Mentioning NONE of the values (omission) or ALL of them (correctly
    hedged) are both left untouched. Neither is the failure this targets.
    """
    corrections = []
    for fact in conflict_facts:
        values = re.findall(r"R[\d,]+", fact)
        if len(values) < 2:
            continue  # not a multi-value conflict, nothing to check

        mentioned = []
        for v in values:
            forms = {v, v.replace(",", "")}
            if any(f in reply for f in forms):
                mentioned.append(v)

        if 0 < len(mentioned) < len(values):
            corrections.append(
                f"Correction: this figure is disputed between sources, {' vs '.join(values)}, not resolved."
            )

    if corrections:
        reply = reply.rstrip() + "\n\n" + " ".join(corrections)

    return reply


def _record_tier_name(record: dict, tier_names: dict) -> str | None:
    """Resolve a single record's Tier link field to a tier name, or None if untiered."""
    tier_field = record.get("Tier") or []
    for link in tier_field:
        if isinstance(link, dict):
            return link.get("name")
        elif isinstance(link, str):
            return tier_names.get(link)
    return None


def _best_earn_match(
    earn_rates: list[dict],
    kb_categories: list[str],
    tier_names: dict,
    held_tier: str | None,
    is_held: bool,
) -> tuple[dict | None, bool]:
    """Returns (best_match, tier_unspecified_flag)."""
    candidates = []
    tier_gated_exists = False
    for rate in earn_rates:
        category = _select_name(rate.get("Spend category"))
        unit = _select_name(rate.get("Earn rate unit"))
        if category not in kb_categories or unit not in RAND_COMPARABLE_UNITS:
            continue
        record_tier = _record_tier_name(rate, tier_names)
        if record_tier is not None:
            tier_gated_exists = True
        if is_held and held_tier is not None:
            if record_tier is not None and record_tier != held_tier:
                continue
        candidates.append(rate)
    if not candidates:
        return None, False
    if is_held and held_tier is None and tier_gated_exists:
        return None, True  # tier_unspecified
    if is_held and held_tier is not None:
        candidates = [
            r for r in candidates
            if _record_tier_name(r, tier_names) is None
            or _record_tier_name(r, tier_names) == held_tier
        ]
        if not candidates:
            return None, False
    return max(candidates, key=lambda r: r.get("Earn rate value") or 0), False


def _best_bank_match(
    earn_rates: list[dict], tier_names: dict, held_tier: str | None
) -> tuple[dict | None, bool]:
    """Category-independent check for a held programme's general-spend cash
    back mechanic (Spend category == 'Bank / non-partner'). Only called for
    programmes the user actually holds, never aspirationally. A held
    programme's Bank/non-partner rate competes on equal footing with that
    same programme's category-specific rate — it is not a fallback
    restricted to categories with no other match, that's intentional, don't
    restrict it to the gap-filling case. Returns (best_match,
    tier_unspecified_flag), same contract as _best_earn_match, since
    Bank/non-partner records can themselves be tier-gated (e.g. ABSA
    Rewards) and picking the best across all tiers when the user's tier
    is unknown would misrepresent the return."""
    candidates = [
        r for r in earn_rates
        if _select_name(r.get("Spend category")) == "Bank / non-partner"
        and _select_name(r.get("Earn rate unit")) in RAND_COMPARABLE_UNITS
    ]
    if not candidates:
        return None, False
    tier_gated_exists = any(_record_tier_name(r, tier_names) is not None for r in candidates)
    if held_tier is None and tier_gated_exists:
        return None, True  # tier_unspecified
    if held_tier is not None:
        candidates = [
            r for r in candidates
            if _record_tier_name(r, tier_names) is None
            or _record_tier_name(r, tier_names) == held_tier
        ]
        if not candidates:
            return None, False
    return max(candidates, key=lambda r: r.get("Earn rate value") or 0), False


def _best_redemption_match(
    redemptions: list[dict],
    kb_categories: list[str],
    tier_names: dict,
    held_tier: str | None,
    is_held: bool,
) -> tuple[dict | None, bool]:
    """Returns (best_match, tier_unspecified_flag)."""
    candidates = []
    tier_gated_exists = False
    for r in redemptions:
        if _select_name(r.get("Category")) not in kb_categories:
            continue
        if r.get("Return value %") is None:
            continue
        record_tier = _record_tier_name(r, tier_names)
        if record_tier is not None:
            tier_gated_exists = True
        if is_held and held_tier is not None:
            if record_tier is not None and record_tier != held_tier:
                continue
        candidates.append(r)
    if not candidates:
        return None, False
    if is_held and held_tier is None and tier_gated_exists:
        return None, True  # tier_unspecified
    if is_held and held_tier is not None:
        candidates = [
            r for r in candidates
            if _record_tier_name(r, tier_names) is None
            or _record_tier_name(r, tier_names) == held_tier
        ]
        if not candidates:
            return None, False
    return max(candidates, key=lambda r: r.get("Return value %") or 0), False


def resolve_spend_routing(user_spec: dict, kb: dict) -> dict:
    """
    Deterministic spend routing engine. Diffs user_spec["categories"]
    (category -> monthly Rand spend) against the KB and returns per-category
    best-programme recommendations plus a total monthly uplift estimate.

    Returns {} if user_spec has no usable categories (unchanged placeholder
    behaviour for Mode 1/3 queries that don't need routing).
    """
    categories = user_spec.get("categories") or {}
    if not categories:
        return {}

    index = _programme_index(kb)
    tier_names = _tier_index(kb)
    # liq.html sends programmes_held as a list of {"name": ..., "tier": ...,
    # "balance": ...} objects. Normalise to dict[programme_name -> held_tier]
    # so tier-aware matching can filter earn rate records to the user's actual
    # tier rather than returning the best rate across all tiers.
    raw_held = user_spec.get("programmes_held") or []
    programmes_held: dict[str, str | None] = {}
    for entry in raw_held:
        if isinstance(entry, str):
            programmes_held[_norm_name(entry)] = None
        elif isinstance(entry, dict) and entry.get("name"):
            programmes_held[_norm_name(entry["name"])] = entry.get("tier") or None

    result_categories = {}
    uncategorised_matches: dict[str, list[str]] = {}
    total_uplift = 0.0
    new_programmes_recommended: set[str] = set()

    for user_category, monthly_spend in categories.items():
        alias = CATEGORY_ALIASES.get(user_category)
        if not alias:
            result_categories[user_category] = {
                "monthly_spend": monthly_spend,
                "best_programme": None,
                "return_type": "unmapped_category",
                "notes": f"'{user_category}' is not a category LIQ currently maps to the KB.",
            }
            continue

        best_percent = None
        best_per_litre = None

        for programme_name, programme in index["programmes"].items():
            earn_rates = index["earn_rates"].get(programme_name, [])
            redemptions = index["redemptions"].get(programme_name, [])

            is_held = _norm_name(programme_name) in programmes_held
            held_tier = programmes_held.get(_norm_name(programme_name))

            earn_match, earn_tier_unspecified = _best_earn_match(
                earn_rates, alias["earn_rates"], tier_names, held_tier, is_held
            )

            # Bank/non-partner: category-independent general-spend cash back,
            # checked only for held programmes, never aspirationally. Competes
            # directly against the category-specific match below — a held
            # programme's own Bank/non-partner rate can beat that same
            # programme's own category-specific rate. Intentional, not a bug.
            if is_held:
                bank_match, bank_tier_unspecified = _best_bank_match(earn_rates, tier_names, held_tier)
                if bank_match:
                    bank_unit = _select_name(bank_match.get("Earn rate unit"))
                    bank_value = bank_match.get("Earn rate value") or 0
                    bank_return_type = "percent" if bank_unit in PERCENT_UNITS else "per_litre"
                    bank_candidate = {
                        "programme_name": programme_name,
                        "record": bank_match,
                        "source_table": "earn_rates",
                        "return_type": bank_return_type,
                        "value": bank_value,
                    }
                    if bank_return_type == "percent":
                        if best_percent is None or bank_value > best_percent["value"]:
                            best_percent = bank_candidate
                    else:
                        if best_per_litre is None or bank_value > best_per_litre["value"]:
                            best_per_litre = bank_candidate
                elif bank_tier_unspecified:
                    uncategorised_matches.setdefault(user_category, [])
                    label = f"{programme_name} (Bank / non-partner rate depends on your tier — specify tier for a priced return)"
                    if label not in uncategorised_matches[user_category]:
                        uncategorised_matches[user_category].append(label)

            source_table = None
            record = None
            tier_unspecified = False
            if earn_match:
                record, source_table = earn_match, "earn_rates"
            elif earn_tier_unspecified:
                tier_unspecified = True
            else:
                redemption_match, redemption_tier_unspecified = _best_redemption_match(
                    redemptions, alias["redemptions"], tier_names, held_tier, is_held
                )
                if redemption_match:
                    record, source_table = redemption_match, "redemptions"
                elif redemption_tier_unspecified:
                    tier_unspecified = True

            if record is None:
                if tier_unspecified:
                    uncategorised_matches.setdefault(user_category, [])
                    label = f"{programme_name} (rate depends on your tier — specify tier for a priced return)"
                    if label not in uncategorised_matches[user_category]:
                        uncategorised_matches[user_category].append(label)
                else:
                    for rate in earn_rates:
                        category = _select_name(rate.get("Spend category"))
                        unit = _select_name(rate.get("Earn rate unit"))
                        if category in alias["earn_rates"] and unit not in RAND_COMPARABLE_UNITS:
                            uncategorised_matches.setdefault(user_category, [])
                            if programme_name not in uncategorised_matches[user_category]:
                                uncategorised_matches[user_category].append(programme_name)
                continue

            if source_table == "earn_rates":
                unit = _select_name(record.get("Earn rate unit"))
                value = record.get("Earn rate value") or 0
                return_type = "percent" if unit in PERCENT_UNITS else "per_litre"
            else:
                value = record.get("Return value %") or 0
                return_type = "percent"

            candidate = {
                "programme_name": programme_name,
                "record": record,
                "source_table": source_table,
                "return_type": return_type,
                "value": value,
            }

            if return_type == "percent":
                if best_percent is None or value > best_percent["value"]:
                    best_percent = candidate
            else:
                if best_per_litre is None or value > best_per_litre["value"]:
                    best_per_litre = candidate

        best_overall = best_percent or best_per_litre
        alternative = best_per_litre if best_percent else None

        if best_overall is None:
            result_categories[user_category] = {
                "monthly_spend": monthly_spend,
                "best_programme": None,
                "return_type": "no_match",
                "notes": "No programme in the current verified KB has a priceable rate for this category.",
            }
            continue

        programme_name = best_overall["programme_name"]
        programme = index["programmes"].get(programme_name, {})
        record = best_overall["record"]
        return_type = best_overall["return_type"]
        value = best_overall["value"]

        entry = {
            "monthly_spend": monthly_spend,
            "best_programme": programme_name,
            "return_type": return_type,
            "return_rate": value,
            "source_table": best_overall["source_table"],
            "friction_score": programme.get("Friction score"),
            "requires_financial_product": programme.get("Requires financial product", False),
            "notes": record.get("Conditions / notes") or record.get("Notes") or "",
        }

        # Cap amount is free text (e.g. "R150/month" or "20% of monthly
        # spend"), not a structured number — flag it in the response notes
        # rather than attempting to parse and enforce it against the sum.
        cap_amount = record.get("Cap amount")
        if cap_amount:
            cap_note = f"Capped: {cap_amount}, not enforced in this total, check the source."
            entry["notes"] = f"{entry['notes']} {cap_note}".strip()

        if return_type == "percent":
            estimated_return = round(monthly_spend * (value / 100), 2)
            entry["estimated_monthly_return"] = estimated_return
            total_uplift += estimated_return
            if _norm_name(programme_name) not in programmes_held:
                new_programmes_recommended.add(programme_name)

        if alternative:
            entry["alternative"] = {
                "programme": alternative["programme_name"],
                "return_type": alternative["return_type"],
                "return_rate": alternative["value"],
                "notes": "Not directly comparable to the percent-based recommendation above — "
                         "this is a flat per-litre rate and cannot be priced without a fuel price assumption.",
            }

        result_categories[user_category] = entry

    friction_penalty_applied = FRICTION_PENALTY * len(new_programmes_recommended)
    total_uplift_net = round(total_uplift - friction_penalty_applied, 2)

    return {
        "categories": result_categories,
        "uncategorised_kb_matches": uncategorised_matches,
        "total_monthly_uplift": total_uplift_net,
        "friction_penalty_applied": friction_penalty_applied,
    }


# ─── Mode Detection ───────────────────────────────────────────────────────────

def detect_mode(message: str, user_spec: dict | None) -> str:
    """
    Classify the user query into Mode 1, 2, or 3.
    - Mode 1 (choice):  short query, retailer or category mentioned, no full spend profile
    - Mode 2 (review):  full spend profile provided (user_spec populated)
    - Mode 3 (joining): specific programme named in query
    Returns "1", "2", or "3".
    """
    if user_spec and user_spec.get("categories"):
        return "2"
    msg_lower = message.lower()
    joining_signals = ["worth it", "should i join", "is it worth", "thinking of joining", "considering"]
    if any(s in msg_lower for s in joining_signals):
        return "3"
    return "1"


# ─── Session Store ────────────────────────────────────────────────────────────

sessions: dict[str, dict] = {}


def get_session(session_id: str) -> dict:
    now = datetime.utcnow()
    if session_id not in sessions:
        sessions[session_id] = {
            "history": [],
            "message_count": 0,
            "created": now,
            "last_active": now,
        }
    sessions[session_id]["last_active"] = now
    return sessions[session_id]


def prune_sessions():
    cutoff = datetime.utcnow() - timedelta(seconds=SESSION_TTL)
    expired = [sid for sid, s in sessions.items() if s["last_active"] < cutoff]
    for sid in expired:
        del sessions[sid]


# ─── Flask App ────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(
    app,
    origins=ALLOWED_ORIGINS,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    supports_credentials=False,
    max_age=86400,
)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "86400"
    return response


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/stats", methods=["GET"])
def stats():
    """Return live KB counts derived from the cached KB."""
    kb = get_kb()
    # Only count programmes with a populated LLM context block — placeholder
    # records exist for upcoming programmes but have no context written yet.
    live = [p for p in kb.get("programmes", []) if p.get("LLM context block")]
    return jsonify({
        "programme_count": len(live),
    })


@app.route("/session", methods=["GET"])
def session_status():
    """Return remaining query count for a session without consuming a query."""
    session_id = request.args.get("session_id", "")
    if not session_id:
        return jsonify({"remaining": RATE_LIMIT})
    prune_sessions()
    s = get_session(session_id)
    remaining = max(0, RATE_LIMIT - s["message_count"])
    return jsonify({"remaining": remaining})


@app.route("/ping", methods=["GET", "OPTIONS"])
def ping():
    return jsonify({"pong": True})


@app.route("/")
def index():
    return send_from_directory(".", "liq.html")


@app.route("/onboarding")
def onboarding():
    return send_from_directory(".", "liq-onboarding.html")


@app.route("/storage-test")
def storage_test():
    # Disposable: cross-origin IndexedDB persistence spike, delete once answered.
    return send_from_directory(".", "storage-test.html")


@app.route("/analyse", methods=["POST"])
def analyse():
    """
    Single-shot structured endpoint.
    Accepts a user spec sheet and returns a pre-computed routing verdict.
    Equivalent to AIGA's /analyse endpoint.

    Request body:
    {
        "message": "optional natural language context",
        "user_spec": {
            "categories": {
                "groceries": 3500,
                "fuel": 1200,
                "pharmacy": 800,
                "dining": 600,
                "clothing": 900,
                "travel": 500
            },
            "programmes_held": ["Xtra Savings", "eBucks"],  // optional
            "lifestyle_flags": []                            // phase 2
        }
    }

    Response:
    {
        "routing": { ... },   // pre-computed spend routing output
        "response": "..."     // LLM prose narration
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        message = html.escape(str(data.get("message", "")).strip())[:MAX_INPUT_LEN]
        user_spec = data.get("user_spec", {})

        if not user_spec and not message:
            return jsonify({"error": "Empty request"}), 400

        kb = get_kb()
        routing = resolve_spend_routing(user_spec, kb)

        # Pull LLM context blocks for all relevant programmes
        context_blocks = [
            p.get("LLM context block", "")
            for p in kb["programmes"]
            if p.get("LLM context block")
        ]

        held_programmes = _held_programmes_display(user_spec)
        system_prompt = build_system_prompt(context_blocks, routing, held_programmes)

        user_content = message or "Analyse my spend profile and return routing advice."
        if routing:
            user_content += f"\n\nMy spend profile: {json.dumps(user_spec)}"

        response = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        return jsonify({
            "routing": routing,
            "response": response.content[0].text,
        })

    except Exception as e:
        print(f"[LIQ] /analyse error: {e}")
        return jsonify({"error": "Analysis failed. Please try again."}), 500


@app.route("/chat", methods=["POST"])
def chat():
    """
    Conversational multi-turn endpoint.
    Handles Mode 1 (choice) and Mode 3 (joining) queries.
    Equivalent to AIGA's /chat endpoint.

    Request body:
    {
        "message": "Is Discovery Vitality worth it?",
        "session_id": "optional-existing-session-id",
        "user_spec": {}   // optional — if provided, triggers Mode 2 routing
    }
    """
    prune_sessions()

    try:
        data = request.get_json(silent=True) or {}
        raw_message = str(data.get("message", "")).strip()
        session_id = str(data.get("session_id", "")).strip() or str(uuid.uuid4())
        user_spec = data.get("user_spec", {})
    except Exception:
        return jsonify({"error": "Invalid request."}), 400

    if not raw_message:
        return jsonify({"error": "Empty message."}), 400

    message = html.escape(raw_message)[:MAX_INPUT_LEN]
    session = get_session(session_id)

    if session["message_count"] >= RATE_LIMIT:
        return jsonify({
            "error": "Daily limit reached. Come back tomorrow.",
            "session_id": session_id,
        }), 429

    kb = get_kb()
    mode = detect_mode(message, user_spec)
    routing = resolve_spend_routing(user_spec, kb) if mode == "2" else {}
    held_programmes = _held_programmes_display(user_spec)

    merchant_facts = []
    if mode in ("1", "3"):
        held_names = [h["name"] for h in held_programmes]
        merchant_facts = _detect_mentioned_partners(message, held_names, kb)

    conflict_facts = []
    if mode == "3":
        evaluated_programme = _detect_evaluated_programme(message, kb)
        if evaluated_programme:
            conflict_facts = _detect_conflict_facts(evaluated_programme, kb)

    context_blocks = [
        p.get("LLM context block", "")
        for p in kb["programmes"]
        if p.get("LLM context block")
    ]

    system_prompt = build_system_prompt(
        context_blocks, routing or None, held_programmes, merchant_facts, conflict_facts
    )

    history = list(session["history"])
    history.append({"role": "user", "content": message})

    try:
        response = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system_prompt,
            messages=history,
        )
        reply = response.content[0].text
        if mode == "3" and conflict_facts:
            reply = _check_partial_conflict_mention(reply, conflict_facts)

        session["history"].append({"role": "user", "content": message})
        session["history"].append({"role": "assistant", "content": reply})
        if len(session["history"]) > CONTEXT_TURNS * 2:
            session["history"] = session["history"][-(CONTEXT_TURNS * 2):]
        session["message_count"] += 1

        remaining = RATE_LIMIT - session["message_count"]
        return jsonify({
            "response": reply,
            "session_id": session_id,
            "remaining": remaining,
            "mode": mode,
        })

    except anthropic.APIStatusError as e:
        if e.status_code == 529:
            return jsonify({
                "response": "LIQ is overloaded right now. Try again in a moment.",
                "session_id": session_id,
                "remaining": RATE_LIMIT - session["message_count"],
            }), 200
        return jsonify({"error": "Could not reach LIQ. Please try again."}), 500
    except Exception as e:
        print(f"[LIQ] /chat error: {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
