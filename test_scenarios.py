from strategies.liquidity_hunting_strategy import LiquidityHuntingStrategy

def run_scenario_analysis():
    """
    Цей скрипт імітує проблемну торгову ситуацію, щоб порівняти два підходи
    до розрахунку Stop-Loss і визначити, який з них надійніший.
    """

    print("--- Аналіз сценаріїв розрахунку Stop-Loss ---")

    # --- 1. Вхідні дані (імітуємо ситуацію з реальних логів) ---
    # Припустимо, стратегія знайшла стіну на ціні 4150.0
    wall_price = 4150.0
    # Припустимо, реальна ціна входу в LONG позицію склала 4150.5 (трохи вище стіни)
    actual_fill_price = 4150.5
    # АЛЕ! Одразу після входу ціна трохи впала до 4149.5
    mark_price_after_fill = 4149.5
    # Мінімальний крок ціни для ETHUSDT
    tick_size = 0.01

    print(f"\nСценарій: Увійшли в LONG по {actual_fill_price}, але ринок одразу впав до {mark_price_after_fill}\n")

    # --- 2. Тестуємо ПІДХІД №1: Збільшені відступи від стіни ---
    print("--- Тестування Підходу №1: Stop-Loss, прив'язаний до 'стіни' ---")
    params_v1 = {
        'entry_offset_ticks': 50, 
        'stop_loss_offset_ticks': 50, 
        'risk_reward_ratio': 1.5
    }
    strategy_v1 = LiquidityHuntingStrategy("test_v1", "ETHUSDT", params_v1)
    
    # Розраховуємо SL/TP за логікою стратегії
    sl_tp_v1_calcs = strategy_v1.calculate_sl_tp(0, 'Long', wall_price=wall_price, tick_size=tick_size)
    # Розраховуємо стоп-лос на основі стіни
    stop_loss_v1 = wall_price - (params_v1['stop_loss_offset_ticks'] * tick_size)

    print(f"Розрахований Stop-Loss: {stop_loss_v1:.2f}")
    
    # Перевіряємо, чи валідний цей стоп-лос відносно поточної ринкової ціни
    # Для LONG позиції, SL має бути НИЖЧИМ за ринкову ціну
    is_valid_v1 = stop_loss_v1 < mark_price_after_fill
    
    if is_valid_v1:
        print(f"РЕЗУЛЬТАТ: ✅ Валідний. {stop_loss_v1:.2f} < {mark_price_after_fill}. Ордер був би прийнятий біржею.")
    else:
        print(f"РЕЗУЛЬТАТ: ❌ Невалідний. {stop_loss_v1:.2f} >= {mark_price_after_fill}. Ордер був би відхилений з помилкою 'Order would immediately trigger'.")


    # --- 3. Тестуємо ПІДХІД №2: Stop-Loss як % від ціни входу ---
    print("\n--- Тестування Підходу №2: Stop-Loss як % від реальної ціни входу ---")
    sl_percentage = 0.005 # 0.5%
    
    # Розраховуємо SL від РЕАЛЬНОЇ ціни входу
    stop_loss_v2 = actual_fill_price * (1 - sl_percentage)

    print(f"Розрахований Stop-Loss ({sl_percentage*100}% від ціни входу): {stop_loss_v2:.2f}")

    # Перевіряємо, чи валідний цей стоп-лос
    is_valid_v2 = stop_loss_v2 < mark_price_after_fill

    if is_valid_v2:
        print(f"РЕЗУЛЬТАТ: ✅ Валідний. {stop_loss_v2:.2f} < {mark_price_after_fill}. Ордер був би прийнятий біржею.")
    else:
        print(f"РЕЗУЛЬТАТ: ❌ Невалідний. {stop_loss_v2:.2f} >= {mark_price_after_fill}. Ордер був би відхилений.")


    # --- 4. Висновок ---
    print("\n--- ВИСНОВОК ---")
    if is_valid_v1 and not is_valid_v2:
        print("Підхід №1 (від стіни) виявився кращим у цьому сценарії.")
    elif not is_valid_v1 and is_valid_v2:
        print("Підхід №2 (% від ціни входу) є значно надійнішим, оскільки він адаптується до реальної ціни входу, а не до теоретичної 'стіни'.")
    elif is_valid_v1 and is_valid_v2:
        print("Обидва підходи валідні в цьому сценарії, але Підхід №2 є більш універсальним.")
    else:
        print("Обидва підходи згенерували невалідний стоп-лос. Можливо, ринковий рух був занадто сильним, або відсоток для стоп-лосу занадто малий.")


if __name__ == "__main__":
    run_scenario_analysis()
