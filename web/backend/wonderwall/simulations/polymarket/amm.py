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
"""Constant-product Automated Market Maker (AMM) for prediction markets.

The AMM uses the x * y = k invariant where:
- x = reserve of outcome A shares
- y = reserve of outcome B shares
- k = constant product

Price of outcome A = reserve_b / (reserve_a + reserve_b)
Price of outcome B = reserve_a / (reserve_a + reserve_b)

Prices always sum to 1.0 (the payout for a winning share).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TradeResult:
    """Result of an AMM trade."""
    shares_received: float
    effective_price: float
    total_cost: float
    new_reserve_a: float
    new_reserve_b: float


def get_prices(reserve_a: float, reserve_b: float) -> tuple[float, float]:
    """Get current prices for outcome A and B.

    Returns:
        (price_a, price_b) where both are in [0, 1] and sum to 1.
    """
    total = reserve_a + reserve_b
    if total == 0:
        return 0.5, 0.5
    return reserve_b / total, reserve_a / total


def quote_buy(
    reserve_a: float,
    reserve_b: float,
    outcome: str,
    amount_usd: float,
    outcome_a_name: str = "YES",
) -> TradeResult:
    """Quote a buy order: spend ``amount_usd`` to get shares of ``outcome``.

    Uses the **mint-and-swap** mechanism standard in prediction markets:

    1. Mint ``amount_usd`` complete sets (one A share + one B share per $1).
    2. Sell the unwanted outcome shares back to the pool.
    3. Keep all desired outcome shares (minted + received from swap).

    This ensures the effective price is always in (0, 1) since each share
    can pay out at most $1.
    """
    if amount_usd <= 0:
        raise ValueError("amount_usd must be positive")

    k = reserve_a * reserve_b
    minted = amount_usd  # 1 complete set per $1

    if outcome == outcome_a_name:
        # Mint: get `minted` A shares + `minted` B shares.
        # Swap the B shares into the pool, get more A shares out.
        new_reserve_b = reserve_b + minted
        new_reserve_a = k / new_reserve_b
        swapped_a_out = reserve_a - new_reserve_a
        shares_out = minted + swapped_a_out
    else:
        # Mint and swap the A shares into the pool for more B shares.
        new_reserve_a = reserve_a + minted
        new_reserve_b = k / new_reserve_a
        swapped_b_out = reserve_b - new_reserve_b
        shares_out = minted + swapped_b_out

    effective_price = amount_usd / shares_out if shares_out > 0 else 0

    return TradeResult(
        shares_received=shares_out,
        effective_price=effective_price,
        total_cost=amount_usd,
        new_reserve_a=new_reserve_a,
        new_reserve_b=new_reserve_b,
    )


def quote_sell(
    reserve_a: float,
    reserve_b: float,
    outcome: str,
    shares: float,
    outcome_a_name: str = "YES",
) -> TradeResult:
    """Quote a sell order: sell ``shares`` of ``outcome`` to get USD back.

    Uses the **split-swap-and-burn** mechanism:

    1. Split ``shares`` into *x* (swapped into pool) and *S - x* (kept).
    2. Swap *x* shares into the pool for the other outcome's shares.
    3. Burn complete sets: pair the kept *(S - x)* shares with the
       received other-outcome shares.  Each pair redeems for $1.

    The split ratio *x* is chosen so the swap output exactly equals
    *S - x*, maximising the number of complete sets burned.  This is
    solved via the quadratic:

        x² + x·(R_a + R_b - S) - S·R_other = 0

    where R_other is the reserve of the side being sold.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")

    import math

    S = shares
    R_a, R_b = reserve_a, reserve_b

    if outcome == outcome_a_name:
        R_other = R_a  # reserve of the side we're selling
    else:
        R_other = R_b

    # Solve x² + x·(R_a + R_b - S) - S·R_other = 0
    a_coeff = 1.0
    b_coeff = R_a + R_b - S
    c_coeff = -S * R_other

    discriminant = b_coeff ** 2 - 4 * a_coeff * c_coeff
    x = (-b_coeff + math.sqrt(discriminant)) / (2 * a_coeff)

    usd_out = S - x  # complete sets burned

    k = R_a * R_b
    if outcome == outcome_a_name:
        new_reserve_a = R_a + x
        new_reserve_b = k / new_reserve_a
    else:
        new_reserve_b = R_b + x
        new_reserve_a = k / new_reserve_b

    effective_price = usd_out / S if S > 0 else 0

    return TradeResult(
        shares_received=usd_out,  # USD received
        effective_price=effective_price,
        total_cost=-usd_out,  # Negative = money received
        new_reserve_a=new_reserve_a,
        new_reserve_b=new_reserve_b,
    )
