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
"""Polymarket prediction market simulation for Wonderwall.

Usage::

    from wonderwall.simulations.polymarket import polymarket_simulation

    env = wonderwall.make(
        agent_graph=agent_graph,
        simulation=polymarket_simulation,
        database_path="./data/polymarket.db",
    )
"""
from wonderwall.simulations.base import SimulationConfig
from wonderwall.simulations.polymarket.actions import PolymarketAction
from wonderwall.simulations.polymarket.environment import PolymarketEnvironment
from wonderwall.simulations.polymarket.platform import PolymarketPlatform
from wonderwall.simulations.polymarket.prompts import PolymarketPromptBuilder

polymarket_simulation = SimulationConfig(
    name="polymarket",
    platform_cls=PolymarketPlatform,
    action_cls=PolymarketAction,
    environment_cls=PolymarketEnvironment,
    prompt_builder=PolymarketPromptBuilder(),
    default_actions=[
        "buy_shares", "sell_shares",
        "do_nothing",
    ],
    platform_kwargs={
        "initial_balance": 1000.0,
        "initial_liquidity": 10000.0,
    },
)

__all__ = [
    "polymarket_simulation",
    "PolymarketPlatform",
    "PolymarketAction",
    "PolymarketEnvironment",
    "PolymarketPromptBuilder",
]
