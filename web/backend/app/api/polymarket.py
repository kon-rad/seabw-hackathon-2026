"""
Polymarket integration: market discovery, research agent, bet placement.
"""

import os
import re
import json
import time
import random
import traceback
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from flask import Blueprint, request, jsonify, Response, stream_with_context, current_app
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup

from ..utils.logger import get_logger

logger = get_logger("miroshark.polymarket")

polymarket_bp = Blueprint("polymarket", __name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
TOGETHER_BASE = os.environ.get("LLM_BASE_URL", "https://api.together.xyz/v1")
TOGETHER_KEY = os.environ.get("LLM_API_KEY", "")
# Use dedicated research model if set; fall back to the app's smart model then default LLM model.
# Note: "-Free" serverless variants are deprecated on Together AI — use the paid turbo names.
RESEARCH_MODEL = (
    os.environ.get("RESEARCH_MODEL")
    or os.environ.get("SMART_MODEL_NAME")
    or os.environ.get("LLM_MODEL_NAME")
    or "meta-llama/Llama-3.3-70B-Instruct-Turbo"
)
RESEARCH_API_KEY = (
    os.environ.get("RESEARCH_MODEL") and os.environ.get("LLM_API_KEY")
    or os.environ.get("SMART_API_KEY")
    or os.environ.get("LLM_API_KEY")
    or ""
)
RESEARCH_BASE_URL = (
    os.environ.get("SMART_BASE_URL")
    or os.environ.get("LLM_BASE_URL")
    or "https://api.together.xyz/v1"
)
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")

# Resolve uploads dir: env var → Docker path → local dev path relative to this file
_DEFAULT_UPLOADS = Path(__file__).parent.parent.parent / "uploads"
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(_DEFAULT_UPLOADS)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm(messages: list[dict], max_tokens: int = 1024) -> str:
    """Call the configured LLM (OpenAI-compatible) and return the assistant text."""
    resp = httpx.post(
        f"{RESEARCH_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {RESEARCH_API_KEY}", "Content-Type": "application/json"},
        json={"model": RESEARCH_MODEL, "messages": messages, "max_tokens": max_tokens},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _extract_search_queries(market_title: str, market_description: str) -> list[tuple[str, str]]:
    """
    Use the LLM to generate focused search queries from the market title.

    Falls back to simple keyword queries if LLM fails. Returns a list of
    (label, query) tuples for each research round.
    """
    prompt = f"""You are helping research a Polymarket prediction market.

Market question: {market_title}
Description snippet: {(market_description or '')[:300]}

Generate 4 specific web search queries to research this market. Each query should:
- Focus on the KEY ENTITIES and SPECIFIC EVENT (not generic words like "over", "will", "committed")
- Be 4-8 words that would actually find relevant news and data
- Cover: recent news, background facts, expert views, key players

Return ONLY a JSON array of 4 objects with "label" and "query" keys. Example format:
[
  {{"label": "latest news", "query": "Printr token launch Sonar 2026"}},
  {{"label": "background", "query": "Printr project fundraising history crypto"}},
  {{"label": "expert views", "query": "Printr token sale predictions analysts"}},
  {{"label": "key entities", "query": "Sonar platform Printr team founders"}}
]"""

    try:
        raw = _llm([{"role": "user", "content": prompt}], max_tokens=400)
        # Extract JSON array from response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            rounds = json.loads(match.group(0))
            if isinstance(rounds, list) and len(rounds) == 4:
                return [(r["label"], r["query"]) for r in rounds]
    except Exception as e:
        logger.warning(f"Query extraction LLM call failed: {e}")

    # Fallback: extract key noun phrases by stripping predicate/threshold words
    _stopwords = {
        "will", "over", "under", "the", "a", "an", "be", "is", "are", "was",
        "committed", "exceed", "reach", "hit", "pass", "more", "than", "by",
        "does", "do", "get", "have", "has", "had", "to", "of", "in", "for",
        "at", "on", "or", "and", "that", "this", "with", "from", "if",
        "sale", "public", "total", "amount", "number",
    }
    # Also strip tokens that are purely numeric/currency (e.g. "$3M", "100%", "$1B")
    def _is_useful(tok: str) -> bool:
        t = tok.strip('?.,!$%').lower()
        return bool(t) and t not in _stopwords and len(t) > 2 and not re.fullmatch(r'[\d.,kmb%$]+', t, re.IGNORECASE)

    words = [w.strip('?.,!') for w in market_title.split() if _is_useful(w)]
    core = " ".join(words[:5])
    return [
        ("latest news", f"{core} news 2026"),
        ("background", f"{core} history fundraising"),
        ("expert predictions", f"{core} forecast analysis"),
        ("key entities", f"{core} team founders investors"),
    ]


def _fetch_page_text(url: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return plain text (best-effort)."""
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text[:max_chars]
    except Exception:
        return ""


def _search_ddg(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo search with retry. Returns list of {title, href, body}."""
    for attempt in range(3):
        try:
            time.sleep(1.5 * (attempt + 1))  # back-off: 1.5s, 3s, 4.5s
            ddgs = DDGS()  # fresh instance each attempt avoids session-level rate limits
            results = list(ddgs.text(query, max_results=max_results))
            if results:
                return results
        except Exception as e:
            logger.warning(f"DDG attempt {attempt + 1} failed for '{query[:60]}': {e}")
    return []


def _search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Tavily search (requires TAVILY_API_KEY). Returns DDG-compatible dicts."""
    if not TAVILY_API_KEY:
        return []
    try:
        resp = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_API_KEY, "query": query, "max_results": max_results,
                  "search_depth": "basic", "include_answer": False},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            {"title": r.get("title", ""), "href": r.get("url", ""), "body": r.get("content", "")}
            for r in resp.json().get("results", [])
        ]
    except Exception as e:
        logger.warning(f"Tavily search failed for '{query[:60]}': {e}")
        return []


def _search_brave(query: str, max_results: int = 5) -> list[dict]:
    """Brave Search API (requires BRAVE_API_KEY). Returns DDG-compatible dicts."""
    if not BRAVE_API_KEY:
        return []
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query, "count": max_results},
            timeout=15,
        )
        resp.raise_for_status()
        web_results = resp.json().get("web", {}).get("results", [])
        return [
            {"title": r.get("title", ""), "href": r.get("url", ""), "body": r.get("description", "")}
            for r in web_results
        ]
    except Exception as e:
        logger.warning(f"Brave search failed for '{query[:60]}': {e}")
        return []


def _web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search with Tavily → Brave → DuckDuckGo fallback chain."""
    results = _search_tavily(query, max_results)
    if results:
        return results
    results = _search_brave(query, max_results)
    if results:
        return results
    return _search_ddg(query, max_results)


def _fetch_polymarket_market_data(market_id: str) -> dict:
    """
    Pull full market data from Polymarket Gamma + CLOB APIs.
    Returns a dict with keys: description, resolution_rules, yes_token_id, no_token_id,
    best_bid, best_ask, price_change_24h, recent_volume.
    All keys default to None on failure.
    """
    result = {
        "description": None, "resolution_rules": None,
        "yes_token_id": None, "no_token_id": None,
        "best_bid": None, "best_ask": None,
        "price_24h_ago": None, "current_price": None,
        "volume_24h": None, "open_interest": None,
    }

    # Try Gamma API — by numeric id, then by conditionId query param
    m = None
    try:
        # Direct numeric-id lookup (most common path)
        resp = httpx.get(f"{GAMMA_API}/markets/{market_id}", timeout=15)
        if resp.status_code == 200:
            m = resp.json()
        else:
            # Fallback: search by conditionId
            resp2 = httpx.get(
                f"{GAMMA_API}/markets",
                params={"condition_id": market_id, "limit": 1},
                timeout=15,
            )
            resp2.raise_for_status()
            markets = resp2.json()
            if markets:
                m = markets[0] if isinstance(markets, list) else markets
    except Exception as e:
        logger.warning(f"Gamma API fetch failed for {market_id}: {e}")

    if m:
        result["description"] = (m.get("description") or "")[:2000]
        result["resolution_rules"] = (
            m.get("resolutionRules") or m.get("resolution_rules") or ""
        )[:1000]

        # clobTokenIds is a JSON-encoded string like '["token1", "token2"]' or a real list
        raw_tokens = m.get("clobTokenIds") or m.get("tokens") or []
        if isinstance(raw_tokens, str):
            try:
                raw_tokens = json.loads(raw_tokens)
            except (json.JSONDecodeError, ValueError):
                raw_tokens = []
        if isinstance(raw_tokens, list) and raw_tokens:
            if isinstance(raw_tokens[0], dict):
                result["yes_token_id"] = raw_tokens[0].get("token_id") or raw_tokens[0].get("id")
                if len(raw_tokens) > 1:
                    result["no_token_id"] = raw_tokens[1].get("token_id") or raw_tokens[1].get("id")
            else:
                result["yes_token_id"] = str(raw_tokens[0])
                if len(raw_tokens) > 1:
                    result["no_token_id"] = str(raw_tokens[1])

        result["volume_24h"] = m.get("volume24hr") or m.get("volume_24hr") or m.get("volume")
        result["open_interest"] = m.get("openInterest") or m.get("open_interest")

    # Fetch order book if we have a YES token
    if result["yes_token_id"]:
        try:
            ob_resp = httpx.get(
                f"{CLOB_API}/book",
                params={"token_id": result["yes_token_id"]},
                timeout=10,
            )
            ob_resp.raise_for_status()
            ob = ob_resp.json()
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            result["best_bid"] = float(bids[0]["price"]) if bids else None
            result["best_ask"] = float(asks[0]["price"]) if asks else None
        except Exception as e:
            logger.warning(f"CLOB order book fetch failed: {e}")

        # Fetch recent price history (uses 'market' param = token_id)
        try:
            ph_resp = httpx.get(
                f"{CLOB_API}/prices-history",
                params={"market": result["yes_token_id"], "interval": "1d", "fidelity": "1"},
                timeout=10,
            )
            ph_resp.raise_for_status()
            history = ph_resp.json().get("history") or []
            if len(history) >= 2:
                result["price_24h_ago"] = float(history[-2].get("p", 0))
                result["current_price"] = float(history[-1].get("p", 0))
        except Exception as e:
            logger.warning(f"CLOB price history fetch failed: {e}")

    return result


def _score_market(m: dict) -> tuple[int, list[str]]:
    """
    Score a Polymarket market for MiroShark simulation suitability.

    MiroShark runs social-narrative simulations: LLM agents (journalists,
    activists, institutions, individuals) debate on Twitter/Reddit and trade
    on a prediction market.  The simulation is most accurate when:

      • The outcome is decided by a named human or institution (not a stat)
      • Clear ideological/political factions exist who'd argue on social media
      • The resolution window is short enough for a quick result (ideally 1–3 days)
      • Social sentiment plausibly moves the market (not just quant noise)

    Returns (score, tags) where tags explain why it scored well/poorly.
    """
    score = 0
    tags: list[str] = []

    question = (m.get("question") or m.get("title") or "")
    desc = (m.get("description") or "")
    text = (question + " " + desc).lower()

    # ── 1. Description richness ──────────────────────────────────────── +0-15
    combined_len = len(question) + len(desc)
    if combined_len > 400:
        score += 15
    elif combined_len > 150:
        score += 8

    # ── 2. Human-decision outcome: named actor makes a verifiable choice ─ +25
    # These resolve based on what a person/institution *decides* — exactly
    # what social simulation agents model. The crowd on Twitter can shift
    # beliefs about whether it will happen.
    human_decision_kws = [
        "win", "elect", "appoint", "resign", "fired", "nominate", "endorse",
        "impeach", "convict", "indict", "pardon", "veto", "sign", "approve",
        "pass ", "announce", "declare", "withdraw", "concede", "certify",
        "sanction", "ban ", "block", "merger", "acquire", "ipo",
        "step down", "take office", "sworn in", "confirmed by",
        "replace", "successor", "reelect",
    ]
    if any(kw in text for kw in human_decision_kws):
        score += 25
        tags.append("human-decision")

    # ── 3. Political / geopolitical factionability ───────────────────── +20
    # Strong ideological camps → authentic agent disagreement → useful signal
    faction_kws = [
        "election", "president", "senator", "governor", "prime minister",
        "congress", "parliament", "cabinet", "supreme court", "referendum",
        "democrat", "republican", "liberal", "conservative", "populist",
        "war", "invasion", "ceasefire", "peace deal", "nato", "treaty",
        "protest", "strike", "movement", "legislation", "policy", "tariff",
        "geopolit", "diplomacy", "sanctions",
    ]
    if any(kw in text for kw in faction_kws):
        score += 20
        tags.append("political-faction")

    # ── 4. Named institution or public figure ────────────────────────── +15
    # Richer entity graph → better, more differentiated agents
    institutional_kws = [
        "ceo", "chief executive", "chairman", "elon", "trump", "biden",
        "harris", "musk", "zelensky", "putin", "xi ", "modi",
        "fed ", "federal reserve", "sec ", "fda ", "fbi ", "cia ",
        "white house", "pentagon", "senate", "house of representatives",
        "un ", "nato", "imf", "world bank", "eu ", "european union",
        "openai", "google", "apple", "microsoft", "nvidia", "spacex",
        "supreme court", "department of", "ministry of",
    ]
    if any(kw in text for kw in institutional_kws):
        score += 15
        tags.append("named-entity")

    # ── 5. Pure-quant / stats PENALTY ────────────────────────────────── -20
    # "Will CPI exceed 3.2%?" resolves on a number — no social faction forms,
    # no LLM agent has an edge from Twitter discourse. Waste of a simulation.
    quant_kws = [
        "cpi", "gdp", "inflation rate", "interest rate", "basis point",
        "unemployment rate", "nonfarm payroll", "pce ", "ppi ",
        "above $", "below $", "exceed $", "reach $", "hit $",
        "price target", "trading at", "market cap exceed",
        "jobs report", "payroll report",
    ]
    if any(kw in text for kw in quant_kws):
        score -= 20
        tags.append("quant-stat⚠")

    # ── 6. Pure sports-score PENALTY ─────────────────────────────────── -15
    # Game outcomes are decided on the field, not by social discourse.
    # Exception: awards, signings, trades = human decisions already caught above.
    sports_score_kws = [
        "win the championship", "win the series", "win the title",
        "beat ", "defeat ", "score more", "advance to the final",
        "super bowl", "nba finals", "world series", "stanley cup",
        "premier league title", "champions league", "la liga",
        "total goals", "total points", "over/under",
    ]
    if any(kw in text for kw in sports_score_kws):
        score -= 15
        tags.append("sports-score⚠")

    # ── 7. Crypto: event-driven ↑, pure price ↓ ──────────────────────── ±15
    # "Will the SEC approve a Bitcoin ETF?" → strong factions (industry vs
    # regulators), newsworthy, socially amplified → great.
    # "Will BTC exceed $100k?" → pure price prediction, quant, no discourse edge.
    has_crypto = any(kw in text for kw in ["bitcoin", "crypto", "ethereum", "btc ", "eth "])
    if has_crypto:
        crypto_event_kws = [
            "etf", "approval", "sec", "regulat", "hack", "collapse",
            "bankrupt", "delist", "listing", "halving", "fork", "lawsuit",
        ]
        crypto_price_kws = [
            "bitcoin price", "btc price", "eth price", "ethereum price",
            "100k", "200k", "50k", "above $", "below $",
        ]
        if any(kw in text for kw in crypto_event_kws):
            score += 15
            tags.append("crypto-event")
        elif any(kw in text for kw in crypto_price_kws):
            score -= 15
            tags.append("crypto-price⚠")

    # ── 8. Volume ────────────────────────────────────────────────────── +0-20
    vol = float(m.get("volume", 0) or 0)
    if vol > 100_000:
        score += 20
        tags.append("high-volume")
    elif vol > 10_000:
        score += 10
        tags.append("mid-volume")

    # ── 9. Time to resolution — tiered to favour quick results ──────────── +0-25
    # We want markets that resolve soon enough that the user can bet and see
    # the outcome quickly, but not so soon that there's no time to run a sim.
    #
    #  < 6h  : too close to act — penalise
    #  6–24h : fastest possible result; small sim window but doable         +10
    #  1–3d  : sweet spot — sim fits, result arrives quickly                +25
    #  3–7d  : good; result within the week                                 +18
    #  7–30d : acceptable but slow                                           +8
    #  > 30d : too far out for a quick feedback loop                         +0
    end_date_str = m.get("endDate") or m.get("end_date_iso") or ""
    try:
        if end_date_str:
            end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            hours_left = (end - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_left < 6:
                score -= 15
                tags.append("too-close⚠")
            elif hours_left < 24:
                score += 10
                tags.append("resolves-today")
            elif hours_left <= 72:
                score += 25
                tags.append("resolves-1-3d")
            elif hours_left <= 168:
                score += 18
                tags.append("resolves-this-week")
            elif hours_left <= 720:
                score += 8
                tags.append("resolves-this-month")
            # > 720h: no bonus, no tag
    except Exception:
        pass

    # ── 10. Price conviction (away from 50/50) ────────────────────────── +0-10
    try:
        best_ask = float(m.get("bestAsk") or m.get("best_ask") or 0.5)
        if abs(best_ask - 0.5) > 0.15:
            score += 10
            tags.append("price-conviction")
        elif abs(best_ask - 0.5) > 0.05:
            score += 4
    except Exception:
        pass

    return score, tags


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@polymarket_bp.route("/markets", methods=["GET"])
def get_markets():
    """
    Fetch Polymarket markets best suited for MiroShark social simulation.

    Fetches 200 active markets by volume, then scores each for simulation
    fitness: human-decision outcomes, named-entity density, political
    factionability. Pure-quant markets (CPI, price targets) and pure
    sports-score markets are penalised. Returns up to 10 results with
    tags explaining each market's fit.
    """
    try:
        # Fetch two offset pages to widen the candidate pool on each refresh.
        # A random offset ensures a different batch every call.
        random_offset = random.randint(0, 300)
        all_markets: list[dict] = []
        for offset in [0, random_offset]:
            r = httpx.get(
                f"{GAMMA_API}/markets",
                params={
                    "closed": "false",
                    "active": "true",
                    "order": "volume",
                    "ascending": "false",
                    "limit": 200,
                    "offset": offset,
                },
                timeout=20,
            )
            r.raise_for_status()
            batch = r.json()
            if isinstance(batch, list):
                all_markets.extend(batch)

        # Deduplicate by id
        seen: set = set()
        markets: list[dict] = []
        for m in all_markets:
            mid = m.get("id") or m.get("conditionId")
            if mid and mid not in seen:
                seen.add(mid)
                markets.append(m)

        scored = []
        for m in markets:
            score, tags = _score_market(m)
            if score > 20:
                scored.append({
                    "id": m.get("id") or m.get("conditionId"),
                    "slug": m.get("slug", ""),
                    "title": m.get("question") or m.get("title", ""),
                    "description": (m.get("description") or "")[:400],
                    "yes_price": m.get("bestAsk") or m.get("best_ask"),
                    "no_price": m.get("bestBid") or m.get("best_bid"),
                    "volume": m.get("volume"),
                    "end_date": m.get("endDate") or m.get("end_date_iso"),
                    "score": score,
                    "sim_tags": tags,
                    "category": m.get("category", ""),
                    "condition_id": m.get("conditionId"),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)

        # From the top 30 candidates, randomly pick 10 so each refresh shows
        # a varied selection while still only surfacing high-quality markets.
        top_pool = scored[:30]
        if len(top_pool) > 10:
            selection = random.sample(top_pool, 10)
            selection.sort(key=lambda x: x["score"], reverse=True)
        else:
            selection = top_pool

        resp = jsonify({"success": True, "markets": selection})
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@polymarket_bp.route("/research/<market_id>", methods=["GET"])
def research_market(market_id: str):
    """
    SSE endpoint. Runs structured research on a Polymarket market and streams progress.

    Phase 0: Pull live data from Polymarket Gamma + CLOB APIs (order book, price history).
    Phases 1-4: Web search rounds (Tavily → Brave → DuckDuckGo fallback chain).

    Writes seed.md + sources.json + metadata.json to uploads/{market_id}/.
    On completion emits research_complete with the raw seed.md text.
    """
    market_title = request.args.get("title", market_id)
    market_description = request.args.get("description", "")

    def generate():
        research_dir = UPLOADS_DIR / market_id
        research_dir.mkdir(parents=True, exist_ok=True)

        sources: list[dict] = []
        seed_sections: list[str] = []

        def emit(event_type: str, data: dict):
            payload = json.dumps({"type": event_type, **data})
            return f"data: {payload}\n\n"

        yield emit("start", {"market_id": market_id, "title": market_title})

        # ------------------------------------------------------------------
        # Phase 0: Fetch live Polymarket data (always runs, no search needed)
        # ------------------------------------------------------------------
        yield emit("round_start", {
            "round": 0, "label": "polymarket data",
            "query": f"Fetching live market data from Polymarket APIs…",
        })
        mkt = _fetch_polymarket_market_data(market_id)

        pm_lines = []
        if mkt["description"] and len(mkt["description"]) > 20:
            pm_lines.append(f"**Full Description:** {mkt['description']}")
        if mkt["resolution_rules"]:
            pm_lines.append(f"**Resolution Rules:** {mkt['resolution_rules']}")
        if mkt["best_bid"] is not None and mkt["best_ask"] is not None:
            spread = round(mkt["best_ask"] - mkt["best_bid"], 4)
            pm_lines.append(
                f"**Order Book:** Best bid {mkt['best_bid']:.3f} / Best ask {mkt['best_ask']:.3f} "
                f"(spread {spread:.3f})"
            )
            sources.append({
                "url": f"https://polymarket.com/event/{market_id}",
                "title": "Polymarket Order Book (live)",
                "round": 0,
            })
            yield emit("source_found", {
                "url": f"https://polymarket.com/event/{market_id}",
                "title": "Polymarket Order Book (live)",
                "round": 0,
            })
        if mkt["current_price"] is not None and mkt["price_24h_ago"] is not None:
            change = round(mkt["current_price"] - mkt["price_24h_ago"], 4)
            direction = "▲" if change >= 0 else "▼"
            pm_lines.append(
                f"**Price:** {mkt['current_price']:.3f} ({direction}{abs(change):.3f} in last 24h)"
            )
        if mkt["volume_24h"]:
            pm_lines.append(f"**24h Volume:** ${float(mkt['volume_24h']):,.0f}")
        if mkt["open_interest"]:
            pm_lines.append(f"**Open Interest:** ${float(mkt['open_interest']):,.0f}")

        if pm_lines:
            section = "## Polymarket Live Data\n\n" + "\n\n".join(pm_lines)
            seed_sections.insert(0, section)
            yield emit("round_complete", {"round": 0, "facts": "\n".join(pm_lines)})
        else:
            yield emit("round_complete", {"round": 0, "facts": "(No live data retrieved)"})

        # ------------------------------------------------------------------
        # Phases 1-4: Web search rounds with LLM-generated smart queries
        # ------------------------------------------------------------------
        rounds = _extract_search_queries(market_title, market_description)

        for round_num, (label, query) in enumerate(rounds, start=1):
            yield emit("round_start", {"round": round_num, "label": label, "query": query})

            results = _web_search(query, max_results=5)

            round_texts = []
            for r in results[:3]:
                url = r.get("href", "")
                title_r = r.get("title", "")
                snippet = r.get("body", "")
                sources.append({"url": url, "title": title_r, "round": round_num})
                yield emit("source_found", {"url": url, "title": title_r, "round": round_num})

                page_text = _fetch_page_text(url) if url else snippet
                round_texts.append(f"Source: {title_r}\nURL: {url}\n{page_text or snippet}")

            if not round_texts:
                yield emit("round_complete", {"round": round_num, "facts": "(No sources found)"})
                continue

            combined = "\n\n---\n\n".join(round_texts)
            prompt = (
                f"You are researching a prediction market: '{market_title}'.\n"
                f"Round focus: {label}\n\n"
                f"Source material:\n{combined[:6000]}\n\n"
                "Extract the 5 most important facts, statistics, or insights relevant to predicting "
                "this market outcome. Be concise. Return as a numbered list."
            )
            try:
                facts_text = _llm([{"role": "user", "content": prompt}], max_tokens=512)
            except Exception as e:
                facts_text = f"(LLM extraction failed: {e})"

            seed_sections.append(f"## {label.title()}\n\n{facts_text}")
            yield emit("round_complete", {"round": round_num, "facts": facts_text})

        # ------------------------------------------------------------------
        # Build structured seed.md following production template format
        # ------------------------------------------------------------------
        web_sources_md = "\n".join(
            f"- [{s['title']}]({s['url']})"
            for s in sources if s.get("url") and s.get("round", 1) > 0
        )

        # Determine current market state block
        state_lines = []
        if mkt["best_bid"] is not None and mkt["best_ask"] is not None:
            mid = round((mkt["best_bid"] + mkt["best_ask"]) / 2, 4)
            state_lines += [
                f"YES best bid: ${mkt['best_bid']:.3f}",
                f"YES best ask: ${mkt['best_ask']:.3f}",
                f"Midpoint: ${mid:.3f}",
            ]
        if mkt["volume_24h"]:
            state_lines.append(f"24h volume: ${float(mkt['volume_24h']):,.0f}")
        if mkt["open_interest"]:
            state_lines.append(f"Open interest: ${float(mkt['open_interest']):,.0f}")
        market_state_block = "\n".join(state_lines) if state_lines else "(live data unavailable)"

        resolution_block = mkt["resolution_rules"] or market_description or "(see market page)"

        seed_md = f"""# {market_title}

## Market Context

{market_description}

**Resolution Rules:**
{resolution_block}

## Current Market State

{market_state_block}

{chr(10).join(seed_sections)}

## Research Sources

{web_sources_md}
"""
        seed_path = research_dir / "seed.md"
        seed_path.write_text(seed_md, encoding="utf-8")

        (research_dir / "sources.json").write_text(
            json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (research_dir / "metadata.json").write_text(
            json.dumps({
                "market_id": market_id,
                "title": market_title,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "rounds": len(rounds),
                "source_count": len(sources),
                "has_live_data": any(v is not None for v in [mkt["best_bid"], mkt["description"]]),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        yield emit("research_complete", {
            "market_id": market_id,
            "seed_md": seed_md,
            "source_count": len(sources),
        })

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@polymarket_bp.route("/bet", methods=["POST"])
def place_bet():
    """
    Place a limit order on Polymarket via the CLOB API.
    Body: { condition_id, outcome: "YES"|"NO", usdc_amount, price }
    """
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
    if not private_key:
        return jsonify({"success": False, "error": "POLYMARKET_PRIVATE_KEY not configured"}), 400

    data = request.get_json(force=True)
    condition_id = data.get("condition_id")
    outcome = data.get("outcome", "YES").upper()
    usdc_amount = float(data.get("usdc_amount", 0))
    price = float(data.get("price", 0.5))

    if not condition_id or usdc_amount <= 0 or not (0 < price < 1):
        return jsonify({"success": False, "error": "Invalid bet parameters"}), 400

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.constants import POLYGON

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=private_key,
            chain_id=POLYGON,
        )
        client.set_api_creds(client.create_or_derive_api_creds())

        # token_id: YES token = index 0, NO token = index 1
        market_info = client.get_market(condition_id)
        tokens = market_info.get("tokens", [])
        token = next((t for t in tokens if t.get("outcome", "").upper() == outcome), None)
        if not token:
            return jsonify({"success": False, "error": f"Could not find {outcome} token"}), 400

        size = round(usdc_amount / price, 2)

        order_args = OrderArgs(
            token_id=token["token_id"],
            price=price,
            size=size,
            side="BUY",
            order_type=OrderType.GTC,
        )
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)

        return jsonify({
            "success": True,
            "order_id": resp.get("orderID"),
            "status": resp.get("status"),
            "size": size,
            "price": price,
            "outcome": outcome,
        })
    except Exception as e:
        logger.error(f"Bet placement failed: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@polymarket_bp.route("/simulate/context", methods=["POST"])
def save_simulation_context():
    """Store Polymarket market context alongside a MiroShark project_id."""
    data = request.get_json(force=True)
    project_id = data.get("project_id")
    if not project_id or not re.fullmatch(r'proj_[a-zA-Z0-9]{1,64}', project_id):
        return jsonify({"success": False, "error": "Invalid or missing project_id"}), 400

    from ..models.project import ProjectManager
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": f"Project not found: {project_id}"}), 404

    try:
        yes_price_val = float(data.get("yes_price", 0.5))
    except (ValueError, TypeError):
        yes_price_val = 0.5

    ctx = {
        "market_id": data.get("market_id", ""),
        "condition_id": data.get("condition_id", ""),
        "title": data.get("title", ""),
        "yes_price": yes_price_val,
        "resolution_date": data.get("resolution_date", ""),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    project_dir = Path(ProjectManager._get_project_dir(project_id))
    try:
        (project_dir / "polymarket_context.json").write_text(
            json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        logger.error(f"Failed to write polymarket context for {project_id}: {e}")
        return jsonify({"success": False, "error": "Failed to save context"}), 500

    logger.info(f"Saved polymarket context for project {project_id}: {ctx['title']}")
    return jsonify({"success": True, "project_id": project_id})


@polymarket_bp.route("/simulate/<project_id>/result", methods=["GET"])
def get_simulation_result(project_id: str):
    """
    Compute and return the Polymarket recommendation for a completed simulation.
    Returns 200 with status="pending" if simulation is still running.
    """
    from ..models.project import ProjectManager
    from ..services.simulation_manager import SimulationManager
    from ..services.simulation_runner import SimulationRunner

    # Load stored Polymarket context
    try:
        project_dir = Path(ProjectManager._get_project_dir(project_id))
    except Exception:
        return jsonify({"success": False, "error": f"Project not found: {project_id}"}), 404

    ctx_path = project_dir / "polymarket_context.json"
    if not ctx_path.exists():
        return jsonify({"success": False, "error": "No Polymarket context saved for this project"}), 404

    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    market_yes_price = float(ctx.get("yes_price", 0.5))

    # Find the most recent simulation for this project
    manager = SimulationManager()
    simulations = manager.list_simulations(project_id=project_id)
    if not simulations:
        return jsonify({"success": True, "status": "no_simulation",
                        "message": "No simulation found for this project yet"}), 200

    sim = sorted(simulations, key=lambda s: s.created_at, reverse=True)[0]
    simulation_id = sim.simulation_id

    # Check run status
    run_state = SimulationRunner.get_run_state(simulation_id)
    if not run_state:
        return jsonify({"success": True, "status": "not_started",
                        "simulation_id": simulation_id}), 200

    if run_state.runner_status.value not in ("completed", "stopped"):
        return jsonify({
            "success": True,
            "status": run_state.runner_status.value,
            "simulation_id": simulation_id,
            "progress_percent": run_state.to_dict().get("progress_percent", 0),
        }), 200

    # Read final swarm price from polymarket.db
    db_path = Path(SimulationRunner.RUN_STATE_DIR) / simulation_id / "polymarket.db"
    swarm_price = None

    if db_path.exists():
        import sqlite3
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT reserve_a, reserve_b FROM market WHERE market_id = 1"
                ).fetchone()
                if row:
                    ra, rb = float(row[0] or 0), float(row[1] or 0)
                    total = ra + rb
                    swarm_price = round(rb / total, 4) if total > 0 else 0.5
        except sqlite3.Error as e:
            logger.warning(f"Could not read polymarket.db for {simulation_id}: {e}")

    if swarm_price is None:
        return jsonify({
            "success": True,
            "status": "completed_no_markets",
            "simulation_id": simulation_id,
            "message": "Simulation completed but no prediction markets were generated.",
        }), 200

    edge = round(swarm_price - market_yes_price, 4)
    abs_edge = abs(edge)

    if edge > 0:
        recommendation = "YES"
        direction = "above"
    elif edge < 0:
        recommendation = "NO"
        direction = "below"
    else:
        recommendation = "NEUTRAL"
        direction = "at"

    if abs_edge == 0:
        confidence = "LOW"
    elif abs_edge >= 0.10:
        confidence = "HIGH"
    elif abs_edge >= 0.04:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    reasoning = (
        f"The swarm ({swarm_price * 100:.1f}%) settled {direction} the Polymarket price "
        f"({market_yes_price * 100:.1f}%) by {abs_edge * 100:.1f} pp. "
        + ("Strong edge — likely tradeable." if abs_edge >= 0.10
           else "Moderate edge — check liquidity before sizing." if abs_edge >= 0.04
           else "Weak edge — fees may eat the spread.")
    )

    result = {
        "success": True,
        "status": "completed",
        "simulation_id": simulation_id,
        "recommendation": recommendation,
        "swarm_price": swarm_price,
        "market_price": market_yes_price,
        "edge": edge,
        "confidence": confidence,
        "reasoning": reasoning,
        "condition_id": ctx.get("condition_id", ""),
        "title": ctx.get("title", ""),
    }

    # Persist result for later inspection (write only once to stay idempotent)
    out_dir = Path(SimulationRunner.RUN_STATE_DIR) / simulation_id
    result_path = out_dir / "result.json"
    if not result_path.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return jsonify(result)
