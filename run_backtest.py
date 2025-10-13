import asyncio
from backtesting.backtester import Backtester
from strategies.ma_crossover_strategy import MACrossoverStrategy
from loguru import logger
import sys

# --- Конфігурація логера для бектесту ---
logger.remove()
logger.add(sys.stderr, level="INFO") # Показувати в консолі тільки INFO і вище

async def run_backtest():
    """
    Налаштовує та запускає процес бектестінгу для заданої стратегії.
    """
    # --- 1. Налаштування параметрів бектесту ---
    strategy_params = {
        'fast_ma': 12,
        'slow_ma': 26,
        'stop_loss_pct': 0.02,
        'take_profit_pct': 0.04
    }
    
    symbol = "BTCUSDT"
    start_date = "2023-01-01"
    end_date = "2023-03-31"
    initial_balance = 10000.0

    # --- 2. Ініціалізація ---
    strategy = MACrossoverStrategy(
        strategy_id=f"MACrossover_{symbol}",
        symbol=symbol,
        params=strategy_params
    )

    backtester = Backtester(
        strategy=strategy,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        initial_balance=initial_balance
    )

    # --- 3. Запуск ---
    await backtester.run()


if __name__ == "__main__":
    asyncio.run(run_backtest())
