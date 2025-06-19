import os
import logging
import requests
import fitz
import openai
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.ext.fastapi_handler import get_webhook_handler
from telegram.constants import ParseMode

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- FastAPI App ---
app = FastAPI()

# --- Article Monitors ---
class ADMISMonitor:
    BASE_URL = "https://www.admis.com"
    LIST_URL = BASE_URL + "/market-information/written-commentary/"

    def __init__(self):
        self.seen = set()

    def check_new(self):
        try:
            resp = requests.get(self.LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch ADMIS: %s", e)
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        new = []
        for h3 in soup.find_all("h3"):
            a = h3.find('a')
            if not a: continue
            title = a.get_text(strip=True)
            href = a['href']
            url = href if href.startswith("http") else self.BASE_URL + href
            date_tag = h3.find_next_sibling("p")
            date = date_tag.get_text(strip=True) if date_tag else ""
            if url not in self.seen:
                self.seen.add(url)
                new.append({
                    "title": title, "url": url, "date": date,
                    "source": "ADMIS Written Commentary"
                })
        return new

class SaxoMonitor:
    INSIGHTS_URL = "https://www.home.saxo/insights"
    BASE_URL = "https://www.home.saxo"

    def __init__(self):
        self.seen = set()

    def check_new(self):
        try:
            resp = requests.get(self.INSIGHTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch Saxo: %s", e)
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        new = []
        for a in soup.find_all("a", href=True):
            href = a['href']
            if "/content/articles/" not in href:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            url = href if href.startswith("http") else self.BASE_URL + href
            if url in self.seen:
                continue
            self.seen.add(url)
            new.append({
                "title": title, "url": url, "date": "",
                "source": "Saxo Bank Research"
            })
        return new

# --- Global Storage ---
pending_articles = {}

# --- Handlers ---
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("✅ Bot is running. I will notify you of new research.")

async def insights_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if not data.startswith("INSIGHTS|"):
        return
    art_id = data.split("|", 1)[1]
    article = pending_articles.get(art_id)
    if not article:
        await query.edit_message_text("Article info not found.")
        return

    title, url, source, date = article["title"], article["url"], article["source"], article["date"]
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch article: %s", e)
        await query.edit_message_text("Failed to fetch article.")
        return

    content = ""
    if url.lower().endswith(".pdf"):
        try:
            doc = fitz.open(stream=resp.content, filetype="pdf")
            for page in doc:
                content += page.get_text("text")
        except Exception as e:
            logger.error("PDF parse error: %s", e)
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
        content = "\n".join(p.get_text() for p in soup.find_all("p"))

    if not content.strip():
        await query.edit_message_text("No content extracted.")
        return

    openai.api_key = OPENAI_API_KEY
    prompt = (
        "Summarize the following research article with sections:\n"
        "Title, Key points, Impact on markets, Source, Date, Link.\n\n"
        f"Title: {title}\nSource: {source}\nDate: {date}\nLink: {url}\n\n"
        f"Article Text:\n{content}"
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        resp = await client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        summary = resp.choices[0].message.content
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        await query.edit_message_text("Summarization failed.")
        return

    await query.edit_message_text(summary[:4000], parse_mode=ParseMode.MARKDOWN)

# --- Scheduled Job ---
async def check_sites(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    monitors = [ADMISMonitor(), SaxoMonitor()]
    for mon in monitors:
        for art in mon.check_new():
            title, url, date, source = art["title"], art["url"], art["date"], art["source"]
            art_id = f"{source}_{hash(url)}"
            if art_id in pending_articles:
                continue
            pending_articles[art_id] = art
            msg = (
                f"*New research from {source}*\n"
                f"📅 {date or 'Unknown'}\n"
                f"📰 {title}\n"
                f"🔗 [Read original]({url})\n\n"
                f"⬇️ Click below to summarize:"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🧠 Load Insights", callback_data=f"INSIGHTS|{art_id}")]
            ])
            await bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            logger.info("Alert sent: %s", title)

# --- Main Entrypoint ---
@app.on_event("startup")
async def startup():
    application.job_queue.run_repeating(check_sites, interval=600, first=5)
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    logger.info("Webhook set.")

# --- Create Telegram App ---
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(CommandHandler("start", start_handler))
application.add_handler(CallbackQueryHandler(insights_callback, pattern=r"^INSIGHTS\|"))

# --- Mount Telegram Webhook ---
app.mount(f"/{BOT_TOKEN}", get_webhook_handler(application))
