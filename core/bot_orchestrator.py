import asyncio
import json
import csv
import os
from loguru import logger
import pandas as pd
from binance.enums import *
import math
from datetime import datetime

from binance import BinanceSocketManager

from core.binance_client import BinanceClient
from core.orderbook_manager import OrderBookManager
from strategies.liquidity_hunting_strategy import LiquidityHuntingStrategy

TRADE_HISTORY_CSV = "logs/trade_history.csv"
POSITIONS_STATE_FILE = "logs/positions_state.json"

class PositionManager:
    """Керує активними позиціями, зберігаючи їх стан у файлі для відновлення."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._positions = self._load_state()

    def _load_state(self) -> dict:
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                valid_positions = {symbol: pos for symbol, pos in state.items() if pos.get('quantity', 0) > 0}
                logger.info(f"Завантажено стан позицій з {self.state_file}: {len(valid_positions)} поз.")
                return valid_positions
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Помилка завантаження стану з {self.state_file}: {e}. Починаємо з чистого стану.")
            return {}

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._positions, f, indent=4)
        except IOError as e:
            logger.error(f"Не вдалося зберегти стан у {self.state_file}: {e}")

    def get_position_by_symbol(self, symbol: str) -> dict | None:
        return self._positions.get(symbol)

    def get_positions_count(self) -> int:
        return len([pos for pos in self._positions.values() if pos.get('quantity', 0) > 0])

    def set_position(self, symbol: str, side: str, quantity: float, entry_price: float, stop_loss: float, take_profit: float, sl_order_id: int | None = None, tp_order_id: int | None = None):
        if side not in ["Long", "Short"]:
            raise ValueError("Напрямок позиції має бути 'Long' або 'Short'")
        if quantity > 0:
            self._positions[symbol] = {
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "sl_order_id": sl_order_id,
                "tp_order_id": tp_order_id
            }
            logger.info(f"[PositionManager] Позицію для {symbol} відкрито/оновлено: {self._positions[symbol]}")
            self._save_state()

    def close_position(self, symbol: str):
        if symbol in self._positions:
            closed_pos = self._positions.pop(symbol)
            logger.info(f"[PositionManager] Позицію для {symbol} закрито: {closed_pos}")
            self._save_state()
            return closed_pos
        return None

class TradeExecutor:
    """Виконує торгові операції для однієї стратегії/символу."""

    def __init__(self, strategy_config: dict, binance_client: BinanceClient, position_manager: PositionManager,
                 orchestrator: 'BotOrchestrator', orderbook_manager: OrderBookManager, max_active_trades: int, 
                 leverage: int, price_precision: int, qty_precision: int, pending_symbols: set):
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
        self.pending_symbols = pending_symbols
        self.strategy = self._initialize_strategy()

    def _initialize_strategy(self):
        strategy_name = self.strategy_id.split('_')[0]
        if strategy_name == "LiquidityHunting":
            return LiquidityHuntingStrategy(self.strategy_id, self.symbol, self.strategy_config["parameters"])
        else:
            raise ValueError(f"Невідома стратегія: {strategy_name}")

    async def execute(self):
        if self.symbol in self.pending_symbols or not self.orderbook_manager.is_initialized:
            return
        if self.position_manager.get_position_by_symbol(self.symbol):
            return
        if self.position_manager.get_positions_count() >= self.max_active_trades:
            return

        signal = self.strategy.check_signal(self.orderbook_manager)
        if signal:
            await self._open_position(signal)

    async def _open_position(self, signal: dict):
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            self.pending_symbols.add(self.symbol)

            symbol_info = await self.binance_client.get_symbol_info(self.symbol)
            tick_size = float(symbol_info['filters'][0]['tickSize'])

            sl_tp = self.strategy.calculate_sl_tp(0, signal["signal_type"], wall_price=signal['wall_price'], tick_size=tick_size)
            if sl_tp is None: 
                self.pending_symbols.remove(self.symbol)
                return

            entry_price = round(sl_tp['entry_price'], self.price_precision)
            stop_loss = round(sl_tp['stop_loss'], self.price_precision)
            take_profit = round(sl_tp['take_profit'], self.price_precision)

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

            logger.info(f"[{self.strategy_id}] Виставлення LIMIT ордеру на вхід: {quantity} {self.symbol} за ціною {entry_price}")
            order = await self.binance_client.futures_create_order(symbol=self.symbol, side=side, type=ORDER_TYPE_LIMIT, quantity=quantity, price=str(entry_price), timeInForce=TIME_IN_FORCE_GTC)
            
            self.orchestrator.pending_sl_tp[order['orderId']] = {
                'sl': stop_loss,
                'tp': take_profit,
                'side': signal['signal_type'],
                'quantity': quantity,
                'symbol': self.symbol
            }
            logger.success(f"[{self.strategy_id}] Лімітний ордер {order['orderId']} виставлено. Очікуємо виконання.")

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Помилка відкриття позиції: {e}")
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)

class BotOrchestrator:
    """Головний клас, що керує всіма процесами бота."""

    def __init__(self, strategies_config_path: str = "configs/strategies.json", 
                 trade_config_path: str = "configs/trading_config.json",
                 symbols_config_path: str = "configs/symbols.json"):
        self.trading_config = self._load_json(trade_config_path)
        
        enabled_symbols = self._load_json(symbols_config_path)
        logger.info(f"Завантажено список дозволених символів: {enabled_symbols}")

        all_strategies = self._load_json(strategies_config_path)
        self.strategies_configs = [
            s for s in all_strategies 
            if s['symbol'] in enabled_symbols and s.get("enabled", False)
        ]
        active_symbols = [s['symbol'] for s in self.strategies_configs]
        logger.info(f"Активовано {len(self.strategies_configs)} стратегій для символів: {active_symbols}")

        self.binance_client: BinanceClient | None = None
        self.position_manager = PositionManager(POSITIONS_STATE_FILE)
        self.orderbook_managers: dict[str, OrderBookManager] = {}
        self.trade_executors: list[TradeExecutor] = []
        self.bsm: BinanceSocketManager | None = None
        self.pending_symbols = set()
        self.pending_sl_tp = {}

    def _load_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _setup_trading_environment(self):
        logger.info("Налаштування торгового середовища...")
        unique_symbols = {s['symbol'] for s in self.strategies_configs}
        valid_symbols = set()
        leverage = self.trading_config['leverage']
        margin_type = self.trading_config['margin_type']
        for symbol in unique_symbols:
            try:
                await self.binance_client.set_leverage(symbol, leverage)
                await self.binance_client.set_margin_type(symbol, margin_type)
                valid_symbols.add(symbol)
            except Exception as e:
                logger.error(f"Не вдалося налаштувати середовище для {symbol}: {e}. Символ пропускається.")
        logger.info("Торгове середовище налаштовано.")
        return valid_symbols

    async def _start_websocket_listener(self, streams: list[str]):
        """Запускає єдиний мультиплексний вебсокет для всіх даних."""
        logger.info(f"Запуск мультиплексного вебсокету для потоків: {streams}")
        async with self.bsm.multiplex_socket(streams) as socket:
            while True:
                try:
                    msg = await socket.recv()
                    
                    # Повідомлення про помилку (напр. розрив з'єднання)
                    if msg and 'e' in msg and 'm' in msg:
                        logger.error(f"Помилка вебсокету: {msg['m']}")
                        continue

                    # Обробка ринкових даних
                    if 'stream' in msg:
                        stream_name = msg['stream']
                        data = msg['data']
                        symbol = stream_name.split('@')[0].upper()
                        if '@depth' in stream_name:
                            if symbol in self.orderbook_managers:
                                await self.orderbook_managers[symbol].process_depth_message(data)
                    # Обробка даних користувача (не мають ключа 'stream')
                    elif 'e' in msg and msg['e'] == 'ORDER_TRADE_UPDATE':
                        logger.info(f"[UserData] Order Update: {msg.get('o')}")
                        await self._handle_user_data_message(msg)

                except Exception as e:
                    logger.error(f"Критична помилка в головному слухачі вебсокетів: {e}. Перепідключення...")
                    await asyncio.sleep(5)

    async def _handle_user_data_message(self, msg: dict):
        order_data = msg.get('o', {})
        order_id = int(order_data.get('i'))
        symbol = order_data.get('s')
        status = order_data.get('X')
        order_type = order_data.get('ot')

        if status == 'FILLED' and order_type in ['LIMIT', 'MARKET'] and order_id in self.pending_sl_tp:
            logger.info(f"[UserData] Ордер на вхід {order_id} для {symbol} виконано.")
            pending_info = self.pending_sl_tp.pop(order_id)
            entry_price = float(order_data.get('ap'))
            position_side = pending_info['side']
            quantity = pending_info['quantity']
            sl_price = pending_info['sl']
            tp_price = pending_info['tp']
            sl_tp_side = SIDE_SELL if position_side == "Long" else SIDE_BUY

            try:
                logger.info(f"[{symbol}] Виставлення ордерів SL ({sl_price}) та TP ({tp_price}).")
                sl_order = await self.binance_client.create_stop_market_order(symbol, sl_tp_side, quantity, sl_price)
                tp_order = await self.binance_client.create_take_profit_market_order(symbol, sl_tp_side, quantity, tp_price)
                self.position_manager.set_position(symbol, position_side, quantity, entry_price, sl_price, tp_price, sl_order['orderId'], tp_order['orderId'])
                logger.success(f"[{symbol}] Позицію успішно відкрито з SL {sl_order['orderId']} та TP {tp_order['orderId']}.")
            except Exception as e:
                logger.error(f"[{symbol}] Не вдалося виставити SL/TP після входу. Запуск відкату позиції. Помилка: {e}")
                await self.binance_client.futures_create_order(symbol=symbol, side=sl_tp_side, type=ORDER_TYPE_MARKET, quantity=quantity)
            finally:
                if symbol in self.pending_symbols:
                    self.pending_symbols.remove(symbol)

        elif status in ['CANCELED', 'EXPIRED'] and order_type == 'LIMIT' and order_id in self.pending_sl_tp:
            logger.warning(f"[UserData] Лімітний ордер на вхід {order_id} для {symbol} було скасовано/прострочено.")
            del self.pending_sl_tp[order_id]
            if symbol in self.pending_symbols:
                self.pending_symbols.remove(symbol)

        elif status == 'EXPIRED' and order_type in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
            logger.info(f"[UserData] Тригерний ордер {order_id} для {symbol} виконано.")
            position = self.position_manager.get_position_by_symbol(symbol)
            if not position: return

            other_order_id = position.get('tp_order_id') if order_id == position.get('sl_order_id') else position.get('sl_order_id')
            if other_order_id:
                try:
                    await self.binance_client.cancel_order(symbol, other_order_id)
                    logger.success(f"[UserData] Успішно скасовано зустрічний ордер {other_order_id}.")
                except Exception as e:
                    if "Order does not exist" not in str(e): logger.error(f"[UserData] Не вдалося скасувати ордер {other_order_id}: {e}")
            
            self.position_manager.close_position(symbol)

    async def _main_loop(self):
        logger.info("Бот запущено. Вхід в основний цикл виконання...")
        while True:
            await asyncio.sleep(0.1)
            tasks = [executor.execute() for executor in self.trade_executors]
            await asyncio.gather(*tasks)

    async def start(self):
        logger.info("Запуск оркестратора...")
        async with BinanceClient() as client:
            self.binance_client = client
            self.bsm = BinanceSocketManager(self.binance_client.get_async_client(), max_queue_size=5000)
            valid_symbols = await self._setup_trading_environment()

            streams = []
            listen_key_data = await self.binance_client.client.futures_stream_get_listen_key()
            if isinstance(listen_key_data, dict) and 'listenKey' in listen_key_data:
                listen_key = listen_key_data['listenKey']
            elif isinstance(listen_key_data, str):
                listen_key = listen_key_data
            else:
                logger.error(f"[UserData] Не вдалося отримати коректний listen key. Відповідь: {listen_key_data}")
                return # Не можемо продовжити без ключа
            
            logger.info("[UserData] Отримано listen key для ф'ючерсів.")
            streams.append(listen_key)

            for config in self.strategies_configs:
                symbol = config['symbol']
                if symbol not in self.orderbook_managers:
                    self.orderbook_managers[symbol] = OrderBookManager(symbol)
                    streams.append(f"{symbol.lower()}@depth")
                    logger.info(f"OrderBookManager буде ініціалізовано для {symbol}")
                
                symbol_info = await self.binance_client.get_symbol_info(symbol)
                price_precision = int(symbol_info['pricePrecision'])
                qty_precision = int(symbol_info['quantityPrecision'])

                executor = TradeExecutor(
                    config, self.binance_client, self.position_manager, self, self.orderbook_managers[symbol],
                    self.trading_config['max_active_trades'], self.trading_config['leverage'],
                    price_precision, qty_precision, self.pending_symbols
                )
                self.trade_executors.append(executor)
                logger.info(f"TradeExecutor ініціалізовано для {config['strategy_id']}")

            if not self.trade_executors: 
                logger.warning("Не знайдено активних стратегій для запуску.")
                return

            logger.info("Ініціалізація біржових стаканів...")
            for symbol, obm in self.orderbook_managers.items():
                snapshot = await self.binance_client.get_futures_order_book(symbol=symbol, limit=1000)
                await obm.initialize_book(snapshot)
            logger.info("Всі біржові стакани ініціалізовано.")

            websocket_task = asyncio.create_task(self._start_websocket_listener(streams))
            main_loop_task = asyncio.create_task(self._main_loop())

            await asyncio.gather(websocket_task, main_loop_task)
