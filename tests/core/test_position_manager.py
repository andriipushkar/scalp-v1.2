import json
import pytest
from unittest.mock import patch, mock_open

from core.bot_orchestrator import PositionManager

@pytest.fixture
def mock_file_content():
    """Фікстура, що надає валідний JSON-контент для файлу стану."""
    return {
        "BTCUSDT": {
            "side": "Long",
            "quantity": 0.01,
            "entry_price": 60000.0,
            "stop_loss": 59000.0,
            "take_profit": 62000.0,
            "sl_order_id": 123,
            "tp_order_id": 456
        }
    }

def test_load_state_file_not_found():
    """ТЕСТ: Перевіряє, що PositionManager ініціалізується порожнім, якщо файл стану не знайдено."""
    with patch("os.path.exists", return_value=False):
        pos_manager = PositionManager("dummy_path.json")
        assert pos_manager.get_positions_count() == 0

def test_load_state_valid_json(mock_file_content):
    """ТЕСТ: Перевіряє, що PositionManager коректно завантажує дані з валідного JSON файлу."""
    # Імітуємо, що файл існує і містить валідний JSON
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data=json.dumps(mock_file_content))):
            pos_manager = PositionManager("dummy_path.json")
            assert pos_manager.get_positions_count() == 1
            btc_pos = pos_manager.get_position_by_symbol("BTCUSDT")
            assert btc_pos is not None
            assert btc_pos['side'] == "Long"

def test_load_state_invalid_json():
    """ТЕСТ: Перевіряє, що PositionManager обробляє пошкоджений JSON і стартує з чистого стану."""
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="{invalid json")):
            pos_manager = PositionManager("dummy_path.json")
            assert pos_manager.get_positions_count() == 0

def test_set_get_and_close_position():
    """ТЕСТ: Перевіряє повний цикл роботи з позицією: додати, отримати, закрити."""
    # Імітуємо, що файл не існує, щоб почати з чистого стану
    with patch("os.path.exists", return_value=False):
        # Ми також повинні "заглушити" спробу запису у файл, оскільки нас цікавить лише стан в пам'яті
        with patch("builtins.open", mock_open()) as mocked_file:
            pos_manager = PositionManager("dummy_path.json")

            # 1. Перевірка початкового стану
            assert pos_manager.get_positions_count() == 0

            # 2. Додавання нової позиції
            pos_manager.set_position(
                symbol="ETHUSDT",
                side="Short",
                quantity=0.5,
                entry_price=4000.0,
                stop_loss=4100.0,
                take_profit=3900.0,
                sl_order_id=789,
                tp_order_id=101
            )

            # 3. Перевірка, що позиція додалася
            assert pos_manager.get_positions_count() == 1
            eth_pos = pos_manager.get_position_by_symbol("ETHUSDT")
            assert eth_pos is not None
            assert eth_pos['quantity'] == 0.5
            assert eth_pos['sl_order_id'] == 789

            # 4. Перевірка, що стан було збережено у файл
            # Збираємо всі частини, що були записані у файл
            written_data = "".join(call.args[0] for call in mocked_file().write.call_args_list)
            saved_json = json.loads(written_data)
            
            # Перевіряємо, що збережені дані відповідають очікуваним
            assert "ETHUSDT" in saved_json
            assert saved_json["ETHUSDT"]["quantity"] == 0.5

            # 5. Закриття позиції
            pos_manager.close_position("ETHUSDT")

            # 6. Перевірка, що позиція закрита
            assert pos_manager.get_positions_count() == 0
            assert pos_manager.get_position_by_symbol("ETHUSDT") is None
