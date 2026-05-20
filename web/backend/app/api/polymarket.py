"""
Polymarket integration: market discovery, research agent, bet placement.
"""

import os
import json
import time
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
TOGETHER_BASE = os.environ.get("LLM_BASE_URL", "https://api.together.xyz/v1")
TOGETHER_KEY = os.environ.get("LLM_API_KEY", "")
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free")

# Resolve uploads dir: env var → Docker path → local dev path relative to this file
_DEFAULT_UPLOADS = Path(__file__).parent.parent.parent / "uploads"
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(_DEFAULT_UPLOADS)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _llm(messages: list[dict], max_tokens: int = 1024) -> str:
    """Call Together AI (OpenAI-compatible) and return the assistant text."""
    resp = httpx.post(
        f"{TOGETHER_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {TOGETHER_KEY}", "Content-Type": "application/json"},
        json={"model": RESEARCH_MODEL, "messages": messages, "max_tokens": max_tokens},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


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


def _score_market(m: dict) -> int:
    """Return 0–100 suitability score for AI research + simulation."""
    score = 0
    desc = (m.get("description") or "") + " " + (m.get("question") or "")

    # Description richness
    if len(desc) > 300:
        score += 20
    elif len(desc) > 100:
        score += 10

    # Topic category
    topic_keywords = {
        30: ["election", "president", "congress", "parliament", "crypto", "bitcoin", "ethereum",
             "fed", "gdp", "inflation", "war", "treaty", "sanction", "nuclear"],
        20: ["sport", "championship", "world cup", "nba", "nfl", "premier league", "olympic"],
        10: ["award", "oscar", "grammy", "celebrity", "movie"],
    }
    desc_lower = desc.lower()
    for pts, kws in topic_keywords.items():
        if any(kw in desc_lower for kw in kws):
            score += pts
            break

    # Volume
    vol = float(m.get("volume", 0) or 0)
    if vol > 100_000:
        score += 20
    elif vol > 10_000:
        score += 10

    # Time to resolution (48h – 30 days = full marks)
    end_date_str = m.get("endDate") or m.get("end_date_iso") or ""
    try:
        if end_date_str:
            end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours_left = (end - now).total_seconds() / 3600
            if 48 <= hours_left <= 720:
                score += 15
            elif hours_left > 720:
                score += 8
    except Exception:
        pass

    # Price edge (not near 50/50)
    try:
        best_ask = float((m.get("bestAsk") or m.get("best_ask") or 0.5))
        if abs(best_ask - 0.5) > 0.15:
            score += 15
        elif abs(best_ask - 0.5) > 0.05:
            score += 5
    except Exception:
        pass

    return score


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@polymarket_bp.route("/markets", methods=["GET"])
def get_markets():
    """Fetch top Polymarket markets scored for AI research suitability."""
    try:
        resp = httpx.get(
            f"{GAMMA_API}/markets",
            params={"closed": "false", "active": "true", "order": "volume", "ascending": "false", "limit": 100},
            timeout=15,
        )
        resp.raise_for_status()
        markets = resp.json()

        scored = []
        for m in markets:
            score = _score_market(m)
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
                    "category": m.get("category", ""),
                    "condition_id": m.get("conditionId"),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return jsonify({"success": True, "markets": scored[:8]})
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@polymarket_bp.route("/research/<market_id>", methods=["GET"])
def research_market(market_id: str):
    """
    SSE endpoint. Runs 4-round web research on a market and streams progress.
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

        rounds = [
            ("latest news", f"{market_title} latest news 2026"),
            ("background", f"{market_title} historical data statistics analysis"),
            ("expert predictions", f"{market_title} expert predictions forecast probability"),
            ("key entities", f"{market_title} key people organizations events relationships"),
        ]

        ddgs = DDGS()

        for round_num, (label, query) in enumerate(rounds, start=1):
            yield emit("round_start", {"round": round_num, "label": label, "query": query})

            # Search
            try:
                results = list(ddgs.text(query, max_results=5))
            except Exception as e:
                logger.warning(f"DDG search failed round {round_num}: {e}")
                results = []

            round_texts = []
            for r in results[:3]:
                url = r.get("href", "")
                title = r.get("title", "")
                snippet = r.get("body", "")
                sources.append({"url": url, "title": title, "round": round_num})
                yield emit("source_found", {"url": url, "title": title, "round": round_num})

                # Fetch full page text
                page_text = _fetch_page_text(url) if url else snippet
                round_texts.append(f"Source: {title}\nURL: {url}\n{page_text or snippet}")

            if not round_texts:
                yield emit("round_complete", {"round": round_num, "facts": []})
                continue

            combined = "\n\n---\n\n".join(round_texts)
            prompt = (
                f"You are researching a prediction market: '{market_title}'.\n"
                f"Round focus: {label}\n\n"
                f"Source material:\n{combined[:6000]}\n\n"
                f"Extract the 5 most important facts, statistics, or insights relevant to predicting this market outcome. "
                f"Be concise. Return as a numbered list."
            )
            try:
                facts_text = _llm([{"role": "user", "content": prompt}], max_tokens=512)
            except Exception as e:
                facts_text = f"(LLM extraction failed: {e})"

            seed_sections.append(f"## {label.title()}\n\n{facts_text}")
            yield emit("round_complete", {"round": round_num, "facts": facts_text})
            time.sleep(0.5)

        # Build seed.md
        seed_md = f"""# {market_title}

## Market Context

{market_description}

{chr(10).join(seed_sections)}

## Research Sources

{chr(10).join(f"- [{s['title']}]({s['url']})" for s in sources if s.get('url'))}
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
