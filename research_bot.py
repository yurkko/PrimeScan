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
import json
import logging
from filelock import FileLock
from dotenv import load_dotenv
from datetime import datetime, timedelta
import pytz
import re

# --- Load environment variables ---
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ALLOWED_USER_IDS = [int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x]

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Files ---
ARTICLES_FILE = "articles.json"

# --- Load and save articles ---
def load_articles():
    try:
        with open(ARTICLES_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"pending_articles": {}}

def save_articles(articles):
    with FileLock(f"{ARTICLES_FILE}.lock"):
        with open(ARTICLES_FILE, "w") as f:
            json.dump(articles, f)

# --- Initialize global data ---
articles_data = load_articles()
pending_articles = articles_data["pending_articles"]

# --- Monitors ---
class ADMISMonitor:
    BASE_URL = "https://www.admis.com"
    LIST_URL = BASE_URL + "/market-information/written-commentary/"
    SEEN_URLS_FILE = "seen_urls_admis.json"  # Окремий файл для ADMIS

    def __init__(self):
        try:
            with open(self.SEEN_URLS_FILE, "r") as f:
                self.seen = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.seen = set()
        logger.info("Initialized ADMISMonitor with %d seen URLs", len(self.seen))

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
            try:
                parsed_date = datetime.strptime(date, "%B %d, %Y")
                date = parsed_date.strftime("%H:%M %d/%m/%Y")
            except (ValueError, TypeError):
                date = date
            source = "ADMIS Written Commentary"
            if url not in self.seen:
                with FileLock(f"{self.SEEN_URLS_FILE}.lock"):
                    self.seen.add(url)
                    with open(self.SEEN_URLS_FILE, "w") as f:
                        json.dump(list(self.seen), f)
                new.append({"title": title, "url": url, "date": date, "source": source})
                logger.info("New URL added: %s", url)
        logger.info("Checked ADMIS, found %d new articles", len(new))
        return new

class SaxoMonitor:
    INSIGHTS_URL = "https://www.home.saxo/insights"
    BASE_URL = "https://www.home.saxo"
    SEEN_URLS_FILE = "seen_urls_saxo.json"  # Окремий файл для Saxo

    def __init__(self):
        try:
            with open(self.SEEN_URLS_FILE, "r") as f:
                self.seen = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.seen = set()
        logger.info("Initialized SaxoMonitor with %d seen URLs", len(self.seen))

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
            date = ""  # Saxo не завжди має дату на сторінці
            source = "Saxo Bank Research"
            if url not in self.seen:
                with FileLock(f"{self.SEEN_URLS_FILE}.lock"):
                    self.seen.add(url)
                    with open(self.SEEN_URLS_FILE, "w") as f:
                        json.dump(list(self.seen), f)
                new.append({"title": title, "url": url, "date": date, "source": source})
                logger.info("New URL added: %s", url)
        logger.info("Checked Saxo, found %d new articles", len(new))
        return new

class SSGAInsightsMonitor:
    BASE_URL = "https://www.ssga.com"
    INSIGHTS_URL = BASE_URL + "/us/en/institutional/insights"
    SEEN_URLS_FILE = "seen_urls_ssga.json"  # Окремий файл для SSGA

    def __init__(self):
        try:
            with open(self.SEEN_URLS_FILE, "r") as f:
                self.seen = set(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            self.seen = set()
        logger.info("Ініціалізовано SSGAInsightsMonitor з %d переглянутими URL", len(self.seen))

    def check_new(self):
    try:
        resp = requests.get(self.INSIGHTS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        results_list = soup.find("ul", class_="results-list")
        logger.info("Results list from requests: %s", "Found" if results_list else "Not found")
    except Exception as e:
        logger.error("Не вдалося отримати сторінку SSGA Insights: %s", e)
        return []

    if not results_list:
        logger.warning("Список статей не знайдено, можливо, JavaScript-завантаження. HTML: %s", soup.prettify()[:500])
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--disable-gpu")
            driver = webdriver.Chrome(options=options)
            driver.get(self.INSIGHTS_URL)
            time.sleep(5)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            driver.quit()
            results_list = soup.find("ul", class_="results-list")
            logger.info("Results list from Selenium: %s", "Found" if results_list else "Not found")
            if not results_list:
                logger.error("Selenium не знайшов список статей. HTML: %s", soup.prettify()[:500])
                return []
        except Exception as e:
            logger.error("Selenium помилка: %s", e)
            return []

    new_articles = []
        for item in results_list.find_all("li", recursive=False):
            title_tag = item.find("h2")
            title = title_tag.get_text(strip=True) if title_tag else "Без заголовка"

            link_tag = item.find("a", href=True)
            href = link_tag["href"] if link_tag else ""
            url = href if href.startswith("http") else self.BASE_URL + href

            date_tag = item.find("time")
            raw_date = date_tag.get_text(strip=True) if date_tag else ""
            # Нормалізація дати
            try:
                parsed_date = datetime.strptime(raw_date, "%B %d, %Y")  # Наприклад, "October 10, 2024"
                date = parsed_date.strftime("%H:%M %d/%m/%Y")
            except (ValueError, TypeError):
                try:
                    parsed_date = datetime.strptime(raw_date, "%m/%d/%Y")  # Альтернативний формат
                    date = parsed_date.strftime("%H:%M %d/%m/%Y")
                except (ValueError, TypeError):
                    date = raw_date  # Якщо формат невідомий, залишити як є

            source = "SSGA Insights"
            if url and url not in self.seen:
                with FileLock(f"{self.SEEN_URLS_FILE}.lock"):
                    self.seen.add(url)
                    with open(self.SEEN_URLS_FILE, "w") as f:
                        json.dump(list(self.seen), f)
                new_articles.append({"title": title, "url": url, "date": date, "source": source})
                logger.info("Додано нову статтю: %s", url)

        logger.info("Перевірено SSGA Insights, знайдено %d нових статей", len(new_articles))
        return new_articles

# --- /start handler ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in [ADMIN_ID] + ALLOWED_USER_IDS:
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
        await query.edit_message_text("Стаття не знайдена. Можливо, вона застаріла або була видалена.")
        logger.error("Стаття не знайдена для art_id: %s", art_id)
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

        if date and date.lower() != "n/a":
            try:
                utc_time = datetime.strptime(date, "%H:%M %d/%m/%Y")
                utc_tz = pytz.UTC
                utc_time = utc_tz.localize(utc_time)
                eest_tz = pytz.timezone("Europe/Kiev")
                eest_time = utc_time.astimezone(eest_tz)
                full_date = eest_time.strftime("%H:%M %d/%m/%Y")
            except ValueError:
                full_date = date
        else:
            eest_tz = pytz.timezone("Europe/Kiev")
            full_date = datetime.now(eest_tz).strftime("%H:%M %d/%m/%Y")

        ua_prompt = (
            "Підсумуйте наступну дослідницьку статтю українською мовою з цією точною структурою з емодзі та жирним текстом, використовуючи деталі з тексту статті:\n"
            "📰 *Title*: " + title + "\n"
            "📌 *Key Points*:\n"
            "  ▪️ [детальний пункт 1 з тексту статті]\n"
            "  ▪️ [детальний пункт 2 з тексту статті]\n"
            "  ▪️ [детальний пункт 3 з тексту статті]\n"
            "📊 *Impact on Markets*:\n"
            "  ▪️ [конкретний опис впливу на ринки на основі тексту статті]\n"
            "📚 *Source*: " + source + "\n"
            "📅 *Date*: " + full_date + "\n"
            "🔗 *Link*: " + url + "\n\n"
            "Article Text:\n" + content
        )
        ua_data = {
            "model": "openai/gpt-4.1",
            "messages": [{"role": "user", "content": ua_prompt}],
            "max_tokens": 1500
        }
        ua_response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=ua_data)
        ua_response.raise_for_status()
        ua_summary = ua_response.json()["choices"][0]["message"]["content"]

        await query.edit_message_text(text=ua_summary, parse_mode='Markdown')
    except Exception as e:
        logger.error("OpenRouter error: %s", e)
        await query.edit_message_text("Error summarizing or translating.")
        return

# --- Periodic job ---
async def check_sites_callback(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    monitors = [ADMISMonitor(), SaxoMonitor(), SSGAInsightsMonitor()]
    new_articles = []

    eest_tz = pytz.timezone("Europe/Kiev")
    seen_in_cycle = set()

    # Очищення старих pending_articles (старше 30 днів)
    global pending_articles
    cutoff_date = datetime.now(eest_tz) - timedelta(days=30)
    pending_articles = {
        art_id: art for art_id, art in pending_articles.items()
        if art.get("date") and (
            not art["date"].lower() == "n/a" and
            datetime.strptime(art["date"], "%H:%M %d/%m/%Y") > cutoff_date
        )
    }
    save_articles({"pending_articles": pending_articles})
    logger.info("Очищено pending_articles, залишилося %d записів", len(pending_articles))

    for mon in monitors:
        for art in mon.check_new():
            url = art["url"]
            if url not in seen_in_cycle:
                seen_in_cycle.add(url)
                title, date, source = art["title"], art["date"], art["source"]
                original_title = title

                # Очищення заголовка з тестуванням регулярних виразів
                prefix_pattern = r'^(?:\w+\s*-\s*(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}|\d+\s+(?:hours|days)\s+ago)\s*)*'
                title = re.sub(prefix_pattern, '', title, flags=re.IGNORECASE).strip()
                if not title:
                    title = original_title  # Повернення оригіналу, якщо заголовок став порожнім
                title = re.sub(r'(.+?)\1+', r'\1', title).strip()
                parts = title.split(".", 1)
                if len(parts) > 1 and parts[0].strip() in parts[1]:
                    title = parts[0].strip() + "."

                if "podcast" in title.lower() or "webinar" in title.lower():
                    logger.info("Skipped: %s (Podcast/Webinar)", original_title)
                    continue

                send_time = datetime.now(eest_tz).strftime("%H:%M %d/%m/%Y")
                msg = (
                    f"📌 *New research from: {source}*\n"
                    f"📅 {send_time}\n"
                    f"📰 **Title**: {title}**\n"
                    f"🔗 [Read the original]({url})\n\n"
                    "⬇️ Click below for a concise analysis:"
                )
                art_id = f"{source}_{hash(url)}"
                if art_id not in pending_articles:
                    pending_articles[art_id] = art
                    new_articles.append((msg, art_id))

    logger.info("Found %d new articles in this cycle", len(new_articles))
    allowed_users = [ADMIN_ID] + ALLOWED_USER_IDS
    for msg, art_id in new_articles:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🧠 Load Insights", callback_data=f"INSIGHTS|{art_id}")]])
        if len(msg) > 4096:
            title_end = msg.find("🔗 [Read the original]")
            if title_end != -1:
                msg = msg[:title_end].strip()[:4000] + f"\n🔗 [Read the original]({new_articles[0][0].split('🔗 [Read the original](')[1].split(')')[0]})"
        for user_id in allowed_users:
            try:
                await bot.send_message(chat_id=user_id, text=msg, reply_markup=kb, parse_mode='Markdown')
                logger.info("Alert sent to %d: %s", user_id, msg.split("\n")[2].replace("📰 **Title**: ", "").replace("**", ""))
            except Exception as e:
                logger.error("Failed to send message to %d: %s", user_id, e)

    save_articles({"pending_articles": pending_articles})

# --- Entrypoint ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CallbackQueryHandler(insights_callback, pattern=r"^INSIGHTS\|"))

    # Schedule scraping every minute
    app.job_queue.run_repeating(check_sites_callback, interval=60, first=5)

    # Start polling
    time.sleep(10)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
