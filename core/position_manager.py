import json
import os
from loguru import logger

class PositionManager:
    """
    Керує станом активних торгових позицій.

    Відповідає за:
    1. Збереження інформації про відкриті позиції у файл.
    2. Завантаження стану позицій при запуску для відновлення роботи.
    3. Надання методів для створення, оновлення та закриття позицій.
    """

    def __init__(self, state_file: str):
        """
        Ініціалізує менеджер позицій.

        Args:
            state_file (str): Шлях до файлу, де зберігається стан позицій (напр., 'positions.json').
        """
        self.state_file = state_file
        self._positions = self._load_state()

    def _load_state(self) -> dict:
        """Завантажує стан позицій з файлу. Якщо файл не існує або пошкоджений, повертає порожній словник."""
        if not os.path.exists(self.state_file):
            logger.info(f"Файл стану '{self.state_file}' не знайдено. Починаємо з чистого стану.")
            return {}
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
                # Фільтруємо "пусті" або некоректні записи
                valid_positions = {symbol: pos for symbol, pos in state.items() if pos and pos.get('quantity', 0) > 0}
                logger.info(f"Завантажено стан {len(valid_positions)} активних позицій з '{self.state_file}'.")
                return valid_positions
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Помилка завантаження стану з '{self.state_file}': {e}. Починаємо з чистого стану.")
            return {}

    def _save_state(self):
        """Зберігає поточний стан позицій у файл."""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self._positions, f, indent=4)
        except IOError as e:
            logger.error(f"Не вдалося зберегти стан у '{self.state_file}': {e}")

    def get_position_by_symbol(self, symbol: str) -> dict | None:
        """Повертає інформацію про позицію для вказаного символу, якщо вона існує."""
        return self._positions.get(symbol)

    def get_all_positions(self) -> dict:
        """Повертає словник з усіма активними позиціями."""
        return self._positions

    def get_positions_count(self) -> int:
        """Повертає кількість активних позицій."""
        return len(self._positions)

    def set_position(self, symbol: str, side: str, quantity: float, entry_price: float, stop_loss: float, take_profit: float, sl_order_id: int | None = None, tp_order_id: int | None = None):
        """
        Створює нову або оновлює існуючу позицію.

        Args:
            symbol (str): Торговий символ.
            side (str): Напрямок позиції ('Long' або 'Short').
            quantity (float): Кількість.
            entry_price (float): Ціна входу.
            stop_loss (float): Ціна Stop-Loss.
            take_profit (float): Ціна Take-Profit.
            sl_order_id (int, optional): ID Stop-Loss ордеру на біржі.
            tp_order_id (int, optional): ID Take-Profit ордеру на біржі.
        """
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

    def close_position(self, symbol: str) -> dict | None:
        """
        Закриває позицію для вказаного символу, видаляючи її зі стану.
        
        Returns:
            dict | None: Словник з даними закритої позиції або None, якщо позиції не було.
        """
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

    async def reconcile_with_exchange(self, binance_client):
        """
        Звіряє стан позицій з файлу з реальними позиціями на біржі.
        """
        logger.info("Початок звірки стану позицій з біржею...")
        try:
            exchange_positions_raw = await binance_client.get_open_positions()
            exchange_positions = {p['symbol']: p for p in exchange_positions_raw}
        except Exception as e:
            logger.error(f"Не вдалося отримати відкриті позиції з біржі для звірки: {e}")
            # У разі помилки, краще не довіряти файлу стану і почати з чистого листа
            self._positions = {}
            self._save_state()
            return

        state_symbols = set(self._positions.keys())
        exchange_symbols = set(exchange_positions.keys())

        # 1. Позиції, що є в файлі, але НЕ на біржі (застарілі)
        stale_symbols = state_symbols - exchange_symbols
        for symbol in stale_symbols:
            logger.warning(f"[Звірка] Позиція для {symbol} є в файлі стану, але відсутня на біржі. Видалення застарілого стану.")
            self._positions.pop(symbol, None)

        # 2. Позиції, що є на біржі, але НЕ в файлі (напр. відкриті вручну)
        manual_symbols = exchange_symbols - state_symbols
        for symbol in manual_symbols:
            logger.warning(f"[Звірка] На біржі знайдено невідстежувану позицію для {symbol}. Бот не буде нею керувати.")

        # 3. Позиції, що є і там, і там (перевірка коректності)
        common_symbols = state_symbols.intersection(exchange_symbols)
        for symbol in common_symbols:
            state_pos = self._positions[symbol]
            exchange_pos = exchange_positions[symbol]
            exchange_qty = float(exchange_pos['positionAmt'])
            
            # Перевіряємо напрямок позиції
            state_side = state_pos['side']
            is_long = exchange_qty > 0
            
            side_matches = (state_side == 'Long' and is_long) or (state_side == 'Short' and not is_long)
            if not side_matches:
                logger.error(f"[Звірка] НЕВІДПОВІДНІСТЬ НАПРЯМКУ для {symbol}! Файл: {state_side}, Біржа: {'Long' if is_long else 'Short'}. Видаляємо позицію зі стану.")
                self._positions.pop(symbol, None)
                continue

            # Оновлюємо кількість про всяк випадок
            if state_pos['quantity'] != abs(exchange_qty):
                logger.warning(f"[Звірка] Оновлення кількості для {symbol} з {state_pos['quantity']} до {abs(exchange_qty)}.")
                state_pos['quantity'] = abs(exchange_qty)

        logger.info("Звірку стану позицій завершено.")
        self._save_state()