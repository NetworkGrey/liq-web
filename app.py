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
SESSION_TTL     = 1800     # 30 minutes in seconds
RATE_LIMIT      = 100      # messages per day per session — TEMPORARY, raised for testing phase, revert to 10 before public launch
FRICTION_PENALTY = 50      # rand penalty per friction point in optimiser

ALLOWED_ORIGINS = [
    "https://liq-web-production.up.railway.app",
    "https://networkgrey.co.za",
    "https://www.networkgrey.co.za",
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
    return [r["fields"] for r in records]


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


def build_system_prompt(context_blocks: list[str], routing_output: dict | None) -> str:
    """
    Assemble the full system prompt for a query:
    - Base persona and rules
    - Relevant programme LLM context blocks
    - Pre-computed routing output (if Mode 1 or 2)
    """
    prompt = LIQ_SYSTEM_PROMPT_BASE

    if context_blocks:
        prompt += "\n\n## VERIFIED PROGRAMME KB DATA\n"
        for block in context_blocks:
            prompt += f"\n{block}\n"

    if routing_output:
        prompt += (
            "\n\n## PRE-COMPUTED SPEND ROUTING (verified, do not recompute)\n"
            + json.dumps(routing_output, ensure_ascii=False, indent=2)
        )

    return prompt


# ─── Spend Routing Engine ─────────────────────────────────────────────────────
# PLACEHOLDER — to be implemented in the next instruction once UX output
# format is confirmed. This function will:
#   1. Accept a user spend profile (categories + monthly amounts)
#   2. Pull earn rates and redemption values from the KB
#   3. Calculate ZAR return per programme per category
#   4. Apply friction scoring
#   5. Rank and return routing advice
#
# The LLM receives the output of this function, not raw KB data.

def resolve_spend_routing(user_spec: dict, kb: dict) -> dict:
    """
    PLACEHOLDER — spend routing engine.
    Returns an empty dict until implemented.
    Replace this function body with the full engine in the next instruction.
    """
    return {}


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
    return jsonify({
        "programme_count": len(kb.get("programmes", [])),
    })


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

        system_prompt = build_system_prompt(context_blocks, routing)

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

    context_blocks = [
        p.get("LLM context block", "")
        for p in kb["programmes"]
        if p.get("LLM context block")
    ]

    system_prompt = build_system_prompt(context_blocks, routing or None)

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
