import os
from dotenv import load_dotenv
import pandas as pd
from typing import AsyncGenerator
from binance import AsyncClient
from binance.enums import *
from loguru import logger
import math


class BinanceClient:
    """Асинхронний клієнт для взаємодії з API Binance Futures."""

    def __init__(self):
        """Ініціалізує клієнт, завантажуючи ключі API з .env файлу."""
        load_dotenv()  # Завантажуємо змінні середовища
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.client: AsyncClient | None = None
        self._exchange_info = None  # Кеш для інформації про біржу

    async def __aenter__(self):
        """Асинхронний контекстний менеджер для ініціалізації клієнта."""
        self.client = await AsyncClient.create(self.api_key, self.api_secret)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Асинхронний контекстний менеджер для закриття сесії клієнта."""
        if self.client:
            await self.client.close_connection()

    def get_async_client(self) -> AsyncClient:
        """Повертає екземпляр асинхронного клієнта."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        return self.client

    async def get_exchange_info(self):
        """Отримує та кешує загальну інформацію про біржу (ліміти, правила)."""
        if self._exchange_info is None:
            if not self.client:
                raise RuntimeError("BinanceClient не ініціалізовано.")
            self._exchange_info = await self.client.futures_exchange_info()
        return self._exchange_info

    async def get_symbol_info(self, symbol: str) -> dict:
        """Отримує торгові правила для конкретного символу (напр., точність ціни, кількість)."""
        exchange_info = await self.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                return s
        raise ValueError(f"Символ {symbol} не знайдено в інформації про біржу.")

    async def get_futures_ticker(self) -> list[dict]:
        """Отримує 24-годинну статистику цін для всіх символів."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            tickers = await self.client.futures_ticker()
            return tickers
        except Exception as e:
            logger.error(f"Помилка отримання тикерів: {e}")
            raise

    async def get_futures_order_book(self, symbol: str, limit: int = 100):
        """Отримує знімок біржового стакану для вказаного символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            order_book = await self.client.futures_order_book(symbol=symbol, limit=limit)
            return order_book
        except Exception as e:
            logger.error(f"Помилка отримання стакану для {symbol}: {e}")
            raise

    async def set_leverage(self, symbol: str, leverage: int):
        """Встановлює кредитне плече для символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"Кредитне плече для {symbol} встановлено на {leverage}x")
        except Exception as e:
            logger.error(f"Помилка встановлення плеча для {symbol}: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str):
        """Встановлює тип маржі (ISOLATED або CROSSED)."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            await self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
            logger.info(f"Тип маржі для {symbol} встановлено на {margin_type}")
        except Exception as e:
            if "No need to change margin type" not in str(e):
                logger.error(f"Помилка зміни типу маржі для {symbol}: {e}")
                raise
            else:
                logger.warning(f"Тип маржі для {symbol} вже є {margin_type}.")

    async def futures_create_order(self, **kwargs):
        """Універсальний метод для створення ф'ючерсних ордерів."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            order = await self.client.futures_create_order(**kwargs)
            return order
        except Exception as e:
            logger.error(f"Помилка створення ордеру з параметрами {kwargs}: {e}")
            raise

    async def create_stop_market_order(self, symbol: str, side: str, quantity: float, stop_price: float):
        """Створює STOP_MARKET ордер."""
        return await self.futures_create_order(
            symbol=symbol, side=side, type=FUTURE_ORDER_TYPE_STOP_MARKET,
            quantity=quantity, stopPrice=str(stop_price), reduceOnly=True
        )

    async def create_take_profit_market_order(self, symbol: str, side: str, quantity: float, stop_price: float):
        """Створює TAKE_PROFIT_MARKET ордер."""
        return await self.futures_create_order(
            symbol=symbol, side=side, type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            quantity=quantity, stopPrice=str(stop_price), reduceOnly=True
        )

    async def cancel_order(self, symbol: str, order_id: int):
        """Скасовує активний ордер за його ID."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            result = await self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return result
        except Exception as e:
            logger.error(f"Помилка скасування ордеру {order_id} для {symbol}: {e}")
            raise

    async def get_account_balance(self, asset: str = "USDT") -> float:
        """Отримує баланс ф'ючерсного гаманця для вказаного активу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            balances = await self.client.futures_account_balance()
            for balance in balances:
                if balance['asset'] == asset:
                    return float(balance['balance'])
            return 0.0
        except Exception as e:
            logger.error(f"Помилка отримання балансу: {e}")
            raise

    async def get_open_positions(self) -> list:
        """Отримує список всіх відкритих ф'ючерсних позицій."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            account_info = await self.client.futures_account()
            open_positions = [p for p in account_info['positions'] if float(p['positionAmt']) != 0]
            return open_positions
        except Exception as e:
            logger.error(f"Помилка отримання відкритих позицій: {e}")
            raise

    async def get_all_account_symbols(self) -> list[str]:
        """Отримує список всіх символів, з якими були операції на ф'ючерсному акаунті."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            account_info = await self.client.futures_account()
            symbols = [p['symbol'] for p in account_info['positions']]
            return symbols
        except Exception as e:
            logger.error(f"Помилка отримання списку символів з акаунту: {e}")
            raise

    async def get_position_for_symbol(self, symbol: str) -> dict | None:
        """Отримує інформацію про позицію для конкретного символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            account_info = await self.client.futures_account()
            for p in account_info['positions']:
                if p['symbol'] == symbol and float(p['positionAmt']) != 0:
                    return p
            return None
        except Exception as e:
            logger.error(f"Помилка отримання позиції для {symbol}: {e}")
            raise

    async def get_account_trades(self, symbol: str, start_time: int = None, end_time: int = None, limit: int = 1000) -> list[dict]:
        """Отримує історію угод для конкретного символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            # Увага: `futures_account_trades` - правильний метод
            trades = await self.client.futures_account_trades(symbol=symbol, startTime=start_time, endTime=end_time, limit=limit)
            return trades
        except Exception as e:
            logger.error(f"Помилка отримання історії угод для {symbol}: {e}")
            raise

    async def get_mark_price(self, symbol: str) -> float:
        """Отримує поточну маркувальну ціну для символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            mark_price_info = await self.client.futures_mark_price(symbol=symbol)
            return float(mark_price_info['markPrice'])
        except Exception as e:
            logger.error(f"Помилка отримання маркувальної ціни для {symbol}: {e}")
            raise

    async def get_historical_klines(self, symbol: str, interval: str, start_str: str = None, end_str: str = None, limit: int = 1000) -> pd.DataFrame:
        """Завантажує історичні свічки (K-лінії) для символу."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        # Note: The client's method is futures_historical_klines, not futures_klines for fetching by date range
        klines = await self.client.futures_historical_klines(symbol=symbol, interval=interval, start_str=start_str, end_str=end_str, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        return df[['open_time', 'open', 'high', 'low', 'close', 'volume']]

    async def keepalive_listen_key(self, listen_key: str):
        """Продовжує термін дії listen key, щоб уникнути закриття потоку даних."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано.")
        try:
            await self.client.futures_stream_keepalive(listen_key)
            logger.info(f"[UserData] Успішно продовжено термін дії listen key.")
        except Exception as e:
            logger.error(f"[UserData] Помилка продовження терміну дії listen key: {e}")
