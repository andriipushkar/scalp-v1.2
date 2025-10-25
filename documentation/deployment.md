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

### Спосіб 3: Docker (рекомендований для портативності)

Docker дозволяє "запакувати" додаток з усім його оточенням в ізольований контейнер. Це забезпечує однакову поведінку вашого бота на будь-якому сервері, де встановлено Docker.

#### 1. Створення файлу `.dockerignore`

Щоб уникнути копіювання непотрібних файлів в образ, створіть у корені проекту файл `.dockerignore` з таким вмістом:

```
# Git
.git
.gitignore

# Python
.venv
__pycache__/
*.pyc
*.pyo
*.pyd

# IDE
.idea/
.vscode/

# Logs
logs/
*.log

# Pytest
.pytest_cache/
```

#### 2. Створення файлу `Dockerfile`

У корені проекту створіть файл `Dockerfile` з таким вмістом:

```dockerfile
# Використовуємо офіційний образ Python
FROM python:3.10-slim

# Встановлюємо робочу директорію в контейнері
WORKDIR /app

# Копіюємо файл залежностей та встановлюємо їх
# Це робиться окремим кроком для кешування Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо решту коду додатку
COPY . .

# Команда для запуску бота при старті контейнера
CMD ["python", "main.py"]
```

#### 3. Збірка та запуск

1.  **Зберіть Docker образ:**
    На вашому сервері перейдіть до директорії проекту та виконайте:
    ```bash
    docker build -t quantum-trader .
    ```
    *   `-t quantum-trader` дає образу ім'я (тег).
    *   `.` вказує, що `Dockerfile` знаходиться в поточній директорії.

2.  **Запустіть Docker контейнер:**
    ```bash
    docker run -d \
      --name quantum-trader-container \
      --restart always \
      -e BINANCE_API_KEY="your_api_key_here" \
      -e BINANCE_API_SECRET="your_api_secret_here" \
      quantum-trader
    ```
    *   `-d`: Запуск у фоновому режимі.
    *   `--name quantum-trader-container`: Ім'я контейнера.
    *   `--restart always`: Автоматично перезапускати контейнер.
    *   `-e ...`: Передача API ключів як змінних середовища.
    *   `quantum-trader`: Назва образу для запуску.

#### 4. Керування контейнером

*   **Перегляд логів:**
    ```bash
    docker logs -f quantum-trader-container
    ```
*   **Зупинка контейнера:**
    ```bash
    docker stop quantum-trader-container
    ```
*   **Видалення контейнера:**
    ```bash
    docker rm quantum-trader-container
    ```

## 4. Моніторинг

Незалежно від способу запуску, регулярно перевіряйте файли логів (`quantum_trader.log`, `journalctl` або `docker logs`), щоб переконатися, що бот працює коректно і не виникає помилок.
