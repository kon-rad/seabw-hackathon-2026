# Simulation Agent × Polymarket

> AI-powered prediction market scout — finds the best bets, researches them, simulates outcomes with a 100-agent swarm, and places the trade on Polygon mainnet.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Node 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)
[![Built at SEABW 2026](https://img.shields.io/badge/built%20at-SEABW%202026-orange.svg)]()

---

## What It Does

Prediction markets misprice events. When they do, there's an edge. Finding that edge requires continuously monitoring markets, running deep research, and synthesizing it into a probability estimate — faster than the market corrects.

**Simulation Agent × Polymarket does this automatically:**

1. **Scout** — fetches live Polymarket markets, scores them for AI-researchability (volume, topic, price inefficiency, time to resolution)
2. **Research** — a 4-round web agent digs into the market: news, statistics, expert analysis, key entities — compiling a structured seed document
3. **Simulate** — Simulation Agent spawns 100+ AI agents with distinct personas who read the research, argue, post, trade, and update beliefs round by round
4. **Bet** — when the swarm price diverges from the market price, one click places a limit order on the Polymarket CLOB on Polygon mainnet

**Cost per run:** research is free (Llama 3.3 70B free tier); simulation is ~$0.10–0.30.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              DigitalOcean VPS (2GB)             │
│                                                  │
│   nginx :80                                      │
│     ├── /api/*  →  Flask :5001  (Simulation Agent) │
│     └── /*      →  Vue.js :3000                 │
│                                                  │
│   Shared volume: /data/research/{marketId}/      │
│     seed.md  sources.json  metadata.json         │
└──────────────────┬──────────────────────────────┘
                   │
        ┌──────────┼──────────────┐
        ▼          ▼              ▼
  Polymarket    Neo4j          Together AI
  Gamma API     Aura Free      Llama 3.1/3.3
  CLOB API      (cloud)        (LLM calls)
```

| Layer | Technology |
|-------|------------|
| Frontend | Vue.js 3 + Vite |
| Backend | Python Flask + Simulation Agent engine |
| Research agent | Together AI — Llama 3.3 70B Instruct Turbo **Free** |
| Swarm agents | Together AI — Llama 3.1 8B Instruct Turbo ($0.18/M tokens) |
| Knowledge graph | Neo4j Aura Free |
| Web search | DuckDuckGo (no API key required) |
| Prediction market | Polymarket Gamma API + CLOB API on Polygon |
| Deployment | Docker Compose |

---

## Quick Start

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Python | 3.11+ | [python.org](https://python.org) |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker + Compose | latest | [docker.com](https://docs.docker.com/get-docker/) |

**External accounts needed:**
- [Together AI](https://api.together.xyz) — free tier is enough
- [Neo4j Aura Free](https://neo4j.com/cloud/aura-free/) — one free instance
- Polygon wallet funded with USDC — for live bet placement

### 1. Clone

```bash
git clone https://github.com/YOUR_USERNAME/seabw-2026.git
cd seabw-2026
```

### 2. Configure

```bash
cp web/.env.together-ai web/.env
```

Open `web/.env` and fill in:

```bash
# Together AI — get your key at api.together.xyz
LLM_API_KEY=your_together_ai_key
SMART_API_KEY=your_together_ai_key
NER_API_KEY=your_together_ai_key
WONDERWALL_API_KEY=your_together_ai_key
OPENAI_API_KEY=your_together_ai_key
EMBEDDING_API_KEY=your_together_ai_key

# Neo4j Aura Free — create at neo4j.com/cloud/aura-free
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_PASSWORD=your_aura_password

# Polygon wallet — export private key from MetaMask
# Must have USDC on Polygon mainnet for bet placement
POLYMARKET_PRIVATE_KEY=your_wallet_private_key

# Admin token — generate with: openssl rand -hex 32
MIROSHARK_ADMIN_TOKEN=your_random_token
```

### 3. Install dependencies

```bash
# Node (frontend + root scripts)
npm run setup --prefix web

# Python (backend)
cd web/backend && uv sync
```

### 4. Run

```bash
npm run dev --prefix web
```

- **App**: http://localhost:3000
- **API**: http://localhost:5001

---

## Docker Deployment

For a DigitalOcean Droplet (2GB RAM / 40GB disk) or any VPS:

```bash
# On the server
git clone https://github.com/YOUR_USERNAME/seabw-2026.git
cd seabw-2026
cp web/.env.together-ai web/.env
nano web/.env  # fill in your keys

docker compose up -d
```

App runs on port 80. Monitor with `docker compose logs -f`.

**RAM budget during peak (simulation running):**

| Service | RAM |
|---------|-----|
| OS + nginx | 200 MB |
| Vue.js (web) | 350 MB |
| Flask (api) | 150 MB |
| Simulation engine | 600 MB |
| **Total peak** | **~1.3 GB** |

Neo4j runs on Aura Free (cloud) — saves ~600MB vs running it locally.

---

## How to Use

**1. Open the app** → the Scout automatically surfaces the top 8 Polymarket markets, scored by research potential.

**2. Click "Research & Simulate"** on any market card → the research agent runs 4 rounds of web searches and builds a structured seed document in real time. Watch sources appear and the document grow.

**3. Click "Run Simulation →"** → Simulation Agent ingests the seed document, builds a knowledge graph, spawns 100+ agents, and runs a Polymarket platform simulation. The dashboard shows agent conversations, market price evolution, and belief state heatmaps live.

**4. When simulation completes** → a bet panel appears bottom-right with the swarm's recommendation (e.g. "Swarm: 73%, Market: 61%, Edge: +12%"). Enter your USDC amount and confirm.

**5. Bet placed** → tx hash shown with a Polygonscan link. Your order is live on the Polymarket order book.

---

## Project Structure

```
seabw-2026/
│
├── web/                              # Vue.js + Flask
│   ├── frontend/
│   │   └── src/
│   │       ├── views/
│   │       │   ├── BetDiscovery.vue  ← market scouting home page
│   │       │   ├── ResearchView.vue  ← research agent UI with SSE stream
│   │       │   └── [other views]
│   │       ├── components/
│   │       │   ├── BetPanel.vue      ← floating bet placement widget
│   │       │   └── [other components]
│   │       └── router/index.js       ← modified: new routes added
│   │
│   ├── backend/
│   │   └── app/
│   │       └── api/
│   │           └── polymarket.py     ← new: markets / research SSE / CLOB bet
│   │
│   ├── .env.together-ai              ← Together AI config template
│   └── Dockerfile
│
├── docker-compose.yml
├── nginx.conf
├── .env.example
└── docs/
    └── superpowers/specs/
        └── 2026-05-20-miroshark-polymarket-design.md
```

### New API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/polymarket/markets` | Top 8 scored Polymarket markets |
| `GET` | `/api/polymarket/research/<market_id>` | SSE: 4-round research agent stream |
| `POST` | `/api/polymarket/bet` | Place limit order on Polymarket CLOB |

---

## Contributing

We welcome contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for full details.

**Quick version:**

```bash
# Fork the repo, then:
git clone https://github.com/YOUR_USERNAME/seabw-2026.git
cd seabw-2026
cp web/.env.together-ai web/.env  # fill in keys
npm run setup --prefix web && cd web/backend && uv sync

# Create a branch
git checkout -b feat/your-feature

# Make changes, then open a PR
```

Good first issues are tagged [`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

---

## Roadmap

- [ ] Multi-simulation queue (run multiple markets in parallel)
- [ ] Portfolio tracking (P&L across all placed bets)
- [ ] USDC-on-Polygon payment gate for public deployment
- [ ] Market alert system (notify when a high-edge opportunity is found)
- [ ] Simulation comparison (run the same market with different research depths)
- [ ] Historical backtest (did the swarm beat the market on past events?)

---

## License

This project is a fork of [MiroShark](https://github.com/aaronjmars/MiroShark) and is licensed under the **GNU Affero General Public License v3.0** — see [web/LICENSE](web/LICENSE).

The AGPL requires that if you run a modified version as a network service, you must make the source available to users of that service.

---

## Credits

- **[MiroShark](https://github.com/aaronjmars/MiroShark)** by [@aaronjmars](https://github.com/aaronjmars) — Universal Swarm Intelligence Engine
- **[Polymarket](https://polymarket.com)** — prediction market platform
- **[Together AI](https://api.together.xyz)** — LLM inference
- Built at **SEABW 2026** (Southeast Asia Builders Week)
