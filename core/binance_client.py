import os
from dotenv import load_dotenv
import pandas as pd
from typing import AsyncGenerator
from binance import AsyncClient
from binance.enums import *
from loguru import logger
import math


class BinanceClient:
    """A client to interact with the Binance API."""

    def __init__(self):
        load_dotenv()  # Load environment variables from .env file
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.client: AsyncClient | None = None
        self._exchange_info = None  # Cache for exchange information

    async def __aenter__(self):
        """Async context manager to initialize the client."""
        self.client = await AsyncClient.create(self.api_key, self.api_secret)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager to close the client session."""
        if self.client:
            await self.client.close_connection()

    def get_async_client(self) -> AsyncClient:
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        return self.client

    async def get_exchange_info(self):
        """Fetches and caches exchange information."""
        if self._exchange_info is None:
            if not self.client:
                raise RuntimeError("BinanceClient is not initialized.")
            self._exchange_info = await self.client.futures_exchange_info()
        return self._exchange_info

    async def get_symbol_info(self, symbol: str) -> dict:
        """Retrieves exchange information for a specific symbol."""
        exchange_info = await self.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                return s
        raise ValueError(f"Symbol {symbol} not found in exchange info.")

    async def set_leverage(self, symbol: str, leverage: int):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            print(f"Leverage for {symbol} set to {leverage}x")
        except Exception as e:
            print(f"Error setting leverage for {symbol}: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            await self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
            print(f"Margin type for {symbol} set to {margin_type}")
        except Exception as e:
            print(f"Error setting margin type for {symbol}: {e}")
            # Ignore error if margin type is already set
            if "No need to change margin type" not in str(e):
                raise
            else:
                print(f"Margin type for {symbol} is already {margin_type}.")

    async def create_order(self, symbol: str, side: str, order_type: str, quantity: float):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized. Use 'async with BinanceClient() as client:'")

        try:
            # For futures, use futures_create_order
            order = await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=order_type,
                quantity=quantity
            )
            return order
        except Exception as e:
            # logger.error(f"Failed to create order: {e}") # Logger not available here
            print(f"Error creating order: {e}")  # Simple print for now
            raise

    async def create_stop_market_order(self, symbol: str, side: str, quantity: float, stop_price: float):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            order = await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                quantity=quantity,
                stopPrice=stop_price,
                closePosition=False # Should not close position, just reduce it
            )
            return order
        except Exception as e:
            print(f"Error creating stop market order: {e}")
            raise

    async def create_take_profit_market_order(self, symbol: str, side: str, quantity: float, stop_price: float):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            order = await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
                quantity=quantity,
                stopPrice=stop_price,
                closePosition=False
            )
            return order
        except Exception as e:
            print(f"Error creating take profit market order: {e}")
            raise

    async def cancel_order(self, symbol: str, order_id: int):
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            result = await self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )
            return result
        except Exception as e:
            print(f"Error canceling order: {e}")
            raise

    async def get_account_balance(self, asset: str = "USDT") -> float:
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized. Use 'async with BinanceClient() as client:'")

        try:
            # For futures, use futures_account_balance
            balances = await self.client.futures_account_balance()
            for balance in balances:
                if balance['asset'] == asset:
                    return float(balance['balance'])
            return 0.0
        except Exception as e:
            print(f"Error getting account balance: {e}")
            raise

    async def get_open_positions(self) -> list:
        """Retrieves all open futures positions."""
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            positions = await self.client.futures_account()
            # Filter for positions where positionAmt is not 0
            open_positions = [p for p in positions['positions'] if float(p['positionAmt']) != 0]
            return open_positions
        except Exception as e:
            logger.error(f"Error getting open positions: {e}")
            raise

    async def get_position_for_symbol(self, symbol: str) -> dict | None:
        """Retrieves position information for a specific symbol."""
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            positions = await self.client.futures_account()
            for p in positions['positions']:
                if p['symbol'] == symbol and float(p['positionAmt']) != 0:
                    return p
            return None
        except Exception as e:
            logger.error(f"Error getting position for {symbol}: {e}")
            raise

    async def get_mark_price(self, symbol: str) -> float:
        """Retrieves the current mark price for a specific symbol."""
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        try:
            mark_price_info = await self.client.futures_mark_price(symbol=symbol)
            return float(mark_price_info['markPrice'])
        except Exception as e:
            logger.error(f"Error getting mark price for {symbol}: {e}")
            raise

    async def get_historical_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized. Use 'async with BinanceClient() as client:'")

        # Use futures_klines for futures data
        klines = await self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)

        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(
            float)

        return df[['open_time', 'open', 'high', 'low', 'close', 'volume']]

    async def get_historical_klines_for_range(self, symbol: str, interval: str, start_str: str, end_str: str) -> pd.DataFrame:
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")

        all_klines = []
        async for kline in await self.client.get_historical_klines_generator(symbol, interval, start_str, end_str):
            all_klines.append(kline)

        df = pd.DataFrame(all_klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df[['open_time', 'open', 'high', 'low', 'close', 'volume']]

    async def get_historical_agg_trades(self, symbol: str, start_str: str, end_str: str) -> list:
        """Fetches historical aggregate trades for a symbol and date range."""
        if not self.client:
            raise RuntimeError("BinanceClient is not initialized.")
        
        logger.info(f"Fetching aggregate trades for {symbol} from {start_str} to {end_str}...")

        # Convert string dates to milliseconds
        start_ts = int(pd.to_datetime(start_str).timestamp() * 1000)
        end_ts = int(pd.to_datetime(end_str).timestamp() * 1000)

        all_trades = []
        while True:
            try:
                trades = await self.client.futures_aggregate_trades(
                    symbol=symbol, 
                    startTime=start_ts, 
                    endTime=end_ts,
                    limit=1000
                )
                if not trades:
                    break # No more trades
                
                all_trades.extend(trades)
                start_ts = trades[-1]['T'] + 1 # Next fetch starts after the last trade

                # Log progress without spamming
                if len(all_trades) % 50000 == 0:
                    last_trade_time = pd.to_datetime(all_trades[-1]['T'], unit='ms')
                    logger.info(f"Fetched {len(all_trades)} trades so far... up to {last_trade_time}")

            except Exception as e:
                logger.error(f"Error fetching aggregate trades: {e}")
                break

        logger.info(f"Fetched a total of {len(all_trades)} aggregate trades for {symbol}.")
        return all_trades