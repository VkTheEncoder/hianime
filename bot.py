#!/usr/bin/env python3
import os
import subprocess
import logging
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ─── Setup & Token ────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Telegram Bot (and delete any stray webhook)
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fetch_hls_and_cookies(page_url: str, timeout: int = 30000):
    """
    If given a page URL, launches headless Chromium, goes there,
    waits for the first .m3u8 request, then returns that URL + cookies.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Wait *while* we navigate
        with page.expect_request("**/*.m3u8", timeout=timeout) as req_info:
            page.goto(page_url, timeout=timeout)
        hls_url = req_info.value.url

        # Grab whatever cookies the page has set
        cookies = context.cookies()
        browser.close()

    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return hls_url, cookie_header

def run_ffmpeg(hls_url: str, cookie_header: str, timeout=600_000):
    """
    Given a signed .m3u8 and its cookies, runs ffmpeg to remux into video.mp4.
    """
    p = urlparse(hls_url)
    referer = f"{p.scheme}://{p.netloc}"

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )

    output = "video.mp4"
    cmd = [
        "ffmpeg",
        "-cookies", "1",
        "-cookie_file", "-",           # read cookies from stdin
        "-user_agent", ua,
        "-referer", referer,
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-i", hls_url,
        "-c", "copy",
        output
    ]

    proc = subprocess.run(
        cmd,
        input=(cookie_header + "\n").encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=timeout
    )
    return output

# ─── Handlers ────────────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    logger.info("Received /start from %s", update.effective_user.id)
    update.message.reply_text(
        "👋 Hello! I’m up. Send:\n\n"
        "`/download <page_url_or_m3u8>`\n\n"
        "and I’ll fetch the video for you."
    )

def download(update: Update, context: CallbackContext):
    logger.info("Received /download from %s args=%r",
                update.effective_user.id, context.args)
    if not context.args:
        return update.message.reply_text(
            "Usage: `/download <page_url_or_m3u8>`", parse_mode="Markdown"
        )

    target = context.args[0]
    chat_id = update.effective_chat.id
    update.message.reply_text("⏳ Working…")

    try:
        # Always use the headless-browser fetch to get both URL & cookies
        hls_url, cookie_header = fetch_hls_and_cookies(target)
        logger.info("Got playlist URL: %s", hls_url)

        video_path = run_ffmpeg(hls_url, cookie_header)
    except PlaywrightTimeoutError:
        return update.message.reply_text("❌ Timeout waiting for the player.")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore")
        logger.error("ffmpeg error: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
    except Exception as e:
        logger.exception("Error in /download")
        return update.message.reply_text(f"❌ Error: {e}")

    # Send the result
    with open(video_path, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(video_path)
    logger.info("Sent %s and cleaned up", video_path)

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    logger.info("Bot is polling…")
    updater.start_polling()
    updater.idle()
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
