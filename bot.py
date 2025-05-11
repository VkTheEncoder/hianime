#!/usr/bin/env python3
import os
import subprocess
import logging

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request

# ─── Setup & Token ─────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Telegram Bot init ─────────────────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Utility: load cookies into a single header string ────────────────────────
def load_cookies(cookie_path="cookies.txt"):
    pairs = []
    with open(cookie_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            # Netscape cookie format: domain, flag, path, secure, expiration, name, value
            parts = line.strip().split("\t")
            if len(parts) >= 7:
                name, value = parts[5], parts[6]
                pairs.append(f"{name}={value}")
    return "; ".join(pairs)

# ─── /start handler ───────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    logger.info(f"/start from {update.effective_user.id}")
    update.message.reply_text(
        "🤖 I’m alive! Send me `/download <m3u8_url>` and I’ll fetch the video."
    )

# ─── /download handler ────────────────────────────────────────────────────────
def download(update: Update, context: CallbackContext):
    logger.info(f"/download from {update.effective_user.id}: {context.args!r}")
    if not context.args:
        return update.message.reply_text("Usage: `/download <m3u8_url>`",
                                         parse_mode="Markdown")
    m3u8_url = context.args[0]
    chat_id   = update.effective_chat.id
    update.message.reply_text("⏳ Downloading…")

    # 1) Real browser UA
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
    # 2) Referer = the host that originally served the playlist
    from urllib.parse import urlparse
    p = urlparse(m3u8_url)
    referer = f"{p.scheme}://{p.netloc}"

    # 3) Load your exported Netscape cookies
    cookie_header = load_cookies("cookies.txt")

    # 4) Build ffmpeg command
    output = "video.mp4"
    headers = (
        f"Referer: {referer}\r\n"
        f"User-Agent: {ua}\r\n"
        f"Cookie: {cookie_header}\r\n"
    )
    cmd = [
        "ffmpeg",
        "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL",
        "-headers", headers,
        "-i", m3u8_url,
        "-c", "copy",
        output
    ]

    try:
        subprocess.run(
            cmd, check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300
        )
    except subprocess.TimeoutExpired:
        return update.message.reply_text("❌ Download timed out.")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="ignore")
        logger.error("ffmpeg failed: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")

    # 5) Send back the MP4 and clean up
    with open(output, "rb") as vid:
        context.bot.send_video(chat_id=chat_id, video=vid)
    os.remove(output)
    logger.info("Sent video and removed %s", output)

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start",   start))
    dp.add_handler(CommandHandler("download", download))

    logger.info("Bot polling…")
    updater.start_polling()
    updater.idle()
    logger.info("Bot stopped.")

if __name__ == "__main__":
    main()
