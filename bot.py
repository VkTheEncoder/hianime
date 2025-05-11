#!/usr/bin/env python3
import os
import subprocess
import logging
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

# ─── Setup & Token ────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Telegram Bot init ─────────────────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── /start handler ───────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hi! Send me:\n\n"
        "`/download <m3u8_url>`\n\n"
        "and I’ll fetch the video for you."
    )

# ─── /download handler ────────────────────────────────────────────────────────
def download(update: Update, context: CallbackContext):
    if not context.args:
        return update.message.reply_text("Usage: `/download <m3u8_url>`", parse_mode="Markdown")

    m3u8_url = context.args[0]
    chat_id   = update.effective_chat.id
    update.message.reply_text("⏳ Downloading…")

    # 1) Derive referer from the URL’s host
    p = urlparse(m3u8_url)
    referer = f"{p.scheme}://{p.netloc}"

    # 2) A real browser User-Agent
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )

    # 3) Build ffmpeg command to use your cookies.txt
    output = "video.mp4"
    cmd = [
        "ffmpeg",
        "-cookies", "1",
        "-cookie_file", "cookies.txt",
        "-user_agent", ua,
        "-referer", referer,
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-i", m3u8_url,
        "-c", "copy",
        output
    ]

    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300  # 5 minutes max
        )
    except subprocess.TimeoutExpired:
        return update.message.reply_text("❌ Download timed out.")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")
        logger.error("ffmpeg failed: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")

    # 4) Send & clean up
    with open(output, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(output)
    logger.info("✅ Sent video and cleaned up")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    logger.info("Bot is polling…")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()

