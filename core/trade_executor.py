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
        self.last_kline_processed_timestamp = 0 # Додаємо для відстеження останнього обробленого часу K-ліній
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
                    kline_key = f"{self.symbol}_{self.strategy.kline_interval}"
                    klines_df = self.orchestrator.kline_data_cache.get(kline_key)
                    
                    if klines_df is not None and not klines_df.empty:
                        latest_kline_close_time = klines_df.iloc[-1]['close_time']
                        # Перевіряємо, чи є нові K-лінії для обробки
                        if latest_kline_close_time > self.last_kline_processed_timestamp:
                            logger.debug(f"[{self.strategy_id}] Нові K-лінії доступні. Обробка сигналу.")
                            await self._check_and_open_position()
                            self.last_kline_processed_timestamp = latest_kline_close_time
                        else:
                            logger.debug(f"[{self.strategy_id}] K-лінії не оновлювалися. Пропуск перевірки сигналу.")
                    else:
                        logger.debug(f"[{self.strategy_id}] K-лінії ще не доступні в кеші. Пропуск перевірки сигналу.")
            except Exception as e:
                logger.error(f"[{self.strategy_id}] Критична помилка в циклі моніторингу: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _check_and_open_position(self):
        """Перевіряє умови для відкриття нової позиції та ініціює її відкриття."""
        if self.symbol in self.pending_symbols:
            logger.debug(f"[{self.strategy_id}] Символ {self.symbol} вже знаходиться в стані очікування ордера. Пропуск.")
            return
        if self.position_manager.get_position_by_symbol(self.symbol):
            logger.debug(f"[{self.strategy_id}] Для символу {self.symbol} вже існує відкрита позиція. Пропуск.")
            return
        if not self.orderbook_manager.is_initialized:
            logger.debug(f"[{self.strategy_id}] Orderbook для {self.symbol} ще не ініціалізовано. Пропуск.")
            return
        if self.position_manager.get_positions_count() >= self.max_active_trades:
            logger.warning(f"[{self.strategy_id}] Досягнуто максимальну кількість активних угод ({self.max_active_trades}). Пропуск.")
            return

        kline_key = f"{self.symbol}_{self.strategy.kline_interval}"
        klines_df = self.orchestrator.kline_data_cache.get(kline_key)
        if klines_df is None or klines_df.empty:
            logger.warning(f"[{self.strategy_id}] K-лінії для {self.symbol} ({self.strategy.kline_interval}) ще не доступні в кеші.")
            return

        signal = await self.strategy.check_signal(self.orderbook_manager, self.binance_client, dataframe=klines_df)
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
            order_type = ORDER_TYPE_MARKET

            price_for_calc = self.orderbook_manager.get_best_ask() if side == SIDE_BUY else self.orderbook_manager.get_best_bid()
            if not price_for_calc:
                logger.warning(f"[{self.strategy_id}] Неможливо отримати ринкову ціну для розрахунку кількості.")
                self.pending_symbols.remove(self.symbol)
                return
            
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

            # Отримуємо параметри SL/TP з конфігурації стратегії
            sl_atr_multiplier = self.strategy.config.get('sl_atr_multiplier', 1.0)
            rr_ratio = self.strategy.config.get('rr_ratio', 1.0)
            max_sl_percentage = self.strategy.config.get('max_sl_percentage', 0.01) # За замовчуванням 1%

            stop_loss_price = 0.0
            take_profit_price = 0.0
            initial_stop_loss = 0.0 # Зберігаємо початковий SL для трейлінгу або інших цілей

            if signal.get('atr'):
                # Розрахунок Stop Loss
                if side == SIDE_BUY: # Long position
                    stop_loss_price = entry_price - (signal['atr'] * sl_atr_multiplier)
                    # Обмеження SL за максимальним відсотком
                    max_allowed_sl_deviation = entry_price * max_sl_percentage
                    if (entry_price - stop_loss_price) > max_allowed_sl_deviation:
                        stop_loss_price = entry_price - max_allowed_sl_deviation
                else: # Short position
                    stop_loss_price = entry_price + (signal['atr'] * sl_atr_multiplier)
                    # Обмеження SL за максимальним відсотком
                    max_allowed_sl_deviation = entry_price * max_sl_percentage
                    if (stop_loss_price - entry_price) > max_allowed_sl_deviation:
                        stop_loss_price = entry_price + max_allowed_sl_deviation
                
                initial_stop_loss = round(stop_loss_price, self.price_precision)

                # Розрахунок Take Profit
                risk_per_trade = abs(entry_price - initial_stop_loss)
                reward_per_trade = risk_per_trade * rr_ratio

                if side == SIDE_BUY:
                    take_profit_price = entry_price + reward_per_trade
                else:
                    take_profit_price = entry_price - reward_per_trade
                
                take_profit_price = round(take_profit_price, self.price_precision)

            self.orchestrator.pending_sl_tp[client_order_id] = {
                'signal_type': signal['signal_type'],
                'strategy_id': self.strategy_id,
                'quantity': quantity,
                'atr': signal.get('atr'),  # Зберігаємо ATR
                'dataframe': signal.get('dataframe'), # Зберігаємо dataframe
                'stop_loss_price': initial_stop_loss, # Зберігаємо розрахований SL
                'take_profit_price': take_profit_price # Зберігаємо розрахований TP
            }

            # Зберігаємо позицію в PositionManager з розрахованими SL/TP
            self.position_manager.set_position(
                symbol=self.symbol,
                side=signal['signal_type'], # 'Long' або 'Short'
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=initial_stop_loss,
                take_profit=take_profit_price,
                initial_stop_loss=initial_stop_loss # Початковий SL
            )

            order_params = {
                "symbol": self.symbol, "side": side, "type": order_type, 
                "quantity": quantity, "newClientOrderId": client_order_id
            }

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
        kline_key = f"{self.symbol}_{self.strategy.kline_interval}"
        klines_df = self.orchestrator.kline_data_cache.get(kline_key)

        adjustment_command = await self.strategy.analyze_and_adjust(position, self.orderbook_manager, self.binance_client, klines_df)
        if not adjustment_command:
            return

        command = adjustment_command.get("command")
        
        if command == "CLOSE_POSITION":
            logger.info(f"[{self.strategy_id}] Отримано команду на завчасне закриття позиції. Причина: {adjustment_command.get('reason')}")
            await self._close_position_safely(position)

        elif command == "UPDATE_STOP_LOSS":
            new_sl = round(adjustment_command.get('new_stop_loss'), self.price_precision)
            if not new_sl:
                return
            
            # Отримуємо поточний TP, щоб передати його в _adjust_sl_tp
            current_tp = position.get('take_profit')
            if not current_tp:
                logger.warning(f"[{self.strategy_id}] Не вдалося отримати поточний TP для оновлення SL. Пропускаємо.")
                return

            logger.info(f"[{self.strategy_id}] Отримано команду на оновлення SL. New SL: {new_sl}")
            await self._adjust_sl_tp(position, new_sl, current_tp)

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

            # 1. Створюємо нові ордери SL/TP
            creation_tasks = [
                self.binance_client.create_stop_market_order(self.symbol, side, position['quantity'], new_sl, self.price_precision, self.qty_precision),
                self.binance_client.create_take_profit_market_order(self.symbol, side, position['quantity'], new_tp, self.price_precision, self.qty_precision)
            ]
            order_results = await asyncio.gather(*creation_tasks, return_exceptions=True)

            new_sl_order = order_results[0] if not isinstance(order_results[0], Exception) else None
            new_tp_order = order_results[1] if not isinstance(order_results[1], Exception) else None

            if new_sl_order and new_tp_order:
                # 2. Якщо нові ордери успішно створені, скасовуємо старі
                cancellation_tasks = []
                if old_sl_id: cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_sl_id))
                if old_tp_id: cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_tp_id))

                if cancellation_tasks:
                    results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)
                    for i, result in enumerate(results):
                        order_id = old_sl_id if i == 0 and old_sl_id else old_tp_id
                        if isinstance(result, Exception) and "Unknown order sent" not in str(result):
                            logger.error(f"[{self.strategy_id}] Неочікувана помилка при скасуванні старого ордеру {order_id}: {result}")
                            # Продовжуємо, оскільки нові ордери вже розміщені
                
                self.position_manager.update_orders(self.symbol, sl_order_id=new_sl_order['orderId'], tp_order_id=new_tp_order['orderId'])
                logger.success(f"[{self.strategy_id}] SL/TP ордери успішно оновлено. New SL ID: {new_sl_order['orderId']}, New TP ID: {new_tp_order['orderId']}")
            else:
                logger.warning(f"[{self.strategy_id}] Не вдалося створити один або обидва нові SL/TP ордери. Позиція залишається захищеною старими ордерами (якщо вони були).")
                return

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Критична помилка в процесі коригування SL/TP: {e}", exc_info=True)

    async def _close_position_safely(self, position: dict):
        """Безпечно закриває позицію, скасовуючи всі пов'язані ордери."""
        logger.warning(f"[{self.strategy_id}] Запуск безпечного закриття позиції для {self.symbol}.")
        side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
        try:
            current_position = self.position_manager.get_position_by_symbol(self.symbol)
            if not current_position or current_position['quantity'] == 0:
                logger.warning(f"[{self.strategy_id}] Спроба закрити позицію для {self.symbol}, але активної позиції не знайдено.")
                return

            await self.binance_client.cancel_all_open_orders(self.symbol)
            await self.binance_client.futures_create_order(
                symbol=self.symbol, side=side, type=ORDER_TYPE_MARKET, quantity=current_position['quantity'], reduceOnly=True
            )
            logger.success(f"[{self.strategy_id}] Ринковий ордер на закриття позиції успішно виставлено.")
            self.position_manager.close_position(self.symbol) # Оновлюємо внутрішній стан менеджера позицій
        except Exception as e:
            if "APIError(code=-2022): ReduceOnly Order is rejected" in str(e):
                logger.warning(f"[{self.strategy_id}] Спроба закрити позицію, якої вже не існує.")
            else:
                logger.error(f"[{self.strategy_id}] Помилка під час безпечного закриття позиції: {e}", exc_info=True)