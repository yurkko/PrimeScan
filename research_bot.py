import os
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram import Bot
import requests, fitz
from bs4 import BeautifulSoup
from openai import OpenAI

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
ADMIN_ID           = os.getenv("ADMIN_ID")
WEBHOOK_URL        = os.getenv("WEBHOOK_URL") or os.getenv("RAILWAY_STATIC_URL")
PORT               = int(os.getenv("PORT", 8080))

if not (TELEGRAM_BOT_TOKEN and OPENAI_API_KEY and WEBHOOK_URL):
    raise RuntimeError("Missing required environment variables")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# FastAPI & Telegram app
app = FastAPI()
telegram_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

# --- Command handlers ---
async def start_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(u.effective_chat.id, "Бот запущено через Webhook!")

telegram_app.add_handler(CommandHandler("start", start_cmd))
telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,ctx: ctx.bot.send_message(u.effective_chat.id, u.message.text)))

# --- Webhook registration on startup ---
@app.on_event("startup")
async def register_webhook():
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    url = WEBHOOK_URL.rstrip("/") + "/" + TELEGRAM_BOT_TOKEN
    await bot.set_webhook(url)
    logger.info(f"Registered webhook: {url}")

# --- Telegram webhook endpoint ---
@app.post(f"/{TELEGRAM_BOT_TOKEN}")
async def telegram_webhook(request: Request):
    data = await request.json()
    upd = Update.de_json(data, Bot(TELEGRAM_BOT_TOKEN))
    await telegram_app.process_update(upd)
    return {"ok": True}
