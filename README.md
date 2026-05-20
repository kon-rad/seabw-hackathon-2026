# MiroShark × Polymarket

**AI-powered prediction market scout.** Finds the best Polymarket bets, runs deep research, simulates outcomes with a 100-agent swarm, and places the trade on Polygon mainnet — automatically.

---

## The Problem

Prediction markets are one of the most information-dense environments on the internet. Polymarket alone processes hundreds of millions of dollars in volume on questions about politics, crypto, science, and world events. The crowd is smart — but it isn't omniscient.

**Edges exist.** Markets misprice events all the time — when news breaks slowly, when expert analysis is buried in obscure sources, when the crowd anchors to narrative instead of data. Finding those edges is the hard part. It requires:

- Continuously monitoring dozens of live markets
- Running deep research across news, statistics, and expert opinion
- Synthesizing that research into a coherent probability estimate
- Acting before the market corrects

No individual can do this at scale. Most people bet on gut instinct. The ones who win systematically are running quantitative research operations. Everyone else is just donating to them.

**MiroShark × Polymarket closes that gap.**

---

## How It Works

**1. Scout**
The app fetches live Polymarket markets and scores each one for AI-researchability — weighting topic category, liquidity, description richness, time to resolution, and current price inefficiency. The top 8 are surfaced in real time.

**2. Research**
A 4-round AI research agent digs into the selected market. Each round hits the web with a targeted query — latest news, historical statistics, expert analysis, key entity relationships. It extracts facts, sentiment signals, and statistical evidence, building a structured seed document that captures everything knowable about the outcome.

**3. Simulate**
MiroShark's swarm engine spawns 100+ AI agents — each with a distinct persona, belief system, and risk tolerance. They read the research, argue about the outcome, post takes, place trades, and update their beliefs as the simulation runs. The swarm converges on a consensus probability the same way a real crowd does — except it runs in minutes instead of days, and it's grounded in the research document rather than social noise.

**4. Bet**
When the swarm price diverges from the market price by a meaningful edge, the app surfaces the recommendation with a confidence rating. One click places a limit order on the Polymarket CLOB via your Polygon wallet. The tx hash links straight to Polygonscan.

---

## Stack

| Layer | Technology |
|-------|------------|
| Frontend | Vue.js (MiroShark fork) |
| Backend | Flask + MiroShark simulation engine |
| Research agent | Together AI — Llama 3.3 70B (free tier) |
| Swarm agents | Together AI — Llama 3.1 8B ($0.18/M tokens) |
| Knowledge graph | Neo4j Aura Free |
| Prediction market | Polymarket Gamma API + CLOB API on Polygon |
| Deployment | Docker Compose, DigitalOcean Droplet |

**Cost per run:** research is free; swarm simulation ~$0.10–0.30.

---

## Setup

### Prerequisites

- Node 18+, Python 3.11+, [uv](https://github.com/astral-sh/uv)
- [Together AI key](https://api.together.xyz) (free tier works)
- [Neo4j Aura Free](https://neo4j.com/cloud/aura-free/) instance
- Polygon wallet funded with USDC

### 1. Configure environment

```bash
cp web/.env.together-ai web/.env
```

Fill in `web/.env`:
- `LLM_API_KEY` — your Together AI key
- `NEO4J_URI` + `NEO4J_PASSWORD` — Aura Free credentials
- `POLYMARKET_PRIVATE_KEY` — Polygon wallet private key
- `MIROSHARK_ADMIN_TOKEN` — run `openssl rand -hex 32`

### 2. Install dependencies

```bash
npm run setup --prefix web
cd web/backend && uv sync
```

### 3. Run locally

```bash
npm run dev --prefix web
```

- Frontend: http://localhost:3000
- Backend: http://localhost:5001

### 4. Deploy (Docker)

```bash
git clone https://github.com/kon-rad/seabw-hackathon-2026.git
cd seabw-hackathon-2026
cp web/.env.together-ai web/.env
# fill in web/.env
docker compose up -d
```

App runs on port 80.

---

## Project Structure

```
seabw-2026/
  web/                          # MiroShark fork (Vue + Flask)
    frontend/src/
      views/
        BetDiscovery.vue        # bet scouting home page
        ResearchView.vue        # research agent UI
      components/
        BetPanel.vue            # bet placement widget
    backend/app/api/
      polymarket.py             # markets / research SSE / bet endpoints
  docker-compose.yml
  nginx.conf
```

---

## Video Script

*3-minute demo script — read straight through, no commentary needed.*

---

Prediction markets are broken — not because they don't work, but because most people don't have the tools to use them well.

Polymarket has hundreds of millions of dollars in volume. The questions are real: Will this candidate win? Will this bill pass? Will this token hit a new high? The crowd sets prices. And sometimes — the crowd is wrong.

When a market is mispriced, there's an edge. A real, exploitable gap between what the market thinks will happen and what the evidence actually says. Finding that edge is the hard part.

That's what MiroShark × Polymarket does.

You open the app. It's already scanning live markets — scoring each one for research potential. Volume, topic, time to resolution, how far the price is from fifty-fifty. The top opportunities surface automatically. No hunting. No spreadsheets.

Pick a market. One click starts the research agent. It runs four rounds of targeted web searches — latest news, historical statistics, expert analysis, key players. It reads the sources. It extracts facts, numbers, sentiment signals. It builds a structured research document in real time. You watch it happen.

When the research is done, you run the simulation. MiroShark spawns over a hundred AI agents — each one with a different persona, a different risk tolerance, a different prior belief about the outcome. They read the research. They argue. They post takes. They place trades inside the simulation and update their beliefs as new information flows through.

The swarm converges on a number. A probability. And we compare it to the market price.

If the swarm thinks there's a 73% chance of YES — and the market is pricing it at 61% — that's a twelve-point edge. That's the signal.

One button. Enter your USDC amount. Confirm. The order goes live on Polymarket's order book via the Polygon blockchain. The transaction hash appears on screen. Your bet is in.

The whole thing — from opening the app to a live bet — takes about fifteen minutes. The research is free. The simulation costs less than a quarter. The edge is real.

This is what systematic prediction market trading looks like when it's accessible to everyone, not just the teams running quantitative research operations.

MiroShark × Polymarket. Built at SEABW 2026.

---

## Notes

- RAM usage peaks at ~1.3GB during simulation — fits a 2GB droplet when Neo4j runs on Aura Free
- Payments are stubbed as free for the demo; the architecture supports USDC-on-Polygon payment gates
- One simulation runs at a time (MVP scope)
