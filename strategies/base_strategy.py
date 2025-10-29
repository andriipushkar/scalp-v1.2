from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
import pandas as pd

from core.orderbook_manager import OrderBookManager

if TYPE_CHECKING:
    from core.binance_client import BinanceClient


class BaseStrategy(ABC):
    """
    Абстрактний базовий клас для всіх торгових стратегій.
    
    Він визначає загальний інтерфейс, якому повинна відповідати кожна конкретна стратегія.
    Це забезпечує уніфікований спосіб взаємодії `TradeExecutor` з різними торговими логіками.
    """

    def __init__(self, strategy_id: str, symbol: str, params: dict):
        """
        Ініціалізує базову стратегію.

        Args:
            strategy_id (str): Унікальний ідентифікатор екземпляру стратегії (напр., 'LiquidityHunting_BTCUSDT').
            symbol (str): Торговий символ, до якого прив'язана стратегія (напр., 'BTCUSDT').
            params (dict): Словник з параметрами, специфічними для цієї стратегії.
        """
        self.strategy_id = strategy_id
        self.symbol = symbol
        self.params = params

    @abstractmethod
    async def check_signal(self, order_book_manager: OrderBookManager, binance_client: 'BinanceClient') -> dict | None:
        """
        Абстрактний метод для перевірки наявності торгового сигналу (Long або Short).
        
        Кожна дочірня стратегія повинна реалізувати цей метод, аналізуючи
        надані ринкові дані для прийняття рішення про вхід.

        Args:
            order_book_manager (OrderBookManager): Менеджер стакану для поточного символу.
            binance_client (BinanceClient): Клієнт Binance для доступу до додаткових даних, напр. свічок.

        Returns:
            dict | None: Словник з деталями сигналу, якщо він знайдений, інакше None.
                         Приклад словника: {'signal_type': 'Long', 'wall_price': 60000.5}
        """
        pass

    @abstractmethod
    def calculate_sl_tp(self, entry_price: float, signal_type: str, order_book_manager: OrderBookManager, tick_size: float) -> dict | None:
        """
        Абстрактний метод для розрахунку рівнів Stop-Loss та Take-Profit.

        Args:
            entry_price (float): Фактична ціна входу в позицію.
            signal_type (str): Тип сигналу ('Long' або 'Short').
            order_book_manager (OrderBookManager): Менеджер стакану для аналізу ринкової ситуації.
            tick_size (float): Мінімальний крок ціни для символу.

        Returns:
            dict | None: Словник з ключами 'stop_loss' та 'take_profit' або None, якщо розрахунок неможливий.
        """
        pass

    def analyze_and_adjust(self, position: dict, order_book_manager: OrderBookManager, binance_client: 'BinanceClient', dataframe: pd.DataFrame | None = None) -> dict | None:
        """
        Опціональний метод для аналізу відкритої позиції та її можливого коригування.
        
        Стратегії можуть реалізувати цей метод для додавання логіки, наприклад,
        трейлінг-стопу або завчасного закриття позиції при зміні ринкових умов.

        Args:
            position (dict): Словник з даними про поточну відкриту позицію.
            order_book_manager (OrderBookManager): Менеджер стакану для аналізу.

        Returns:
            dict | None: Словник з командою на коригування (напр., {'command': 'CLOSE_POSITION'}) або None.
        """
        return None # За замовчуванням ніяких дій не виконується
