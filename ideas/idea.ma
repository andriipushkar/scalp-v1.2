Група 1: Стратегії слідування за трендом

(Заробляють на довгих, стійких рухах)

1. MACD Crossover + Фільтр тренду

    Назва файлу: macd_trend_filter_strategy.py

    Клас: MacdTrendFilterStrategy

    Концепція: Використовує MACD для визначення зміни короткострокового імпульсу, але дозволяє входити тільки в напрямку глобального тренду, який визначається 200-періодною EMA.

    Необхідні індикатори (з pandas-ta):

        df.ta.macd(fast=12, slow=26, signal=9, append=True)

        df.ta.ema(length=200, append=True) (назвемо її EMA_200)

        df.ta.atr(length=14, append=True) (для SL та трейлінгу)

    Умови входу (Long):

        Фільтр тренду: current_candle['close'] > current_candle['EMA_200'] (Ціна вище глобального тренду).

        Перетин MACD: prev_candle['MACD_12_26_9'] < prev_candle['MACDs_12_26_9'] (лінія MACD була нижче сигнальної) ТА current_candle['MACD_12_26_9'] > current_candle['MACDs_12_26_9'] (лінія MACD перетнула сигнальну вгору).

    Умови входу (Short):

        Фільтр тренду: current_candle['close'] < current_candle['EMA_200'] (Ціна нижче глобального тренду).

        Перетин MACD: prev_candle['MACD_12_26_9'] > prev_candle['MACDs_12_26_9'] (лінія MACD була вище сигнальної) ТА current_candle['MACD_12_26_9'] < current_candle['MACDs_12_26_9'] (лінія MACD перетнула сигнальну вниз).

    Stop Loss (в calculate_sl_tp):

        Використовуйте вашу існуючу логіку _calculate_stop_loss на основі ATR з EmaTrendFollowingStrategy.

    Take Profit (в calculate_sl_tp):

        Встановіть None. Вихід з позиції буде здійснюватися виключно за трейлінг-стопом.

    Управління позицією (в analyze_and_adjust):

        Повністю скопіюйте логіку вашого методу analyze_and_adjust з EmaTrendFollowingStrategy. Він вже містить ідеально підходящі Трейлінг-стоп по ATR та Переведення в беззбиток.

2. SuperTrend

    Назва файлу: supertrend_strategy.py

    Клас: SuperTrendStrategy

    Концепція: Один з найчіткіших індикаторів тренду. Входимо, коли індикатор змінює свій напрямок ("перевертається").

    Необхідні індикатори (з pandas-ta):

        df.ta.supertrend(length=10, multiplier=3, append=True) (поверне колонки SUPERT_10_3.0, SUPERTd_10_3.0 і т.д.)

    Умови входу (Long):

        Зміна напрямку: prev_candle['SUPERTd_10_3.0'] == -1 (попередній тренд був низхідним) ТА current_candle['SUPERTd_10_3.0'] == 1 (поточний тренд став висхідним).

    Умови входу (Short):

        Зміна напрямку: prev_candle['SUPERTd_10_3.0'] == 1 (попередній тренд був висхідним) ТА current_candle['SUPERTd_10_3.0'] == -1 (поточний тренд став низхідним).

    Stop Loss (в calculate_sl_tp):

        Початковий Stop Loss встановлюється точно на лінію індикатора: stop_loss = current_candle['SUPERT_10_3.0'].

    Take Profit (в calculate_sl_tp):

        None. Вихід з позиції відбувається тільки по трейлінг-стопу.

    Управління позицією (в analyze_and_adjust):

        Реалізуйте Трейлінг-стоп на основі самого індикатора.

        На кожній свічці отримуйте нове значення лінії current_atr_sl_line = current_candle['SUPERT_10_3.0'].

        Переміщуйте SL на цей рівень, якщо він вигідніший за поточний SL (для Long – якщо current_atr_sl_line > position['stop_loss'], для Short – якщо current_atr_sl_line < position['stop_loss']).

        Беззбиток (Breakeven): Також додайте вашу існуючу логіку беззбитку.

3. Ichimoku Cloud (Пробій Хмари Комо)

    Назва файлу: ichimoku_breakout_strategy.py

    Клас: IchimokuBreakoutStrategy

    Концепція: Потужна трендова система. "Хмара" (Kumo) є динамічною зоною підтримки/опору. Ми входимо, коли ціна пробиває цю хмару, що свідчить про сильний тренд.

    Необхідні індикатори (з pandas-ta):

        df.ta.ichimoku(tenkan=9, kijun=26, senkou=52, append=True) (поверне 5 колонок). Нам потрібні SPANa_9_26_52 (Senkou A), SPANb_9_26_52 (Senkou B) та KIJUN_9_26_52 (Kijun-sen).

    Умови входу (Long):

        Пробій Хмари вгору: current_candle['close'] > current_candle['SPANa_9_26_52'] ТА current_candle['close'] > current_candle['SPANb_9_26_52'].

        Фільтр (щоб не входити на старій свічці): prev_candle['close'] < max(prev_candle['SPANa_9_26_52'], prev_candle['SPANb_9_26_52']) (тобто ми щойно пробили хмару).

    Умови входу (Short):

        Пробій Хмари вниз: current_candle['close'] < current_candle['SPANa_9_26_52'] ТА current_candle['close'] < current_candle['SPANb_9_26_52'].

        Фільтр: prev_candle['close'] > min(prev_candle['SPANa_9_26_52'], prev_candle['SPANb_9_26_52']) (щойно пробили хмару вниз).

    Stop Loss (в calculate_sl_tp):

        Початковий SL можна встановити на лінію Kijun-sen (Базова лінія): stop_loss = current_candle['KIJUN_9_26_52']. Або, для уніфікації, використайте вашу логіку ATR.

    Take Profit (в calculate_sl_tp):

        None.

    Управління позицією (в analyze_and_adjust):

        Реалізуйте Трейлінг-стоп на основі лінії Kijun-sen. Логіка така ж, як у SuperTrend: new_stop_loss = current_candle['KIJUN_9_26_52'].

        Додайте вашу логіку Беззбитку.

🔄 Група 2: Стратегії повернення до середнього (Mean Reversion)

(Найкраще працюють у боковому ринку (флеті), де ваша EmaTrendFollowingStrategy може втрачати)

4. Bollinger Bands® + RSI

    Назва файлу: bollinger_reversion_strategy.py

    Клас: BollingerReversionStrategy

    Тип: Контртренд (Повернення до середнього).

    Концепція: Купувати, коли ціна падає "занадто низько" (нижче нижньої смуги) і є перепроданість (RSI < 30). Продавати, коли "занадто високо".

    Необхідні індикатори (з pandas-ta):

        df.ta.bbands(length=20, std=2, append=True) (потрібні BBL_20_2.0 (Нижня), BBU_20_2.0 (Верхня) та BBM_20_2.0 (Середня)).

        df.ta.rsi(length=14, append=True)

        df.ta.atr(length=14, append=True) (для SL)

    Умови входу (Long):

        Вихід за смугу: current_candle['close'] < current_candle['BBL_20_2.0'].

        Фільтр RSI: current_candle['RSI_14'] < 30.

    Умови входу (Short):

        Вихід за смугу: current_candle['close'] > current_candle['BBU_20_2.0'].

        Фільтр RSI: current_candle['RSI_14'] > 70.

    Stop Loss (в calculate_sl_tp):

        Використовуйте вашу логіку ATR.

    Take Profit (в calculate_sl_tp):

        Обов'язково: Середня лінія Смуг Боллінджера. take_profit = current_candle['BBM_20_2.0']. Це і є "середнє", до якого ми очікуємо повернення.

    Управління позицією (в analyze_and_adjust):

        Трейлінг-стоп тут НЕ ПОТРІБЕН. Стратегія має чітку ціль.

        Використовуйте тільки вашу логіку Беззбитку (Breakeven).

5. Осцилятор Стохастик (Stochastic Crossover)

    Назва файлу: stochastic_crossover_strategy.py

    Клас: StochasticCrossoverStrategy

    Тип: Контртренд (Імпульс).

    Концепція: Швидкий осцилятор для флету. Входимо, коли його лінії перетинаються в екстремальних зонах (перепроданість/перекупленість).

    Необхідні індикатори (з pandas-ta):

        df.ta.stoch(k=14, d=3, smooth_k=3, append=True) (потрібні STOCHk_14_3_3 (%K) та STOCHd_14_3_3 (%D)).

        df.ta.atr(length=14, append=True) (для SL)

    Умови входу (Long):

        Перетин: prev_candle['STOCHk_14_3_3'] < prev_candle['STOCHd_14_3_3'] ТА current_candle['STOCHk_14_3_3'] > current_candle['STOCHd_14_3_3'].

        Зона перепроданості: current_candle['STOCHk_14_3_3'] < 20 (або current_candle['STOCHd_14_3_3'] < 20).

    Умови входу (Short):

        Перетин: prev_candle['STOCHk_14_3_3'] > prev_candle['STOCHd_14_3_3'] ТА current_candle['STOCHk_14_3_3'] < current_candle['STOCHd_14_3_3'].

        Зона перекупленості: current_candle['STOCHk_14_3_3'] > 80 (або current_candle['STOCHd_14_3_3'] > 80).

    Stop Loss (в calculate_sl_tp):

        Використовуйте вашу логіку ATR.

    Take Profit (в calculate_sl_tp):

        Фіксоване співвідношення R:R, наприклад, rr_ratio: 1.5 (задається в конфізі).

    Управління позицією (в analyze_and_adjust):

        Використовуйте тільки вашу логіку Беззбитку.

6. Індекс відносного бадьорості (RVI Crossover)

    Назва файлу: rvi_crossover_strategy.py

    Клас: RviCrossoverStrategy

    Тип: Імпульс / Повернення до середнього.

    Концепція: Вимірює "силу" руху. Сигнали генеруються, коли лінія RVI перетинає свою сигнальну лінію, часто вказуючи на розворот.

    Необхідні індикатори (з pandas-ta):

        df.ta.rvi(length=10, swma_length=4, append=True) (потрібні RVI_10_4 та RVIs_10_4).

        df.ta.atr(length=14, append=True) (для SL)

    Умови входу (Long):

        Перетин: prev_candle['RVI_10_4'] < prev_candle['RVIs_10_4'] ТА current_candle['RVI_10_4'] > current_candle['RVIs_10_4'].

    Умови входу (Short):

        Перетин: prev_candle['RVI_10_4'] > prev_candle['RVIs_10_4'] ТА current_candle['RVI_10_4'] < current_candle['RVIs_10_4'].

    Stop Loss (в calculate_sl_tp):

        Використовуйте вашу логіку ATR.

    Take Profit (в calculate_sl_tp):

        Фіксоване R:R (напр., rr_ratio: 2.0).

    Управління позицією (в analyze_and_adjust):

        Використовуйте вашу готову логіку Трейлінг-стопу та Беззбитку.

⚡ Група 3: Стратегії пробою та волатильності

(Заробляють на різких рухах після періодів затишшя)

7. Пробій Каналу Кельтнера (Keltner Channel Breakout)

    Назва файлу: keltner_breakout_strategy.py

    Клас: KeltnerBreakoutStrategy

    Тип: Пробій / Слідування за трендом.

    Концепція: Канали Кельтнера (на основі ATR) показують нормальний діапазон руху. Закриття за межами каналу свідчить про початок сильного імпульсу, який ми і торгуємо.

    Необхідні індикатори (з pandas-ta):

        df.ta.kc(length=20, atr_length=10, multiplier=2, append=True) (потрібні KCUe_20_10_2 (Верхній), KCLe_20_10_2 (Нижній) та KCm_20_10_2 (Середній)).

        df.ta.adx(length=14, append=True) (для фільтра).

    Умови входу (Long):

        Пробій: current_candle['close'] > current_candle['KCUe_20_10_2'].

        Фільтр тренду: current_candle['ADX_14'] > 25 (щоб підтвердити, що це імпульс, а не просто шум).

    Умови входу (Short):

        Пробій: current_candle['close'] < current_candle['KCLe_20_10_2'].

        Фільтр тренду: current_candle['ADX_14'] > 25.

    Stop Loss (в calculate_sl_tp):

        Встановити на середню лінію Каналу Кельтнера: stop_loss = current_candle['KCm_20_10_2'].

    Take Profit (в calculate_sl_tp):

        None.

    Управління позицією (в analyze_and_adjust):

        Обов'язкове використання Трейлінг-стопу (на основі ATR) та Беззбитку.

8. Стиснення Смуг Боллінджера (Bollinger Band Squeeze)

    Назва файлу: bb_squeeze_strategy.py

    Клас: BbSqueezeStrategy

    Тип: Пробій волатильності.

    Концепція: Періоди низької волатильності ("стиснення" смуг) часто передують потужним імпульсам. Ми чекаємо на таке стиснення, а потім торгуємо в напрямку пробою.

    Необхідні індикатори (з pandas-ta):

        df.ta.bbands(length=20, std=2, append=True) (потрібні BBL_20_2.0, BBU_20_2.0, BBM_20_2.0).

        df.ta.bandwidth(length=20, std=2, append=True) (потрібна BBW_20_2.0).

Детальна логіка:

    Логіка check_signal:

        Стан стратегії: Вам потрібно додати self.is_in_squeeze = False в __init__.

        (Етап 1) Перевірка стиснення:

            Розрахуйте rolling_min_bbw = df['BBW_20_2.0'].rolling(window=100).min().

            Якщо current_candle['BBW_20_2.0'] <= rolling_min_bbw.iloc[-1] (ширина смуг на 100-свічковому мінімумі), встановіть self.is_in_squeeze = True.

        (Етап 2) Умови входу (Long):

            self.is_in_squeeze == True (ми в стані очікування пробою) ТА

            current_candle['close'] > current_candle['BBU_20_2.0'] (відбувся пробій вгору).

            Після сигналу: self.is_in_squeeze = False.

        (Етап 3) Умови входу (Short):

            self.is_in_squeeze == True ТА

            current_candle['close'] < current_candle['BBL_20_2.0'] (відбувся пробій вниз).

            Після сигналу: self.is_in_squeeze = False.

    Управління позицією (calculate_sl_tp та analyze_and_adjust):

        Stop Loss: Середня лінія (BBM_20_2.0).

        Take Profit: None.

        analyze_and_adjust: Обов'язкове використання Трейлінг-стопу та Беззбитку.

9. Патерн "Внутрішній бар" (Inside Bar Breakout)

    Назва файлу: inside_bar_breakout_strategy.py

    Клас: InsideBarBreakoutStrategy

    Тип: Пробій / Волатильність.

    Концепція: "Внутрішній бар" (свічка, що повністю знаходиться в діапазоні попередньої) вказує на нерішучість та консолідацію. Ми торгуємо пробій цього короткострокового діапазону.

    Необхідні індикатори: Не потрібні (тільки High та Low з K-ліній).

Детальна логіка:

    Логіка check_signal:

        Беремо 3 останні свічки: current_candle (поточна), prev_candle (внутрішній бар), prev_prev_candle (материнський бар).

        (Етап 1) Пошук патерну:

            is_inside_bar = (prev_candle['high'] < prev_prev_candle['high']) AND (prev_candle['low'] > prev_prev_candle['low']).

        (Етап 2) Умови входу (Long):

            is_inside_bar == True ТА

            current_candle['close'] > prev_prev_candle['high'] (пробій максимуму материнської свічки).

        (Етап 3) Умови входу (Short):

            is_inside_bar == True ТА

            current_candle['close'] < prev_prev_candle['low'] (пробій мінімуму материнської свічки).

    Управління позицією (calculate_sl_tp та analyze_and_adjust):

        Stop Loss: Протилежний кінець "материнської" свічки.

            Для Long: stop_loss = prev_prev_candle['low'].

            Для Short: stop_loss = prev_prev_candle['high'].

        Take Profit: Фіксоване R:R (напр., rr_ratio: 2.0).

        analyze_and_adjust: Використовуйте тільки вашу логіку Беззбитку (Breakeven).