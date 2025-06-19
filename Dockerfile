# Вказуємо базовий образ
FROM python:3.12-slim

# Створюємо робочий каталог всередині контейнера
WORKDIR /app

# Копіюємо всі файли з репозиторію в контейнер
COPY . .

# Створюємо віртуальне середовище та встановлюємо залежності
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# Встановлюємо середовище як основне (Railway/Nixpacks буде використовувати цей шлях)
ENV PATH="/opt/venv/bin:$PATH"

# Вказуємо команду запуску (замінити на вашу, наприклад main.py або bot.py)
CMD ["python", "main.py"]
