import os
import requests
import fitz  # PyMuPDF
import logging
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
)
from dotenv import load_dotenv
import openai

# --- Load .env configuration ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Monitors ---
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
            logger.error("Failed to fetch ADMIS page: %s", e)
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        new_articles = []
        for h3 in soup.find_all("h3"):
            a = h3.find('a')
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a['href']
            url = href if href.startswith("http") else self.BASE_URL + href
            date_tag = h3.find_next_sibling("p")
            date = date_tag.get_text(strip=True) if date_tag else ""
            source = "ADMIS Written Commentary"
            if url not in self.seen:
                self.seen.add(url)
                new_articles.append({"title": title, "url": url, "date": date, "source": source})
        return new_articles


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
            logger.error("Failed to fetch Saxo Insights page: %s", e)
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        new_articles = []

        for a in soup.find_all("a", href=True):
            href = a['href']
            if not href.startswith("/content/articles/"):
                continue
            title = a.get_text(strip=True)
            if not title or title.lower() in ("learn more", "read more"):
                continue
            url = self.BASE_URL + href
            if url in self.seen:
                continue
            self.seen.add(url)
            new_articles.append({
                "title": title,
                "url": url,
                "date": "",
                "source": "Saxo Bank Research"
            })
            logger.info("New SAXO article detected: %s", title)

        return new_articles

# --- Globals ---
pending_articles = {}

# --- Command: /start ---
def start_bot(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("Unauthorized.")
        return
    update.message.reply_text("Bot is running. I will notify you of new research articles.")

# --- Callback: summarize article ---
def insights_callback(update: Update, context):
    query = update.callback_query
    query.answer()
    data = query.data
    if not data.startswith("INSIGHTS|"):
        return
    article_id = data.split("|", 1)[1]
    article = pending_articles.get(article_id)
    if not article:
        query.edit_message_text("Article info not found.")
        return

    title, url, source, date = (
        article["title"],
        article["url"],
        article["source"],
        article["date"],
    )

    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch article content: %s", e)
        query.edit_message_text("Failed to load article content.")
        return

    content = ""
    if url.lower().endswith(".pdf"):
        try:
            doc = fitz.open(stream=resp.content, filetype="pdf")
            for page in doc:
                content += page.get_text("text")
        except Exception as e:
            logger.error("PyMuPDF failed: %s", e)
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
        paragraphs = soup.find_all('p')
        content = "\n".join(p.get_text() for p in paragraphs)

    if not content.strip():
        query.edit_message_text("No text extracted from article.")
        return

    openai.api_key = OPENAI_API_KEY
    prompt = (
        "Summarize the following research article with sections:\n"
        "Title, Key points, Impact on markets, Source, Date, Link.\n\n"
        f"Title: {title}\n"
        f"Source: {source}\n"
        f"Date: {date}\n"
        f"Link: {url}\n\n"
        "Article Text:\n" + content
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        summary = response.choices[0].message.content
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        query.edit_message_text("Error summarizing the article.")
        return

    query.edit_message_text(text=summary, parse_mode='Markdown')

# --- Periodic job ---
def check_sites_callback(context):
    bot = context.bot
    monitors = [ADMISMonitor(), SaxoMonitor()]
    for monitor in monitors:
        for article in monitor.check_new():
            title, url, date, source = (
                article["title"],
                article["url"],
                article["date"],
                article["source"],
            )
            msg = (
                f"📌 *New research from {source}*\n"
                f"📅 {date or 'N/A'}\n"
                f"📰 Title: {title}\n"
                f"🔗 [Read the original]({url})\n"
                "⬇️ Click below for a concise analysis:"
            )
            article_id = f"{source}_{hash(url)}"
            pending_articles[article_id] = article
            button = InlineKeyboardButton("🧠 Load Insights", callback_data=f"INSIGHTS|{article_id}")
            keyboard = InlineKeyboardMarkup([[button]])
            bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            logger.info("Sent alert for new article: %s", title)

# --- Main ---
def main():
    # Build the bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Clear webhook (so polling works)
    app.bot.delete_webhook()

    # Handlers
    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CallbackQueryHandler(insights_callback, pattern=r"^INSIGHTS\|"))

    # Schedule the polling job
    app.job_queue.run_repeating(check_sites_callback, interval=600, first=0)

    # Start polling (drop old updates)
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
import os
import requests
import fitz  # PyMuPDF
import logging
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
)
from dotenv import load_dotenv
import openai

# --- Load .env configuration ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Monitors ---
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
            logger.error("Failed to fetch ADMIS page: %s", e)
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        new_articles = []
        for h3 in soup.find_all("h3"):
            a = h3.find('a')
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a['href']
            url = href if href.startswith("http") else self.BASE_URL + href
            date_tag = h3.find_next_sibling("p")
            date = date_tag.get_text(strip=True) if date_tag else ""
            source = "ADMIS Written Commentary"
            if url not in self.seen:
                self.seen.add(url)
                new_articles.append({"title": title, "url": url, "date": date, "source": source})
        return new_articles


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
            logger.error("Failed to fetch Saxo Insights page: %s", e)
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        new_articles = []

        for a in soup.find_all("a", href=True):
            href = a['href']
            if not href.startswith("/content/articles/"):
                continue
            title = a.get_text(strip=True)
            if not title or title.lower() in ("learn more", "read more"):
                continue
            url = self.BASE_URL + href
            if url in self.seen:
                continue
            self.seen.add(url)
            new_articles.append({
                "title": title,
                "url": url,
                "date": "",
                "source": "Saxo Bank Research"
            })
            logger.info("New SAXO article detected: %s", title)

        return new_articles

# --- Globals ---
pending_articles = {}

# --- Command: /start ---
def start_bot(update: Update, context):
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("Unauthorized.")
        return
    update.message.reply_text("Bot is running. I will notify you of new research articles.")

# --- Callback: summarize article ---
def insights_callback(update: Update, context):
    query = update.callback_query
    query.answer()
    data = query.data
    if not data.startswith("INSIGHTS|"):
        return
    article_id = data.split("|", 1)[1]
    article = pending_articles.get(article_id)
    if not article:
        query.edit_message_text("Article info not found.")
        return

    title, url, source, date = (
        article["title"],
        article["url"],
        article["source"],
        article["date"],
    )

    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch article content: %s", e)
        query.edit_message_text("Failed to load article content.")
        return

    content = ""
    if url.lower().endswith(".pdf"):
        try:
            doc = fitz.open(stream=resp.content, filetype="pdf")
            for page in doc:
                content += page.get_text("text")
        except Exception as e:
            logger.error("PyMuPDF failed: %s", e)
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
        paragraphs = soup.find_all('p')
        content = "\n".join(p.get_text() for p in paragraphs)

    if not content.strip():
        query.edit_message_text("No text extracted from article.")
        return

    openai.api_key = OPENAI_API_KEY
    prompt = (
        "Summarize the following research article with sections:\n"
        "Title, Key points, Impact on markets, Source, Date, Link.\n\n"
        f"Title: {title}\n"
        f"Source: {source}\n"
        f"Date: {date}\n"
        f"Link: {url}\n\n"
        "Article Text:\n" + content
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        summary = response.choices[0].message.content
    except Exception as e:
        logger.error("OpenAI API error: %s", e)
        query.edit_message_text("Error summarizing the article.")
        return

    query.edit_message_text(text=summary, parse_mode='Markdown')

# --- Periodic job ---
def check_sites_callback(context):
    bot = context.bot
    monitors = [ADMISMonitor(), SaxoMonitor()]
    for monitor in monitors:
        for article in monitor.check_new():
            title, url, date, source = (
                article["title"],
                article["url"],
                article["date"],
                article["source"],
            )
            msg = (
                f"📌 *New research from {source}*\n"
                f"📅 {date or 'N/A'}\n"
                f"📰 Title: {title}\n"
                f"🔗 [Read the original]({url})\n"
                "⬇️ Click below for a concise analysis:"
            )
            article_id = f"{source}_{hash(url)}"
            pending_articles[article_id] = article
            button = InlineKeyboardButton("🧠 Load Insights", callback_data=f"INSIGHTS|{article_id}")
            keyboard = InlineKeyboardMarkup([[button]])
            bot.send_message(
                chat_id=ADMIN_ID,
                text=msg,
                reply_markup=keyboard,
                parse_mode='Markdown'
            )
            logger.info("Sent alert for new article: %s", title)

# --- Main ---
def main():
    # Build the bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Clear webhook (so polling works)
    app.bot.delete_webhook()

    # Handlers
    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CallbackQueryHandler(insights_callback, pattern=r"^INSIGHTS\|"))

    # Schedule the polling job
    app.job_queue.run_repeating(check_sites_callback, interval=600, first=0)

    # Start polling (drop old updates)
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
