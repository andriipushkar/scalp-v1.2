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

# Використовуємо для уникнення циклічних імпортів, надаючи type hints
if TYPE_CHECKING:
    from core.bot_orchestrator import BotOrchestrator

class TradeExecutor:
    """
    Виконує торгові операції для однієї конкретної стратегії/символу.
    
    Відповідає за:
    1. Моніторинг торгових сигналів від своєї стратегії.
    2. Розрахунок розміру позиції на основі ризик-менеджменту.
    3. Виставлення ордерів на вхід.
    4. Коригування або закриття позицій за командою стратегії.
    """

    def __init__(self, strategy_config: dict, binance_client: BinanceClient, position_manager: PositionManager,
                 orchestrator: 'BotOrchestrator', orderbook_manager: OrderBookManager, max_active_trades: int, 
                 leverage: int, price_precision: int, qty_precision: int, tick_size: float, pending_symbols: set):
        """Ініціалізує виконавця угод."""
        self.strategy_config = strategy_config
        self.strategy_id = strategy_config["strategy_id"]
        self.symbol = strategy_config["symbol"]
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.orchestrator = orchestrator # Посилання на головний оркестратор
        self.orderbook_manager = orderbook_manager
        self.max_active_trades = max_active_trades
        self.leverage = leverage
        self.price_precision = price_precision # Кількість знаків після коми для ціни
        self.qty_precision = qty_precision   # Кількість знаків після коми для кількості
        self.tick_size = tick_size           # Мінімальний крок ціни
        self.pending_symbols = pending_symbols # Спільний set для всіх executor-ів
        self.strategy = self._initialize_strategy()

    def _initialize_strategy(self):
        """Фабричний метод для ініціалізації відповідної стратегії на основі конфігурації."""
        strategy_name = self.strategy_config.get("strategy_name", self.strategy_id.split('_')[0])
        params = self.strategy_config.get("params", {})

        logger.info(f"[{self.strategy_id}] Ініціалізація стратегії '{strategy_name}'...")
        if strategy_name == "LiquidityHunting":
            return LiquidityHuntingStrategy(self.strategy_id, self.symbol, params)
        elif strategy_name == "DynamicOrderbook":
            return DynamicOrderbookStrategy(self.strategy_id, self.symbol, params)
        else:
            raise ValueError(f"Невідома назва стратегії: {strategy_name}")

    async def start_monitoring(self):
        """
        Основний асинхронний цикл моніторингу для цього екземпляру стратегії.
        Працює безкінечно, очікуючи на оновлення стакану.
        """
        logger.info(f"[{self.strategy_id}] Запуск моніторингу для символу {self.symbol}.")
        while True:
            try:
                # Блокується, доки не надійде нове оновлення стакану від OrderBookManager
                await self.orderbook_manager.update_queue.get()

                position = self.position_manager.get_position_by_symbol(self.symbol)

                if position:
                    # Якщо позиція вже відкрита, передаємо керування логіці коригування
                    await self._handle_position_adjustment(position)
                else:
                    # Якщо позиції немає, перевіряємо наявність сигналу на вхід
                    await self._check_and_open_position()
            except Exception as e:
                logger.error(f"[{self.strategy_id}] Критична помилка в циклі моніторингу: {e}", exc_info=True)
                await asyncio.sleep(5) # Пауза перед наступною ітерацією

    async def _check_and_open_position(self):
        """Перевіряє умови для відкриття нової позиції та ініціює її відкриття."""
        # --- Захисні перевірки ---
        # 1. Чи не очікуємо ми вже виконання ордеру по цьому символу?
        if self.symbol in self.pending_symbols:
            return
        # 2. Чи стакан вже повністю ініціалізований?
        if not self.orderbook_manager.is_initialized:
            return
        # 3. Чи не перевищено ліміт одночасно відкритих позицій?
        if self.position_manager.get_positions_count() >= self.max_active_trades:
            return

        # Отримуємо сигнал від стратегії
        signal = self.strategy.check_signal(self.orderbook_manager)
        if not signal:
            return

        logger.info(f"[{self.strategy_id}] Отримано сигнал на вхід: {signal}")
        await self._open_position(signal)

    async def _open_position(self, signal: dict):
        """Формує та відправляє ордер на відкриття позиції."""
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            # Додаємо символ до set-у "очікуючих", щоб уникнути дублювання угод
            self.pending_symbols.add(self.symbol)

            # Визначаємо тип ордера з параметрів стратегії (LIMIT або MARKET)
            order_type = self.strategy.params.get("entry_order_type", ORDER_TYPE_LIMIT)

            # --- Розрахунок кількості (quantity) ---
            # Для розрахунку нам потрібна приблизна ціна входу
            if order_type == ORDER_TYPE_MARKET:
                # Для ринкового ордера беремо поточну найкращу ціну з протилежної сторони стакану
                price_for_calc = self.orderbook_manager.get_best_ask() if side == SIDE_BUY else self.orderbook_manager.get_best_bid()
                if not price_for_calc:
                    logger.warning(f"[{self.strategy_id}] Неможливо отримати ринкову ціну для розрахунку кількості.")
                    self.pending_symbols.remove(self.symbol)
                    return
            else: # Для LIMIT ордера
                # Розраховуємо ціну входу на основі ціни "стіни" та відступу
                entry_offset_ticks = self.strategy.params.get('entry_offset_ticks', 1)
                wall_price = signal['wall_price']
                price_for_calc = wall_price + (entry_offset_ticks * self.tick_size) if signal['signal_type'] == 'Long' else wall_price - (entry_offset_ticks * self.tick_size)
            
            entry_price = round(price_for_calc, self.price_precision)

            # --- Розрахунок розміру позиції ---
            balance = await self.binance_client.get_account_balance()
            margin_pct = self.orchestrator.trading_config.get('margin_per_trade_pct', 0.01) # 1% від балансу за замовчуванням
            margin_to_use = balance * margin_pct
            notional_size = margin_to_use * self.leverage # Номінальна вартість позиції
            quantity = notional_size / entry_price

            # Округлюємо кількість до дозволеної точності (stepSize)
            quantity = math.floor(quantity * (10**self.qty_precision)) / (10**self.qty_precision)

            if quantity <= 0:
                logger.warning(f"[{self.strategy_id}] Розрахована кількість дорівнює нулю. Угоду скасовано.")
                self.pending_symbols.remove(self.symbol)
                return

            # Створюємо унікальний ID для ордеру
            client_order_id = f"qt_{self.strategy_id}_{int(datetime.now().timestamp() * 1000)}_entry"

            # Реєструємо "відкладене" завдання в оркестраторі
            # Після виконання цього ордеру, оркестратор виставить SL/TP
            self.orchestrator.pending_sl_tp[client_order_id] = {
                'signal_type': signal['signal_type'],
                'strategy_id': self.strategy_id,
                'quantity': quantity
            }

            # Формуємо параметри для ордеру
            order_params = {
                "symbol": self.symbol, "side": side, "type": order_type, 
                "quantity": quantity, "newClientOrderId": client_order_id
            }
            if order_type == ORDER_TYPE_LIMIT:
                order_params["price"] = str(entry_price)
                order_params["timeInForce"] = TIME_IN_FORCE_GTC # Good-Til-Canceled

            logger.info(f"[{self.strategy_id}] Виставлення {order_type} ордеру на вхід: {order_params}")
            await self.binance_client.futures_create_order(**order_params)
            
            logger.success(f"[{self.strategy_id}] {order_type} ордер {client_order_id} успішно виставлено. Очікуємо виконання.")

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Помилка під час відкриття позиції: {e}", exc_info=True)
            # Прибираємо символ з очікуючих у разі помилки
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)
            # Видаляємо відкладене завдання, якщо воно було створене
            if 'client_order_id' in locals() and client_order_id in self.orchestrator.pending_sl_tp:
                del self.orchestrator.pending_sl_tp[client_order_id]

    async def _handle_position_adjustment(self, position: dict):
        """
        Обробляє логіку коригування для вже відкритої позиції (напр., трейлінг-стоп або завчасне закриття).
        """
        # Переконуємось, що стратегія підтримує динамічне коригування
        if not hasattr(self.strategy, 'analyze_and_adjust'):
            return

        # Отримуємо команду від стратегії
        adjustment_command = self.strategy.analyze_and_adjust(position, self.orderbook_manager)
        if not adjustment_command:
            return

        command = adjustment_command.get("command")
        
        # --- Логіка завчасного закриття позиції ---
        if command == "CLOSE_POSITION":
            logger.info(f"[{self.strategy_id}] Отримано команду на завчасне закриття позиції. Причина: {adjustment_command.get('reason')}")
            side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
            try:
                # --- Скасування існуючих SL/TP ордерів ---
                cancellation_tasks = []
                old_sl_id = position.get('sl_order_id')
                old_tp_id = position.get('tp_order_id')
                if old_sl_id:
                    cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_sl_id))
                if old_tp_id:
                    cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_tp_id))

                if cancellation_tasks:
                    results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)
                    for i, result in enumerate(results):
                        order_id = old_sl_id if i == 0 and old_sl_id else old_tp_id
                        if isinstance(result, Exception):
                            if "Unknown order sent" in str(result):
                                logger.warning(f"[{self.strategy_id}] Не вдалося скасувати ордер {order_id} для завчасного закриття (ймовірно, вже виконаний).")
                            else:
                                logger.error(f"[{self.strategy_id}] Неочікувана помилка при скасуванні ордеру {order_id}: {result}")
                
                # --- Закриття позиції ринковим ордером ---
                await self.binance_client.futures_create_order(
                    symbol=self.symbol, side=side, type=ORDER_TYPE_MARKET, quantity=position['quantity'], reduceOnly=True
                )
                logger.success(f"[{self.strategy_id}] Ринковий ордер на закриття позиції виставлено.")
            except Exception as e:
                if "APIError(code=-2022): ReduceOnly Order is rejected" in str(e):
                    logger.warning(f"[{self.strategy_id}] Спроба закрити позицію, якої вже не існує (ймовірно, закрита раніше). Це очікувана поведінка.")
                else:
                    logger.error(f"[{self.strategy_id}] Помилка під час завчасного закриття позиції: {e}")

        # --- Логіка коригування SL/TP (трейлінг) ---
        elif command == "ADJUST_TP_SL":
            new_sl = round(adjustment_command.get('stop_loss'), self.price_precision)
            new_tp = round(adjustment_command.get('take_profit'), self.price_precision)
            if not new_sl or not new_tp:
                return

            logger.info(f"[{self.strategy_id}] Отримано команду на коригування SL/TP. New SL: {new_sl}, New TP: {new_tp}")
            side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
            
            try:
                # --- Крок 1: Скасування старих ордерів ---
                old_sl_id = position.get('sl_order_id')
                old_tp_id = position.get('tp_order_id')
                
                cancellation_tasks = []
                if old_sl_id:
                    cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_sl_id))
                if old_tp_id:
                    cancellation_tasks.append(self.binance_client.cancel_order(self.symbol, old_tp_id))

                if cancellation_tasks:
                    results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)
                    for i, result in enumerate(results):
                        order_id = old_sl_id if i == 0 and old_sl_id else old_tp_id
                        if isinstance(result, Exception):
                            # Ігноруємо помилку, якщо ордер вже не існує, але логуємо інші помилки
                            if "Unknown order sent" in str(result):
                                logger.warning(f"[{self.strategy_id}] Не вдалося скасувати старий ордер {order_id}, можливо, він вже виконаний або скасований.")
                            else:
                                logger.error(f"[{self.strategy_id}] Неочікувана помилка при скасуванні ордеру {order_id}: {result}")
                                return # Перериваємо коригування, якщо скасування не вдалося

                # --- Крок 2: Створення нових ордерів (тільки якщо скасування пройшло успішно) ---
                creation_tasks = [
                    self.binance_client.create_stop_market_order(self.symbol, side, position['quantity'], new_sl, self.price_precision, self.qty_precision),
                    self.binance_client.create_take_profit_market_order(self.symbol, side, position['quantity'], new_tp, self.price_precision, self.qty_precision)
                ]
                
                order_results = await asyncio.gather(*creation_tasks, return_exceptions=True)

                new_sl_order = None
                new_tp_order = None

                if isinstance(order_results[0], Exception):
                    logger.error(f"[{self.strategy_id}] Помилка створення нового SL ордера: {order_results[0]}", exc_info=True)
                else:
                    new_sl_order = order_results[0]

                if isinstance(order_results[1], Exception):
                    logger.error(f"[{self.strategy_id}] Помилка створення нового TP ордера: {order_results[1]}", exc_info=True)
                else:
                    new_tp_order = order_results[1]

                # --- Крок 3: Оновлення даних в PositionManager ---
                if new_sl_order and new_tp_order:
                    self.position_manager.update_orders(self.symbol, sl_order_id=new_sl_order['orderId'], tp_order_id=new_tp_order['orderId'])
                    logger.success(f"[{self.strategy_id}] SL/TP ордери успішно оновлено. New SL ID: {new_sl_order['orderId']}, New TP ID: {new_tp_order['orderId']}")
                else:
                    # Якщо один з ордерів не вдалося створити, потрібно закрити позицію, щоб уникнути ризику
                    logger.warning(f"[{self.strategy_id}] Не вдалося створити один або обидва SL/TP ордери. Ініціюю закриття позиції для безпеки.")
                    await self._close_position_safely(position)

            except Exception as e:
                logger.error(f"[{self.strategy_id}] Критична помилка в процесі коригування SL/TP: {e}", exc_info=True)

    async def _close_position_safely(self, position: dict):
        """Безпечно закриває позицію, скасовуючи всі пов'язані ордери."""
        logger.warning(f"[{self.strategy_id}] Запуск безпечного закриття позиції для {self.symbol}.")
        side = SIDE_SELL if position['side'] == 'Long' else SIDE_BUY
        try:
            # Скасовуємо всі ордери для символу, щоб уникнути будь-яких залишків
            await self.binance_client.cancel_all_open_orders(self.symbol)
            
            # Закриваємо позицію ринковим ордером
            await self.binance_client.futures_create_order(
                symbol=self.symbol, side=side, type=ORDER_TYPE_MARKET, quantity=position['quantity'], reduceOnly=True
            )
            logger.success(f"[{self.strategy_id}] Ринковий ордер на закриття позиції успішно виставлено.")
        except Exception as e:
            # Окремо обробляємо помилку, коли позиції для закриття вже не існує
            if "APIError(code=-2022): ReduceOnly Order is rejected" in str(e):
                logger.warning(f"[{self.strategy_id}] Спроба закрити позицію, якої вже не існує (ймовірно, закрита раніше). Це очікувана поведінка.")
            else:
                logger.error(f"[{self.strategy_id}] Помилка під час безпечного закриття позиції: {e}", exc_info=True)