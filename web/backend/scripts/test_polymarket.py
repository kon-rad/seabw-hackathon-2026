"""
Standalone test for Polymarket simulation integration.

Creates a minimal simulation directory with test profiles and config,
then runs 3 rounds of Polymarket trading. No Neo4j or frontend needed.

Usage:
    cd backend/scripts
    python test_polymarket.py
"""
import asyncio
import json
import os
import sys
import shutil

# Add scripts dir to path
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _scripts_dir)

# Load .env from project root
from dotenv import load_dotenv
_project_root = os.path.abspath(os.path.join(_scripts_dir, '..', '..'))
for env_path in [os.path.join(_project_root, '.env'), os.path.join(_scripts_dir, '..', '.env')]:
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded env: {env_path}")
        break

# Verify LLM key is set
if not os.environ.get("LLM_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
    print("ERROR: Set LLM_API_KEY (or OPENAI_API_KEY) in your .env file")
    sys.exit(1)


def create_test_data(test_dir: str):
    """Create minimal test profiles and config."""
    os.makedirs(test_dir, exist_ok=True)

    # Polymarket profiles (3 traders with different risk profiles)
    profiles = [
        {
            "user_id": 0,
            "name": "alice_quant",
            "description": "Quantitative analyst",
            "risk_tolerance": "high",
            "user_profile": "Former hedge fund quant. Believes in EMH but exploits mispricings."
        },
        {
            "user_id": 1,
            "name": "bob_fundamental",
            "description": "Political analyst",
            "risk_tolerance": "moderate",
            "user_profile": "Journalist with deep knowledge of US politics and geopolitics."
        },
        {
            "user_id": 2,
            "name": "carol_contrarian",
            "description": "Contrarian trader",
            "risk_tolerance": "high",
            "user_profile": "Veteran trader who profits from market overreactions."
        },
    ]

    with open(os.path.join(test_dir, "polymarket_profiles.json"), 'w') as f:
        json.dump(profiles, f, indent=2)

    # Minimal simulation config
    config = {
        "simulation_id": "test_polymarket",
        "time_config": {
            "total_simulation_hours": 2,
            "minutes_per_round": 30,
            "agents_per_hour_min": 2,
            "agents_per_hour_max": 3,
            "peak_hours": list(range(24)),
            "off_peak_hours": [],
            "peak_activity_multiplier": 1.0,
            "off_peak_activity_multiplier": 1.0,
        },
        "agent_configs": [
            {"agent_id": i, "entity_name": p["name"], "active_hours": list(range(24)), "activity_level": 0.9}
            for i, p in enumerate(profiles)
        ],
        "event_config": {
            "initial_markets": [
                {"question": "Will Bitcoin exceed $150k by end of 2025?", "outcome_a": "YES", "outcome_b": "NO"},
                {"question": "Will the US enter a recession in 2025?", "outcome_a": "YES", "outcome_b": "NO"},
            ]
        },
    }

    config_path = os.path.join(test_dir, "simulation_config.json")
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    return config_path


async def main():
    test_dir = os.path.join(_scripts_dir, "_test_polymarket")

    # Clean previous run
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)

    config_path = create_test_data(test_dir)
    print(f"\nTest data created in: {test_dir}")
    print(f"Config: {config_path}\n")

    # Import after env is loaded
    from action_logger import SimulationLogManager
    from run_parallel_simulation import (
        run_polymarket_simulation,
        load_config,
    )

    # Suppress Wonderwall verbose logging
    import logging
    for name in ["social.agent", "wonderwall.env", "table"]:
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.handlers.clear()
        logger.propagate = False

    config = load_config(config_path)
    log_manager = SimulationLogManager(test_dir)
    pm_logger = log_manager.get_polymarket_logger()

    print("=" * 50)
    print("Running Polymarket simulation (3 rounds)...")
    print("=" * 50)

    result = await run_polymarket_simulation(
        config=config,
        simulation_dir=test_dir,
        action_logger=pm_logger,
        main_logger=log_manager,
        max_rounds=3,
    )

    print("\n" + "=" * 50)
    print(f"Done! Total actions: {result.total_actions}")
    print(f"Actions log: {test_dir}/polymarket/actions.jsonl")
    print(f"Database:    {test_dir}/polymarket_simulation.db")
    print("=" * 50)

    # Print action summary
    actions_file = os.path.join(test_dir, "polymarket", "actions.jsonl")
    if os.path.exists(actions_file):
        print("\nAction log:")
        with open(actions_file, 'r') as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if "event_type" in data:
                        print(f"  [{data['event_type']}] round={data.get('round', '-')}")
                    elif "action_type" in data:
                        agent = data.get('agent_name', '?')
                        action = data.get('action_type', '?')
                        args = data.get('action_args', {})
                        # Summarize key info
                        detail = ''
                        if 'content' in args:
                            detail = f' "{args["content"][:60]}"'
                        elif 'question' in args:
                            detail = f' "{args["question"][:60]}"'
                        elif 'market_id' in args:
                            detail = f' market#{args["market_id"]}'
                            if 'outcome' in args:
                                detail += f' {args["outcome"]}'
                        print(f"  [{agent}] {action}{detail}")
                except json.JSONDecodeError:
                    pass

    # Close env
    if result.env:
        await result.env.close()


if __name__ == "__main__":
    asyncio.run(main())
