#!/usr/bin/env python3
import os
import subprocess
import logging
import glob
from urllib.parse import urljoin, urlparse

import m3u8
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram.utils.request import Request
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
def fetch_playlist_and_cookies(page_url: str, timeout: int = 30000):
    """
    1) Loads page_url in a headless browser
    2) Waits for the master .m3u8 response
    3) Returns (playlist_url, cookie_header, playlist_text)
       but if it's a VARIANT (master) playlist, auto-fetches
       the media playlist and returns that instead.
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        # Wait for the master playlist response
        with page.expect_response("**/*.m3u8", timeout=timeout) as rsp_info:
            page.goto(page_url, timeout=timeout)
        master_rsp = rsp_info.value
        master_url = master_rsp.url
        master_text = master_rsp.text()

        # Grab cookies
        cookies = ctx.cookies()

        # If master_text is a variant playlist, fetch the first media playlist
        playlist = m3u8.loads(master_text)
        if playlist.is_variant:
            variant_uri = playlist.playlists[0].uri
            variant_url = urljoin(master_url, variant_uri)
            variant_rsp = ctx.request.get(variant_url)
            playlist_text = variant_rsp.text()
            final_url = variant_url
        else:
            playlist_text = master_text
            final_url = master_url

        browser.close()

    # Build a Cookie header string
    cookie_header = "; ".join(
        f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value")
    )
    return final_url, cookie_header, playlist_text

def download_segments_and_concat(hls_url: str, cookie_header: str, playlist_text: str):
    """
    Given a media playlist (playlist_text) and its cookies, download each
    TS segment via Playwright + ffmpeg concat into video.mp4.
    """
    # Parse the media playlist
    pl = m3u8.loads(playlist_text)
    base = hls_url.rsplit("/", 1)[0] + "/"
    segments = [urljoin(base, seg.uri) for seg in pl.segments]
    logger.info("Found %d ts segments", len(segments))

    # Download each segment
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        # Inject the cookies for the HLS host
        host = urlparse(hls_url).hostname
        cookie_list = []
        for pair in cookie_header.split("; "):
            if "=" not in pair:
                continue
            name, val = pair.split("=", 1)
            cookie_list.append({
                "name": name, "value": val,
                "domain": host, "path": "/"
            })
        ctx.add_cookies(cookie_list)

        for idx, seg_url in enumerate(segments):
            logger.info("Downloading segment %d/%d", idx+1, len(segments))
            resp = ctx.request.get(seg_url)
            data = resp.body()
            with open(f"seg{idx:05d}.ts", "wb") as f:
                f.write(data)

        browser.close()

    # Write ffmpeg concat file
    with open("inputs.txt", "w") as f:
        for idx in range(len(segments)):
            f.write(f"file 'seg{idx:05d}.ts'\n")

    # Run ffmpeg to concatenate
    out = "video.mp4"
    subprocess.run([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", "inputs.txt", "-c", "copy", out
    ], check=True)

    # Cleanup
    for fn in glob.glob("seg*.ts") + ["inputs.txt"]:
        os.remove(fn)

    return out

# ─── Handlers ────────────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hello! Send me:\n`/download <video_page_url>`\nand I'll grab the video for you."
    )

def download(update: Update, context: CallbackContext):
    if not context.args:
        return update.message.reply_text("Usage: `/download <video_page_url>`",
                                         parse_mode="Markdown")

    page_url = context.args[0]
    chat_id  = update.effective_chat.id
    update.message.reply_text("⏳ Starting download—this may take 30–60s…")

    try:
        hls_url, cookie_hdr, pl_text = fetch_playlist_and_cookies(page_url)
        logger.info("Using playlist URL: %s", hls_url)

        video_file = download_segments_and_concat(hls_url, cookie_hdr, pl_text)
    except PWTimeout:
        return update.message.reply_text("❌ Timeout loading the player.")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore") if e.stderr else str(e)
        logger.error("ffmpeg failed: %s", err)
        return update.message.reply_text(f"❌ ffmpeg error:\n{err[:200]}")
    except Exception as e:
        logger.exception("Error in download handler")
        return update.message.reply_text(f"❌ Error: {e}")

    with open(video_file, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(video_file)

# ─── Main ──────────────────────────────────────────────────────────────────────
def main():
    updater = Updater(bot=bot, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("download", download))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
