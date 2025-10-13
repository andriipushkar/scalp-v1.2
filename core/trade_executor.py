from __future__ import annotations
import asyncio
import math
from datetime import datetime
from loguru import logger
from binance.enums import *
from typing import TYPE_CHECKING

from core.binance_client import BinanceClient
from core.orderbook_manager import OrderBookManager
from strategies.liquidity_hunting_strategy import LiquidityHuntingStrategy
from core.position_manager import PositionManager

if TYPE_CHECKING:
    from core.bot_orchestrator import BotOrchestrator

class TradeExecutor:
    """Виконує торгові операції для однієї стратегії/символу."""

    def __init__(self, strategy_config: dict, binance_client: BinanceClient, position_manager: PositionManager,
                 orchestrator: 'BotOrchestrator', orderbook_manager: OrderBookManager, max_active_trades: int, 
                 leverage: int, price_precision: int, qty_precision: int, tick_size: float, pending_symbols: set):
        self.strategy_config = strategy_config
        self.strategy_id = strategy_config["strategy_id"]
        self.symbol = strategy_config["symbol"]
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.orchestrator = orchestrator
        self.orderbook_manager = orderbook_manager
        self.max_active_trades = max_active_trades
        self.leverage = leverage
        self.price_precision = price_precision
        self.qty_precision = qty_precision
        self.tick_size = tick_size
        self.pending_symbols = pending_symbols
        self.strategy = self._initialize_strategy()

    def _initialize_strategy(self):
        strategy_name = self.strategy_id.split('_')[0]
        if strategy_name == "LiquidityHunting":
            return LiquidityHuntingStrategy(self.strategy_id, self.symbol, self.strategy_config["parameters"])
        else:
            raise ValueError(f"Невідома стратегія: {strategy_name}")

    async def execute(self):
        # --- Debugging Logs ---
        logger.debug(f"[{self.symbol}] Checking execution guards: pending={self.symbol in self.pending_symbols}, position_exists={self.position_manager.get_position_by_symbol(self.symbol) is not None}, positions_count={self.position_manager.get_positions_count()}")
        
        if self.symbol in self.pending_symbols or not self.orderbook_manager.is_initialized:
            return
        if self.position_manager.get_position_by_symbol(self.symbol):
            return
        if self.position_manager.get_positions_count() >= self.max_active_trades:
            return

        signal = self.strategy.check_signal(self.orderbook_manager)
        logger.debug(f"[{self.symbol}] Signal checked. Result: {signal}")

        if signal:
            await self._open_position(signal)

    async def start_monitoring(self):
        logger.info(f"[{self.strategy_id}] Запуск моніторингу сигналів.")
        while True:
            await self.orderbook_manager.update_queue.get()  # Чекаємо на оновлення стакану
            await self.execute()  # Перевіряємо сигнал

    async def _open_position(self, signal: dict):
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            self.pending_symbols.add(self.symbol)

            symbol_info = await self.binance_client.get_symbol_info(self.symbol)
            tick_size = float(symbol_info['filters'][0]['tickSize'])

            # Розраховуємо теоретичну ціну входу на основі стіни
            entry_offset_ticks = self.strategy.params.get('entry_offset_ticks', 50)
            wall_price = signal['wall_price']
            if signal['signal_type'] == 'Long':
                entry_price = wall_price + (entry_offset_ticks * tick_size)
            else: # Short
                entry_price = wall_price - (entry_offset_ticks * tick_size)
            
            entry_price = round(entry_price, self.price_precision)

            balance = await self.binance_client.get_account_balance()
            margin_pct = self.orchestrator.trading_config.get('margin_per_trade_pct', 0.1)
            margin_to_use = balance * margin_pct
            notional_size = margin_to_use * self.leverage
            quantity = notional_size / entry_price

            step_size = float(symbol_info['filters'][1]['stepSize'])
            quantity = math.floor(quantity / step_size) * step_size

            if quantity == 0: 
                self.pending_symbols.remove(self.symbol)
                return

            client_order_id = f"qt{int(datetime.now().timestamp() * 1000)}"

            # Зберігаємо лише тип сигналу та ID стратегії, оскільки SL/TP будуть розраховані після виконання
            self.orchestrator.pending_sl_tp[client_order_id] = {
                'signal_type': signal['signal_type'],
                'strategy_id': self.strategy_id,
                'quantity': quantity
            }

            logger.info(f"[{self.strategy_id}] Виставлення LIMIT ордеру на вхід: {quantity} {self.symbol} за ціною {entry_price}")
            await self.binance_client.futures_create_order(
                symbol=self.symbol, 
                side=side, 
                type=ORDER_TYPE_LIMIT, 
                quantity=quantity, 
                price=str(entry_price), 
                timeInForce=TIME_IN_FORCE_GTC,
                newClientOrderId=client_order_id
            )
            
            logger.success(f"[{self.strategy_id}] Лімітний ордер {client_order_id} виставлено. Очікуємо виконання.")

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Помилка відкриття позиції: {e}")
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)
