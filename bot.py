#!/usr/bin/env python3
import os
import subprocess
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── Load & validate BOT_TOKEN ────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment")

# ─── Telegram command handler ─────────────────────────────────────────────────
def download(update: Update, context: CallbackContext):
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
        update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
        return

    with open(output, "rb") as video:
        context.bot.send_video(chat_id=chat_id, video=video)
    os.remove(output)

# ─── Main: start bot + dummy HTTP server ──────────────────────────────────────
def main():
    # 1) Start Telegram bot polling
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("download", download))
    updater.start_polling()

    # 2) Spin up a no-op HTTP server so Railway sees a bound $PORT
    port = int(os.environ.get("PORT", 8000))
    class NoOpHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

    HTTPServer(("", port), NoOpHandler).serve_forever()

if __name__ == "__main__":
    main()
