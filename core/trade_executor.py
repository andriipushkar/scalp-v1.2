from __future__ import annotations
import asyncio
import math
from datetime import datetime
from loguru import logger
from binance.enums import *
from typing import TYPE_CHECKING

from core.binance_client import BinanceClient
from core.orderbook_manager import OrderBookManager
from strategies.base_strategy import BaseStrategy
from core.position_manager import PositionManager

# Використовуємо для уникнення циклічних імпортів, надаючи type hints
if TYPE_CHECKING:
    from core.bot_orchestrator import BotOrchestrator

class TradeExecutor:
    """
    Виконує торгові операції для однієї конкретної стратегії/символу.
    """

    def __init__(self, strategy: BaseStrategy, binance_client: BinanceClient, position_manager: PositionManager,
                 orchestrator: 'BotOrchestrator', orderbook_manager: OrderBookManager, max_active_trades: int, 
                 leverage: int, price_precision: int, qty_precision: int, tick_size: float, pending_symbols: set):
        """Ініціалізує виконавця угод."""
        self.strategy = strategy
        self.strategy_id = strategy.strategy_id
        self.symbol = strategy.symbol
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
        logger.info(f"[{self.strategy_id}] Ініціалізовано TradeExecutor.")

    async def start_monitoring(self):
        """
        Основний асинхронний цикл моніторингу для цього екземпляру стратегії.
        """
        logger.info(f"[{self.strategy_id}] Запуск моніторингу для символу {self.symbol}.")
        while True:
            try:
                await self.orderbook_manager.update_queue.get()
                position = self.position_manager.get_position_by_symbol(self.symbol)
                if position:
                    await self._handle_position_adjustment(position)
                else:
                    await self._check_and_open_position()
            except Exception as e:
                logger.error(f"[{self.strategy_id}] Критична помилка в циклі моніторингу: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _check_and_open_position(self):
        """Перевіряє умови для відкриття нової позиції та ініціює її відкриття."""
        if self.symbol in self.pending_symbols:
            return
        if not self.orderbook_manager.is_initialized:
            return
        if self.position_manager.get_positions_count() >= self.max_active_trades:
            return

        signal = self.strategy.check_signal(self.orderbook_manager)
        if not signal:
            return

        logger.info(f"[{self.strategy_id}] Отримано сигнал на вхід: {signal}")
        await self._open_position(signal)

    async def _open_position(self, signal: dict):
        """Формує та відправляє ордер на відкриття позиції."""
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        # Створюємо короткий, але унікальний ID для ордеру, щоб відповідати лімітам біржі
        strategy_name_short = self.strategy.strategy_id.split('_')[0][:8]
        client_order_id = f"qt_{strategy_name_short}_{self.symbol}_{int(datetime.now().timestamp() * 1000)}"
        
        try:
            self.pending_symbols.add(self.symbol)
            order_type = self.strategy.params.get("entry_order_type", ORDER_TYPE_LIMIT)

            if order_type == ORDER_TYPE_MARKET:
                price_for_calc = self.orderbook_manager.get_best_ask() if side == SIDE_BUY else self.orderbook_manager.get_best_bid()
                if not price_for_calc:
                    logger.warning(f"[{self.strategy_id}] Неможливо отримати ринкову ціну для розрахунку кількості.")
                    self.pending_symbols.remove(self.symbol)
                    return
            else:
                entry_offset_ticks = self.strategy.params.get('entry_offset_ticks', 1)
                wall_price = signal['wall_price']
                price_for_calc = wall_price + (entry_offset_ticks * self.tick_size) if signal['signal_type'] == 'Long' else wall_price - (entry_offset_ticks * self.tick_size)
            
            entry_price = round(price_for_calc, self.price_precision)

            balance = await self.binance_client.get_account_balance()
            margin_pct = self.orchestrator.trading_config.get('margin_per_trade_pct', 0.01)
            margin_to_use = balance * margin_pct
            notional_size = margin_to_use * self.leverage
            quantity = notional_size / entry_price
            quantity = math.floor(quantity * (10**self.qty_precision)) / (10**self.qty_precision)

            if quantity <= 0:
                logger.warning(f"[{self.strategy_id}] Розрахована кількість дорівнює нулю. Угоду скасовано.")
                self.pending_symbols.remove(self.symbol)
                return

            self.orchestrator.pending_sl_tp[client_order_id] = {
                'signal_type': signal['signal_type'],
                'strategy_id': self.strategy_id,
                'quantity': quantity
            }

            order_params = {
                "symbol": self.symbol, "side": side, "type": order_type, 
                "quantity": quantity, "newClientOrderId": client_order_id
            }
            if order_type == ORDER_TYPE_LIMIT:
                order_params["price"] = f"{entry_price:.{self.price_precision}f}"
                order_params["timeInForce"] = TIME_IN_FORCE_GTC

            logger.info(f"[{self.strategy_id}] Виставлення {order_type} ордеру на вхід: {order_params}")
            await self.binance_client.futures_create_order(**order_params)
            logger.success(f"[{self.strategy_id}] {order_type} ордер {client_order_id} успішно виставлено.")

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Помилка під час відкриття позиції: {e}", exc_info=True)
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)
            if client_order_id in self.orchestrator.pending_sl_tp:
                del self.orchestrator.pending_sl_tp[client_order_id]

    async def _handle_position_adjustment(self, position: dict):
        """
        Обробляє логіку коригування для вже відкритої позиції.
        """
        if not hasattr(self.strategy, 'analyze_and_adjust'):
            return

        adjustment_command = self.strategy.analyze_and_adjust(position, self.orderbook_manager)
        if not adjustment_command:
            return

        command = adjustment_command.get("command")
        
        if command == "CLOSE_POSITION":
            logger.info(f"[{self.strategy_id}] Отримано команду на завчасне закриття позиції. Причина: {adjustment_command.get('reason')}")
            await self._close_position_safely(position)

        elif command == "ADJUST_TP_SL":
            new_sl = round(adjustment_command.get('stop_loss'), self.price_precision)
            new_tp = round(adjustment_command.get('take_profit'), self.price_precision)
            if not new_sl or not new_tp:
                return

            logger.info(f"[{self.strategy_id}] Отримано команду на коригування SL/TP. New SL: {new_sl}, New TP: {new_tp}")
            await self._adjust_sl_tp(position, new_sl, new_tp)

    async def _adjust_sl_tp(self, position: dict, new_sl: float, new_tp: float):
        """Коригує SL/TP для відкритої позиції."""
        side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
        try:
            old_sl_id = position.get('sl_order_id')
            old_tp_id = position.get('tp_order_id')
            
            cancellation_tasks = []
            if old_sl_id: cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_sl_id))
            if old_tp_id: cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_tp_id))

            if cancellation_tasks:
                results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    order_id = old_sl_id if i == 0 and old_sl_id else old_tp_id
                    if isinstance(result, Exception) and "Unknown order sent" not in str(result):
                        logger.error(f"[{self.strategy_id}] Неочікувана помилка при скасуванні ордеру {order_id}: {result}")
                        return

            creation_tasks = [
                self.binance_client.create_stop_market_order(self.symbol, side, position['quantity'], new_sl, self.price_precision, self.qty_precision),
                self.binance_client.create_take_profit_market_order(self.symbol, side, position['quantity'], new_tp, self.price_precision, self.qty_precision)
            ]
            order_results = await asyncio.gather(*creation_tasks, return_exceptions=True)

            new_sl_order = order_results[0] if not isinstance(order_results[0], Exception) else None
            new_tp_order = order_results[1] if not isinstance(order_results[1], Exception) else None

            if new_sl_order and new_tp_order:
                self.position_manager.update_orders(self.symbol, sl_order_id=new_sl_order['orderId'], tp_order_id=new_tp_order['orderId'])
                logger.success(f"[{self.strategy_id}] SL/TP ордери успішно оновлено. New SL ID: {new_sl_order['orderId']}, New TP ID: {new_tp_order['orderId']}")
            else:
                logger.warning(f"[{self.strategy_id}] Не вдалося створити один або обидва SL/TP ордери. Ініціюю закриття позиції.")
                await self._close_position_safely(position)

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Критична помилка в процесі коригування SL/TP: {e}", exc_info=True)

    async def _close_position_safely(self, position: dict):
        """Безпечно закриває позицію, скасовуючи всі пов'язані ордери."""
        logger.warning(f"[{self.strategy_id}] Запуск безпечного закриття позиції для {self.symbol}.")
        side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
        try:
            await self.binance_client.cancel_all_open_orders(self.symbol)
            await self.binance_client.futures_create_order(
                symbol=self.symbol, side=side, type=ORDER_TYPE_MARKET, quantity=position['quantity'], reduceOnly=True
            )
            logger.success(f"[{self.strategy_id}] Ринковий ордер на закриття позиції успішно виставлено.")
        except Exception as e:
            if "APIError(code=-2022): ReduceOnly Order is rejected" in str(e):
                logger.warning(f"[{self.strategy_id}] Спроба закрити позицію, якої вже не існує.")
            else:
                logger.error(f"[{self.strategy_id}] Помилка під час безпечного закриття позиції: {e}", exc_info=True)