from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    """
    Абстрактний базовий клас для всіх торгових стратегій.
    Визначає інтерфейс, якому повинна відповідати кожна стратегія.
    """

    def __init__(self, strategy_id: str, symbol: str):
        """
        Ініціалізує базову стратегію.

        :param strategy_id: Унікальний ідентифікатор екземпляру стратегії.
        :param symbol: Торговий символ (напр., 'BTCUSDT').
        """
        self.strategy_id = strategy_id
        self.symbol = symbol

    @abstractmethod
    def check_signal(self, *args, **kwargs) -> dict | None:
        """
        Перевіряє наявність торгового сигналу (Long/Short).
        Приймає довільні аргументи, оскільки різні стратегії можуть
        вимагати різні дані (напр., свічки, стакан, індикатори).

        :return: Словник з деталями сигналу або None, якщо сигналу немає.
        """
        pass

    @abstractmethod
    def calculate_sl_tp(self, entry_price: float, signal_type: str, **kwargs) -> dict:
        """
        Розраховує рівні Stop-Loss та Take-Profit.
        Приймає довільні аргументи для гнучкості.

        :param entry_price: Ціна входу.
        :param signal_type: Тип сигналу ('Long' або 'Short').
        :return: Словник з ключами 'stop_loss' та 'take_profit'.
        """
        pass