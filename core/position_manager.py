import json
import os
from loguru import logger

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

    def update_orders(self, symbol: str, sl_order_id: int | None = None, tp_order_id: int | None = None):
        """Оновлює ID стоп-лос та/або тейк-профіт ордерів для існуючої позиції."""
        position = self.get_position_by_symbol(symbol)
        if not position:
            logger.warning(f"[{symbol}] Спроба оновити ордери для неіснуючої позиції.")
            return

        if sl_order_id is not None:
            position['sl_order_id'] = sl_order_id
            logger.info(f"[{symbol}] Оновлено ID SL ордера на {sl_order_id}.")
        
        if tp_order_id is not None:
            position['tp_order_id'] = tp_order_id
            logger.info(f"[{symbol}] Оновлено ID TP ордера на {tp_order_id}.")

        self._save_state()
