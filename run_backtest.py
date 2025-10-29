import asyncio
from backtesting.backtester import Backtester
from strategies.ema_trend_following_strategy import EmaTrendFollowingStrategy
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
        'fast_ema_period': 20,
        'slow_ema_period': 50,
        'rsi_period': 14,
        'volume_ma_period': 20,
        'atr_period': 14,
        'sl_atr_multiplier': 1.5,
        'rr_ratio': 2.0,
        'kline_interval': '15m',
        'tp_method': 'rr_ratio',
        'kline_limit': 55
    }
    
    symbol = "BTCUSDT"
    start_date = "2023-01-01"
    end_date = "2023-03-31"
    initial_balance = 10000.0

    # --- 2. Ініціалізація ---
    strategy = EmaTrendFollowingStrategy(
        strategy_id=f"EmaTrendFollowing_{symbol}",
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
