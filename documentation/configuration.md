# Конфігурація

Цей документ детально описує всі параметри конфігурації, доступні в боті QuantumTrader.

## 1. Головний файл конфігурації (`configs/config.yaml`)

Цей файл містить глобальні налаштування для всього бота.

| Параметр | Опис | Приклад |
|---|---|---|
| `symbols` | Список символів для торгівлі. Якщо залишити порожнім, бот спробує завантажити всі доступні символи з біржі. | `['BTCUSDT', 'ETHUSDT']` |
| `enabled_strategies` | Список стратегій, які бот буде запускати. Назви повинні відповідати назвам класів стратегій. | `['EmaTrendFollowingStrategy']` |
| `strategy_settings` | Шляхи до файлів з налаштуваннями для кожної стратегії. | `EmaTrendFollowingStrategy: "configs/strategies/ema_trend_following.yaml"` |
| `trading_parameters` | Загальні параметри торгівлі та ризик-менеджменту. | |
| `margin_per_trade_pct` | Відсоток від депозиту, що використовується для однієї угоди. | `0.1` (10%) |
| `fee_pct` | Комісія біржі у відсотках. | `0.0004` (0.04%) |
| `leverage` | Кредитне плече. | `10` |
| `margin_type` | Тип маржі (`ISOLATED` або `CROSSED`). | `ISOLATED` |
| `max_active_trades` | Максимальна кількість одночасно відкритих позицій. | `10` |
| `max_concurrent_symbols` | Максимальна кількість символів для одночасного моніторингу. | `100` |

---

## 2. Конфігурація стратегій

Кожна стратегія має свій власний файл конфігурації в папці `configs/strategies/`.

### Загальна структура

Файли конфігурації стратегій мають дві основні секції:

1.  **`default`**: Тут знаходяться налаштування, які застосовуються до всіх торгових пар за замовчуванням.
2.  **Специфічні налаштування для символів (напр., `BTCUSDT:`)**: Ця секція дозволяє перевизначити будь-який параметр із секції `default` для конкретної торгової пари. Це корисно, оскільки різні активи можуть вимагати різних налаштувань для оптимальної роботи.

**Приклад логіки:** Якщо в `default` `kline_interval` встановлено на `'15m'`, а в секції `BTCUSDT:` `kline_interval` встановлено на `'1h'`, то для всіх пар, крім BTCUSDT, буде використовуватися 15-хвилинний таймфрейм, а для BTCUSDT — годинний.

### EMA Trend Following (`ema_trend_following.yaml`)

Стратегія, заснована на перетині двох ковзних середніх (EMA).

| Параметр | Опис |
|---|---|
| `fast_ema_period` | Період для розрахунку швидкої EMA. |
| `slow_ema_period` | Період для розрахунку повільної EMA. |
| `rsi_period` | Період для розрахунку RSI. |
| `volume_ma_period` | Період для розрахунку середнього обсягу. |
| `atr_period` | Період для розрахунку ATR (Average True Range). |
| `kline_interval` | Таймфрейм, на якому працює стратегія (напр., '5m', '15m', '1h'). |
| `sl_atr_multiplier` | Множник ATR для розрахунку стоп-лосу. `Stop Loss = ATR * sl_atr_multiplier`. |
| `rr_ratio` | Співвідношення ризик/прибуток. `Take Profit = Risk * rr_ratio`. |
| `tp_method` | Метод розрахунку тейк-профіту (`rr_ratio` або `local_extremum`). |
| `use_rsi_filter` | Вмикає або вимикає фільтр за RSI для уникнення входів у перекупленому/перепроданому ринку. |
| `use_volume_filter` | Вмикає або вимикає фільтр за обсягом для підтвердження сили тренду. |

### Dynamic Orderbook (`dynamic_orderbook.yaml`)

Скальпінг-стратегія, що реагує на великі лімітні ордери ("стіни") у біржовому стакані.

| Параметр | Опис |
|---|---|
| `ema_filter_enabled` | Вмикає або вимикає фільтр за EMA для визначення загального тренду. |
| `ema_period` | Період для розрахунку EMA. |
| `ema_timeframe` | Таймфрейм для розрахунку EMA. |
| `rsi_filter_enabled` | Вмикає або вимикає фільтр за RSI. |
| `rsi_period` | Період для розрахунку RSI. |
| `rsi_timeframe` | Таймфрейм для розрахунку RSI. |
| `rsi_long_threshold` | Поріг RSI для довгих позицій (не входити в лонг, якщо RSI вище). |
| `rsi_short_threshold` | Поріг RSI для коротких позицій (не входити в шорт, якщо RSI нижче). |
| `wall_volume_multiplier` | У скільки разів обсяг "стіни" має перевищувати середній обсяг у стакані. |
| `activation_distance_ticks` | Максимальна відстань від ціни до "стіни" для активації сигналу. |
| `max_spread_bps` | Максимальний спред у базисних пунктах (0.01%). |
| `min_wall_volume` | Мінімальний обсяг "стіни" в базовій валюті. |
| `use_atr_sl_tp` | Використовувати динамічний SL/TP на основі ATR. |
| `atr_period` | Період для розрахунку ATR. |
| `atr_sl_multiplier` | Множник ATR для розрахунку стоп-лосу. |
| `atr_tp_multiplier` | Множник ATR для розрахунку тейк-профіту. |
| `entry_offset_ticks` | Відступ від "стіни" для розміщення ордера на вхід. |
| `stop_loss_percent` | Відсоток стоп-лосу (використовується, якщо `use_atr_sl_tp` вимкнено). |
| `initial_tp_min_search_percent`| Мінімальний відсоток для пошуку тейк-профіту. |
| `initial_tp_search_percent`| Максимальний відсоток для пошуку тейк-профіту. |
| `pre_emptive_close_threshold_mult` | Множник для передчасного закриття позиції при зміні тиску в стакані. |

---

## 3. Приклади використання

Ось як може виглядати ваш головний файл `configs/config.yaml` для запуску конкретних стратегій.

### Приклад 1: Запуск `EmaTrendFollowingStrategy` на двох парах

Цей приклад показує, як запустити трендову стратегію на BTCUSDT та ETHUSDT.

```yaml
# configs/config.yaml

symbols:
  - BTCUSDT
  - ETHUSDT

enabled_strategies:
  - EmaTrendFollowingStrategy

strategy_settings:
  EmaTrendFollowingStrategy: "configs/strategies/ema_trend_following.yaml"

trading_parameters:
  margin_per_trade_pct: 0.1
  fee_pct: 0.0004
  leverage: 10
  margin_type: "ISOLATED"
  max_active_trades: 5
  max_concurrent_symbols: 10
```

При цьому у файлі `configs/strategies/ema_trend_following.yaml` можуть бути задані специфічні налаштування для BTC:

```yaml
# configs/strategies/ema_trend_following.yaml

default:
  kline_interval: '15m'
  sl_atr_multiplier: 1.5
  # ... інші параметри

BTCUSDT:
  kline_interval: '1h'      # Для BTC використовуємо довший таймфрейм
  sl_atr_multiplier: 2.0    # І ширший стоп-лос
```

### Приклад 2: Запуск `DynamicOrderbookStrategy` для скальпінгу

Цей приклад налаштовує бота для скальпінгу на всіх доступних парах.

```yaml
# configs/config.yaml

symbols: [] # Порожній список означає "всі символи"

enabled_strategies:
  - DynamicOrderbookStrategy

strategy_settings:
  DynamicOrderbookStrategy: "configs/strategies/dynamic_orderbook.yaml"

trading_parameters:
  margin_per_trade_pct: 0.05 # Менший ризик на угоду для скальпінгу
  fee_pct: 0.0004
  leverage: 20 # Вище плече
  margin_type: "ISOLATED"
  max_active_trades: 10
  max_concurrent_symbols: 50
```

