from datetime import datetime
import pandas as pd
from loguru import logger
from binance import AsyncClient

from core.binance_client import BinanceClient
from strategies.base_strategy import BaseStrategy

class Backtester:
    """
    Керує процесом бектестінгу стратегії на історичних даних.
    """

    def __init__(self, strategy: BaseStrategy, symbol: str, start_date: str, end_date: str, initial_balance: float = 10000.0, leverage: int = 10, log_enabled: bool = True):
        self.strategy = strategy
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.leverage = leverage
        self.position = None
        self.trades = []
        self.binance_client = BinanceClient()
        self.log_enabled = log_enabled

    async def _fetch_data(self) -> pd.DataFrame:
        if self.log_enabled:
            logger.info(f"Завантаження історичних даних для {self.symbol}...")
        async with self.binance_client as client:
            klines = await client.get_historical_klines(
                symbol=self.symbol,
                interval=AsyncClient.KLINE_INTERVAL_1HOUR,
                start_str=self.start_date,
                end_str=self.end_date
            )
            if self.log_enabled:
                logger.success(f"Завантажено {len(klines)} свічок.")
            return klines

    async def run(self) -> dict:
        if self.log_enabled:
            logger.info(f"--- Запуск бектестінгу для {self.symbol} ---")
            logger.info(f"Період: {self.start_date} - {self.end_date}")
            logger.info(f"Початковий баланс: ${self.initial_balance:,.2f}")

        data = await self._fetch_data()
        if data.empty:
            if self.log_enabled:
                logger.error("Не вдалося завантажити дані. Бектестінг зупинено.")
            return {}

        for i in range(1, len(data)):
            current_data = data.iloc[:i+1].copy() # Use .copy() to avoid SettingWithCopyWarning
            current_price = current_data.iloc[-1]['close']
            
            if self.position:
                if self.position['side'] == 'Long' and (current_price <= self.position['sl'] or current_price >= self.position['tp']):
                    self._close_position(current_price)
                elif self.position['side'] == 'Short' and (current_price >= self.position['sl'] or current_price <= self.position['tp']):
                    self._close_position(current_price)

            if not self.position:
                signal = self.strategy.check_signal(current_data)
                if signal:
                    self._open_position(signal)
        
        if self.position:
            self._close_position(data.iloc[-1]['close'])

        if self.log_enabled:
            logger.info("--- Бектестінг завершено ---")
            
        return self._calculate_performance()

    def _open_position(self, signal: dict):
        entry_price = signal['price']
        sl_tp = self.strategy.calculate_sl_tp(entry_price=entry_price, signal_type=signal['signal_type'])
        
        trade_size_usd = self.balance * 0.1 * self.leverage
        quantity = trade_size_usd / entry_price

        self.position = {
            'side': signal['signal_type'],
            'quantity': quantity,
            'entry_price': entry_price,
            'sl': sl_tp['stop_loss'],
            'tp': sl_tp['take_profit']
        }
        if self.log_enabled:
            logger.info(f"Відкрито {self.position['side']} позицію: {quantity:.4f} {self.symbol} за ціною {entry_price:.2f}")

    def _close_position(self, exit_price: float):
        pnl = (exit_price - self.position['entry_price']) * self.position['quantity']
        if self.position['side'] == 'Short':
            pnl = -pnl
        
        self.balance += pnl
        self.trades.append({
            'entry_price': self.position['entry_price'],
            'exit_price': exit_price,
            'side': self.position['side'],
            'pnl': pnl
        })
        if self.log_enabled:
            logger.info(f"Закрито {self.position['side']} позицію за ціною {exit_price:.2f}. PnL: ${pnl:,.2f}. Баланс: ${self.balance:,.2f}")
        self.position = None

    def _calculate_performance(self) -> dict:
        if not self.trades:
            if self.log_enabled:
                logger.warning("Не було жодної угоди. Аналіз неможливий.")
            return {}

        total_pnl = self.balance - self.initial_balance
        total_trades = len(self.trades)
        wins = [t for t in self.trades if t['pnl'] > 0]
        losses = [t for t in self.trades if t['pnl'] <= 0]
        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        
        avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t['pnl'] for t in wins) / sum(t['pnl'] for t in losses)) if losses and sum(t['pnl'] for t in losses) != 0 else float('inf')

        results = {
            "total_pnl": total_pnl,
            "total_return_pct": total_pnl / self.initial_balance,
            "total_trades": total_trades,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor
        }

        if self.log_enabled:
            logger.info("--- Результати бектестінгу ---")
            logger.info(f"Загальний PnL: ${total_pnl:,.2f}")
            logger.info(f"Загальний дохід: {results['total_return_pct']:.2%}")
            logger.info(f"Всього угод: {total_trades}")
            logger.info(f"Прибуткових угод: {len(wins)} ({win_rate:.2%})")
            logger.info(f"Середній прибуток: ${avg_win:,.2f}")
            logger.info(f"Середній збиток: ${avg_loss:,.2f}")
            logger.info(f"Профіт-фактор: {profit_factor:.2f}")
        
        return results
