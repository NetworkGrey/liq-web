# LoyaltyIQ Web (liq-web)

LoyaltyIQ is a personalised SA loyalty programme optimiser built by Network Grey, powered by Anthropic Claude.

## Architecture

Forked from `NetworkGrey/aiga-web`. Retains the Flask + Railway architecture.

## Endpoints

| Route | Method | Description |
|---|---|---|
| /health | GET | Health check |
| /ping | GET | Ping |
| / | GET | LIQ UI (placeholder) |
| /analyse | POST | Single-shot spend routing — structured input, JSON verdict |
| /chat | POST | Conversational adviser — multi-turn, session-managed |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| ANTHROPIC_API_KEY | Yes | Anthropic API key |
| AIRTABLE_API_KEY | Yes | Airtable personal access token |
| PORT | No | Port (default 8080 — Railway sets automatically) |

## KB

Live-fetched from Airtable base `appOHcS0fhY2jLyJJ` at server start. Cached for 60 minutes. No redeployment needed when KB data changes in Airtable.

## What's not yet built

- `resolve_spend_routing()` — spend routing engine (next instruction)
- `liq.html` — full UI (pending UX thread)

## Stack

| Component | Detail |
|---|---|
| Language | Python 3.12.8 |
| Framework | Flask + Flask-CORS |
| AI backend | Anthropic Claude Sonnet 4.6 |
| KB | Airtable REST API |
| Hosting | Railway.app |
| Deployment | Auto-deploy from main branch |

Built by Network Grey | Powered by Anthropic Claude
