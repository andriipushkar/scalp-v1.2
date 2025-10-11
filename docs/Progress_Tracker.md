# Відстеження прогресу проєкту "QuantumTrader"

Цей документ відображає поточний стан виконання дорожньої карти проєкту.

## Документація з ручного тестування

*   [x] Створено `docs/manual_testing/Phase_1_Manual_Testing.md` з інструкціями для ручного тестування Фази 1.

## Фаза 1: Створення фундаменту (Дні 1-2)

*   **Крок 1: Налаштування середовища розробки**
    *   [x] Створити новий репозиторій Git (`git init`).
    *   [x] Налаштувати віртуальне середовище Python (`python3 -m venv .venv`).
    *   [x] Створити файл `requirements.txt` та додати до нього основні залежності.
    *   [x] Створити файл `.gitignore`.
    *   [x] Створити файл `.env.example`.

*   **Крок 2: Створення структури проєкту**
    *   [x] Створити всі папки та порожні `__init__.py` файли.
    *   [x] Створити порожні файли для основних класів: `main.py`, `core/binance_client.py`, `analysis/technical_analyzer.py` і т.д.

*   **Крок 3: Налаштування конфігурації та логування**
    *   [x] Створити файли `configs/strategy_config.json` (еквівалент `main_config.json` для MVP).
    *   [x] Реалізовано запис торгових сигналів у окремий CSV-файл (`logs/trade_signals.csv`).

## Фаза 2: Розробка ядра (Дні 3-5)

*   **Крок 4: Реалізація BinanceClient (`core/binance_client.py`)**
    *   [x] Реалізувати клас `BinanceClient`.
    *   [x] Додати асинхронний метод `get_historical_klines` для завантаження історичних свічок та їх перетворення у `pandas.DataFrame`.
    *   [x] Додати асинхронний метод `connect_kline_socket` для підключення до WebSocket та отримання свічок у реальному часі.

*   **Крок 5: Реалізація аналітичних інструментів (`analysis/`)**

    *   [x] В `analysis/technical_analyzer.py` реалізувати клас `TechnicalAnalyzer` зі статичними методами для розрахунку основних індикаторів (add_ema), використовуючи `pandas`.

    *   [x] В `analysis/risk_manager.py` реалізувати клас `RiskManager` зі статичним методом `calculate_position_size`.




## Фаза 4: Оркестрація та запуск (Дні 8-9)

*   **Крок 8: Реалізація DataManager (`core/data_manager.py`)**
    *   [x] Реалізувати клас `DataManager`, який буде завантажувати історичні дані та оновлювати їх за допомогою WebSocket-з'єднання.

*   **Крок 9: Рефакторинг BotOrchestrator для підтримки кількох стратегій/пар**
    *   [x] Створено `configs/strategies.json` з простішою структурою (плоский список конфігурацій) для 50 пар.
    *   [x] Відкочено `BotOrchestrator._load_config()` для завантаження простого списку конфігурацій.
    *   [x] Відкочено `BotOrchestrator.start()` для ітерації по простому списку конфігурацій.
    *   [x] Виправлено `IndentationError` у `core/bot_orchestrator.py` та відновлено коректне визначення класів `StrategyExecutor` та `BotOrchestrator`.

*   **Крок 10: Перший запуск**
    *   [x] Зібрати все в `main.py`.
    *   [x] Виправлено `AttributeError: 'AsyncClient' object has no attribute 'futures_socket'` у `core/binance_client.py` шляхом коректного використання ф'ючерсного клієнта та `BinanceSocketManager`.


