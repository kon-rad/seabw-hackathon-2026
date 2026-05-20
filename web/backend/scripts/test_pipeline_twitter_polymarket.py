#!/usr/bin/env python3
"""
Test Twitter + Polymarket simulation (5 rounds, all 3 platforms).

Reuses the graph + config from previous runs.
"""

import json
import os
import sys
import time
import csv
from datetime import datetime

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(_backend_dir, '..', '.env'))

OUT_DIR = os.path.join(_backend_dir, 'pipeline_test_output')
SIM_DIR = os.path.join(OUT_DIR, 'sim_full')

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


def setup_simulation():
    """Build simulation directory with all required files."""
    banner("SETUP: Preparing 3-platform simulation")

    os.makedirs(SIM_DIR, exist_ok=True)
    os.makedirs(os.path.join(SIM_DIR, 'twitter'), exist_ok=True)
    os.makedirs(os.path.join(SIM_DIR, 'reddit'), exist_ok=True)

    # Load config from phase 5
    with open(os.path.join(OUT_DIR, '05_simulation_config.json')) as f:
        config = json.load(f)

    # Load profiles from phase 4
    with open(os.path.join(OUT_DIR, '04_profiles.json')) as f:
        phase4_profiles = json.load(f)
    profile_lookup = {p['name'].lower(): p for p in phase4_profiles}

    agent_configs = config.get('agent_configs', [])

    # Override for test: force all agents active, 5 rounds
    config['time_config']['total_simulation_hours'] = 72
    config['time_config']['minutes_per_round'] = 60
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

    # ── Reddit profiles (JSON) ──
    reddit_profiles = []
    for i, ac in enumerate(agent_configs):
        name = ac.get('entity_name', f'Agent_{i}')
        p4 = profile_lookup.get(name.lower(), {})
        reddit_profiles.append({
            "user_id": i + 1,
            "username": (p4.get('user_name') or name.lower().replace(' ', '_')),
            "name": name,
            "bio": p4.get('bio', f"Agent for {ac.get('entity_type', 'Entity')}"),
            "persona": p4.get('persona', f"{name} participates in online discussions."),
            "karma": p4.get('karma', 1000),
            "age": p4.get('age', 30),
            "gender": p4.get('gender', 'other'),
            "mbti": p4.get('mbti', 'INTJ'),
            "country": p4.get('country', 'US'),
            "profession": p4.get('profession', ac.get('entity_type', 'Unknown')),
            "interested_topics": p4.get('interested_topics', ['General']),
            "created_at": "2024-01-01",
        })

    with open(os.path.join(SIM_DIR, 'reddit_profiles.json'), 'w') as f:
        json.dump(reddit_profiles, f, ensure_ascii=False, indent=2)
    print(f"  Reddit profiles: {len(reddit_profiles)}")

    # ── Twitter profiles (CSV) ──
    # CAMEL expects: user_id, name, username, user_char, description
    # user_char = full persona (for LLM system prompt)
    # description = short bio (displayed in profile)
    twitter_fields = ['user_id', 'name', 'username', 'user_char', 'description']
    twitter_path = os.path.join(SIM_DIR, 'twitter_profiles.csv')
    with open(twitter_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=twitter_fields)
        writer.writeheader()
        for i, ac in enumerate(agent_configs):
            name = ac.get('entity_name', f'Agent_{i}')
            p4 = profile_lookup.get(name.lower(), {})

            bio = p4.get('bio', f"Agent for {ac.get('entity_type', 'Entity')}")
            persona = p4.get('persona', f"{name} participates in discussions.")
            # user_char is bio + persona, newlines stripped
            user_char = f"{bio} {persona}".replace('\n', ' ').replace('\r', ' ')

            writer.writerow({
                'user_id': i + 1,
                'name': name,
                'username': (p4.get('user_name') or name.lower().replace(' ', '_')),
                'user_char': user_char,
                'description': bio.replace('\n', ' ').replace('\r', ' '),
            })
    print(f"  Twitter profiles: {len(agent_configs)}")

    # ── Polymarket profiles (JSON) ──
    # Polymarket uses 0-based agent IDs (igraph vertex index)
    polymarket_profiles = []
    for i, ac in enumerate(agent_configs):
        name = ac.get('entity_name', f'Agent_{i}')
        p4 = profile_lookup.get(name.lower(), {})
        polymarket_profiles.append({
            "user_id": i,
            "name": (p4.get('user_name') or name.lower().replace(' ', '_')),
            "description": p4.get('bio', f"Trader: {name}"),
            "risk_tolerance": p4.get('risk_tolerance', 'moderate'),
            "user_profile": p4.get('persona', f"{name} trades on prediction markets."),
        })

    with open(os.path.join(SIM_DIR, 'polymarket_profiles.json'), 'w') as f:
        json.dump(polymarket_profiles, f, ensure_ascii=False, indent=2)
    print(f"  Polymarket profiles: {len(polymarket_profiles)}")

    # ── Add Polymarket markets to config ──
    config['polymarket_config'] = {
        'initial_markets': [
            {
                'question': 'Will Polymarket face new regulatory action within 6 months?',
                'outcome_a': 'YES',
                'outcome_b': 'NO',
            },
            {
                'question': 'Will prediction markets become mainstream by 2026?',
                'outcome_a': 'YES',
                'outcome_b': 'NO',
            },
        ],
        'initial_balance': 1000.0,
        'initial_liquidity': 100.0,
    }

    # Save config
    config_path = os.path.join(SIM_DIR, 'simulation_config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"  Config saved")

    return config_path


def run_simulation(config_path):
    """Run all 3 platforms for 5 rounds."""
    banner("SIMULATION: 5 rounds, Twitter + Reddit + Polymarket")

    import subprocess
    cmd = [
        sys.executable, os.path.join(_scripts_dir, 'run_parallel_simulation.py'),
        '--config', config_path,
        '--max-rounds', '5',
        '--no-wait',
        '--cross-platform',
    ]
    print(f"  Command: {os.path.basename(cmd[0])} {' '.join(cmd[2:])}")

    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=_backend_dir,
    )

    output_lines = []
    for line in proc.stdout:
        line = line.rstrip()
        output_lines.append(line)
        # Print key progress lines
        if any(k in line for k in [
            'Round ', 'Environment', 'complete', 'FAILED', 'Error',
            'Starting', 'Published', 'active', 'Sync', 'Bridge',
            'Memory', 'Belief', 'Trajectory', 'elapsed', 'Simulation',
        ]):
            print(f"    {line}")

    proc.wait()
    elapsed = time.time() - t0

    print(f"\n  Exit code: {proc.returncode}")
    print(f"  Time: {elapsed:.1f}s")

    # Save full output
    out_path = os.path.join(OUT_DIR, '07_full_simulation_output.txt')
    with open(out_path, 'w') as f:
        f.write('\n'.join(output_lines))
    print(f"  Full log: {out_path}")

    return elapsed


def analyze_results():
    """Analyze all platform outputs."""
    banner("ANALYSIS")

    for platform in ['twitter', 'reddit', 'polymarket']:
        actions_path = os.path.join(SIM_DIR, platform, 'actions.jsonl')
        if not os.path.exists(actions_path):
            print(f"\n  [{platform.upper()}] No actions file")
            continue

        with open(actions_path) as f:
            all_lines = [json.loads(l) for l in f if l.strip()]

        # Separate metadata from real actions
        actions = [a for a in all_lines if 'action_type' in a]
        metadata = [a for a in all_lines if 'event_type' in a]

        from collections import Counter
        types = Counter(a['action_type'] for a in actions)

        print(f"\n  [{platform.upper()}] {len(actions)} actions, {len(metadata)} metadata events")
        print(f"  Action breakdown:")
        for t, c in types.most_common():
            print(f"    {t}: {c}")

        # DO_NOTHING rate
        do_nothing = types.get('DO_NOTHING', 0) + types.get('do_nothing', 0)
        total = len(actions)
        if total > 0:
            print(f"  DO_NOTHING rate: {do_nothing}/{total} ({do_nothing/total*100:.0f}%)")

        # Sample posts/content
        posts = [a for a in actions if a.get('action_type') in ('CREATE_POST', 'create_post')]
        comments = [a for a in actions if a.get('action_type') in ('CREATE_COMMENT', 'create_comment')]
        trades = [a for a in actions if a.get('action_type') in ('buy_shares', 'sell_shares')]
        market_comments = [a for a in actions if a.get('action_type') == 'comment_on_market']

        if posts:
            print(f"\n  Sample posts ({len(posts)} total):")
            for p in posts[:4]:
                agent = p.get('agent_name', '?')
                content = p.get('action_args', {}).get('content', '')[:180]
                print(f"    [{agent}] {content}")

        if comments:
            print(f"\n  Sample comments ({len(comments)} total):")
            for c in comments[:3]:
                agent = c.get('agent_name', '?')
                content = c.get('action_args', {}).get('content', '')[:180]
                print(f"    [{agent}] {content}")

        if trades:
            print(f"\n  Trades ({len(trades)} total):")
            for t in trades[:5]:
                agent = t.get('agent_name', '?')
                args = t.get('action_args', {})
                side = t.get('action_type', '')
                outcome = args.get('outcome', '?')
                amount = args.get('amount_usd', args.get('num_shares', '?'))
                market = args.get('market_id', '?')
                print(f"    [{agent}] {side} market#{market} {outcome} ${amount}")

        if market_comments:
            print(f"\n  Market comments ({len(market_comments)} total):")
            for mc in market_comments[:3]:
                agent = mc.get('agent_name', '?')
                content = mc.get('action_args', {}).get('content', '')[:150]
                print(f"    [{agent}] {content}")

    # Check Polymarket DB for market state
    pm_db = os.path.join(SIM_DIR, 'polymarket_simulation.db')
    if os.path.exists(pm_db):
        import sqlite3
        conn = sqlite3.connect(pm_db)
        conn.row_factory = sqlite3.Row

        print(f"\n  [POLYMARKET MARKETS]")
        for row in conn.execute("SELECT * FROM market"):
            ra, rb = row['reserve_a'], row['reserve_b']
            total = ra + rb
            price_yes = rb / total if total > 0 else 0.5
            print(f"    #{row['market_id']}: \"{row['question']}\"")
            print(f"      YES: ${price_yes:.3f}, trades: {conn.execute('SELECT COUNT(*) FROM trade WHERE market_id=?', (row['market_id'],)).fetchone()[0]}")

        print(f"\n  [POLYMARKET PORTFOLIOS]")
        for row in conn.execute("SELECT p.user_id, p.balance, u.user_name FROM portfolio p JOIN user u ON p.user_id = u.user_id"):
            print(f"    {row['user_name']}: ${row['balance']:.2f}")

        conn.close()


def main():
    print(f"\n{'#'*60}")
    print(f"  MiroShark — Full 3-Platform Simulation Test")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")

    config_path = setup_simulation()
    elapsed = run_simulation(config_path)
    analyze_results()

    banner("DONE")
    print(f"  Total: {elapsed:.0f}s")


if __name__ == '__main__':
    main()
