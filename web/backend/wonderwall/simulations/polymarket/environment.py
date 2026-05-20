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
"""Polymarket agent environment — what the agent sees each turn."""
from __future__ import annotations


from wonderwall.simulations.base import BaseEnvironment


class PolymarketEnvironment(BaseEnvironment):
    """Converts Polymarket state into the text prompt the agent observes."""

    async def to_text_prompt(self) -> str:
        portfolio = await self.action.view_portfolio()
        markets = await self.action.browse_markets()

        parts = []

        if portfolio.get("success"):
            balance = portfolio['balance']
            parts.append(f"YOUR PORTFOLIO:\n  Cash: ${balance:.2f}")

            positions = portfolio.get("positions", [])
            if positions:
                total_invested = 0
                total_value = 0
                parts.append("  Open positions:")
                for pos in positions:
                    cost_basis = pos['shares'] * 0.50  # approximate (bought near 0.50)
                    current_value = pos['current_value']
                    pnl = current_value - cost_basis
                    pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0
                    total_invested += cost_basis
                    total_value += current_value

                    # Flag actionable positions
                    flag = ""
                    if pos['current_price'] > 0.90:
                        flag = " ⚠️ PRICE NEAR MAX — consider taking profit"
                    elif pos['current_price'] < 0.10:
                        flag = " ⚠️ PRICE NEAR ZERO — consider cutting loss"
                    elif pnl_pct > 30:
                        flag = " 📈 IN PROFIT — consider selling some"
                    elif pnl_pct < -30:
                        flag = " 📉 AT LOSS — reassess your thesis"

                    parts.append(
                        f"    - Market #{pos['market_id']}: "
                        f"\"{pos['question']}\" — "
                        f"{pos['shares']:.1f} {pos['outcome']} shares "
                        f"@ ${pos['current_price']:.3f} "
                        f"(value: ${current_value:.2f}, "
                        f"P&L: {'+'if pnl>=0 else ''}{pnl:.2f})"
                        f"{flag}"
                    )

                portfolio_value = balance + total_value
                parts.append(f"  Total portfolio value: ${portfolio_value:.2f}")
            else:
                parts.append("  No open positions.")

        if markets.get("success") and markets.get("markets"):
            parts.append("\nACTIVE MARKETS:")
            for m in markets["markets"]:
                price_keys = [k for k in m if k.startswith("price_")]
                price_str = ", ".join(
                    f"{k.replace('price_', '')}: ${m[k]:.3f}"
                    for k in price_keys
                )
                num_trades = m.get('num_trades', 0)

                # Highlight markets with extreme prices (potential sell/contrarian signal)
                note = ""
                for k in price_keys:
                    if m[k] > 0.90:
                        note = " — market is very confident, contrarian opportunity?"
                    elif m[k] < 0.10:
                        note = " — market is very confident, contrarian opportunity?"

                parts.append(
                    f"  #{m['market_id']}: \"{m['question']}\" "
                    f"[{price_str}] "
                    f"({num_trades} trades){note}"
                )
        else:
            parts.append("\nNo active markets yet.")

        # Inject cross-platform social media context directly into observation
        if self.extra_observation_context:
            parts.append(f"\nSOCIAL MEDIA CONTEXT:\n{self.extra_observation_context}")

        parts.append(
            "\nDecide: buy_shares, sell_shares, or do_nothing."
            "\nConsider the social media context above — it tells you what "
            "people are discussing on Twitter and Reddit. If social sentiment "
            "conflicts with the market price, that's a trading signal."
        )

        return "\n".join(parts)
