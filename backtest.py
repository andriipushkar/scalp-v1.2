import argparse
import asyncio
import json
from datetime import datetime
from loguru import logger

from core.backtest_engine import BacktestEngine

async def main(args):
    logger.info("--- Starting Batch Backtest ---")
    
    # Load configurations
    with open("configs/strategies.json", 'r') as f:
        strategies_configs = json.load(f)
    with open("configs/trading_config.json", 'r') as f:
        trading_config = json.load(f)

    # Filter for enabled strategies and take the first 20
    enabled_strategies = [s for s in strategies_configs if s.get("enabled", False)]
    strategies_to_test = enabled_strategies[:20]
    
    logger.info(f"Found {len(enabled_strategies)} enabled strategies. Testing the first {len(strategies_to_test)}.")

    for i, strategy_config in enumerate(strategies_to_test):
        print("\n" + "="*80)
        logger.info(f"Running backtest {i+1}/{len(strategies_to_test)} for: {strategy_config['strategy_id']}")
        print("="*80 + "\n")

        # Prepare backtest configuration for the current strategy
        backtest_config = {
            "symbol": strategy_config["symbol"],
            "interval": strategy_config["interval"],
            "start_date": args.start_datetime,
            "end_date": args.end_datetime,
            "risk_per_trade_pct": trading_config["risk_per_trade_pct"]
        }

        # Initialize and run the backtest engine
        engine = BacktestEngine(strategy_config=strategy_config, backtest_config=backtest_config)
        await engine.run()

    logger.info("--- Batch Backtest Finished ---")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a batch backtest for strategies.")
    
    parser.add_argument("--start-datetime", type=str, required=True, help="Start datetime in YYYY-MM-DDTHH:MM:SS format")
    parser.add_argument("--end-datetime", type=str, required=True, help="End datetime in YYYY-MM-DDTHH:MM:SS format")

    args = parser.parse_args()
    
    # Configure logger for backtest
    logger.remove()
    logger.add(lambda msg: print(msg, end=''), level="DEBUG")

    asyncio.run(main(args))
