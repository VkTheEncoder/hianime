#!/usr/bin/env python3
import os
import subprocess
import logging

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

# ─── Load & validate BOT_TOKEN ────────────────────────────────────────────────
load_dotenv()                        
BOT_TOKEN = os.getenv("BOT_TOKEN")  
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment")

# ─── Enable logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Create Bot with larger HTTP pool & clear any existing webhook ─────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── /start handler for smoke-testing connectivity ─────────────────────────────
def start(update: Update, context: CallbackContext):
    logger.info(f"Got /start from {update.effective_user.id}")
    update.message.reply_text(
        "🤖 Hi! I’m alive. Send me `/download <m3u8_url>` and I’ll fetch the video for you."
    )

# ─── /download handler ─────────────────────────────────────────────────────────
def download(update: Update, context: CallbackContext):
    logger.info(f"Got /download from {update.effective_user.id}: {context.args!r}")
    chat_id = update.effective_chat.id

    if not context.args:
        update.message.reply_text("Usage: /download <m3u8_url>")
        return

    m3u8_url = context.args[0]
    output = "video.mp4"
    update.message.reply_text("⏳ Fetching and remuxing…")

    cmd = [
        "ffmpeg",
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-i", m3u8_url,
        "-c", "copy",
        output
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")
        logger.error(f"ffmpeg failed: {err}")
        update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
        return

    with open(output, "rb") as video:
        context.bot.send_video(chat_id=chat_id, video=video)
    os.remove(output)
    logger.info("Video sent and cleaned up.")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher

    # Register handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    # Start polling Telegram
    updater.start_polling()
    logger.info("Bot started, polling for updates…")

    # Block until Ctrl-C or process termination
    updater.idle()
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
