import asyncio
import json
import csv
import os
from loguru import logger
import pandas as pd
from binance.enums import *
import math

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

    def set_position(self, symbol: str, side: str, quantity: float, entry_price: float, stop_loss: float, take_profit: float, sl_order_id: int | None = None):
        if side not in ["Long", "Short"]:
            raise ValueError("Side must be either 'Long' or 'Short'")
        if quantity > 0:
            self._positions[symbol] = {
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "sl_order_id": sl_order_id
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
                 risk_per_trade_pct: float, max_active_trades: int, fee_pct: float, orderflow_manager: OrderflowManager | None = None):
        self.strategy_config = strategy_config
        self.strategy_id = strategy_config["strategy_id"]
        self.symbol = strategy_config["symbol"]
        self.ltf_manager = ltf_manager
        self.htf_manager = htf_manager
        self.binance_client = binance_client
        self.position_manager = position_manager
        self.risk_per_trade_pct = risk_per_trade_pct / 100
        self.max_active_trades = max_active_trades
        self.fee_pct = fee_pct # Store fee_pct
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
        ltf_df = await self.ltf_manager.get_current_klines()
        # The htf_df is no longer used by OrderFlowScalpingStrategy, but might be expected by other strategies.
        # For now, we fetch it but it won't be passed to OrderFlowScalpingStrategy.
        htf_df = await self.htf_manager.get_current_klines()
        if ltf_df.empty:
            return

        position = self.position_manager.get_position_by_symbol(self.symbol)

        if position:
            await self._check_open_position(position, ltf_df)
        else:
            # Check if max active trades reached before checking for signal
            if self.position_manager.get_positions_count() >= self.max_active_trades:
                logger.warning(f"[{self.strategy_id}] Max active trades reached. Skipping signal check for {self.symbol}.")
                return

            signal = self.strategy.check_signal(ltf_df)
            if signal:
                await self._handle_new_signal(signal, ltf_df)

    async def _check_open_position(self, position: dict, ltf_df: pd.DataFrame):
                    current_price = await self.binance_client.get_mark_price(symbol)        side = position['side']
        stop_loss = position['stop_loss']
        take_profit = position['take_profit']

        # Stop-loss is now handled by the exchange, so we only check for take-profit and reversal.
        if (side == "Long" and current_price >= take_profit) or (side == "Short" and current_price <= take_profit):
            await self._close_position(position, reason="Take-Profit")
            return
        
        # Reversal check requires the latest data
        signal = self.strategy.check_signal(ltf_df)
        if signal and signal['signal_type'] != side:
            await self._close_position(position, reason="Reversal")
            await self._open_position(signal, ltf_df)

    async def _handle_new_signal(self, signal: dict, ltf_df: pd.DataFrame):
        logger.info(f"[{self.strategy_id}] New entry signal: {signal['signal_type']} at {signal['entry_price']}")
        self._log_signal_to_csv(signal, ltf_df)
        await self._open_position(signal, ltf_df)

    async def _open_position(self, signal: dict, ltf_df: pd.DataFrame):
        price = signal['entry_price']
        side = SIDE_BUY if signal["signal_type"] == "Long" else SIDE_SELL
        try:
            sl_tp = self.strategy.calculate_sl_tp(price, signal["signal_type"], ltf_df, self.fee_pct)
            if sl_tp is None:  # Trade not profitable after fees
                logger.warning(f"[{self.strategy_id}] Trade is not profitable after fees or SL/TP could not be calculated. Skipping.")
                return
            stop_loss = sl_tp['stop_loss']
            take_profit = sl_tp['take_profit']
            balance = await self.binance_client.get_account_balance()
            open_positions = await self.binance_client.get_open_positions()
            
            used_margin = 0.0
            for pos in open_positions:
                logger.debug(f"Position data: {pos}")
                try:
                    mark_price = await self.binance_client.get_mark_price(pos['symbol'])
                except Exception as e:
                    logger.warning(f"Could not fetch mark price for {pos['symbol']}. Falling back to position data. Error: {e}")
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
            logger.success(f"[{self.strategy_id}] Order successful: {order['status']}")

            await asyncio.sleep(3)

            position_info = await self.binance_client.get_position_for_symbol(self.symbol)

            if position_info and float(position_info['positionAmt']) != 0:
                entry_price = float(position_info['entryPrice'])
                position_quantity = abs(float(position_info['positionAmt']))

                sl_side = SIDE_SELL if signal["signal_type"] == "Long" else SIDE_BUY
                sl_order = await self.binance_client.create_stop_market_order(self.symbol, sl_side, position_quantity, stop_loss)
                logger.info(f"[{self.strategy_id}] Placed stop-loss order: {sl_order['orderId']}")
                
                self.position_manager.set_position(self.symbol, signal["signal_type"], position_quantity, entry_price, stop_loss, take_profit, sl_order['orderId'])
                self._log_trade_to_csv(order, "OPEN")
            else:
                logger.warning(f"[{self.strategy_id}] Position for {self.symbol} not found or quantity is zero after creating order.")
        except Exception as e:
            logger.error(f"[{self.strategy_id}] Failed to open position: {e}")

    async def _close_position(self, position: dict, reason: str):
        quantity = position["quantity"]
        side = SIDE_SELL if position["side"] == "Long" else SIDE_BUY
        sl_order_id = position.get("sl_order_id")

        try:
            if sl_order_id:
                try:
                    await self.binance_client.cancel_order(self.symbol, sl_order_id)
                    logger.info(f"[{self.strategy_id}] Canceled stop-loss order: {sl_order_id}")
                except Exception as e:
                    logger.error(f"[{self.strategy_id}] Failed to cancel stop-loss order {sl_order_id}: {e}")
            
            logger.info(f"[{self.strategy_id}] Closing {position['side']} position due to {reason}: {quantity} {self.symbol}")
            order = await self.binance_client.create_order(self.symbol, side, ORDER_TYPE_MARKET, quantity)
            logger.success(f"[{self.strategy_id}] Close order successful: {order['status']}")
            closed_pos = self.position_manager.close_position(self.symbol)
            if closed_pos:
                self._log_trade_to_csv(order, f"CLOSE_{reason.upper()}", closed_pos['entry_price'])
        except Exception as e:
            logger.error(f"[{self.strategy_id}] Failed to close position: {e}")

    def _log_signal_to_csv(self, signal: dict, df: pd.DataFrame):
        pass

    def _log_trade_to_csv(self, order: dict, trade_type: str, entry_price: float | None = None):
        pass


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

    async def _start_websocket_listener(self, streams: list[str]):
        logger.info(f"Starting multiplexed websocket listener for streams: {streams}")
        async with self.bsm.multiplex_socket(streams) as socket:
            while True:
                msg = await socket.recv()
                if isinstance(msg, dict) and 'stream' in msg and 'data' in msg:
                    stream = msg['stream']
                    data = msg['data']
                    
                    # Kline stream
                    if '@kline_' in stream:
                        symbol, interval = stream.split('@kline_')
                        pair_key = f"{symbol.upper()}-{interval}"
                        if pair_key in self.data_managers:
                            await self.data_managers[pair_key].process_kline_message(data)
                    
                    # Aggregate trade stream
                    elif '@aggTrade' in stream:
                        symbol = stream.split('@')[0].upper()
                        if symbol in self.orderflow_managers:
                            await self.orderflow_managers[symbol].process_aggtrade_message(data)
                else:
                    logger.warning(f"Received unexpected message from websocket: {msg}")

    async def start(self):
        logger.info("Starting BotOrchestrator...")
        async with BinanceClient() as client:
            self.binance_client = client
            
            self.bsm = BinanceSocketManager(self.binance_client.get_async_client(), max_queue_size=5000)
            valid_symbols = await self._setup_trading_environment()

            risk_pct = self.trading_config['risk_per_trade_pct']
            max_trades = self.trading_config['max_active_trades']
            fee_pct = self.trading_config['fee_pct']

            logger.debug(f"[BotOrchestrator] Loaded trading_config: {self.trading_config}")
            logger.debug(f"[BotOrchestrator] fee_pct: {fee_pct}")

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

                executor = TradeExecutor(config, data_manager, data_manager, self.binance_client, 
                                         self.position_manager, risk_pct, max_trades, fee_pct, orderflow_manager)
                self.trade_executors.append(executor)
                logger.info(f"TradeExecutor initialized for {config['strategy_id']}")

            if not self.trade_executors:
                logger.warning("No enabled strategies found. Bot will not run.")
                return

            # Start the single websocket listener
            asyncio.create_task(self._start_websocket_listener(streams))

            logger.info("BotOrchestrator started. Entering main execution loop...")
            while True:
                tasks = [executor.execute() for executor in self.trade_executors]
                await asyncio.gather(*tasks)
                await asyncio.sleep(1)