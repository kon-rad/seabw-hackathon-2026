# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
"""Polymarket prediction market platform.

Server-side platform that handles market creation, trading, resolution,
and portfolio management using a constant-product AMM.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from wonderwall.clock.clock import Clock
from wonderwall.simulations.base import BasePlatform
from wonderwall.simulations.polymarket.amm import get_prices, quote_buy, quote_sell

logger = logging.getLogger(__name__)


class PolymarketPlatform(BasePlatform):
    """Prediction market platform with AMM-based pricing."""

    required_schemas = [
        "market.sql",
        "portfolio.sql",
        "position.sql",
        "trade.sql",
        "comment.sql",
    ]

    # Also load follow schema from core for trader following
    core_schemas = ["user.sql", "trace.sql"]

    def __init__(
        self,
        db_path: str,
        channel: Any = None,
        sandbox_clock: Clock | None = None,
        start_time: datetime | None = None,
        initial_balance: float = 1000.0,
        initial_liquidity: float = 10000.0,
    ):
        self.initial_balance = initial_balance
        self.initial_liquidity = initial_liquidity
        super().__init__(
            db_path=db_path,
            channel=channel,
            sandbox_clock=sandbox_clock,
            start_time=start_time,
        )

    # ------------------------------------------------------------------
    # Override sign_up to also create a portfolio
    # ------------------------------------------------------------------

    async def sign_up(self, agent_id, user_message):
        """Register agent and create their portfolio with initial balance."""
        result = await super().sign_up(agent_id, user_message)
        if result["success"]:
            self._execute_db_command(
                "INSERT INTO portfolio (user_id, balance) VALUES (?, ?)",
                (agent_id, self.initial_balance),
                commit=True,
            )
        return result

    # ------------------------------------------------------------------
    # Market actions
    # ------------------------------------------------------------------

    async def create_market(self, agent_id, market_message):
        """Create a new prediction market."""
        # Support optional initial_probability as 4th element
        if len(market_message) >= 4:
            question, outcome_a, outcome_b, initial_prob = market_message
            initial_prob = max(0.05, min(0.95, float(initial_prob)))
        else:
            question, outcome_a, outcome_b = market_message
            initial_prob = 0.5

        current_time = self.get_current_time()
        try:
            liq = self.initial_liquidity
            # Set reserves so price_YES = initial_prob
            # price_YES = reserve_b / (reserve_a + reserve_b)
            # With constant product k = reserve_a * reserve_b = liq^2
            import math
            k = liq * liq
            reserve_b = math.sqrt(k * initial_prob / (1 - initial_prob))
            reserve_a = k / reserve_b

            self._execute_db_command(
                "INSERT INTO market (creator_id, question, outcome_a, "
                "outcome_b, reserve_a, reserve_b, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (agent_id, question, outcome_a, outcome_b, reserve_a, reserve_b,
                 current_time),
                commit=True,
            )
            market_id = self.db_cursor.lastrowid
            self._record_trace(
                agent_id, "create_market",
                {"market_id": market_id, "question": question},
                current_time,
            )
            return {"success": True, "market_id": market_id}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def buy_shares(self, agent_id, trade_message):
        """Buy shares in a market outcome using the AMM."""
        market_id, outcome, amount_usd = trade_message
        amount_usd = float(amount_usd)
        current_time = self.get_current_time()

        # Check balance
        self._execute_db_command(
            "SELECT balance FROM portfolio WHERE user_id = ?", (agent_id,))
        row = self.db_cursor.fetchone()
        if not row:
            return {"success": False, "error": "No portfolio found"}
        balance = row[0]
        if balance < amount_usd:
            return {"success": False, "error": "Insufficient balance",
                    "balance": balance, "requested": amount_usd}

        # Get market
        self._execute_db_command(
            "SELECT reserve_a, reserve_b, outcome_a, outcome_b, resolved "
            "FROM market WHERE market_id = ?", (market_id,))
        market = self.db_cursor.fetchone()
        if not market:
            return {"success": False, "error": "Market not found"}
        reserve_a, reserve_b, outcome_a, outcome_b, resolved = market
        if resolved:
            return {"success": False, "error": "Market is already resolved"}

        # Validate outcome
        if outcome not in (outcome_a, outcome_b):
            return {"success": False,
                    "error": f"Invalid outcome '{outcome}'. "
                    f"Choose '{outcome_a}' or '{outcome_b}'"}

        # Cap trade size at 2% of total pool to prevent price manipulation
        pool_total = reserve_a + reserve_b
        max_trade = pool_total * 0.02
        if amount_usd > max_trade:
            amount_usd = max_trade

        # Execute trade via AMM
        trade = quote_buy(reserve_a, reserve_b, outcome, amount_usd,
                          outcome_a_name=outcome_a)

        # Update reserves
        self._execute_db_command(
            "UPDATE market SET reserve_a = ?, reserve_b = ? "
            "WHERE market_id = ?",
            (trade.new_reserve_a, trade.new_reserve_b, market_id),
            commit=True,
        )

        # Deduct balance
        self._execute_db_command(
            "UPDATE portfolio SET balance = balance - ? WHERE user_id = ?",
            (amount_usd, agent_id),
            commit=True,
        )

        # Add to position (upsert)
        self._execute_db_command(
            "INSERT INTO position (user_id, market_id, outcome, shares) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, market_id, outcome) "
            "DO UPDATE SET shares = shares + ?",
            (agent_id, market_id, outcome, trade.shares_received,
             trade.shares_received),
            commit=True,
        )

        # Record trade
        self._execute_db_command(
            "INSERT INTO trade (user_id, market_id, side, outcome, shares, "
            "price, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, market_id, "buy", outcome, trade.shares_received,
             trade.effective_price, amount_usd, current_time),
            commit=True,
        )

        self._record_trace(
            agent_id, "buy_shares",
            {"market_id": market_id, "outcome": outcome,
             "shares": trade.shares_received,
             "price": trade.effective_price, "cost": amount_usd},
            current_time,
        )

        return {
            "success": True,
            "shares_received": round(trade.shares_received, 4),
            "effective_price": round(trade.effective_price, 4),
            "total_cost": round(amount_usd, 2),
        }

    async def sell_shares(self, agent_id, trade_message):
        """Sell shares back to the AMM."""
        market_id, outcome, num_shares = trade_message
        num_shares = float(num_shares)
        current_time = self.get_current_time()

        # Check position
        self._execute_db_command(
            "SELECT shares FROM position "
            "WHERE user_id = ? AND market_id = ? AND outcome = ?",
            (agent_id, market_id, outcome))
        row = self.db_cursor.fetchone()
        if not row or row[0] < num_shares:
            return {"success": False, "error": "Insufficient shares",
                    "held": row[0] if row else 0}

        # Get market
        self._execute_db_command(
            "SELECT reserve_a, reserve_b, outcome_a, resolved "
            "FROM market WHERE market_id = ?", (market_id,))
        market = self.db_cursor.fetchone()
        if not market:
            return {"success": False, "error": "Market not found"}
        reserve_a, reserve_b, outcome_a, resolved = market
        if resolved:
            return {"success": False, "error": "Market is already resolved"}

        # Execute sell via AMM
        trade = quote_sell(reserve_a, reserve_b, outcome, num_shares,
                           outcome_a_name=outcome_a)

        usd_received = trade.shares_received  # For sells this is USD out

        # Update reserves
        self._execute_db_command(
            "UPDATE market SET reserve_a = ?, reserve_b = ? "
            "WHERE market_id = ?",
            (trade.new_reserve_a, trade.new_reserve_b, market_id),
            commit=True,
        )

        # Add to balance
        self._execute_db_command(
            "UPDATE portfolio SET balance = balance + ? WHERE user_id = ?",
            (usd_received, agent_id),
            commit=True,
        )

        # Reduce position
        self._execute_db_command(
            "UPDATE position SET shares = shares - ? "
            "WHERE user_id = ? AND market_id = ? AND outcome = ?",
            (num_shares, agent_id, market_id, outcome),
            commit=True,
        )

        # Record trade
        self._execute_db_command(
            "INSERT INTO trade (user_id, market_id, side, outcome, shares, "
            "price, cost, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (agent_id, market_id, "sell", outcome, num_shares,
             trade.effective_price, -usd_received, current_time),
            commit=True,
        )

        self._record_trace(
            agent_id, "sell_shares",
            {"market_id": market_id, "outcome": outcome,
             "shares": num_shares, "price": trade.effective_price,
             "usd_received": usd_received},
            current_time,
        )

        return {
            "success": True,
            "usd_received": round(usd_received, 2),
            "effective_price": round(trade.effective_price, 4),
        }

    async def browse_markets(self, agent_id):
        """Browse active markets with current prices."""
        self._execute_db_command(
            "SELECT market_id, question, outcome_a, outcome_b, "
            "reserve_a, reserve_b, created_at "
            "FROM market WHERE resolved = 0 "
            "ORDER BY market_id DESC LIMIT 10"
        )
        rows = self.db_cursor.fetchall()
        markets = []
        for row in rows:
            mid, question, oa, ob, ra, rb, created = row
            price_a, price_b = get_prices(ra, rb)
            # Count trades as volume proxy
            self._execute_db_command(
                "SELECT COUNT(*) FROM trade WHERE market_id = ?", (mid,))
            trade_count = self.db_cursor.fetchone()[0]
            markets.append({
                "market_id": mid,
                "question": question,
                f"price_{oa}": round(price_a, 3),
                f"price_{ob}": round(price_b, 3),
                "num_trades": trade_count,
                "created_at": created,
            })
        return {"success": True, "markets": markets}

    async def view_portfolio(self, agent_id):
        """View agent's balance and positions."""
        # Balance
        self._execute_db_command(
            "SELECT balance FROM portfolio WHERE user_id = ?", (agent_id,))
        row = self.db_cursor.fetchone()
        balance = row[0] if row else 0

        # Positions
        self._execute_db_command(
            "SELECT p.market_id, p.outcome, p.shares, m.question, "
            "m.reserve_a, m.reserve_b, m.outcome_a "
            "FROM position p JOIN market m ON p.market_id = m.market_id "
            "WHERE p.user_id = ? AND p.shares > 0",
            (agent_id,))
        rows = self.db_cursor.fetchall()
        positions = []
        for row in rows:
            mid, outcome, shares, question, ra, rb, oa = row
            price_a, price_b = get_prices(ra, rb)
            current_price = price_a if outcome == oa else price_b
            positions.append({
                "market_id": mid,
                "question": question,
                "outcome": outcome,
                "shares": round(shares, 4),
                "current_price": round(current_price, 3),
                "current_value": round(shares * current_price, 2),
            })

        return {
            "success": True,
            "balance": round(balance, 2),
            "positions": positions,
        }

    async def comment_on_market(self, agent_id, comment_message):
        """Comment on a market."""
        market_id, content = comment_message
        current_time = self.get_current_time()
        try:
            self._execute_db_command(
                "INSERT INTO market_comment (market_id, user_id, content, "
                "created_at) VALUES (?, ?, ?, ?)",
                (market_id, agent_id, content, current_time),
                commit=True,
            )
            self._record_trace(
                agent_id, "comment_on_market",
                {"market_id": market_id, "content": content},
                current_time,
            )
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def resolve_market(self, agent_id, resolve_message):
        """Resolve a market and pay out winning positions."""
        market_id, winning_outcome = resolve_message
        current_time = self.get_current_time()

        # Only creator can resolve
        self._execute_db_command(
            "SELECT creator_id, resolved FROM market WHERE market_id = ?",
            (market_id,))
        row = self.db_cursor.fetchone()
        if not row:
            return {"success": False, "error": "Market not found"}
        creator_id, resolved = row
        if resolved:
            return {"success": False, "error": "Already resolved"}
        if creator_id != agent_id:
            return {"success": False, "error": "Only the creator can resolve"}

        # Mark resolved
        self._execute_db_command(
            "UPDATE market SET resolved = 1, winning_outcome = ? "
            "WHERE market_id = ?",
            (winning_outcome, market_id),
            commit=True,
        )

        # Pay out winners: each winning share = $1.00
        self._execute_db_command(
            "SELECT user_id, shares FROM position "
            "WHERE market_id = ? AND outcome = ? AND shares > 0",
            (market_id, winning_outcome))
        winners = self.db_cursor.fetchall()
        for user_id, shares in winners:
            self._execute_db_command(
                "UPDATE portfolio SET balance = balance + ? "
                "WHERE user_id = ?",
                (shares, user_id),  # $1 per winning share
                commit=True,
            )

        self._record_trace(
            agent_id, "resolve_market",
            {"market_id": market_id, "winning_outcome": winning_outcome,
             "num_winners": len(winners)},
            current_time,
        )

        return {
            "success": True,
            "winning_outcome": winning_outcome,
            "num_winners": len(winners),
        }

    def tick_clock(self):
        """Advance the simulation clock by one step."""
        self.sandbox_clock.time_step += 1
