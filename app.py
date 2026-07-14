"""
LoyaltyIQ Web App
SA Loyalty Programme Optimiser
Built by Network Grey | Powered by Anthropic Claude
"""

import os
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
- Make programme comparisons outside the 6 POC programmes in the current KB
- Give personalised investment or financial advice
"""


def build_system_prompt(
    context_blocks: list[str],
    routing_output: dict | None,
    held_programmes: list[dict] | None = None,
) -> str:
    """
    Assemble the full system prompt for a query:
    - Base persona and rules
    - Relevant programme LLM context blocks
    - User's stated held programmes (fact, every mode, independent of routing)
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

    context_blocks = [
        p.get("LLM context block", "")
        for p in kb["programmes"]
        if p.get("LLM context block")
    ]

    system_prompt = build_system_prompt(context_blocks, routing or None, held_programmes)

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
