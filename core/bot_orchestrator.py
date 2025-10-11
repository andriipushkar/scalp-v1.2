import asyncio
import json
import csv
import os
import websockets
from loguru import logger
import pandas as pd
from binance.enums import *
import math
from datetime import datetime

from binance import BinanceSocketManager

from core.binance_client import BinanceClient
from core.data_manager import DataManager
from strategies.scalping.order_flow_scalping import OrderFlowScalpingStrategy
from core.orderflow_manager import OrderflowManager

TRADE_SIGNALS_CSV = "logs/trade_signals.csv"
TRADE_HISTORY_CSV = "logs/trade_history.csv"


POSITIONS_STATE_FILE = "logs/positions_state.json"


class PositionManager:
    """Manages active positions for all symbols, with state persistence."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self._positions = self._load_state() # Key: symbol, Value: position_data

    def _load_state(self) -> dict:
        if not os.path.exists(self.state_file):
            return {}
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                # Filter out positions with 0 quantity
                valid_positions = {symbol: pos for symbol, pos in state.items() if pos.get('quantity', 0) > 0}
                logger.info(f"Loaded position state from {self.state_file}: {len(valid_positions)} positions.")
                return valid_positions
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading state from {self.state_file}: {e}. Starting fresh.")
            return {}

    def _save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self._positions, f, indent=4)
        except IOError as e:
            logger.error(f"Failed to save state to {self.state_file}: {e}")

    def get_position_by_symbol(self, symbol: str) -> dict | None:
        """Retrieves an open position for a given symbol."""
        return self._positions.get(symbol)

    def get_positions_count(self) -> int:
        # Count only positions with quantity > 0
        return len([pos for pos in self._positions.values() if pos.get('quantity', 0) > 0])

    def set_position(self, symbol: str, side: str, quantity: float, entry_price: float, stop_loss: float, take_profit: float, sl_order_id: int | None = None, tp_order_id: int | None = None):
        if side not in ["Long", "Short"]:
            raise ValueError("Side must be either 'Long' or 'Short'")
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
            logger.info(f"[PositionManager] Opened/Updated position for {symbol}: {self._positions[symbol]}")
            self._save_state()
        else:
            logger.warning(f"[PositionManager] Attempted to set position with quantity 0 for {symbol}. Ignoring.")


    def close_position(self, symbol: str):
        if symbol in self._positions:
            closed_pos = self._positions.pop(symbol)
            logger.info(f"[PositionManager] Closed position for {symbol}: {closed_pos}")
            self._save_state()
            return closed_pos
        return None


class TradeExecutor:
    """Executes trades based on signals and manages position state."""

    def __init__(self, strategy_config: dict, ltf_manager: DataManager, htf_manager: DataManager,
                 binance_client: BinanceClient, position_manager: PositionManager,
                 risk_per_trade_pct: float, max_active_trades: int, fee_pct: float, leverage: int,
                 price_precision: int, pending_symbols: set, orderflow_manager: OrderflowManager | None = None):
        self.strategy_config = strategy_config
        self.strategy_id = strategy_config["strategy_id"]
        self.symbol = strategy_config["symbol"]
        self.ltf_manager = ltf_manager
        self.htf_manager = htf_manager
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.risk_per_trade_pct = risk_per_trade_pct / 100
        self.max_active_trades = max_active_trades
        self.fee_pct = fee_pct
        self.leverage = leverage
        self.price_precision = price_precision
        self.pending_symbols = pending_symbols
        self.orderflow_manager = orderflow_manager
        self.strategy = self._initialize_strategy()

    def _initialize_strategy(self):
        strategy_name = self.strategy_id.split('_')[0]
        if strategy_name == "OrderFlowScalping":
            return OrderFlowScalpingStrategy(self.strategy_id, self.symbol, self.strategy_config["interval"],
                                             self.strategy_config["parameters"])
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

    async def execute(self):
        if self.symbol in self.pending_symbols:
            logger.trace(f"[{self.strategy_id}] Skipping execution for {self.symbol} as it is pending a transaction.")
            return

        ltf_df = await self.ltf_manager.get_current_klines()
        htf_df = await self.htf_manager.get_current_klines()
        if ltf_df.empty:
            return

        position = self.position_manager.get_position_by_symbol(self.symbol)

        if position:
            await self._check_for_reversal(position, ltf_df)
        else:
            if self.position_manager.get_positions_count() >= self.max_active_trades:
                logger.warning(f"[{self.strategy_id}] Max active trades reached. Skipping signal check for {self.symbol}.")
                return

            signal = self.strategy.check_signal(ltf_df)
            if signal:
                await self._handle_new_signal(signal, ltf_df)

    async def _check_for_reversal(self, position: dict, ltf_df: pd.DataFrame):
        signal = self.strategy.check_signal(ltf_df)
        if signal and signal['signal_type'] != position['side']:
            logger.info(f"[{self.strategy_id}] Reversal signal detected. Closing current position to open a new one.")
            await self._close_position(position, reason="Reversal")
            await self._open_position(signal, ltf_df)

    async def _handle_new_signal(self, signal: dict, ltf_df: pd.DataFrame):
        logger.info(f"[{self.strategy_id}] New entry signal: {signal['signal_type']} at {signal['entry_price']}")
        await self._open_position(signal, ltf_df)

    async def _open_position(self, signal: dict, ltf_df: pd.DataFrame):
        if self.symbol in self.pending_symbols:
            logger.warning(f"[{self.strategy_id}] Attempted to open position for {self.symbol} while it was already pending. Aborting.")
            return

        price = signal['entry_price']
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            self.pending_symbols.add(self.symbol)

            sl_tp = self.strategy.calculate_sl_tp(price, signal["signal_type"], ltf_df, self.fee_pct)
            if sl_tp is None:
                logger.warning(f"[{self.strategy_id}] Trade is not profitable after fees or SL/TP could not be calculated. Skipping.")
                return
            stop_loss = round(sl_tp['stop_loss'], self.price_precision)
            take_profit = round(sl_tp['take_profit'], self.price_precision)

            balance = await self.binance_client.get_account_balance()
            open_positions = await self.binance_client.get_open_positions()

            used_margin = 0.0
            for pos in open_positions:
                try:
                    mark_price = await self.binance_client.get_mark_price(pos['symbol'])
                except Exception as e:
                    logger.warning(f"Could not fetch mark price for {pos['symbol']}. Falling back to position data. Error: {e}")
                    mark_price = float(pos['markPrice'])
                position_value = float(pos['positionAmt']) * mark_price
                used_margin += abs(position_value) / float(pos['leverage'])

            available_capital = balance - used_margin
            if available_capital <= 0:
                logger.warning(f"[{self.strategy_id}] Insufficient available capital ({available_capital:.2f} USDT) for new trade. Skipping.")
                return

            risk_amount_usdt = available_capital * self.risk_per_trade_pct
            sl_distance = abs(price - stop_loss)
            if sl_distance == 0:
                logger.warning("Stop-loss distance is zero. Skipping trade.")
                return
            quantity = round(risk_amount_usdt / sl_distance, 6)

            symbol_info = await self.binance_client.get_symbol_info(self.symbol)
            step_size = float(symbol_info['filters'][1]['stepSize'])
            quantity = math.floor(quantity / step_size) * step_size
            quantity = float(f'{quantity:.8f}')

            if quantity == 0:
                logger.warning("Calculated quantity is zero after adjusting for precision. Skipping trade.")
                return

            logger.info(f"[{self.strategy_id}] Opening {signal['signal_type']} position: {quantity} {self.symbol} at {price}")
            order = await self.binance_client.create_order(self.symbol, side, ORDER_TYPE_MARKET, quantity)
            logger.success(f"[{self.strategy_id}] Market order successful: {order['status']}")

            try:
                await asyncio.sleep(2)
                position_info = await self.binance_client.get_position_for_symbol(self.symbol)

                if position_info and float(position_info['positionAmt']) != 0:
                    entry_price = float(position_info['entryPrice'])
                    position_quantity = abs(float(position_info['positionAmt']))
                    sl_tp_side = SIDE_SELL if signal["signal_type"] == "Long" else SIDE_BUY

                    logger.info(f"[{self.strategy_id}] Preparing to place SL/TP orders. Symbol: {self.symbol}, Side: {sl_tp_side}, Quantity: {position_quantity}, Entry: {entry_price}, SL: {stop_loss}, TP: {take_profit}")

                    sl_order = await self.binance_client.create_stop_market_order(self.symbol, sl_tp_side, position_quantity, stop_loss)
                    logger.info(f"[{self.strategy_id}] Placed stop-loss order: {sl_order['orderId']}")

                    tp_order = await self.binance_client.create_take_profit_market_order(self.symbol, sl_tp_side, position_quantity, take_profit)
                    logger.info(f"[{self.strategy_id}] Placed take-profit order: {tp_order['orderId']}")

                    self.position_manager.set_position(self.symbol, signal["signal_type"], position_quantity, entry_price, stop_loss, take_profit, sl_order['orderId'], tp_order['orderId'])
                    self._log_trade_to_csv(order, "OPEN", stop_loss, take_profit, entry_price=entry_price, stop_order_id=sl_order.get('orderId'), stop_status=sl_order.get('status'))
                else:
                    logger.warning(f"[{self.strategy_id}] Position for {self.symbol} not found or quantity is zero after creating order. Initiating rollback.")
                    await self._rollback_position(side, quantity)

            except Exception as e:
                logger.error(f"[{self.strategy_id}] Failed to place SL/TP orders or update state for {self.symbol}. Initiating rollback. Error: {e}")
                await self._rollback_position(side, quantity)

        except Exception as e:
            logger.error(f"[{self.strategy_id}] Failed to open position: {e}")
        finally:
            if self.symbol in self.pending_symbols:
                self.pending_symbols.remove(self.symbol)

    async def _rollback_position(self, side: str, quantity: float):
        """Closes a position immediately after opening due to an error in post-opening logic."""
        logger.critical(f"[{self.strategy_id}] CRITICAL: Rolling back position for {self.symbol} due to failure in post-opening logic.")
        close_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
        try:
            await self.binance_client.create_order(self.symbol, close_side, ORDER_TYPE_MARKET, quantity)
            logger.success(f"[{self.strategy_id}] Rollback successful. Position for {self.symbol} should be closed.")
        except Exception as e:
            logger.error(f"[{self.strategy_id}] CRITICAL: FAILED TO ROLLBACK POSITION for {self.symbol}. Manual intervention required. Error: {e}")

    async def _close_position(self, position: dict, reason: str):
        quantity = position["quantity"]
        side = SIDE_SELL if position["side"] == "Long" else SIDE_BUY
        sl_order_id = position.get("sl_order_id")
        tp_order_id = position.get("tp_order_id")

        try:
            for order_id in [sl_order_id, tp_order_id]:
                if order_id:
                    try:
                        await self.binance_client.cancel_order(self.symbol, order_id)
                        logger.info(f"[{self.strategy_id}] Canceled order: {order_id}")
                    except Exception as e:
                        if "Order does not exist" not in str(e):
                            logger.error(f"[{self.strategy_id}] Failed to cancel order {order_id}: {e}")

            logger.info(f"[{self.strategy_id}] Closing {position['side']} position due to {reason}: {quantity} {self.symbol}")
            order = await self.binance_client.create_order(self.symbol, side, ORDER_TYPE_MARKET, quantity)
            logger.success(f"[{self.strategy_id}] Close order successful: {order['status']}")
            closed_pos = self.position_manager.close_position(self.symbol)
            if closed_pos:
                entry_price = closed_pos.get('entry_price')
                self._log_trade_to_csv(order, f"CLOSE_{reason.upper()}", closed_pos['stop_loss'], closed_pos['take_profit'], entry_price=entry_price, stop_order_id=closed_pos.get('sl_order_id'))
        except Exception as e:
            logger.error(f"[{self.strategy_id}] Failed to close position: {e}")

    def _log_trade_to_csv(self, order: dict, trade_type: str, stop_loss: float, take_profit: float, entry_price: float | None = None, stop_order_id: int | None = None, stop_status: str | None = None):
        file_exists = os.path.isfile(TRADE_HISTORY_CSV)
        with open(TRADE_HISTORY_CSV, 'a', newline='') as f:
            writer = csv.writer(f)
            if not file_exists or os.path.getsize(TRADE_HISTORY_CSV) == 0:
                writer.writerow(["timestamp", "symbol", "side", "quantity", "price", "stop_loss", "take_profit", "leverage", "trade_type", "order_id", "stop_order_id", "stop_status"])

            price = entry_price if entry_price is not None else order.get('avgPrice', order.get('price'))

            writer.writerow([
                datetime.utcnow().isoformat(),
                self.symbol,
                order.get('side'),
                order.get('origQty'),
                price,
                stop_loss,
                take_profit,
                self.leverage,
                trade_type,
                order.get('orderId'),
                stop_order_id,
                stop_status
            ])


class BotOrchestrator:
    """Orchestrates the trading bot operations."""

    def __init__(self, strategies_config_path: str = "configs/strategies.json", trade_config_path: str = "configs/trading_config.json"):
        self.strategies_configs = self._load_json(strategies_config_path)
        self.trading_config = self._load_json(trade_config_path)
        self.binance_client: BinanceClient | None = None
        self.position_manager = PositionManager(POSITIONS_STATE_FILE)
        self.data_managers: dict[str, DataManager] = {}
        self.orderflow_managers: dict[str, OrderflowManager] = {}
        self.trade_executors: list[TradeExecutor] = []
        self.bsm: BinanceSocketManager | None = None
        self.pending_symbols = set()

    def _load_json(self, path: str) -> dict:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    async def _setup_trading_environment(self):
        logger.info("Setting up trading environment (leverage, margin type)...")
        unique_symbols = {s['symbol'] for s in self.strategies_configs if s.get("enabled")}
        valid_symbols = set()
        leverage = self.trading_config['leverage']
        margin_type = self.trading_config['margin_type']
        for symbol in unique_symbols:
            try:
                await self.binance_client.set_leverage(symbol, leverage)
                await self.binance_client.set_margin_type(symbol, margin_type)
                valid_symbols.add(symbol)
            except Exception as e:
                logger.error(f"Failed to setup environment for symbol {symbol}: {e}. Skipping this symbol.")
        logger.info("Trading environment setup complete.")
        return valid_symbols

    async def _start_market_data_listener(self, streams: list[str]):
        logger.info(f"Starting multiplexed websocket listener for streams: {streams}")
        async with self.bsm.multiplex_socket(streams) as socket:
            while True:
                try:
                    msg = await socket.recv()
                    if isinstance(msg, dict) and 'stream' in msg and 'data' in msg:
                        stream = msg['stream']
                        data = msg['data']

                        if '@kline_' in stream:
                            symbol, interval = stream.split('@kline_')
                            pair_key = f"{symbol.upper()}-{interval}"
                            if pair_key in self.data_managers:
                                await self.data_managers[pair_key].process_kline_message(data)

                        elif '@aggTrade' in stream:
                            symbol = stream.split('@')[0].upper()
                            if symbol in self.orderflow_managers:
                                await self.orderflow_managers[symbol].process_aggtrade_message(data)
                    else:
                        logger.warning(f"Received unexpected market data message: {msg}")
                except Exception as e:
                    logger.error(f"Error in market data listener: {e}. Reconnecting...")
                    await asyncio.sleep(5)

    async def _keep_listen_key_alive(self, listen_key: str):
        while True:
            try:
                await asyncio.sleep(30 * 60) # Keep alive every 30 minutes
                await self.binance_client.client.futures_stream_keepalive(listen_key)
                logger.info("[UserData] Futures listen key kept alive.")
            except Exception as e:
                logger.error(f"[UserData] Failed to keep listen key alive: {e}. It might expire.")
                break

    async def _start_user_data_listener(self):
        logger.info("Starting futures user data websocket listener...")
        try:
            listen_key_data = await self.binance_client.client.futures_stream_get_listen_key()
            listen_key = listen_key_data['listenKey']
            logger.info("[UserData] Got futures listen key.")

            keepalive_task = asyncio.create_task(self._keep_listen_key_alive(listen_key))

            url = f"wss://fstream.binance.com/ws/{listen_key}"
            
            async with websockets.connect(url) as socket:
                logger.info("[UserData] Connected to futures user data stream.")
                while True:
                    msg_raw = await socket.recv()
                    msg = json.loads(msg_raw)
                    if isinstance(msg, dict) and 'e' in msg:
                        await self._handle_user_data_message(msg)
                    else:
                        logger.warning(f"[UserData] Received unexpected message: {msg}")

        except Exception as e:
            logger.error(f"Error in user data listener: {e}. Will attempt to reconnect...")
            await asyncio.sleep(5)
            asyncio.create_task(self._start_user_data_listener())

    async def _handle_user_data_message(self, msg: dict):
        if msg.get('e') == 'ORDER_TRADE_UPDATE':
            # --- TEMPORARY DEBUG LOG ---
            logger.critical(f"[UserData] RAW_ORDER_UPDATE: {msg}")
            # -------------------------
            order_data = msg.get('o', {})
            symbol = order_data.get('s')
            status = order_data.get('X')
            order_id = order_data.get('i')

            if status == 'EXPIRED' and order_data.get('ot') in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                logger.info(f"[UserData] Received EXPIRED status for trigger order {order_id} on {symbol}. Assuming it was executed.")

                position = self.position_manager.get_position_by_symbol(symbol)
                if not position:
                    logger.info(f"[UserData] Position for {symbol} not found or already closed. Ignoring FILLED event.")
                    return

                sl_order_id = position.get('sl_order_id')
                tp_order_id = position.get('tp_order_id')

                other_order_to_cancel = None
                reason = ""
                if order_id == sl_order_id:
                    other_order_to_cancel = tp_order_id
                    reason = "STOP_LOSS"
                    logger.info(f"[UserData] Stop-loss filled for {symbol}. Canceling take-profit order {other_order_to_cancel}.")
                elif order_id == tp_order_id:
                    other_order_to_cancel = sl_order_id
                    reason = "TAKE_PROFIT"
                    logger.info(f"[UserData] Take-profit filled for {symbol}. Canceling stop-loss order {other_order_to_cancel}.")

                if other_order_to_cancel:
                    try:
                        await self.binance_client.cancel_order(symbol, other_order_to_cancel)
                        logger.success(f"[UserData] Successfully canceled order {other_order_to_cancel} for {symbol}.")
                    except Exception as e:
                        if "Order does not exist" not in str(e):
                            logger.error(f"[UserData] Failed to cancel order {other_order_to_cancel} for {symbol}: {e}")

                closed_pos = self.position_manager.close_position(symbol)
                if closed_pos:
                    executor = next((exc for exc in self.trade_executors if exc.symbol == symbol), None)
                    if executor:
                        log_order = {
                            'side': order_data.get('S'), 'origQty': order_data.get('q'),
                            'avgPrice': order_data.get('ap'), 'price': order_data.get('p'),
                            'orderId': order_id
                        }
                        executor._log_trade_to_csv(
                            log_order, f"CLOSE_{reason}", closed_pos['stop_loss'],
                            closed_pos['take_profit'], entry_price=closed_pos.get('entry_price'),
                            stop_order_id=closed_pos.get('sl_order_id')
                        )
                    else:
                        logger.error(f"[UserData] Could not find TradeExecutor for {symbol} to log trade closure.")

    async def _main_loop(self):
        logger.info("BotOrchestrator started. Entering main execution loop...")
        while True:
            tasks = [executor.execute() for executor in self.trade_executors]
            await asyncio.gather(*tasks)
            await asyncio.sleep(1)

    async def start(self):
        logger.info("Starting BotOrchestrator...")
        async with BinanceClient() as client:
            self.binance_client = client

            self.bsm = BinanceSocketManager(self.binance_client.get_async_client(), max_queue_size=5000)
            valid_symbols = await self._setup_trading_environment()

            risk_pct = self.trading_config['risk_per_trade_pct']
            max_trades = self.trading_config['max_active_trades']
            fee_pct = self.trading_config['fee_pct']
            leverage = self.trading_config['leverage']

            streams = []
            for config in self.strategies_configs:
                if not config.get("enabled", False) or config['symbol'] not in valid_symbols:
                    continue

                symbol = config['symbol']
                interval = config['interval']
                strategy_name = config['strategy_id'].split('_')[0]

                orderflow_manager = None
                if strategy_name == "OrderFlowScalping":
                    if symbol not in self.orderflow_managers:
                        ofm = OrderflowManager(symbol=symbol)
                        self.orderflow_managers[symbol] = ofm
                        streams.append(f"{symbol.lower()}@aggTrade")
                        logger.info(f"OrderflowManager initialized for {symbol}")
                    orderflow_manager = self.orderflow_managers[symbol]

                pair_key = f"{symbol}-{interval}"
                if pair_key not in self.data_managers:
                    lookback = 100
                    if "parameters" in config and "climax_bar_lookback" in config["parameters"]:
                        lookback = config["parameters"]["climax_bar_lookback"] + 5

                    dm = DataManager(self.binance_client, orderflow_manager, symbol, interval, lookback)
                    await dm.load_historical_data()
                    self.data_managers[pair_key] = dm
                    streams.append(f"{symbol.lower()}@kline_{interval}")
                    logger.info(f"DataManager initialized for {pair_key}")

                data_manager = self.data_managers[pair_key]

                symbol_info = await self.binance_client.get_symbol_info(symbol)
                price_precision = int(symbol_info['pricePrecision'])

                executor = TradeExecutor(config, data_manager, data_manager, self.binance_client,
                                         self.position_manager, risk_pct, max_trades, fee_pct, leverage,
                                         price_precision, self.pending_symbols, orderflow_manager)
                self.trade_executors.append(executor)
                logger.info(f"TradeExecutor initialized for {config['strategy_id']}")

            if not self.trade_executors:
                logger.warning("No enabled strategies found. Bot will not run.")
                return

            market_data_task = asyncio.create_task(self._start_market_data_listener(streams))
            user_data_task = asyncio.create_task(self._start_user_data_listener())
            main_loop_task = asyncio.create_task(self._main_loop())

            await asyncio.gather(market_data_task, user_data_task, main_loop_task)