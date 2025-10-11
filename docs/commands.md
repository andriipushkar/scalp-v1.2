# QuantumTrader Commands

This file contains the main commands for running and testing the trading bot.

## Backtesting

The `backtest.py` script is used to test strategies on historical data.

### Running a Backtest for a Specific Time Range

Use the `--start-datetime` and `--end-datetime` arguments to specify a precise window for the backtest. This is useful for quick, targeted tests.

**Command:**
```bash
python3 backtest.py --start-datetime YYYY-MM-DDTHH:MM:SS --end-datetime YYYY-MM-DDTHH:MM:SS
```

**Example (running a 3-hour test):**
```bash
python3 backtest.py --start-datetime 2025-10-07T14:00:00 --end-datetime 2025-10-07T17:00:00
```

### Configuration

The backtester uses the `configs/strategies.json` file. You can enable/disable strategies, change parameters, and toggle the CVD filter (`"cvd_filter_enabled": true/false`) in this file before running a backtest.

---

## Live Trading

To run the bot for live trading with real money, use the `main.py` script. The bot will use the strategies enabled in `configs/strategies.json`.

**Command:**
```bash
python3 main.py
```

### Configuration for Live Trading

- **Enable/Disable Strategies:** Before running, make sure only the strategies you want to trade live are set to `"enabled": true` in `configs/strategies.json`.
- **Tune Parameters:** Ensure all parameters, especially `cvd_filter_enabled`, are set to your desired values for live trading. It is recommended to run with the CVD filter enabled (`true`).
- **API Keys:** Make sure your `BINANCE_API_KEY` and `BINANCE_API_SECRET` are correctly set in your environment or `.env` file.
