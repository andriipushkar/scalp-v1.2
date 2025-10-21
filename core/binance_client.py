import os
from dotenv import load_dotenv
import pandas as pd
from typing import AsyncGenerator
from binance import AsyncClient
from binance.enums import *
from loguru import logger
import math


class BinanceClient:
    """
    Асинхронний клієнт-обгортка для взаємодії з API Binance Futures.
    Надає зручні методи для виконання торгових операцій, отримання ринкових даних
    та управління акаунтом, а також обробку помилок та логування.
    """

    def __init__(self):
        """Ініціалізує клієнт, завантажуючи ключі API з .env файлу."""
        load_dotenv()  # Завантажуємо змінні середовища (BINANCE_API_KEY, BINANCE_API_SECRET)
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        if not self.api_key or not self.api_secret:
            raise ValueError("API ключі не знайдено в .env файлі.")
        self.client: AsyncClient | None = None
        self._exchange_info = None  # Кеш для інформації про біржу, щоб не робити зайвих запитів

    async def __aenter__(self):
        """Асинхронний контекстний менеджер для ініціалізації та відкриття сесії клієнта."""
        self.client = await AsyncClient.create(self.api_key, self.api_secret)
        logger.info("Binance асинхронний клієнт успішно створено.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Асинхронний контекстний менеджер для коректного закриття сесії клієнта."""
        if self.client:
            await self.client.close_connection()
            logger.info("З'єднання з Binance API закрито.")

    def get_async_client(self) -> AsyncClient:
        """Повертає екземпляр асинхронного клієнта `AsyncClient`."""
        if not self.client:
            raise RuntimeError("BinanceClient не ініціалізовано. Використовуйте 'async with BinanceClient() as client:'.")
        return self.client

    async def get_exchange_info(self):
        """Отримує та кешує загальну інформацію про біржу (ліміти, правила, символи)."""
        if self._exchange_info is None:
            logger.debug("Отримання інформації про біржу (exchange info)...")
            self._exchange_info = await self.client.futures_exchange_info()
        return self._exchange_info

    async def get_symbol_info(self, symbol: str) -> dict:
        """Отримує торгові правила для конкретного символу (точність ціни, крок кількості тощо)."""
        exchange_info = await self.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                return s
        raise ValueError(f"Символ {symbol} не знайдено в інформації про біржу.")

    async def get_leverage_brackets(self, symbol: str) -> list[dict] | None:
        """
        Отримує інформацію про доступні кредитні плечі для символу.
        """
        try:
            brackets = await self.client.futures_leverage_bracket(symbol=symbol)
            return brackets
        except Exception as e:
            if "Symbol is closed" in str(e):
                logger.warning(f"Символ {symbol} наразі закритий для торгівлі.")
            else:
                logger.error(f"Помилка отримання даних про кредитне плече для {symbol}: {e}")
            return None

    async def get_futures_ticker(self) -> list[dict]:
        """Отримує 24-годинну статистику цін (тікери) для всіх ф'ючерсних символів."""
        try:
            return await self.client.futures_ticker()
        except Exception as e:
            logger.error(f"Помилка отримання тикерів: {e}")
            raise

    async def get_futures_order_book(self, symbol: str, limit: int = 100):
        """Отримує знімок біржового стакану (order book) для вказаного символу."""
        try:
            return await self.client.futures_order_book(symbol=symbol, limit=limit)
        except Exception as e:
            logger.error(f"Помилка отримання стакану для {symbol}: {e}")
            raise

    async def set_leverage(self, symbol: str, leverage: int):
        """Встановлює кредитне плече для символу."""
        try:
            await self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"Кредитне плече для {symbol} встановлено на {leverage}x")
        except Exception as e:
            logger.error(f"Помилка встановлення плеча для {symbol}: {e}")
            raise

    async def set_margin_type(self, symbol: str, margin_type: str):
        """Встановлює тип маржі (ISOLATED або CROSSED), тільки якщо це необхідно."""
        try:
            # Отримуємо інформацію про позицію, щоб перевірити поточний тип маржі
            position_info = await self.client.futures_position_information(symbol=symbol)
            
            # position_information повертає список, нам потрібен перший елемент
            if position_info:
                current_margin_type = position_info[0].get('marginType')
                if current_margin_type and current_margin_type.lower() == margin_type.lower():
                    logger.debug(f"Тип маржі для {symbol} вже є {margin_type}, зміна не потрібна.")
                    return

            # Якщо тип маржі інший, змінюємо його
            await self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type.upper())
            logger.info(f"Тип маржі для {symbol} успішно змінено на {margin_type}.")

        except Exception as e:
            if "No need to change margin type" in str(e):
                # Це не повинно відбуватися з новою логікою, але залишаємо як запобіжник
                logger.debug(f"Тип маржі для {symbol} вже є {margin_type.upper()}.")
            else:
                logger.error(f"Помилка зміни типу маржі для {symbol}: {e}")
                raise

    async def futures_create_order(self, **kwargs):
        """Універсальний метод для створення ф'ючерсних ордерів."""
        try:
            logger.info(f"Створення ордеру: {kwargs}")
            order = await self.client.futures_create_order(**kwargs)
            return order
        except Exception as e:
            logger.error(f"Помилка створення ордеру з параметрами {kwargs}: {e}")
            raise

    async def create_stop_market_order(self, symbol: str, side: str, quantity: float, stop_price: float, price_precision: int, qty_precision: int):
        """Створює STOP_MARKET ордер (використовується для Stop-Loss)."""
        quantity = math.floor(quantity * (10**qty_precision)) / (10**qty_precision)
        stop_price = round(stop_price, price_precision)
        return await self.futures_create_order(
            symbol=symbol, 
            side=side, 
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            quantity=quantity, 
            stopPrice=str(stop_price), 
            reduceOnly=True # Ордер тільки зменшує позицію, не відкриваючи нову
        )

    async def create_take_profit_market_order(self, symbol: str, side: str, quantity: float, stop_price: float, price_precision: int, qty_precision: int):
        """Створює TAKE_PROFIT_MARKET ордер (використовується для Take-Profit)."""
        quantity = math.floor(quantity * (10**qty_precision)) / (10**qty_precision)
        stop_price = round(stop_price, price_precision)
        return await self.futures_create_order(
            symbol=symbol, 
            side=side, 
            type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            quantity=quantity, 
            stopPrice=str(stop_price), 
            reduceOnly=True # Ордер тільки зменшує позицію
        )

    async def cancel_order(self, symbol: str, order_id: int):
        """Скасовує активний ордер за його ID."""
        try:
            logger.warning(f"Скасування ордеру {order_id} для {symbol}...")
            result = await self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            return result
        except Exception as e:
            logger.error(f"Помилка скасування ордеру {order_id} для {symbol}: {e}")
            raise

    async def cancel_all_open_orders(self, symbol: str):
        """Скасовує всі відкриті ордери для вказаного символу."""
        try:
            logger.warning(f"Скасування всіх відкритих ордерів для {symbol}...")
            result = await self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"Успішно скасовано всі відкриті ордери для {symbol}.")
            return result
        except Exception as e:
            logger.error(f"Помилка скасування всіх ордерів для {symbol}: {e}")
            raise

    async def get_account_balance(self, asset: str = "USDT") -> float:
        """Отримує баланс ф'ючерсного гаманця для вказаного активу (за замовчуванням USDT)."""
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
        """Отримує список всіх відкритих ф'ючерсних позицій (з ненульовим розміром)."""
        try:
            account_info = await self.client.futures_account()
            open_positions = [p for p in account_info['positions'] if float(p['positionAmt']) != 0]
            return open_positions
        except Exception as e:
            logger.error(f"Помилка отримання відкритих позицій: {e}")
            raise