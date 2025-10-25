# Розгортання (Deployment)

Цей посібник описує, як розгорнути та запустити торгового бота QuantumTrader на віддаленому сервері (наприклад, VPS) для безперервної роботи.

## 1. Підготовка сервера

Переконайтеся, що на вашому сервері встановлено:

*   **Python 3.10+**
*   **Git**

## 2. Налаштування

Процес налаштування аналогічний до локального, але з деякими особливостями.

### Клонування репозиторію

```bash
git clone <URL_вашого_репозиторію>
cd QuantumTrader
```

### Віртуальне середовище та залежності

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### API Ключі

На сервері **особливо важливо** не зберігати ключі у відкритому вигляді. Використовуйте змінні середовища.

Додайте ваші ключі до файлу `.bashrc` або `.profile`:

```bash
echo 'export BINANCE_API_KEY="your_api_key_here"' >> ~/.bashrc
echo 'export BINANCE_API_SECRET="your_api_secret_here"' >> ~/.bashrc
source ~/.bashrc
```

Таким чином, ключі будуть доступні для бота, але не будуть зберігатися в його файлах.

## 3. Запуск у фоновому режимі

Щоб бот продовжував працювати після того, як ви закриєте термінал, його потрібно запустити у фоновому режимі. Ось два поширених способи:

### Спосіб 1: `nohup` (простий)

`nohup` (no hang up) — це проста команда, яка дозволяє процесу продовжувати роботу, навіть якщо термінал закрито.

```bash
nohup python main.py > quantum_trader.log 2>&1 &
```

*   `nohup ... &` запускає процес у фоні.
*   `> quantum_trader.log 2>&1` перенаправляє весь вивід (stdout та stderr) у файл `quantum_trader.log`, щоб ви могли переглядати логи.

Щоб зупинити бота, вам потрібно буде знайти його PID (`ps aux | grep main.py`) і зупинити його командою `kill <PID>`.

### Спосіб 2: `systemd` (рекомендований, більш надійний)

`systemd` — це системний менеджер для Linux, який дозволяє створювати та керувати сервісами. Це найнадійніший спосіб для запуску бота, оскільки `systemd` може автоматично перезапускати його у разі збою.

1.  **Створіть файл сервісу:**

    ```bash
    sudo nano /etc/systemd/system/quantum_trader.service
    ```

2.  **Додайте в нього наступний вміст:**

    ```ini
    [Unit]
    Description=QuantumTrader Bot
    After=network.target

    [Service]
    User=<your_username>
    Group=<your_group>
    WorkingDirectory=/home/<your_username>/QuantumTrader
    ExecStart=/home/<your_username>/QuantumTrader/.venv/bin/python main.py
    Restart=always

    [Install]
    WantedBy=multi-user.target
    ```

    **Важливо:** Замініть `<your_username>` та `<your_group>` на ваше ім'я користувача та групу на сервері.

3.  **Керування сервісом:**

    *   Перезавантажте `systemd`, щоб він побачив новий сервіс:
        ```bash
        sudo systemctl daemon-reload
        ```
    *   Запустіть бота:
        ```bash
        sudo systemctl start quantum_trader
        ```
    *   Перевірте статус:
        ```bash
        sudo systemctl status quantum_trader
        ```
    *   Увімкніть автозапуск при старті системи:
        ```bash
        sudo systemctl enable quantum_trader
        ```
    *   Щоб переглянути логи, використовуйте `journalctl`:
        ```bash
        journalctl -u quantum_trader -f
        ```

## 4. Моніторинг

Незалежно від способу запуску, регулярно перевіряйте файли логів (`quantum_trader.log` або через `journalctl`), щоб переконатися, що бот працює коректно і не виникає помилок.
