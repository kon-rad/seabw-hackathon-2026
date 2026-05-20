# MiroShark Polymarket Platform — Design Spec
**Date:** 2026-05-20  
**Status:** Approved  

---

## 1. Goal

A web application that automatically finds the best Polymarket prediction markets, runs deep AI research on each one, simulates them through MiroShark's swarm intelligence engine, and places actual bets on Polymarket mainnet based on the simulation outcome. The user only needs to open the app, pick a bet, and approve the final trade.

---

## 2. Architecture Overview

```
DigitalOcean VPS (2GB RAM, 40GB disk)
┌──────────────────────────────────────────────────────┐
│                                                      │
│  ┌─────────────────────┐   ┌──────────────────────┐  │
│  │  web (Next.js :3000)│   │  api (FastAPI :8000) │  │
│  │  Fork of MiroShark  │◄──►  MiroShark Python    │  │
│  │                     │   │  engine wrapper      │  │
│  │  • Bet discovery    │   │                      │  │
│  │  • Research UI      │   │  • /simulate         │  │
│  │  • Pi-mono agent    │   │  • /simulate stream  │  │
│  │  • Simulation dash  │   │  • /bet              │  │
│  └─────────────────────┘   └──────────────────────┘  │
│           │                          │               │
│           └──────── /data/research ──┘               │
│                   (shared volume)                    │
│                                                      │
│  nginx :80  (reverse proxy)                          │
└──────────────────────────────────────────────────────┘
          │                        │
  ┌───────▼──────┐        ┌────────▼──────────┐
  │  Polymarket  │        │  Neo4j Aura Free  │
  │  Gamma API   │        │  (cloud, external)│
  │  CLOB API    │        └────────┬──────────┘
  └──────────────┘                 │
          │                ┌───────▼──────┐
          └────────────────►  Together AI │
                           │  (LLM calls) │
                           └──────────────┘
```

**Two Docker services:**
- `web` — MiroShark Next.js app (forked), extended with bet discovery and research pages
- `api` — Python FastAPI service wrapping MiroShark's simulation engine

**One shared Docker volume** (`research_data`) mounted at `/data/research/` in both containers so the pi-mono agent (runs in `web`) can write research files that the MiroShark engine (runs in `api`) ingests.

---

## 3. Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend + research agent | Next.js 14 (fork of MiroShark), TypeScript |
| Simulation dashboard | MiroShark existing UI (reused as-is) |
| Research agent framework | pi-mono (TypeScript agent toolkit) |
| Backend simulation service | Python FastAPI |
| Simulation engine | MiroShark (fork of aaronjmars/MiroShark) |
| Knowledge graph | Neo4j Aura Free (cloud) |
| LLM — research agent | Together AI `meta-llama/Llama-3.3-70B-Instruct-Turbo-Free` |
| LLM — swarm agents | Together AI `meta-llama/Llama-3.1-8B-Instruct-Turbo` |
| Prediction market discovery | Polymarket Gamma API |
| Prediction market trading | Polymarket CLOB API (Polygon mainnet) |
| Reverse proxy | nginx |
| Deployment | Docker Compose, DigitalOcean Droplet |
| Payments | Stubbed (free for demo) |

---

## 4. LLM Configuration

Both pi-mono and MiroShark use Together AI's OpenAI-compatible endpoint:

```
Base URL: https://api.together.xyz/v1
API Key:  TOGETHER_AI_API_KEY
```

**Pi-mono research agent:** `meta-llama/Llama-3.3-70B-Instruct-Turbo-Free`
- Free tier, rate-limited but sufficient for sequential research rounds
- Used for: web result synthesis, seed document compilation

**MiroShark swarm agents:** `meta-llama/Llama-3.1-8B-Instruct-Turbo`
- $0.18/M tokens — cheapest paid model
- Used for: 100s of agent persona responses per simulation round
- MiroShark configured via env var override (replaces default OpenRouter key)

---

## 5. Components

### 5.1 Polymarket Scout

**Location:** `web/app/api/markets/route.ts`

Calls the Polymarket Gamma API and returns the top 8 scored markets.

**Gamma API call:**
```
GET https://gamma-api.polymarket.com/markets
  ?closed=false
  &active=true
  &order=volume
  &ascending=false
  &limit=100
```

**Suitability scoring (0–100):**

| Signal | Weight | Logic |
|--------|--------|-------|
| Description length | 20 | >200 chars = full score |
| Topic category | 30 | Politics/crypto/tech=30, sports=20, celebrity=10 |
| Volume | 20 | >$100k=20, >$10k=10, else=0 |
| Time to resolution | 15 | 48h–30 days = full score |
| Price not near 50/50 | 15 | \|price - 0.5\| > 0.1 = full score (edge exists) |

Returns top 8 markets with score, title, current YES price, volume, resolution date.

---

### 5.2 Pi-mono Research Agent

**Location:** `web/app/api/research/[marketId]/route.ts` (SSE endpoint)

Runs as a streaming Next.js API route using pi-mono's agent loop. Writes output to the shared volume.

**Research loop (4 rounds):**

```
Round 1: web_search("[bet title] latest news 2026")
Round 2: web_search("[bet title] historical data statistics")
Round 3: web_search("[bet title] expert analysis prediction")
Round 4: web_search("[key entities from bet] relationships background")
```

Each round:
1. Fetches top 5 search results
2. Reads full text of top 3 results
3. LLM extracts: key facts, entities, sentiment signals, statistics
4. Appends structured findings to `seed.md`
5. Streams a progress event to the UI

**Output files written to `/data/research/{marketId}/`:**

```
seed.md        — structured markdown document for MiroShark ingestion
sources.json   — [{url, title, round}] list of all sources used
metadata.json  — {marketId, title, resolutionDate, startedAt, completedAt, rounds}
```

**`seed.md` structure:**
```markdown
# [Bet Title]

## Market Context
[description, current price, volume, resolution date]

## Key Facts
- [fact 1]
- [fact 2]

## Key Entities
[people, organizations, events]

## Statistical Evidence
[numbers, polls, historical rates]

## Expert Opinions & Predictions
[analyst quotes, forecast data]

## Recent Developments
[news timeline]

## Sentiment Signals
[social/media sentiment direction]
```

**SSE events emitted to UI:**
```json
{"type": "round_start", "round": 1, "query": "..."}
{"type": "source_found", "url": "...", "title": "..."}
{"type": "facts_extracted", "facts": ["...", "..."]}
{"type": "round_complete", "round": 1}
{"type": "research_complete", "seedPath": "/data/research/{id}/seed.md"}
```

---

### 5.3 MiroShark Simulation Service

**Location:** `api/` (FastAPI, Python)

Thin HTTP wrapper around MiroShark's Python engine. Manages simulation lifecycle and streams events.

**Endpoints:**

```
POST /simulate
  Body: { marketId, marketTitle, resolutionDate, currentYesPrice }
  Action: reads seed.md from shared volume, starts MiroShark simulation
  Returns: { simulationId }

GET /simulate/{simulationId}/stream
  SSE stream of live simulation events (agent posts, trades, belief updates)
  Forwards MiroShark's internal event bus to the HTTP client

GET /simulate/{simulationId}/result
  Returns: {
    recommendation: "YES" | "NO",
    swarmPrice: 0.73,
    marketPrice: 0.61,
    edge: 0.12,
    confidence: "HIGH" | "MEDIUM" | "LOW",
    reasoning: "..."
  }

POST /bet
  Body: { marketId, outcome: "YES"|"NO", usdcAmount }
  Action: signs + submits limit order via Polymarket CLOB API
  Returns: { txHash, orderId }
```

**MiroShark configuration overrides (env vars):**
```
TOGETHER_AI_API_KEY        — replaces OpenRouter key
TOGETHER_BASE_URL          — https://api.together.xyz/v1
SWARM_MODEL                — meta-llama/Llama-3.1-8B-Instruct-Turbo
NEO4J_URI                  — Aura Free URI
NEO4J_PASSWORD
SIMULATION_PLATFORM        — polymarket   (activates Polymarket sim module)
```

MiroShark's Polymarket simulation module (Wonderwall) is already present in the repo — it was added in a commit bundling the full simulation engine. We enable it by setting `SIMULATION_PLATFORM=polymarket`.

---

### 5.4 Frontend Pages

All pages live in the MiroShark Next.js fork under `web/app/`.

**`/` — Bet Discovery**
- On load: fetches `/api/markets`, shows 8 scored market cards
- Each card: title, YES price, volume, resolution date, suitability score badge
- "Research This Bet" button → navigates to `/research/[marketId]`
- Auto-refreshes every 5 minutes

**`/research/[marketId]` — Research Progress**
- Opens SSE connection to `/api/research/[marketId]`
- Shows: round progress indicator, sources list (clickable links), live seed.md preview
- On `research_complete` event: "Run Simulation →" button appears
- Clicking it: POSTs to `api` service `/simulate`, navigates to `/simulate/[simulationId]`

**`/simulate/[simulationId]` — MiroShark Dashboard (existing UI)**
- MiroShark's existing simulation dashboard, unchanged
- Opens SSE connection to `api` service `/simulate/{id}/stream`
- Shows: agent conversation feed, market price chart, belief state heatmap, portfolio P&L
- On simulation complete: recommendation banner appears with edge calculation
- "Place Bet" button → opens confirmation modal (outcome, amount, estimated fill price)
- On confirm: POSTs to `/api/bet` → shows tx hash + Polygonscan link

---

## 6. User Journey

```
1. Open app
   └─► Bet Discovery page loads with top 8 Polymarket markets

2. Click "Research This Bet" on a market card
   └─► Pi-mono agent starts (4 rounds, ~2-3 min)
   └─► Watch sources found and seed doc building in real time

3. Click "Run Simulation →"
   └─► MiroShark starts with seed.md as input
   └─► Navigate to simulation dashboard

4. Watch simulation (~10 min)
   └─► Agents post, trade, update beliefs
   └─► Market price evolves toward swarm consensus

5. Simulation completes
   └─► Banner: "BET YES — Swarm: 73%, Market: 61%, Edge: +12%"
   └─► Enter USDC amount → click "Place Bet"

6. Bet placed
   └─► Tx hash shown with Polygonscan link
   └─► Order visible in Polymarket UI
```

---

## 7. Shared Volume Structure

```
/data/research/
  {marketId}/
    seed.md          ← pi-mono writes, MiroShark reads
    sources.json     ← shown in research UI
    metadata.json    ← bet info + research timestamps

/data/simulations/
  {simulationId}/
    result.json      ← final recommendation
    events.jsonl     ← full event log for replay/inspection
```

---

## 8. Docker Compose

```yaml
version: "3.9"

services:
  nginx:
    image: nginx:alpine
    ports: ["80:80"]
    volumes: ["./nginx.conf:/etc/nginx/nginx.conf:ro"]
    depends_on: [web, api]

  web:
    build: ./web
    environment:
      - API_SERVICE_URL=http://api:8000
      - TOGETHER_AI_API_KEY=${TOGETHER_AI_API_KEY}
      - POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
    volumes:
      - research_data:/data/research

  api:
    build: ./api
    environment:
      - TOGETHER_AI_API_KEY=${TOGETHER_AI_API_KEY}
      - TOGETHER_BASE_URL=https://api.together.xyz/v1
      - SWARM_MODEL=meta-llama/Llama-3.1-8B-Instruct-Turbo
      - NEO4J_URI=${NEO4J_URI}
      - NEO4J_PASSWORD=${NEO4J_PASSWORD}
      - POLYMARKET_PRIVATE_KEY=${POLYMARKET_PRIVATE_KEY}
      - SIMULATION_PLATFORM=polymarket
    volumes:
      - research_data:/data/research

volumes:
  research_data:
```

**nginx.conf routing:**
```
/api/simulate  → api:8000
/api/bet       → api:8000
/*             → web:3000
```

---

## 9. Environment Variables

```bash
# Together AI
TOGETHER_AI_API_KEY=

# Neo4j Aura Free
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_PASSWORD=

# Polymarket (Polygon mainnet wallet, funded with USDC)
POLYMARKET_PRIVATE_KEY=
```

---

## 10. RAM Budget

| Service | Idle | Peak (simulation running) |
|---------|------|--------------------------|
| OS + nginx | 200MB | 200MB |
| Next.js (web) | 250MB | 350MB |
| FastAPI (api) | 150MB | 150MB |
| MiroShark simulation | 0MB | 600MB |
| **Total** | **600MB** | **1.3GB** |

Fits within 2GB. Neo4j on Aura Free (cloud) saves ~600MB vs running it locally.

---

## 11. Payments (Stubbed)

Payments are free for the demo. The payment gate is a no-op middleware in the Next.js API routes. The architecture supports adding USDC-on-Polygon payment verification later: check for an on-chain transfer to a treasury wallet before allowing research/simulation to proceed.

---

## 12. Repository Structure

```
seabw-2026/
  web/              ← MiroShark Next.js fork
    app/
      page.tsx                        ← bet discovery
      research/[marketId]/page.tsx    ← research progress
      simulate/[simulationId]/page.tsx← simulation dashboard (existing)
      api/
        markets/route.ts              ← Polymarket Gamma API + scorer
        research/[marketId]/route.ts  ← pi-mono SSE agent
        bet/route.ts                  ← proxy to api service
  api/              ← FastAPI + MiroShark Python
    main.py                           ← FastAPI app
    simulate.py                       ← MiroShark wrapper
    bet.py                            ← CLOB API integration
  nginx.conf
  docker-compose.yml
  .env.example
  docs/
    superpowers/specs/
      2026-05-20-miroshark-polymarket-design.md
```

---

## 13. Out of Scope (MVP)

- User accounts / auth
- Multi-user concurrency (one simulation at a time)
- Crypto payment rails (stubbed as free)
- Portfolio tracking / bet history
- Multiple simultaneous simulations
- Custom bet entry (AI scouts markets automatically)
