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
from strategies.dynamic_orderbook_strategy import DynamicOrderbookStrategy
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
        strategy_name = self.strategy_config.get("strategy_name", self.strategy_id.split('_')[0])
        params = self.strategy_config.get("params", {})

        if strategy_name == "LiquidityHunting":
            return LiquidityHuntingStrategy(self.strategy_id, self.symbol, params)
        elif strategy_name == "DynamicOrderbook":
            return DynamicOrderbookStrategy(self.strategy_id, self.symbol, params)
        else:
            raise ValueError(f"Невідома стратегія: {strategy_name}")

    async def _check_and_open_position(self):
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
        logger.info(f"[{self.strategy_id}] Запуск моніторингу.")
        while True:
            await self.orderbook_manager.update_queue.get()  # Чекаємо на оновлення стакану

            position = self.position_manager.get_position_by_symbol(self.symbol)

            if position:
                # Якщо позиція відкрита, моніторимо для коригувань
                await self._handle_position_adjustment(position)
            else:
                # Якщо позиції немає, перевіряємо сигнали на вхід
                await self._check_and_open_position()

    async def _handle_position_adjustment(self, position: dict):
        # Переконуємось, що стратегія підтримує динамічне коригування
        if not hasattr(self.strategy, 'analyze_and_adjust'):
            return

        adjustment_command = self.strategy.analyze_and_adjust(position, self.orderbook_manager)

        if not adjustment_command:
            return

        command = adjustment_command.get("command")
        if command == "CLOSE_POSITION":
            logger.info(f"[{self.strategy_id}] Отримано команду на завчасне закриття позиції.")
            side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
            try:
                # Спочатку скасовуємо існуючі SL/TP ордери
                if position.get('sl_order_id'):
                    await self.binance_client.cancel_order(self.symbol, position['sl_order_id'])
                if position.get('tp_order_id'):
                    await self.binance_client.cancel_order(self.symbol, position['tp_order_id'])
                
                # Закриваємо позицію ринковим ордером
                await self.binance_client.futures_create_order(
                    symbol=self.symbol, side=side, type=ORDER_TYPE_MARKET, quantity=position['quantity']
                )
                # Видаляємо позицію з менеджера
                self.position_manager.close_position(self.symbol)
                logger.success(f"[{self.strategy_id}] Позицію успішно закрито за ринковою ціною за командою стратегії.")
            except Exception as e:
                logger.error(f"[{self.strategy_id}] Помилка під час завчасного закриття позиції: {e}")

        elif command == "ADJUST_TP_SL":
            new_sl = adjustment_command.get('stop_loss')
            new_tp = adjustment_command.get('take_profit')
            if not new_sl or not new_tp:
                return

            logger.info(f"[{self.strategy_id}] Отримано команду на коригування SL/TP. New SL: {new_sl}, New TP: {new_tp}")
            side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
            try:
                # Скасовуємо старі ордери
                if position.get('sl_order_id'):
                    await self.binance_client.cancel_order(self.symbol, position['sl_order_id'])
                if position.get('tp_order_id'):
                    await self.binance_client.cancel_order(self.symbol, position['tp_order_id'])

                # Створюємо нові ордери
                new_sl_order = await self.binance_client.create_stop_market_order(self.symbol, side, position['quantity'], new_sl)
                new_tp_order = await self.binance_client.create_take_profit_market_order(self.symbol, side, position['quantity'], new_tp)

                # Оновлюємо дані в PositionManager
                self.position_manager.update_orders(self.symbol, sl_order_id=new_sl_order['orderId'], tp_order_id=new_tp_order['orderId'])
                # Також оновлюємо ціни SL/TP в самій позиції
                position['stop_loss'] = new_sl
                position['take_profit'] = new_tp
                logger.success(f"[{self.strategy_id}] SL/TP ордери успішно оновлено. New SL ID: {new_sl_order['orderId']}, New TP ID: {new_tp_order['orderId']}")
            except Exception as e:
                logger.error(f"[{self.strategy_id}] Помилка під час коригування SL/TP: {e}")


    async def _open_position(self, signal: dict):
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            self.pending_symbols.add(self.symbol)

            symbol_info = await self.binance_client.get_symbol_info(self.symbol)
            
            # Визначаємо тип ордера з параметрів стратегії
            order_type = self.strategy.params.get("entry_order_type", ORDER_TYPE_LIMIT)

            # Розрахунок ціни для визначення кількості
            if order_type == ORDER_TYPE_MARKET:
                # Для ринкового ордера використовуємо поточну найкращу ціну для розрахунку
                price_for_calc = self.orderbook_manager.get_best_ask() if side == SIDE_BUY else self.orderbook_manager.get_best_bid()
                if not price_for_calc:
                    logger.warning(f"[{self.strategy_id}] Неможливо отримати ринкову ціну для розрахунку кількості.")
                    self.pending_symbols.remove(self.symbol)
                    return
            else: # Для LIMIT ордера
                tick_size = float(symbol_info['filters'][0]['tickSize'])
                entry_offset_ticks = self.strategy.params.get('entry_offset_ticks', 50)
                wall_price = signal['wall_price']
                price_for_calc = wall_price + (entry_offset_ticks * tick_size) if signal['signal_type'] == 'Long' else wall_price - (entry_offset_ticks * tick_size)
            
            entry_price = round(price_for_calc, self.price_precision)

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
                order_params["price"] = str(entry_price)
                order_params["timeInForce"] = TIME_IN_FORCE_GTC

            logger.info(f"[{self.strategy_id}] Виставлення {order_type} ордеру на вхід: {order_params}")
            await self.binance_client.futures_create_order(**order_params)
            
            logger.success(f"[{self.strategy_id}] {order_type} ордер {client_order_id} виставлено. Очікуємо виконання.")

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Помилка відкриття позиції: {e}")
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)
