#!/usr/bin/env python3
import os
import subprocess
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext

# Load from env vars
BOT_TOKEN = os.getenv("7882374719:AAGVuPlEQL_3gM0lhGptDHdakRtG3MdnrtI")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

def download(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if not context.args:
        update.message.reply_text("Usage: /download <m3u8_url>")
        return

    m3u8_url = context.args[0]
    output = "video.mp4"
    update.message.reply_text(f"Fetching and remuxing…")

    # Run ffmpeg
    cmd = [
        "ffmpeg", "-protocol_whitelist", "file,tls,tcp,https,crypto",
        "-allowed_extensions", "ALL", "-i", m3u8_url,
        "-c", "copy", output
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        update.message.reply_text(f"Error during ffmpeg:\n{e.stderr.decode()[:200]}")
        return

    # Send the resulting file
    with open(output, "rb") as video:
        context.bot.send_video(chat_id=chat_id, video=video)
    # Clean up
    os.remove(output)

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("download", download))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
