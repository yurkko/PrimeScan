import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
import openai
import requests
from bs4 import BeautifulSoup
# Імпорт ваших існуючих модулів моніторингу та обробки (ADMISMonitor, SaxoMonitor тощо)
# from admis_monitor import ADMISMonitor
# from saxo_monitor import SaxoMonitor

# Завантаження змінних середовища з файлу .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

# Налаштування логування
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Налаштування ключа OpenAI
openai.api_key = OPENAI_API_KEY

# Ініціалізація FastAPI-додатку
app = FastAPI()

# Ініціалізація Telegram-бота з ApplicationBuilder
application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

# ------------------ Обробники команд ------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник команди /start."""
    user = update.effective_user
    await context.bot.send_message(chat_id=update.effective_chat.id,
                                   text=f"Привіт, {user.first_name}! Я готовий.")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приклад обмеження доступу за ADMIN_ID."""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await context.bot.send_message(chat_id=update.effective_chat.id, 
                                       text="Ви не маєте доступу до цієї команди.")
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, 
                                   text="Команда доступна адміністратору.")

# ------------------ Логіка моніторингу (заглушки) ------------------
# Тут потрібно вставити реальну логіку ваших класів/функцій ADMISMonitor, SaxoMonitor тощо.

async def check_admis(context: ContextTypes.DEFAULT_TYPE):
    # Приклад функції моніторингу ADMIS (запуск оn та зміну стратегії)
    # admi_monitor = ADMISMonitor()
    # new_reports = admi_monitor.check_updates()
    # for report in new_reports:
    #     summary = admi_monitor.summarize_report(report)
    #     await context.bot.send_message(chat_id=ADMIN_ID, text=summary)
    pass

async def check_saxo(context: ContextTypes.DEFAULT_TYPE):
    # Приклад функції моніторингу Saxo
    # saxo_monitor = SaxoMonitor()
    # updates = saxo_monitor.check_updates()
    # for update_text in updates:
    #     await context.bot.send_message(chat_id=ADMIN_ID, text=update_text)
    pass

# ------------------ Команди для ручного запуску (якщо потрібно) ------------------

async def admis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручний запуск перевірки ADMIS (тільки для адміністратора)."""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Немає доступу.")
        return
    # admi_monitor = ADMISMonitor()
    # summary = admi_monitor.get_latest_summary()
    # await update.message.reply_text(summary)
    await update.message.reply_text("ADMIS summary готовий (заглушка).")

async def saxo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручний запуск перевірки Saxo (тільки для адміністратора)."""
    user_id = str(update.effective_user.id)
    if user_id != ADMIN_ID:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Немає доступу.")
        return
    # saxo_monitor = SaxoMonitor()
    # summary = saxo_monitor.get_latest_summary()
    # await update.message.reply_text(summary)
    await update.message.reply_text("Saxo summary готовий (заглушка).")

# ------------------ Обробник довільних повідомлень ------------------
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проста ехо-функція для тестування."""
    await context.bot.send_message(chat_id=update.effective_chat.id, text=update.message.text)

# ------------------ Додавання обробників ------------------
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("admin", admin_command))
application.add_handler(CommandHandler("admis", admis_command))
application.add_handler(CommandHandler("saxo", saxo_command))
application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))

# ------------------ Webhook-ендпоїнт FastAPI ------------------
@app.post(f"/{TELEGRAM_BOT_TOKEN}")
async def telegram_webhook(request: Request):
    """Отримує оновлення від Telegram і передає їх в обробник python-telegram-bot."""
    data = await request.json()
    update = Update.de_json(data, Bot(TELEGRAM_BOT_TOKEN))
    await application.process_update(update)
    return {"ok": True}

# ------------------ Запуск вебхука ------------------
if __name__ == "__main__":
    # Встановлюємо вебхук у Telegram
    webhook_full_url = WEBHOOK_URL.rstrip("/") + "/" + TELEGRAM_BOT_TOKEN
    # Виконуємо запуск вебхука (налаштовує Bot.set_webhook() та запускає вебсервер)
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=webhook_full_url,
        # Додаткові параметри (максимум 40 з'єднань, ключ/сертифікат не потрібні при зовнішньому HTTPS)
    )
