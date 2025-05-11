#!/usr/bin/env python3
import os, subprocess, logging
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─── Load token ───────────────────────────────────────────────────────────────
load_dotenv()  
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ─── Telegram init ────────────────────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fetch_signed_hls_and_cookies(page_url, timeout=30000):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        with page.expect_request("**/*.m3u8", timeout=timeout) as req:
            page.goto(page_url, timeout=timeout)
        hls = req.value.url
        cookies = ctx.cookies()
        browser.close()
    # build Cookie header
    cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return hls, cookie_hdr

def ffmpeg_download(hls_url, cookie_header, timeout=600_000):
    p = urlparse(hls_url)
    referer = f"{p.scheme}://{p.netloc}"
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/114.0.0.0 Safari/537.36")
    headers = (
        f"Cookie: {cookie_header}\r\n"
        f"Referer: {referer}\r\n"
        f"User-Agent: {ua}\r\n"
    )
    out = "video.mp4"
    cmd = [
        "ffmpeg",
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-headers", headers,
        "-i", hls_url,
        "-c", "copy",
        out
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return out

# ─── Handlers ────────────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 I’m alive! Send:\n\n"
        "`/download <page_url_or_m3u8>`\n\n"
        "and I’ll fetch the video for you."
    )

def download(update: Update, context: CallbackContext):
    if not context.args:
        return update.message.reply_text("Usage: `/download <page_url_or_m3u8>`", parse_mode="Markdown")
    target = context.args[0]
    chat = update.effective_chat.id
    update.message.reply_text("⏳ Fetching stream…")

    try:
        # always run the headless browser step to get a signed HLS + cookies
        hls_url, cookie_hdr = fetch_signed_hls_and_cookies(target)
        logger.info("Got HLS: %s", hls_url)
        video = ffmpeg_download(hls_url, cookie_hdr)
    except PWTimeout:
        return update.message.reply_text("❌ Timeout waiting for the player.")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore")
        logger.error("FFmpeg failed: %s", err)
        return update.message.reply_text(f"❌ FFmpeg error:\n{err[:200]}")
    except Exception as e:
        logger.exception("Download error")
        return update.message.reply_text(f"❌ Error: {e}")

    with open(video, "rb") as f:
        context.bot.send_video(chat_id=chat, video=f)
    os.remove(video)
    logger.info("Done.")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    logger.info("Bot polling…")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
