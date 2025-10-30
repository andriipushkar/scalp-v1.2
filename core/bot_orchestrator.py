import asyncio
import yaml
import os
import pandas as pd
from loguru import logger
from binance import BinanceSocketManager
from binance.enums import *
from importlib import import_module

from core.binance_client import BinanceClient
from core.orderbook_manager import OrderBookManager
from core.position_manager import PositionManager
from core.trade_executor import TradeExecutor
from core.symbol_screener import SymbolScreener

# Файл для збереження стану відкритих позицій між перезапусками
POSITIONS_STATE_FILE = "logs/positions_state.json"
RECONCILE_INTERVAL_SECONDS = 60 # Інтервал звірки стану позицій з біржею (в секундах)

class BotOrchestrator:
    """
    Головний клас, що керує всіма процесами торгового бота.
    Відповідає за ініціалізацію, запуск та координацію всіх компонентів системи.
    """

    def __init__(self, config_path: str = "configs/config.yaml"):
        """
        Ініціалізує оркестратор.

        Args:
            config_path (str): Шлях до основного файлу конфігурації YAML.
        """
        logger.info("Ініціалізація BotOrchestrator...")
        # Завантаження конфігурацій
        self.config = self._load_yaml(config_path)
        self.trading_config = self.config.get('trading_parameters', {})
        
        # Ініціалізація ключових компонентів
        self.binance_client: BinanceClient | None = None
        self.position_manager = PositionManager(POSITIONS_STATE_FILE)
        self.orderbook_managers: dict[str, OrderBookManager] = {}
        self.trade_executors: list[TradeExecutor] = []
        self.bsm: BinanceSocketManager | None = None
        
        # Словники для відстеження стану ордерів
        self.pending_symbols = set()
        self.pending_sl_tp = {}
        self.kline_data_cache: dict[str, pd.DataFrame] = {}

    def _load_yaml(self, path: str) -> dict:
        """Допоміжна функція для завантаження YAML файлів."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Конфігураційний файл не знайдено: {path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Помилка декодування YAML у файлі: {path}. Помилка: {e}")
            raise

    def _get_strategy_class(self, strategy_name: str):
        """Динамічно імпортує та повертає клас стратегії за її назвою."""
        try:
            # Конвертуємо 'MyStrategyName' в 'my_strategy_name' для назви файлу
            module_name = ''.join(['_' + i.lower() if i.isupper() else i for i in strategy_name]).lstrip('_')
            module_path = f"strategies.{module_name}"
            module = import_module(module_path)
            return getattr(module, strategy_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"Не вдалося завантажити клас стратегії '{strategy_name}': {e}")
            raise

    async def _setup_trading_environment(self, symbols: list[str]):
        """
        Налаштовує торгове середовище для списку символів (встановлює кредитне плече та тип маржі).
        """
        logger.info(f"Налаштування торгового середовища для {len(symbols)} символів...")
        unique_symbols = set(symbols)
        valid_symbols = set()
        leverage_to_set = self.trading_config.get('leverage', 10)
        margin_type = self.trading_config.get('margin_type', 'ISOLATED')

        for symbol in unique_symbols:
            try:
                # --- Перевірка кредитного плеча ---
                brackets_info = await self.binance_client.get_leverage_brackets(symbol)
                if brackets_info:
                    # The endpoint returns a list, for a single symbol it has one element
                    symbol_brackets = brackets_info[0]['brackets']
                    max_leverage = max(b['initialLeverage'] for b in symbol_brackets)

                    if leverage_to_set > max_leverage:
                        logger.warning(f"Задане кредитне плече {leverage_to_set}x для {symbol} перевищує максимальне ({max_leverage}x). Символ пропускається.")
                        continue
                else:
                    logger.warning(f"Не вдалося отримати інформацію про кредитне плече для {symbol}. Символ пропускається.")
                    continue

                # --- Встановлення параметрів ---
                await self.binance_client.set_leverage(symbol, leverage_to_set)
                await self.binance_client.set_margin_type(symbol, margin_type)
                valid_symbols.add(symbol)

            except Exception as e:
                logger.error(f"Не вдалося налаштувати середовище для {symbol}: {e}. Символ пропускається.")

        logger.info(f"Торгове середовище успішно налаштовано для {len(valid_symbols)} символів.")
        return list(valid_symbols)

    async def _market_data_listener(self, market_data_streams: list[str]):
        """
        Асинхронна задача, що слухає ринкові дані (стакани) для всіх активних символів.
        """
        logger.info(f"Запуск слухача ринкових даних для потоків: {market_data_streams}")
        async with self.bsm.multiplex_socket(market_data_streams) as socket:
            while True:
                try:
                    msg = await socket.recv()
                    if msg and 'e' in msg and 'm' in msg:
                        logger.error(f"Помилка вебсокету ринкових даних: {msg['m']}")
                        continue
                    if 'stream' in msg:
                        stream_name = msg['stream']
                        data = msg['data']
                        symbol = stream_name.split('@')[0].upper()
                        if '@depth' in stream_name and symbol in self.orderbook_managers:
                            await self.orderbook_managers[symbol].process_depth_message(data)
                except Exception as e:
                    logger.error(f"Критична помилка в слухачі ринкових даних: {e}. Перезапуск через 5с...")
                    await asyncio.sleep(5)

    async def _user_data_listener(self):
        """
        Асинхронна задача, що слухає потік даних користувача.
        """
        logger.info("Запуск слухача даних користувача...")
        async with self.bsm.futures_user_socket() as socket:
            while True:
                try:
                    msg = await socket.recv()
                    await self._handle_user_data_message(msg)
                except Exception as e:
                    logger.error(f"Критична помилка в слухачі даних користувача: {e}. Перезапуск через 5с...")
                    await asyncio.sleep(5)

    async def _handle_user_data_message(self, msg: dict):
        """
        Обробляє повідомлення з потоку даних користувача.
        """
        if msg.get('e') != 'ORDER_TRADE_UPDATE':
            return

        logger.debug(f"[RAW USER DATA] {msg}")
        order_data = msg.get('o', {})
        client_order_id = order_data.get('c')
        symbol = order_data.get('s')
        status = order_data.get('X')
        order_type = order_data.get('ot')
        
        if not all([client_order_id, symbol, status, order_type]):
            return
            
        order_id = int(order_data.get('i'))

        if status == 'FILLED' and order_type in ['LIMIT', 'MARKET'] and client_order_id in self.pending_sl_tp:
            logger.info(f"[UserData] Ордер на вхід {client_order_id} (ID: {order_id}) для {symbol} виконано.")
            pending_info = self.pending_sl_tp.pop(client_order_id)
            actual_entry_price = float(order_data.get('ap'))
            signal_type = pending_info['signal_type']
            strategy_id = pending_info['strategy_id']

            executor = next((ex for ex in self.trade_executors if ex.strategy_id == strategy_id), None)
            if not executor:
                logger.error(f"Не знайдено executor для strategy_id {strategy_id}")
                return

            sl_price = pending_info.get('stop_loss_price')
            tp_price = pending_info.get('take_profit_price')
            quantity = float(order_data.get('q'))
            sl_tp_side = SIDE_SELL if signal_type == "Long" else SIDE_BUY

            if sl_price is None or tp_price is None:
                logger.error(f"[{symbol}] Не вдалося отримати розраховані SL/TP ціни з pending_sl_tp. Аварійне закриття.")
                await self.binance_client.futures_create_order(symbol=symbol, side=sl_tp_side, type=ORDER_TYPE_MARKET, quantity=quantity)
                return

            try:
                logger.info(f"[{symbol}] Виставлення ордерів SL ({sl_price}) та TP ({tp_price}).")
                sl_order = await self.binance_client.create_stop_market_order(symbol, sl_tp_side, quantity, sl_price, executor.price_precision, executor.qty_precision)
                tp_order = await self.binance_client.create_take_profit_market_order(symbol, sl_tp_side, quantity, tp_price, executor.price_precision, executor.qty_precision)
                
                # Оновлюємо позицію в PositionManager з ID ордерів SL/TP
                self.position_manager.set_position(
                    symbol=symbol,
                    side=signal_type,
                    quantity=quantity,
                    entry_price=actual_entry_price,
                    stop_loss=sl_price,
                    take_profit=tp_price,
                    initial_stop_loss=sl_price, # initial_stop_loss також встановлюємо як sl_price
                    sl_order_id=sl_order['orderId'],
                    tp_order_id=tp_order['orderId']
                )
                logger.success(f"[{symbol}] Позицію успішно відкрито з SL {sl_order['orderId']} та TP {tp_order['orderId']}.")
            except Exception as e:
                logger.error(f"[{symbol}] Не вдалося виставити SL/TP. Запуск відкату позиції. Помилка: {e}")
                await self.binance_client.futures_create_order(symbol=symbol, side=sl_tp_side, type=ORDER_TYPE_MARKET, quantity=quantity)
            finally:
                if symbol in self.pending_symbols:
                    self.pending_symbols.remove(symbol)
            return

        position = self.position_manager.get_position_by_symbol(symbol)
        if position and status == 'FILLED' and order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
            sl_id = position.get('sl_order_id')
            tp_id = position.get('tp_order_id')

            if order_id == sl_id or order_id == tp_id:
                exit_type = "Stop-Loss" if order_id == sl_id else "Take-Profit"
                logger.info(f"[UserData] Ордер {exit_type} {order_id} для {symbol} виконано. Закриття позиції.")
                
                other_order_id = tp_id if order_id == sl_id else sl_id
                if other_order_id:
                    try:
                        await self.binance_client.cancel_order(symbol, other_order_id)
                        logger.success(f"[UserData] Успішно скасовано зустрічний ордер {other_order_id}.")
                    except Exception as e:
                        # Ігноруємо помилку, якщо ордер вже не існує (був виконаний або скасований раніше)
                        if "Order does not exist" not in str(e) and "APIError(code=-2011)" not in str(e):
                            logger.error(f"[UserData] Не вдалося скасувати ордер {other_order_id}: {e}")
                
                self.position_manager.close_position(symbol)
            return

        if status in ['CANCELED', 'EXPIRED'] and order_type == 'LIMIT' and client_order_id in self.pending_sl_tp:
            logger.warning(f"[UserData] Лімітний ордер на вхід {client_order_id} для {symbol} було скасовано/прострочено.")
            del self.pending_sl_tp[client_order_id]
            if symbol in self.pending_symbols:
                self.pending_symbols.remove(symbol)

    async def _periodic_kline_fetcher(self):
        """
        Періодично отримує K-лінії для всіх активних символів та кешує їх.
        """
        logger.info("Запуск задачі періодичного отримання K-ліній...")
        while True:
            start_time = asyncio.get_event_loop().time()

            # Збираємо унікальні пари (символ, інтервал) для запитів
            kline_requests = {}  # {(symbol, interval): kline_limit}
            for executor in self.trade_executors:
                symbol = executor.strategy.symbol
                interval = executor.strategy.kline_interval
                limit = executor.strategy.kline_limit
                kline_requests[(symbol, interval)] = max(kline_requests.get((symbol, interval), 0), limit)

            tasks = []
            request_params = []
            for (symbol, interval), limit in kline_requests.items():
                tasks.append(self.binance_client.client.futures_klines(symbol=symbol, interval=interval, limit=limit))
                request_params.append((symbol, interval, limit))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                symbol, interval, limit = request_params[i]
                if isinstance(result, Exception):
                    logger.error(f"Помилка отримання K-ліній для {symbol} ({interval}): {result}")
                    continue

                klines = result
                if klines:
                    df = pd.DataFrame(klines, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time',
                                                       'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume',
                                                       'taker_buy_quote_asset_volume', 'ignore'])
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = pd.to_numeric(df[col])
                    self.kline_data_cache[f"{symbol}_{interval}"] = df
                    logger.debug(f"Оновлено K-лінії для {symbol} ({interval}).")
                else:
                    logger.warning(f"Не отримано K-ліній для {symbol} ({interval}).")

            # Розраховуємо час до наступного повного циклу
            end_time = asyncio.get_event_loop().time()
            elapsed_time = end_time - start_time

            min_interval_seconds = float('inf')
            for executor in self.trade_executors:
                interval_str = executor.strategy.kline_interval
                if interval_str.endswith('m'):
                    seconds = int(interval_str[:-1]) * 60
                elif interval_str.endswith('h'):
                    seconds = int(interval_str[:-1]) * 3600
                elif interval_str.endswith('d'):
                    seconds = int(interval_str[:-1]) * 86400
                else:
                    seconds = 60  # За замовчуванням 1 хвилина
                min_interval_seconds = min(min_interval_seconds, seconds)

            sleep_duration = max(0, min_interval_seconds - elapsed_time)
            logger.debug(f"Наступне оновлення K-ліній через {sleep_duration:.2f} секунд.")
            await asyncio.sleep(sleep_duration)

    async def _periodic_reconcile(self):
        """
        Періодично звіряє стан позицій бота з біржею.
        """
        logger.info(f"Запуск задачі періодичної звірки позицій (кожні {RECONCILE_INTERVAL_SECONDS} секунд)...")
        while True:
            try:
                await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
                await self.position_manager.reconcile_with_exchange(self.binance_client)
            except Exception as e:
                logger.error(f"Помилка в задачі періодичної звірки: {e}", exc_info=True)

    async def start(self):
        """
        Основний метод, що запускає всі компоненти бота в правильній послідовдовності.
        """
        logger.info("Запуск оркестратора...")
        async with BinanceClient() as client:
            self.binance_client = client
            
            await self.position_manager.reconcile_with_exchange(self.binance_client)
            self.bsm = BinanceSocketManager(self.binance_client.get_async_client(), max_queue_size=5000)

            # --- 1. Визначення списку символів ---
            active_symbols = self.config.get('symbols', [])
            if not active_symbols:
                logger.info("Список символів у конфігурації порожній. Запуск скринера для вибору топ-символів...")
                screener_config = self.trading_config.get('screener', {})
                min_volume = screener_config.get('min_volume', 100000000)
                screener = SymbolScreener(self.binance_client)
                active_symbols = await screener.get_top_symbols_by_volume(
                    min_volume=min_volume,
                    n=self.trading_config.get("max_concurrent_symbols", 20)
                )
                if not active_symbols:
                    logger.error("Не вдалося отримати список символів від скринера. Зупинка.")
                    return
            logger.info(f"Будуть використовуватися символи: {active_symbols}")

            # --- 2. Налаштування торгового середовища ---
            valid_symbols = await self._setup_trading_environment(active_symbols)

            # --- 3. Ініціалізація стратегій та виконавців ---
            market_data_streams = []
            enabled_strategies = self.config.get('enabled_strategies', [])
            strategy_settings_paths = self.config.get('strategy_settings', {})

            for strategy_name in enabled_strategies:
                strategy_config_path = strategy_settings_paths.get(strategy_name)
                if not strategy_config_path:
                    logger.warning(f"Не знайдено шлях до налаштувань для стратегії '{strategy_name}'. Пропускається.")
                    continue
                
                strategy_params_full = self._load_yaml(strategy_config_path)
                default_params = strategy_params_full.get('default', {})
                symbol_specific_params = strategy_params_full.get('symbol_specific', {})
                
                StrategyClass = self._get_strategy_class(strategy_name)

                for symbol in valid_symbols:
                    # Створення унікального ID для пари стратегія-символ
                    strategy_id = f"{strategy_name}_{symbol}"
                    
                    # Об'єднання параметрів: спершу базові, потім специфічні для символу
                    final_params = default_params.copy()
                    final_params.update(symbol_specific_params.get(symbol, {}))
                    
                    # Створюємо менеджер стакану, якщо його ще немає
                    if symbol not in self.orderbook_managers:
                        self.orderbook_managers[symbol] = OrderBookManager(symbol)
                        market_data_streams.append(f"{symbol.lower()}@depth")
                        logger.info(f"OrderBookManager буде ініціалізовано для {symbol}")

                    symbol_info = await self.binance_client.get_symbol_info(symbol)
                    price_precision = int(symbol_info['pricePrecision'])
                    qty_precision = int(symbol_info['quantityPrecision'])
                    tick_size = float(symbol_info['filters'][0]['tickSize'])

                    # Створення екземпляру стратегії
                    strategy_instance = StrategyClass(strategy_id, symbol, final_params)

                    # Створення TradeExecutor
                    executor = TradeExecutor(
                        strategy_instance, self.binance_client, self.position_manager, self, 
                        self.orderbook_managers[symbol], self.trading_config['max_active_trades'], 
                        self.trading_config['leverage'], price_precision, qty_precision, 
                        tick_size, self.pending_symbols
                    )
                    self.trade_executors.append(executor)
                    logger.info(f"TradeExecutor ініціалізовано для {strategy_id}")

            if not self.trade_executors:
                logger.warning("Не знайдено активних стратегій для запуску. Зупинка.")
                return

            # --- 4. Ініціалізація біржових стаканів ---
            logger.info("Ініціалізація біржових стаканів (snapshots)...")
            for symbol, obm in self.orderbook_managers.items():
                snapshot = await self.binance_client.get_futures_order_book(symbol=symbol, limit=1000)
                await obm.initialize_book(snapshot)
            logger.info("Всі біржові стакани ініціалізовано.")

            # --- 5. Запуск основних асинхронних задач ---
            logger.info("Запуск основних задач: слухачі даних та моніторинг стратегій.")
            user_data_task = asyncio.create_task(self._user_data_listener())
            market_data_task = asyncio.create_task(self._market_data_listener(list(set(market_data_streams))))
            monitoring_tasks = [asyncio.create_task(ex.start_monitoring()) for ex in self.trade_executors]
            reconciliation_task = asyncio.create_task(self._periodic_reconcile())
            kline_fetcher_task = asyncio.create_task(self._periodic_kline_fetcher())
            
            await asyncio.gather(user_data_task, market_data_task, reconciliation_task, kline_fetcher_task, *monitoring_tasks)