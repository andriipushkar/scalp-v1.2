import asyncio
import json
import os
from loguru import logger
from binance import BinanceSocketManager
from binance.enums import *

from core.binance_client import BinanceClient
from core.orderbook_manager import OrderBookManager
from core.position_manager import PositionManager
from core.trade_executor import TradeExecutor
from core.symbol_screener import SymbolScreener

# Файл для збереження стану відкритих позицій між перезапусками
POSITIONS_STATE_FILE = "logs/positions_state.json"

class BotOrchestrator:
    """
    Головний клас, що керує всіма процесами торгового бота.
    Відповідає за ініціалізацію, запуск та координацію всіх компонентів системи.
    """

    def __init__(self, strategies_config_path: str = "configs/strategies.json", 
                 trade_config_path: str = "configs/trading_config.json"):
        """
        Ініціалізує оркестратор.

        Args:
            strategies_config_path (str): Шлях до файлу з конфігурацією стратегій.
            trade_config_path (str): Шлях до файлу з основними торговими налаштуваннями.
        """
        logger.info("Ініціалізація BotOrchestrator...")
        # Завантаження конфігурацій
        self.trading_config = self._load_json(trade_config_path)
        self.strategies_configs = self._load_json(strategies_config_path)
        
        # Ініціалізація ключових компонентів
        self.binance_client: BinanceClient | None = None
        self.position_manager = PositionManager(POSITIONS_STATE_FILE)
        self.orderbook_managers: dict[str, OrderBookManager] = {} # Словник для зберігання менеджерів стаканів по кожному символу
        self.trade_executors: list[TradeExecutor] = [] # Список виконавців угод
        self.bsm: BinanceSocketManager | None = None # Менеджер WebSocket сокетів
        
        # Словники для відстеження стану ордерів, що очікують виконання
        self.pending_symbols = set() # Символи, для яких виставлено ордер на вхід, але він ще не виконаний
        self.pending_sl_tp = {} # Деталі про ордери, що очікують на виставлення SL/TP після входу

    def _load_json(self, path: str) -> dict:
        """Допоміжна функція для завантаження JSON файлів."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Конфігураційний файл не знайдено: {path}")
            raise
        except json.JSONDecodeError:
            logger.error(f"Помилка декодування JSON у файлі: {path}")
            raise

    async def _setup_trading_environment(self, symbols: list[str]):
        """
        Налаштовує торгове середовище для списку символів (встановлює кредитне плече та тип маржі).
        """
        logger.info(f"Налаштування торгового середовища для {len(symbols)} символів...")
        unique_symbols = set(symbols)
        valid_symbols = set()
        leverage = self.trading_config.get('leverage', 10)
        margin_type = self.trading_config.get('margin_type', 'ISOLATED')
        
        for symbol in unique_symbols:
            try:
                # Встановлюємо кредитне плече та тип маржі для кожного символу
                await self.binance_client.set_leverage(symbol, leverage)
                await self.binance_client.set_margin_type(symbol, margin_type)
                valid_symbols.add(symbol)
            except Exception as e:
                logger.error(f"Не вдалося налаштувати середовище для {symbol}: {e}. Символ пропускається.")
        
        logger.info(f"Торгове середовище успішно налаштовано для {len(valid_symbols)} символів.")
        return list(valid_symbols)

    async def _market_data_listener(self, market_data_streams: list[str]):
        """
        Асинхронна задача, що слухає ринкові дані (стакани) для всіх активних символів
        через мультиплексний WebSocket сокет.
        """
        logger.info(f"Запуск слухача ринкових даних для потоків: {market_data_streams}")
        async with self.bsm.multiplex_socket(market_data_streams) as socket:
            while True:
                try:
                    msg = await socket.recv()
                    
                    # Обробка повідомлень про помилки від WebSocket
                    if msg and 'e' in msg and 'm' in msg:
                        logger.error(f"Помилка вебсокету ринкових даних: {msg['m']}")
                        continue
                    
                    # Перевірка, чи повідомлення містить дані потоку
                    if 'stream' in msg:
                        stream_name = msg['stream']
                        data = msg['data']
                        symbol = stream_name.split('@')[0].upper()
                        
                        # Якщо це оновлення стакану, передаємо його відповідному менеджеру
                        if '@depth' in stream_name and symbol in self.orderbook_managers:
                            await self.orderbook_managers[symbol].process_depth_message(data)
                except Exception as e:
                    logger.error(f"Критична помилка в слухачі ринкових даних: {e}. Перезапуск через 5с...")
                    await asyncio.sleep(5) # Пауза перед перезапуском

    async def _user_data_listener(self):
        """
        Асинхронна задача, що слухає потік даних користувача (зміни по ордерах, балансу).
        """
        logger.info("Запуск слухача даних користувача...")
        async with self.bsm.futures_user_socket() as socket:
            while True:
                try:
                    msg = await socket.recv()
                    # Обробляємо кожне повідомлення в окремій функції
                    await self._handle_user_data_message(msg)
                except Exception as e:
                    logger.error(f"Критична помилка в слухачі даних користувача: {e}. Перезапуск через 5с...")
                    await asyncio.sleep(5)

    async def _handle_user_data_message(self, msg: dict):
        """
        Обробляє повідомлення з потоку даних користувача. 
        Це серце логіки управління життєвим циклом ордерів.
        """
        # Ігноруємо повідомлення, що не стосуються оновлення ордерів
        if msg.get('e') != 'ORDER_TRADE_UPDATE':
            return

        logger.debug(f"[RAW USER DATA] {msg}")
        order_data = msg.get('o', {})
        client_order_id = order_data.get('c')
        symbol = order_data.get('s')
        status = order_data.get('X')
        order_type = order_data.get('ot')
        
        # Перевірка наявності ключових полів
        if not all([client_order_id, symbol, status, order_type]):
            return
            
        order_id = int(order_data.get('i'))

        # --- Крок 1: Обробка виконання ордеру на ВХІД --- 
        # Якщо ордер виконано (FILLED) і він є в списку очікуючих на SL/TP
        if status == 'FILLED' and order_type in ['LIMIT', 'MARKET'] and client_order_id in self.pending_sl_tp:
            logger.info(f"[UserData] Ордер на вхід {client_order_id} (ID: {order_id}) для {symbol} виконано.")
            pending_info = self.pending_sl_tp.pop(client_order_id)
            actual_entry_price = float(order_data.get('ap')) # Реальна ціна входу
            signal_type = pending_info['signal_type']
            strategy_id = pending_info['strategy_id']

            # Знаходимо відповідний executor та стратегію
            executor = next((ex for ex in self.trade_executors if ex.strategy_id == strategy_id), None)
            if not executor:
                logger.error(f"Не знайдено executor для strategy_id {strategy_id}")
                return

            # Розраховуємо SL/TP на основі РЕАЛЬНОЇ ціни входу
            sl_tp_prices = executor.strategy.calculate_sl_tp(
                entry_price=actual_entry_price, 
                signal_type=signal_type,
                order_book_manager=executor.orderbook_manager,
                tick_size=executor.tick_size
            )
            if not sl_tp_prices:
                logger.error(f"[{symbol}] Не вдалося розрахувати SL/TP для ціни входу {actual_entry_price}. Аварійне закриття.")
                # Якщо не можемо розрахувати SL/TP, аварійно закриваємо позицію
                await self.binance_client.futures_create_order(symbol=symbol, side=SIDE_SELL if signal_type == 'Long' else SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=float(order_data.get('q')))
                return

            sl_price = round(sl_tp_prices['stop_loss'], executor.price_precision)
            tp_price = round(sl_tp_prices['take_profit'], executor.price_precision)
            quantity = float(order_data.get('q'))
            sl_tp_side = SIDE_SELL if signal_type == "Long" else SIDE_BUY

            try:
                logger.info(f"[{symbol}] Виставлення ордерів SL ({sl_price}) та TP ({tp_price}).")
                # Створюємо SL та TP ордери
                sl_order = await self.binance_client.create_stop_market_order(symbol, sl_tp_side, quantity, sl_price, executor.price_precision, executor.qty_precision)
                tp_order = await self.binance_client.create_take_profit_market_order(symbol, sl_tp_side, quantity, tp_price, executor.price_precision, executor.qty_precision)
                # Зберігаємо інформацію про відкриту позицію
                self.position_manager.set_position(symbol, signal_type, quantity, actual_entry_price, sl_price, tp_price, sl_order['orderId'], tp_order['orderId'])
                logger.success(f"[{symbol}] Позицію успішно відкрито з SL {sl_order['orderId']} та TP {tp_order['orderId']}.")
            except Exception as e:
                logger.error(f"[{symbol}] Не вдалося виставити SL/TP після входу. Запуск відкату позиції. Помилка: {e}")
                # Якщо SL/TP не виставились, аварійно закриваємо позицію, щоб уникнути ризику
                await self.binance_client.futures_create_order(symbol=symbol, side=sl_tp_side, type=ORDER_TYPE_MARKET, quantity=quantity)
            finally:
                # Видаляємо символ зі списку очікуючих
                if symbol in self.pending_symbols:
                    self.pending_symbols.remove(symbol)
            return

        # --- Крок 2: Обробка виконання ордерів на ВИХІД (SL/TP) ---
        position = self.position_manager.get_position_by_symbol(symbol)
        if position and status == 'FILLED' and order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
            sl_id = position.get('sl_order_id')
            tp_id = position.get('tp_order_id')

            # Перевіряємо, чи виконаний ордер є нашим SL або TP
            if order_id == sl_id or order_id == tp_id:
                exit_type = "Stop-Loss" if order_id == sl_id else "Take-Profit"
                logger.info(f"[UserData] Ордер {exit_type} {order_id} для {symbol} виконано. Закриття позиції.")
                
                # Визначаємо ID іншого (зустрічного) ордеру, який треба скасувати
                other_order_id = tp_id if order_id == sl_id else sl_id
                if other_order_id:
                    try:
                        await self.binance_client.cancel_order(symbol, other_order_id)
                        logger.success(f"[UserData] Успішно скасовано зустрічний ордер {other_order_id}.")
                    except Exception as e:
                        # Ігноруємо помилку, якщо ордер вже не існує (напр., виконався одночасно)
                        if "Order does not exist" not in str(e): 
                            logger.error(f"[UserData] Не вдалося скасувати ордер {other_order_id}: {e}")
                
                # Видаляємо позицію з менеджера
                self.position_manager.close_position(symbol)
            return

        # --- Крок 3: Обробка скасування лімітного ордеру на вхід ---
        if status in ['CANCELED', 'EXPIRED'] and order_type == 'LIMIT' and client_order_id in self.pending_sl_tp:
            logger.warning(f"[UserData] Лімітний ордер на вхід {client_order_id} для {symbol} було скасовано/прострочено.")
            del self.pending_sl_tp[client_order_id]
            if symbol in self.pending_symbols:
                self.pending_symbols.remove(symbol)

    async def start(self):
        """
        Основний метод, що запускає всі компоненти бота в правильній послідовності.
        """
        logger.info("Запуск оркестратора...")
        async with BinanceClient() as client:
            self.binance_client = client
            
            # Звіряємо стан позицій з біржею перед початком роботи
            await self.position_manager.reconcile_with_exchange(self.binance_client)

            self.bsm = BinanceSocketManager(self.binance_client.get_async_client(), max_queue_size=5000)

            # --- 1. Динамічний вибір символів ---
            screener = SymbolScreener(self.binance_client)
            top_symbols = await screener.get_top_symbols_by_volume(n=self.trading_config.get("max_concurrent_symbols", 20))
            
            if not top_symbols:
                logger.error("Не вдалося отримати список символів від скринера. Зупинка.")
                return

            # --- 2. Фільтрація та активація стратегій ---
            active_strategies = [
                s for s in self.strategies_configs 
                if s['symbol'] in top_symbols and s.get("enabled", False)
            ]
            active_symbols = [s['symbol'] for s in active_strategies]
            logger.info(f"Активовано {len(active_strategies)} стратегій для символів: {active_symbols}")

            # --- 3. Налаштування торгового середовища ---
            await self._setup_trading_environment(active_symbols)

            # --- 4. Ініціалізація виконавців та менеджерів стаканів ---
            market_data_streams = []
            for config in active_strategies:
                symbol = config['symbol']
                # Створюємо менеджер стакану, якщо його ще немає для цього символу
                if symbol not in self.orderbook_managers:
                    self.orderbook_managers[symbol] = OrderBookManager(symbol)
                    market_data_streams.append(f"{symbol.lower()}@depth")
                    logger.info(f"OrderBookManager буде ініціалізовано для {symbol}")
                
                # Отримуємо торгові правила для символу (точність ціни, кількість)
                symbol_info = await self.binance_client.get_symbol_info(symbol)
                price_precision = int(symbol_info['pricePrecision'])
                qty_precision = int(symbol_info['quantityPrecision'])
                tick_size = float(symbol_info['filters'][0]['tickSize'])

                # Створюємо виконавця угод для кожної стратегії
                executor = TradeExecutor(
                    config, self.binance_client, self.position_manager, self, self.orderbook_managers[symbol],
                    self.trading_config['max_active_trades'], self.trading_config['leverage'],
                    price_precision, qty_precision, tick_size, self.pending_symbols
                )
                self.trade_executors.append(executor)
                logger.info(f"TradeExecutor ініціалізовано для {config['strategy_id']}")

            if not self.trade_executors: 
                logger.warning("Не знайдено активних стратегій для запуску. Зупинка.")
                return

            # --- 5. Ініціалізація біржових стаканів ---
            logger.info("Ініціалізація біржових стаканів (snapshots)...")
            for symbol, obm in self.orderbook_managers.items():
                snapshot = await self.binance_client.get_futures_order_book(symbol=symbol, limit=1000)
                await obm.initialize_book(snapshot)
            logger.info("Всі біржові стакани ініціалізовано.")

            # --- 6. Запуск основних асинхронних задач ---
            logger.info("Запуск основних задач: слухачі даних та моніторинг стратегій.")
            user_data_task = asyncio.create_task(self._user_data_listener())
            market_data_task = asyncio.create_task(self._market_data_listener(market_data_streams))
            monitoring_tasks = [asyncio.create_task(ex.start_monitoring()) for ex in self.trade_executors]
            
            # Очікуємо завершення всіх задач (працюватимуть безкінечно)
            await asyncio.gather(user_data_task, market_data_task, *monitoring_tasks)