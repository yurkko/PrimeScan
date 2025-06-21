import os
import requests
import fitz  # PyMuPDF
import logging
import asyncio
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
import time
import json  # Added for JSON file handling
from dotenv import load_dotenv

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")  # Оновлено з OPENAI_API_KEY
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Monitors ---
class ADMISMonitor:
    BASE_URL = "https://www.admis.com"
    LIST_URL = BASE_URL + "/market-information/written-commentary/"

    def __init__(self):
        try:
            with open("seen_urls.txt", "r") as f:
                self.seen = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.seen = set()

    def check_new(self):
        try:
            resp = requests.get(self.LIST_URL, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch ADMIS page: %s", e)
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        new = []
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
                with open("seen_urls.txt", "w") as f:
                    json.dump(list(self.seen), f)
                new.append({"title": title, "url": url, "date": date, "source": source})
        return new

class SaxoMonitor:
    INSIGHTS_URL = "https://www.home.saxo/insights"
    BASE_URL = "https://www.home.saxo"

    def __init__(self):
        try:
            with open("seen_urls.txt", "r") as f:
                self.seen = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.seen = set()

    def check_new(self):
        try:
            resp = requests.get(self.INSIGHTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch Saxo Insights page: %s", e)
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
            if url not in self.seen:
                self.seen.add(url)
                with open("seen_urls.txt", "w") as f:
                    json.dump(list(self.seen), f)
                new.append({"title": title, "url": url, "date": "", "source": "Saxo Bank Research"})
        return new

# --- Globals ---
pending_articles = {}

# --- /start handler ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Bot is running. I will notify you of new research articles.")

# --- Button callback ---
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

    title, url, source, date = (
        article["title"], article["url"], article["source"], article["date"]
    )

    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch article: %s", e)
        await query.edit_message_text("Failed to load article content.")
        return

    content = ""
    if url.lower().endswith(".pdf"):
        try:
            doc = fitz.open(stream=resp.content, filetype="pdf")
            for page in doc:
                content += page.get_text("text")
        except Exception as e:
            logger.error("PyMuPDF error: %s", e)
    else:
        soup = BeautifulSoup(resp.text, 'html.parser')
        content = "\n".join(p.get_text() for p in soup.find_all("p"))

    if not content.strip():
        await query.edit_message_text("No text extracted.")
        return

    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }

        # Використовуємо поточний час, якщо дата відсутня
        from datetime import datetime
        full_date = date if date and date.lower() != "n/a" else datetime.now().strftime("%H:%M %d/%m/%Y")

        # Генерація підсумку українською з деталями
        ua_prompt = (
            "Підсумуйте наступну дослідницьку статтю українською мовою з цією точною структурою з емодзі та жирним текстом, використовуючи деталі з тексту статті:\n"
            "📰 **Title**: " + title + "\n"
            "📌 **Key Points**:\n"
            "  ▪️ [детальний пункт 1 з тексту статті]\n"
            "  ▪️ [детальний пункт 2 з тексту статті]\n"
            "  ▪️ [детальний пункт 3 з тексту статті]\n"
            "📊 **Impact on Markets**:\n"
            "  ▪️ [конкретний опис впливу на ринки на основі тексту статті]\n"
            "📚 **Source**: " + source + "\n"
            "📅 **Date**: " + full_date + "\n"
            "🔗 **Link**: " + url + "\n\n"
            "Article Text:\n" + content
        )
        ua_data = {
            "model": "openai/gpt-4.1",
            "messages": [{"role": "user", "content": ua_prompt}],
            "max_tokens": 700  # Збільшено для уникнення обрізання
        }
        ua_response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=ua_data)
        ua_response.raise_for_status()
        ua_summary = ua_response.json()["choices"][0]["message"]["content"]

        await query.edit_message_text(text=ua_summary, parse_mode='Markdown')
    except Exception as e:
        logger.error("OpenRouter error: %s", e)
        await query.edit_message_text("Error summarizing or translating.")
        return

    await query.edit_message_text(text=summary, parse_mode='Markdown')

# --- Periodic job ---
async def check_sites_callback(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    monitors = [ADMISMonitor(), SaxoMonitor()]
    new_articles = []  # Тимчасовий список для збору статей

    # Імпорт регулярних виразів і datetime
    import re
    from datetime import datetime

    # Збираємо всі нові статті
    for mon in monitors:
        for art in mon.check_new():
            title, url, date, source = art["title"], art["url"], art["date"], art["source"]
            
            # Видаляємо префікси та часові позначки
            original_title = title
            prefix_pattern = r'^(Options|Macro|Equities|Commodities|Podcast)\s*-\s*(\d+\s+(minutes|hours|days)\s+ago)?\s*'
            title = re.sub(prefix_pattern, '', title, flags=re.IGNORECASE).strip()

            # Видаляємо подвоєння тексту
            if len(title) > 10:
                half_length = len(title) // 2
                if title[half_length:] == title[:half_length]:
                    title = title[:half_length].strip()
                elif title.count(title[:len(title)//3]) > 1:
                    unique_part = re.match(r'^(.+?)(?:\1)', title)
                    if unique_part:
                        title = unique_part.group(1).strip()

            # Додаткова обробка подвоєння через крапку
            parts = title.split(".", 1)
            if len(parts) > 1 and parts[0].strip() in parts[1]:
                title = parts[0].strip() + "."

            # Пропускаємо, якщо це Podcast або Webinar
            if "podcast" in title.lower() or "webinar" in title.lower():
                logger.info("Skipped: %s (Podcast/Webinar)", original_title)
                continue

            # Час відправлення
            send_time = datetime.now().strftime("%H:%M %d/%m/%Y")

            msg = (
                f"📌 *New research from: {source}*\n"
                f"📅 {send_time}\n"
                f"📰 *Title: {title}*\n"  # Повернуто "Title:" із жирним форматуванням
                f"🔗 [Read the original]({url})\n\n"
                "⬇️ Click below for a concise analysis:"
            )
            art_id = f"{source}_{hash(url)}"
            pending_articles[art_id] = art
            new_articles.append((msg, art_id))

    # Відправляємо статті від старіших до новішої
    for msg, art_id in new_articles:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧠 Load Insights", callback_data=f"INSIGHTS|{art_id}")]])
        await bot.send_message(chat_id=ADMIN_ID, text=msg, reply_markup=kb, parse_mode='Markdown')
        logger.info("Alert sent: %s", msg.split("\n")[2].replace("📰 **Title: ", "").replace("**", ""))  # Логуємо title

# --- Entrypoint ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CallbackQueryHandler(insights_callback, pattern=r"^INSIGHTS\|"))

    # Schedule scraping every 10 minutes
    app.job_queue.run_repeating(check_sites_callback, interval=60, first=5)

    # Start polling (blocks, handles its own loop)
    time.sleep(10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
