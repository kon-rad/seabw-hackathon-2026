#!/usr/bin/env python3
"""
Test market generation + initial pricing in a live Polymarket simulation.

1. Generate markets from config generator (LLM call)
2. Seed them with non-50/50 prices
3. Run 5 rounds and verify prices, trades, and agent behavior
"""

import json
import os
import sys
import time
import sqlite3
from datetime import datetime

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_dir, '..', '.env'))

OUT_DIR = os.path.join(_backend_dir, 'pipeline_test_output')
SIM_DIR = os.path.join(OUT_DIR, 'sim_markets')

SIMULATION_REQUIREMENT = (
    "Simulate public reaction on Twitter, Reddit, and Polymarket to this article "
    "about Polymarket's rise. Focus on: crypto community reactions, regulatory "
    "concerns, prediction market enthusiasts vs. skeptics, and how the market "
    "itself might react to increased media attention."
)


def banner(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def test_market_generation():
    """Test the LLM market generation step independently."""
    banner("STEP 1: Generate prediction markets (LLM)")
    t0 = time.time()

    from app.services.simulation_config_generator import SimulationConfigGenerator, EventConfig

    generator = SimulationConfigGenerator()

    # Build minimal context
    with open(os.path.join(OUT_DIR, '01_parsed_text.txt')) as f:
        doc_text = f.read()

    context = f"## Simulation Requirement\n{SIMULATION_REQUIREMENT}\n\n## Document\n{doc_text[:2000]}"

    # Create a minimal event config with hot topics
    event_config = EventConfig()
    event_config.hot_topics = [
        "Polymarket rise", "prediction markets", "crypto regulation",
        "election forecasting", "CFTC enforcement",
    ]

    markets = generator._generate_prediction_markets(context, SIMULATION_REQUIREMENT, event_config)

    elapsed = time.time() - t0
    print(f"  Generated {len(markets)} markets in {elapsed:.1f}s\n")

    for i, m in enumerate(markets):
        prob = m.get('initial_probability', 0.5)
        print(f"  Market {i+1}: \"{m['question']}\"")
        print(f"    Starting price: YES ${prob:.2f} / NO ${1-prob:.2f}")
        print(f"    Reasoning: {m.get('reasoning', 'N/A')}")
        print()

    return markets


def test_amm_pricing(markets):
    """Verify AMM creates correct initial prices."""
    banner("STEP 2: Verify AMM initial pricing")

    import math
    liq = 100.0  # default initial_liquidity

    for i, m in enumerate(markets):
        prob = m.get('initial_probability', 0.5)
        k = liq * liq
        reserve_b = math.sqrt(k * prob / (1 - prob))
        reserve_a = k / reserve_b
        actual_price_yes = reserve_b / (reserve_a + reserve_b)

        diff = abs(actual_price_yes - prob)
        status = "OK" if diff < 0.001 else f"DRIFT {diff:.4f}"

        print(f"  Market {i+1}: target={prob:.3f} actual={actual_price_yes:.3f} "
              f"reserves=({reserve_a:.1f}, {reserve_b:.1f}) [{status}]")


def test_live_simulation(markets):
    """Run a live 5-round Polymarket-only simulation with pre-seeded markets."""
    banner("STEP 3: Live Polymarket simulation (5 rounds)")

    os.makedirs(SIM_DIR, exist_ok=True)
    os.makedirs(os.path.join(SIM_DIR, 'polymarket'), exist_ok=True)

    # Load profiles
    with open(os.path.join(OUT_DIR, '04_profiles.json')) as f:
        phase4_profiles = json.load(f)

    # Load base config
    with open(os.path.join(OUT_DIR, '05_simulation_config.json')) as f:
        config = json.load(f)

    agent_configs = config.get('agent_configs', [])

    # Build Polymarket profiles (0-based IDs)
    pm_profiles = []
    for i, ac in enumerate(agent_configs):
        name = ac.get('entity_name', f'Agent_{i}')
        p4 = next((p for p in phase4_profiles if p['name'].lower() == name.lower()), {})
        pm_profiles.append({
            "user_id": i,
            "name": (p4.get('user_name') or name.lower().replace(' ', '_')),
            "description": p4.get('bio', f"Trader: {name}"),
            "risk_tolerance": p4.get('risk_tolerance', 'moderate'),
            "user_profile": p4.get('persona', f"{name} trades on prediction markets."),
        })

    with open(os.path.join(SIM_DIR, 'polymarket_profiles.json'), 'w') as f:
        json.dump(pm_profiles, f, ensure_ascii=False, indent=2)

    # Override config
    config['time_config']['off_peak_activity_multiplier'] = 1.0
    config['time_config']['morning_activity_multiplier'] = 1.0
    config['time_config']['work_activity_multiplier'] = 1.0
    config['time_config']['peak_activity_multiplier'] = 1.0
    config['time_config']['agents_per_hour_min'] = len(agent_configs)
    config['time_config']['agents_per_hour_max'] = len(agent_configs)
    config['simulation_requirement'] = SIMULATION_REQUIREMENT

    for ac in agent_configs:
        ac['activity_level'] = 1.0
        ac['active_hours'] = list(range(0, 24))

    # Inject generated markets into event_config
    config['event_config'] = config.get('event_config', {})
    config['event_config']['initial_markets'] = markets

    config_path = os.path.join(SIM_DIR, 'simulation_config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"  {len(pm_profiles)} traders, {len(markets)} markets, 5 rounds")
    print(f"  Markets seeded:")
    for m in markets:
        prob = m['initial_probability']
        print(f"    \"{m['question'][:60]}...\" @ YES ${prob:.2f}")

    # Run simulation
    import subprocess
    cmd = [
        sys.executable, os.path.join(_scripts_dir, 'run_parallel_simulation.py'),
        '--config', config_path,
        '--polymarket-only',
        '--max-rounds', '5',
        '--no-wait',
    ]

    t0 = time.time()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=_backend_dir,
    )

    for line in proc.stdout:
        line = line.rstrip()
        if any(k in line for k in [
            'Seeded', 'Market:', 'Round', 'complete', 'Error',
            'FAILED', 'elapsed', 'Simulation', 'action',
        ]):
            print(f"    {line}")

    proc.wait()
    elapsed = time.time() - t0
    print(f"\n  Exit code: {proc.returncode}, Time: {elapsed:.1f}s")
    return elapsed


def analyze_market_results(markets):
    """Analyze final market state and compare to initial prices."""
    banner("STEP 4: Market analysis")

    db_path = os.path.join(SIM_DIR, 'polymarket_simulation.db')
    if not os.path.exists(db_path):
        print("  No database found — simulation may have failed")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get initial prices from config
    initial_prices = {m['question'][:50]: m['initial_probability'] for m in markets}

    print(f"\n  {'Market':<55} {'Start':>7} {'Final':>7} {'Move':>7} {'Trades':>6}")
    print(f"  {'-'*55} {'-'*7} {'-'*7} {'-'*7} {'-'*6}")

    for row in conn.execute("SELECT * FROM market ORDER BY market_id"):
        ra, rb = row['reserve_a'], row['reserve_b']
        total = ra + rb
        final_price = rb / total if total > 0 else 0.5

        q_short = row['question'][:50]
        start_price = initial_prices.get(q_short, 0.5)
        move = final_price - start_price

        trades = conn.execute(
            "SELECT COUNT(*) FROM trade WHERE market_id=?",
            (row['market_id'],)
        ).fetchone()[0]

        direction = "↑" if move > 0.01 else "↓" if move < -0.01 else "→"

        print(f"  {q_short:<55} ${start_price:.2f}  ${final_price:.2f}  {direction}{abs(move):.2f}   {trades:>4}")

    # Trade details
    print(f"\n  All trades:")
    for t in conn.execute("""
        SELECT t.*, u.user_name FROM trade t
        JOIN user u ON t.user_id = u.user_id
        ORDER BY t.rowid
    """):
        agent = t['user_name'] or f"Agent_{t['user_id']}"
        side = t['side'].upper()
        outcome = t['outcome']
        shares = t['shares']
        price = t['price']
        cost = abs(t['cost'])
        mid = t['market_id']

        if side == 'BUY':
            print(f"    {side:4s} | {agent:35s} | M#{mid} {shares:6.1f} {outcome:3s} @ ${price:.3f} | -${cost:.2f}")
        else:
            print(f"    {side:4s} | {agent:35s} | M#{mid} {shares:6.1f} {outcome:3s} @ ${price:.3f} | +${cost:.2f}")

    # Portfolio P&L
    print(f"\n  Trader P&L:")
    for row in conn.execute("""
        SELECT p.user_id, p.balance, u.user_name FROM portfolio p
        JOIN user u ON p.user_id = u.user_id ORDER BY p.user_id
    """):
        uid = row['user_id']
        balance = row['balance']
        agent = row['user_name'] or f"Agent_{uid}"

        # Calculate position value
        pos_value = 0
        positions = []
        for pos in conn.execute("""
            SELECT pos.*, m.reserve_a, m.reserve_b, m.outcome_a, m.question
            FROM position pos JOIN market m ON pos.market_id = m.market_id
            WHERE pos.user_id = ? AND pos.shares > 0.01
        """, (uid,)):
            ra, rb = pos['reserve_a'], pos['reserve_b']
            total = ra + rb
            price_a = rb / total if total > 0 else 0.5
            price_b = ra / total if total > 0 else 0.5
            cp = price_a if pos['outcome'] == pos['outcome_a'] else price_b
            val = pos['shares'] * cp
            pos_value += val
            positions.append(f"M#{pos['market_id']} {pos['shares']:.0f}{pos['outcome']} @${cp:.2f}")

        total_val = balance + pos_value
        pnl = total_val - 1000.0
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

        pos_str = ", ".join(positions) if positions else "no positions"
        print(f"    {agent:35s} | Cash: ${balance:.0f} | Positions: ${pos_value:.0f} | Total: ${total_val:.0f} | P&L: {pnl_str}")
        if positions:
            print(f"      {pos_str}")

    # Comments
    comments = conn.execute("""
        SELECT mc.*, u.user_name FROM market_comment mc
        JOIN user u ON mc.user_id = u.user_id ORDER BY mc.rowid
    """).fetchall()
    if comments:
        print(f"\n  Market comments ({len(comments)}):")
        for c in comments:
            agent = c['user_name'] or f"Agent_{c['user_id']}"
            print(f"    [{agent}] M#{c['market_id']}: {c['content'][:150]}")

    conn.close()


def main():
    print(f"\n{'#'*60}")
    print(f"  Market Generation + Pricing Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    # Step 1: Generate markets
    markets = test_market_generation()

    # Step 2: Verify AMM math
    test_amm_pricing(markets)

    # Step 3: Live simulation
    test_live_simulation(markets)

    # Step 4: Analyze results
    analyze_market_results(markets)

    banner("TEST COMPLETE")


if __name__ == '__main__':
    main()
