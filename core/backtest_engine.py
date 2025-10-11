from loguru import logger
import pandas as pd
import numpy as np

# Import necessary components from the live system
from core.binance_client import BinanceClient
from analysis.technical_analyzer import TechnicalAnalyzer
from strategies.scalping.order_flow_scalping import OrderFlowScalpingStrategy

class BacktestPortfolio:
    """Manages the portfolio, trades, and statistics for a backtest."""

    def __init__(self, initial_capital=1000.0, fee_pct=0.04, risk_per_trade_pct=1.0):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.fee_pct = fee_pct / 100
        self.risk_per_trade_pct = risk_per_trade_pct / 100
        
        self.open_position = None
        self.trade_history = []
        self.equity_curve = []
        logger.info(f"Portfolio initialized with {initial_capital:.2f} USDT and {risk_per_trade_pct:.2f}% risk per trade.")

    def open_trade(self, timestamp, side, price, stop_loss, take_profit):
        if self.open_position:
            return

        # Calculate position size based on risk
        risk_amount = self.current_capital * self.risk_per_trade_pct
        sl_distance = abs(price - stop_loss)
        if sl_distance == 0:
            logger.warning("Stop-loss distance is zero. Cannot calculate position size.")
            return
        quantity = risk_amount / sl_distance

        fee = price * quantity * self.fee_pct
        self.current_capital -= fee
        
        self.open_position = {
            "side": side,
            "entry_price": price,
            "quantity": quantity,
            "entry_time": timestamp,
            "stop_loss": stop_loss,
            "take_profit": take_profit
        }
        self.equity_curve.append((timestamp, self.current_capital))
        # logger.debug(f"{timestamp} | OPEN {side} at {price:.2f}")

    def close_trade(self, timestamp, price, reason):
        if not self.open_position:
            return

        quantity = self.open_position["quantity"]
        entry_price = self.open_position["entry_price"]
        side = self.open_position["side"]

        fee = price * quantity * self.fee_pct
        self.current_capital -= fee

        pnl = (price - entry_price) * quantity if side == "Long" else (entry_price - price) * quantity
        self.current_capital += pnl

        self.trade_history.append({
            "entry_time": self.open_position["entry_time"],
            "exit_time": timestamp,
            "side": side,
            "entry_price": entry_price,
            "exit_price": price,
            "pnl": pnl,
            "reason": reason
        })
        self.open_position = None
        self.equity_curve.append((timestamp, self.current_capital))
        # logger.debug(f"{timestamp} | CLOSE {side} at {price:.2f} | PnL: {pnl:.2f} | Reason: {reason}")

    def generate_report(self):
        logger.info("--- Backtest Finished ---")
        if not self.trade_history:
            logger.warning("No trades were executed.")
            return

        total_trades = len(self.trade_history)
        wins = [t for t in self.trade_history if t["pnl"] > 0]
        losses = [t for t in self.trade_history if t["pnl"] <= 0]
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0
        
        total_net_pnl = self.current_capital - self.initial_capital
        gross_profit = sum(t['pnl'] for t in wins)
        gross_loss = abs(sum(t['pnl'] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        avg_win = gross_profit / len(wins) if wins else 0
        avg_loss = gross_loss / len(losses) if losses else 0

        print("\n--- Backtest Performance Report ---")
        print(f"Period: {self.trade_history[0]['entry_time'].date()} to {self.trade_history[-1]['exit_time'].date()}")
        print(f"Initial Capital: {self.initial_capital:.2f} USDT")
        print(f"Final Capital:   {self.current_capital:.2f} USDT")
        print(f"Total Net PnL:     {total_net_pnl:.2f} USDT ({total_net_pnl/self.initial_capital*100:.2f}%)")
        print("-------------------------------------")
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Profit Factor: {profit_factor:.2f}")
        print(f"Average Win: {avg_win:.2f} USDT")
        print(f"Average Loss: {avg_loss:.2f} USDT")
        print("-------------------------------------\n")


class BacktestEngine:
    """The main engine for running backtests."""

    def __init__(self, strategy_config: dict, backtest_config: dict):
        self.strategy_config = strategy_config
        self.backtest_config = backtest_config
        self.portfolio = BacktestPortfolio(risk_per_trade_pct=backtest_config.get('risk_per_trade_pct', 1.0))
        self.strategy = self._initialize_strategy()
        logger.info("BacktestEngine initialized.")

    def _initialize_strategy(self):
        strategy_name = self.strategy_config["strategy_id"].split('_')[0]
        if strategy_name == "EMARibbonScalping":
            return EMARibbonScalping(self.strategy_config["strategy_id"], self.backtest_config["symbol"], 
                                     self.strategy_config["interval"], self.strategy_config["parameters"])
        elif strategy_name == "OrderFlowScalping":
            return OrderFlowScalpingStrategy(self.strategy_config["strategy_id"], self.backtest_config["symbol"], 
                                             self.strategy_config["interval"], self.strategy_config["parameters"])
        else:
            raise ValueError(f"Unknown strategy: {strategy_name}")

    async def _prepare_data(self, client: BinanceClient, symbol: str, interval: str, start_date: str, end_date: str):
        """Fetches and prepares all necessary data for the backtest."""
        logger.info(f"Fetching historical k-lines for {symbol}...")
        ltf_df = await client.get_historical_klines_for_range(symbol, interval, start_date, end_date)
        logger.info(f"Fetched {len(ltf_df)} klines.")

        if ltf_df.empty:
            return pd.DataFrame()

        ltf_df.set_index('open_time', inplace=True)

        # If it's an order flow strategy, fetch trades and calculate CVD
        if isinstance(self.strategy, OrderFlowScalpingStrategy):
            trades = await client.get_historical_agg_trades(symbol, start_date, end_date)
            if not trades:
                logger.warning("Could not fetch aggregate trades for CVD calculation. Proceeding without it.")
                ltf_df['cvd'] = 0.0
                return ltf_df

            trades_df = pd.DataFrame(trades)
            trades_df['T'] = pd.to_datetime(trades_df['T'], unit='ms')
            trades_df['q'] = trades_df['q'].astype(float)
            
            # Calculate delta for each trade
            trades_df['delta'] = trades_df.apply(lambda row: row['q'] if not row['m'] else -row['q'], axis=1)
            
            # Resample delta to the k-line interval and calculate cumulative sum
            cvd_series = trades_df.set_index('T')['delta'].resample(interval).sum().cumsum()
            
            # Merge CVD into the main dataframe
            ltf_df = ltf_df.join(cvd_series.rename('cvd'))
            ltf_df['cvd'].fillna(method='ffill', inplace=True)
            ltf_df['cvd'].fillna(0, inplace=True) # Fill any remaining NaNs at the beginning
            logger.info("Successfully calculated and merged historical CVD.")

        return ltf_df

    async def run(self):
        logger.info("Starting backtest run...")
        symbol = self.backtest_config["symbol"]
        interval = self.strategy_config["interval"]
        start_date = self.backtest_config["start_date"]
        end_date = self.backtest_config["end_date"]

        async with BinanceClient() as client:
            main_df = await self._prepare_data(client, symbol, interval, start_date, end_date)

        if main_df.empty:
            logger.error("Not enough data to run backtest. Aborting.")
            return

        # Main backtest loop
        for i in range(1, len(main_df)):
            df_slice = main_df.iloc[0:i]
            current_candle = df_slice.iloc[-1]
            current_time = current_candle.name # Use index name (open_time)

            # If a position is open, check for exit conditions
            if self.portfolio.open_position:
                pos = self.portfolio.open_position
                if pos['side'] == 'Long':
                    if current_candle['low'] <= pos['stop_loss']:
                        self.portfolio.close_trade(current_time, pos['stop_loss'], "Stop-Loss")
                        continue
                    if current_candle['high'] >= pos['take_profit']:
                        self.portfolio.close_trade(current_time, pos['take_profit'], "Take-Profit")
                        continue
                elif pos['side'] == 'Short':
                    if current_candle['high'] >= pos['stop_loss']:
                        self.portfolio.close_trade(current_time, pos['stop_loss'], "Stop-Loss")
                        continue
                    if current_candle['low'] <= pos['take_profit']:
                        self.portfolio.close_trade(current_time, pos['take_profit'], "Take-Profit")
                        continue

            # If no position is open, check for a new signal
            if not self.portfolio.open_position:
                # Prepare arguments for check_signal
                signal_args = {"df": df_slice}
                if isinstance(self.strategy, OrderFlowScalpingStrategy):
                    signal_args["current_cvd"] = current_candle['cvd']
                else: # For old strategies that might expect htf_df
                    signal_args["htf_df"] = pd.DataFrame() # Pass empty df

                signal = self.strategy.check_signal(**signal_args)
                
                if signal:
                    try:
                        sl_tp = self.strategy.calculate_sl_tp(signal['entry_price'], signal['signal_type'], df_slice, self.portfolio.fee_pct)
                        self.portfolio.open_trade(current_time, signal['signal_type'], signal['entry_price'], 
                                                sl_tp['stop_loss'], sl_tp['take_profit'])
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Could not calculate SL/TP for signal at {current_time}: {e}")

        # At the end of the backtest, close any open position
        if self.portfolio.open_position:
            last_candle = main_df.iloc[-1]
            self.portfolio.close_trade(last_candle.name, last_candle['close'], "End of Backtest")

        self.portfolio.generate_report()
