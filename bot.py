#!/usr/bin/env python3
import os
import subprocess
import logging
import glob
from urllib.parse import urljoin, urlparse

from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import m3u8

# ─── Setup & Token ────────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment")

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Telegram Bot init ─────────────────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fetch_signed_hls_and_cookies(page_url: str, timeout: int = 30000):
    """
    Load the page in headless Chromium, wait for the .m3u8 response,
    return (playlist_url, cookie_header, playlist_text).
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Wait for the playlist response
        with page.expect_response("**/*.m3u8", timeout=timeout) as resp_info:
            page.goto(page_url, timeout=timeout)
        resp = resp_info.value
        hls_url = resp.url
        playlist_text = resp.text()

        # Grab cookies for that page
        cookies = ctx.cookies()
        browser.close()

    # Build Cookie header string
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    return hls_url, cookie_header, playlist_text

def download_segments_and_concat(hls_url: str, cookie_header: str, playlist_text: str):
    """
    Parse the playlist_text, download each .ts segment via Playwright's request
    API reusing the same cookies, then concat them into video.mp4.
    Returns the output filename.
    """
    # Parse segment URIs
    playlist = m3u8.loads(playlist_text)
    base = hls_url.rsplit("/", 1)[0] + "/"
    segments = [urljoin(base, seg.uri) for seg in playlist.segments]
    logger.info("Found %d segments", len(segments))

    # Launch a new browser context to download segments
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        # Inject cookies so that requests are authenticated
        cookie_list = [
            {
                "name": name, "value": val,
                "domain": urlparse(hls_url).hostname,
                "path": "/"
            }
            for name, val in (pair.split("=", 1) for pair in cookie_header.split("; "))
        ]
        ctx.add_cookies(cookie_list)
        # Download each segment
        for i, seg_url in enumerate(segments):
            r = ctx.request.get(seg_url)
            data = r.body()
            fname = f"seg{i:05d}.ts"
            with open(fname, "wb") as f:
                f.write(data)
        browser.close()

    # Create ffmpeg concat list
    with open("inputs.txt", "w") as f:
        for i in range(len(segments)):
            f.write(f"file 'seg{i:05d}.ts'\n")

    # Run ffmpeg to concat
    output = "video.mp4"
    subprocess.run([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", "inputs.txt", "-c", "copy", output
    ], check=True)

    # Clean up segment files and inputs.txt
    for fn in glob.glob("seg*.ts") + ["inputs.txt"]:
        os.remove(fn)

    return output

# ─── Handlers ────────────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    logger.info("Received /start from %s", update.effective_user.id)
    update.message.reply_text(
        "👋 Hi! Send me:\n\n"
        "`/download <video_page_url>`\n\n"
        "and I’ll fetch and send you the video."
    )

def download(update: Update, context: CallbackContext):
    args = context.args or []
    logger.info("Received /download from %s: %r", update.effective_user.id, args)
    if len(args) != 1:
        return update.message.reply_text(
            "Usage: `/download <video_page_url>`", parse_mode="Markdown"
        )

    page_url = args[0]
    chat_id  = update.effective_chat.id
    update.message.reply_text("⏳ Downloading… This may take a minute.")

    try:
        # Step 1: Get signed HLS + cookies + raw playlist text
        hls_url, cookie_header, playlist_text = fetch_signed_hls_and_cookies(page_url)
        logger.info("Got playlist URL: %s", hls_url)

        # Step 2: Download segments & concat into MP4
        video_file = download_segments_and_concat(hls_url, cookie_header, playlist_text)
    except PWTimeout:
        return update.message.reply_text("❌ Timeout waiting for the player to load.")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        logger.error("ffmpeg concat failed: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
    except Exception as e:
        logger.exception("Error in download handler")
        return update.message.reply_text(f"❌ Error: {e}")

    # Step 3: Send the resulting video
    with open(video_file, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(video_file)
    logger.info("Sent video and cleaned up.")

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
