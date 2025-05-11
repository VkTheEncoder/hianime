#!/usr/bin/env python3
import os
import logging
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

import yt_dlp

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

# ─── Create Bot & clear any webhook ────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── /start handler ───────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    logger.info(f"Got /start from {update.effective_user.id}")
    update.message.reply_text(
        "🤖 Hi! Send me:\n\n"
        "`/download https://.../index-f1-v1-a1.m3u8`\n\n"
        "and I’ll fetch and upload the video for you."
    )

# ─── /download handler (using yt_dlp) ────────────────────────────────────────
def download(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    args   = context.args or []
    logger.info(f"Got /download from {update.effective_user.id}: {args!r}")

    if len(args) != 1:
        return update.message.reply_text("Usage: `/download <m3u8_url>`", parse_mode="Markdown")

    m3u8_url = args[0]
    update.message.reply_text("⏳ Downloading with yt-dlp…")

    # Prepare yt_dlp options
    ydl_opts = {
        "format": "best",              # pick the highest-quality stream
        "outtmpl": "video.%(ext)s",    # save as video.mp4 (or .mkv)
        "hls_use_mpegts": True,        # container-friendly
        "noprogress": True,
        "quiet": True,
        # you can uncomment these if you hit SSL/host issues:
        # "no_check_certificate": True,
        # "prefer_insecure": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(m3u8_url, download=True)
            # info["ext"] might be "mp4" or "mkv"
            fname = ydl.prepare_filename(info)
    except Exception as e:
        logger.error("yt-dlp failed", exc_info=e)
        return update.message.reply_text(f"❌ yt-dlp error:\n`{e}`", parse_mode="Markdown")

    # Send back the file
    try:
        with open(fname, "rb") as f:
            context.bot.send_video(chat_id=chat_id, video=f)
    except Exception as e:
        logger.error("failed to send video", exc_info=e)
        return update.message.reply_text(f"❌ Send error:\n`{e}`", parse_mode="Markdown")
    finally:
        # clean up
        if os.path.exists(fname):
            os.remove(fname)
        logger.info("Cleaned up downloaded file.")

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    logger.info("Bot starting…")
    updater.start_polling()
    updater.idle()
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
