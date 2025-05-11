#!/usr/bin/env python3
import os
import subprocess
import logging
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

# headless browser
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ─── Setup & Token ────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Helper: launch Playwright, grab m3u8 + cookies ───────────────────────────
def fetch_hls_and_cookies(page_url: str, timeout: int = 30000):
    """
    Loads the video page in headless Chromium, waits for the .m3u8 request,
    and returns the first playlist URL plus all session cookies.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # wait for the first .m3u8 request
        try:
            page.goto(page_url, timeout=timeout)
            m3u8_req = page.wait_for_request(
                lambda req: req.url.endswith(".m3u8"), timeout=timeout
            )
        except PlaywrightTimeoutError:
            browser.close()
            raise RuntimeError("Timed out waiting for HLS playlist")

        hls_url = m3u8_req.url
        # collect cookies for that host
        raw_cookies = context.cookies()
        browser.close()

    # build cookie header string
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in raw_cookies)
    return hls_url, cookie_header

# ─── /start handler ───────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hi! Send me:\n\n"
        "`/download_page <video_page_url>`\n\n"
        "and I’ll fetch and upload the video for you."
    )

# ─── /download_page handler ──────────────────────────────────────────────────
def download_page(update: Update, context: CallbackContext):
    if not context.args:
        return update.message.reply_text(
            "Usage: `/download_page <video_page_url>`", parse_mode="Markdown"
        )

    page_url = context.args[0]
    chat_id  = update.effective_chat.id
    update.message.reply_text("⏳ Launching browser to fetch HLS…")

    try:
        hls_url, cookie_header = fetch_hls_and_cookies(page_url)
    except Exception as e:
        logger.error("Playwright failed", exc_info=e)
        return update.message.reply_text(f"❌ Browser error: {e}")

    update.message.reply_text(f"🔗 Got playlist: `{hls_url}`", parse_mode="Markdown")

    # Build ffmpeg command using the exact cookies & referer
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
    parsed = urlparse(hls_url)
    referer = f"{parsed.scheme}://{parsed.netloc}"

    output = "video.mp4"
    cmd = [
        "ffmpeg",
        "-cookies", "1",
        "-cookie_file", "-",            # read cookies from stdin
        "-user_agent", ua,
        "-referer", referer,
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-i", hls_url,
        "-c", "copy",
        output
    ]

    # run ffmpeg, piping in the cookie header
    try:
        proc = subprocess.run(
            cmd,
            input=f"{cookie_header}\n".encode(),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600_000  # 10 minutes max
        )
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore")
        logger.error("ffmpeg failed: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
    except subprocess.TimeoutExpired:
        return update.message.reply_text("❌ Download timed out.")

    # send back the video
    with open(output, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(output)
    logger.info("✅ Video sent and cleaned up")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download_page", download_page))

    logger.info("Bot is polling…")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
