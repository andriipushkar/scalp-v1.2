import asyncio
import pandas as pd
from loguru import logger
from core.binance_client import BinanceClient

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.orderflow_manager import OrderflowManager

class DataManager:
    """Manages historical and real-time market data."""

    def __init__(self, binance_client: BinanceClient, orderflow_manager: "OrderflowManager", symbol: str, interval: str, lookback_limit: int = 500):
        self.binance_client = binance_client
        self.orderflow_manager = orderflow_manager
        self.symbol = symbol
        self.interval = interval
        self.lookback_limit = lookback_limit
        self.klines_df = pd.DataFrame()
        self.lock = asyncio.Lock() # To protect klines_df from concurrent access

    async def load_historical_data(self):
        """Loads historical klines and initializes the DataFrame."""
        logger.info(f"Loading historical data for {self.symbol} {self.interval}...")
        klines = await self.binance_client.get_historical_klines(self.symbol, self.interval, self.lookback_limit)
        async with self.lock:
            self.klines_df = klines
            # Initialize 'cvd' column to 0.0 for historical data
            self.klines_df['cvd'] = 0.0
        logger.info(f"Historical data loaded. {len(self.klines_df)} candles for {self.symbol}.")

    async def process_kline_message(self, kline_data: dict):
        """Processes a single kline message from the websocket."""
        kline = kline_data['k']
        kline_df_format = {
            'open_time': pd.to_datetime(kline['t'], unit='ms'),
            'open': float(kline['o']),
            'high': float(kline['h']),
            'low': float(kline['l']),
            'close': float(kline['c']),
            'volume': float(kline['v'])
        }

        async with self.lock:
            current_cvd = self.orderflow_manager.cumulative_volume_delta
            new_kline_df = pd.DataFrame([kline_df_format])
            new_kline_df['cvd'] = current_cvd

            # If the new kline closes a candle, append it
            # Otherwise, update the last row
            if kline['x']:
                self.klines_df = pd.concat([self.klines_df, new_kline_df], ignore_index=True)
                if len(self.klines_df) > self.lookback_limit:
                    self.klines_df = self.klines_df.iloc[1:]
            else:
                if not self.klines_df.empty:
                    # Ensure columns match before assigning
                    for col in new_kline_df.columns:
                        if col in self.klines_df.columns:
                            self.klines_df.iloc[-1, self.klines_df.columns.get_loc(col)] = new_kline_df.iloc[0][col]

            logger.debug(f"Processed kline for {self.symbol}: {kline_df_format['close']}")

    async def get_current_klines(self) -> pd.DataFrame:
        """Returns a copy of the current klines DataFrame."""
        async with self.lock:
            return self.klines_df.copy()
