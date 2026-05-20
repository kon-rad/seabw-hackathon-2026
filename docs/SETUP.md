# Setup & API Keys Guide

This document covers every credential and external account needed to run MiroShark × Polymarket.

---

## Required

### 1. Together AI

Used for all LLM calls (research agent, swarm simulation, embeddings, NER). One key fills every slot.

**Steps:**
1. Sign up at [api.together.xyz](https://api.together.xyz)
2. Dashboard → **API Keys** → **Create new key**
3. Copy the key and paste it into all six slots in `web/.env`:

```env
LLM_API_KEY=your_together_key
SMART_API_KEY=your_together_key
NER_API_KEY=your_together_key
WONDERWALL_API_KEY=your_together_key
OPENAI_API_KEY=your_together_key
EMBEDDING_API_KEY=your_together_key
```

**Cost:** The research model (`Llama-3.3-70B-Free`) is free. Swarm simulation runs ~$0.10–0.30 per run on the paid tier.

---

### 2. Neo4j Aura Free

Used as the knowledge graph for storing entities, relations, and reasoning traces.

**Steps:**
1. Sign up at [neo4j.com/cloud/aura-free](https://neo4j.com/cloud/aura-free)
2. Create a new **AuraDB Free** instance (takes ~2 minutes)
3. When the instance is ready, copy the **Connection URI** and **Password** — the password is only shown once
4. Fill in:

```env
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_aura_password
```

---

### 3. Polymarket — Polygon Wallet Private Key

Polymarket does not issue API keys. Authentication is your crypto wallet private key on Polygon mainnet. This is only needed when placing live bets.

**Steps:**
1. Install [MetaMask](https://metamask.io) (browser extension)
2. Create a dedicated wallet — do not reuse your main wallet
3. Switch the network to **Polygon Mainnet**
4. Fund the wallet with **USDC on Polygon** (buy on Coinbase/Binance and withdraw to Polygon, or bridge from Ethereum)
5. Export the private key: MetaMask → Account menu → **Account Details** → **Export Private Key**
6. Fill in:

```env
POLYMARKET_PRIVATE_KEY=0x...your_private_key
```

> **Security warning:** Never commit this key to version control. Use a dedicated wallet funded with only what you are willing to lose. The app accesses this key to sign CLOB orders on Polygon mainnet.

---

### 4. Admin Token

A self-generated secret that guards the write endpoints (`/publish`, `/resolve`, `/outcome`). No external account needed.

**Generate one:**
```bash
openssl rand -hex 32
```

Fill in:
```env
MIROSHARK_ADMIN_TOKEN=the_output_from_above
```

If this is left blank, the gated endpoints return `503` — they do not silently allow requests.

---

## Optional

These are not required to run the app but unlock additional features.

| Variable | Purpose | Where to get |
|----------|---------|--------------|
| `DISCORD_WEBHOOK_URL` | Posts simulation results as a rich embed to a Discord channel | Discord → Server Settings → Integrations → Webhooks |
| `SLACK_WEBHOOK_URL` | Posts simulation results as a Block Kit message to Slack | [api.slack.com/apps](https://api.slack.com/apps) → Incoming Webhooks |
| `FEEDORACLE_API_KEY` | Live market data enrichment for preset templates | [mcp.feedoracle.io](https://mcp.feedoracle.io) |
| `SMTP_HOST` / `SMTP_PASSWORD` | Email notifications on simulation complete/fail | Any SMTP provider (Gmail App Password, SendGrid, etc.) |

---

## Not Required

- **Web search** — uses DuckDuckGo, no API key needed
- **Reranker** — runs locally via `sentence-transformers`, downloads model on first use

---

---

## Domain & SSL (aum.lol)

### Namecheap DNS — A Records

In Namecheap → **Advanced DNS**, add these two records pointing to your server IP:

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A Record | `@` | `YOUR_SERVER_IP` | Automatic |
| A Record | `www` | `YOUR_SERVER_IP` | Automatic |

Delete any default Namecheap parking records (`@` CNAME, etc.) that might conflict.

DNS propagation takes 1–30 minutes. Verify with:
```bash
dig aum.lol +short
```

### Issue the SSL Certificate (first deploy only)

The `certbot` service in `docker-compose.yml` handles Let's Encrypt. On first deploy:

```bash
# 1. Start nginx on port 80 only (cert doesn't exist yet, so skip the HTTPS block)
#    Temporarily comment out the HTTPS server block in nginx.conf, then:
docker compose up -d nginx

# 2. Run certbot once to issue the cert
docker compose run --rm certbot

# 3. Uncomment the HTTPS server block, then reload nginx
docker compose exec nginx nginx -s reload
```

### Auto-renew (add to VPS crontab)

```bash
# Renew every 60 days, reload nginx if cert changes
0 3 1 */2 * docker compose -f /path/to/docker-compose.yml run --rm certbot renew && docker compose -f /path/to/docker-compose.yml exec nginx nginx -s reload
```

---

## Quick Checklist

```
[ ] Together AI key created and pasted into all 6 slots
[ ] Neo4j Aura instance created, URI and password saved
[ ] MIROSHARK_ADMIN_TOKEN generated
[ ] (when ready to bet) Polygon wallet created, funded with USDC, private key exported
```

---

## Full .env Template

See [`web/.env.together-ai`](../web/.env.together-ai) for the complete template with all variables and their defaults.
