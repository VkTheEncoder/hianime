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

# ─── Config & Logging ─────────────────────────────────────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Telegram Init ─────────────────────────────────────────────────────────────
request = Request(con_pool_size=8)
bot = Bot(token=BOT_TOKEN, request=request)
bot.delete_webhook()

# ─── Helpers ──────────────────────────────────────────────────────────────────
def fetch_playlist_and_cookies(page_url: str, timeout: int = 30000):
    """
    1) Navigate to page_url in headless Chromium
    2) Wait for the first .m3u8 response (master or media)
    3) If it's a master (variant) playlist, fetch the first media playlist
    4) Return (media_playlist_url, cookie_header, playlist_text)
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context()
        page    = ctx.new_page()

        # 1) catch the first m3u8 response
        with page.expect_response("**/*.m3u8", timeout=timeout) as resp_info:
            page.goto(page_url, timeout=timeout)
        resp = resp_info.value
        master_url  = resp.url
        master_text = resp.text()

        # collect cookies from the page context
        cookies = ctx.cookies()

        # 2) detect & fetch variant → media playlist
        playlist = m3u8.loads(master_text)
        if playlist.is_variant and playlist.playlists:
            # pick the first variant
            var_uri    = playlist.playlists[0].uri
            media_url  = urljoin(master_url, var_uri)
            media_resp = ctx.request.get(media_url)
            media_text = media_resp.text()
            final_url  = media_url
            final_txt  = media_text
        else:
            final_url  = master_url
            final_txt  = master_text

        browser.close()

    # build a Cookie: header string
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
    return final_url, cookie_header, final_txt

def download_segments_and_concat(hls_url: str, cookie_header: str, playlist_text: str):
    """
    Given a media playlist and its cookie header:
    1) Parse out all TS segment URLs
    2) Download each via Playwright with the correct headers
    3) Write them to seg00000.ts … segNNNN.ts
    4) Build inputs.txt and run ffmpeg concat → video.mp4
    """
    # parse TS URIs
    pl       = m3u8.loads(playlist_text)
    base_url = hls_url.rsplit("/", 1)[0] + "/"
    segments = [urljoin(base_url, seg.uri) for seg in pl.segments]
    logger.info("Found %d TS segments", len(segments))

    # define headers
    referer = hls_url
    ua      = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    )
    hdrs = {
        "Referer":    referer,
        "User-Agent": ua,
        "Cookie":     cookie_header
    }

    # download each TS
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx     = browser.new_context()
        for i, url in enumerate(segments):
            logger.info("Fetching segment %d/%d", i+1, len(segments))
            r = ctx.request.get(url, headers=hdrs)
            if r.status != 200:
                raise RuntimeError(f"Segment {i} failed with {r.status}")
            with open(f"seg{i:05d}.ts", "wb") as f:
                f.write(r.body())
        browser.close()

    # write ffmpeg concat file
    with open("inputs.txt", "w") as f:
        for i in range(len(segments)):
            f.write(f"file 'seg{i:05d}.ts'\n")

    # run ffmpeg concat
    out = "video.mp4"
    subprocess.run([
        "ffmpeg", "-f", "concat", "-safe", "0",
        "-i", "inputs.txt", "-c", "copy", out
    ], check=True)

    # cleanup segments + list
    for fn in glob.glob("seg*.ts") + ["inputs.txt"]:
        os.remove(fn)

    return out

# ─── Handlers ────────────────────────────────────────────────────────────────
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Hi! Send me:\n`/download <video_page_url>`\nand I'll grab the video for you."
    )

def download(update: Update, context: CallbackContext):
    if not context.args:
        return update.message.reply_text(
            "Usage: `/download <video_page_url>`", parse_mode="Markdown"
        )
    page_url = context.args[0]
    chat_id  = update.effective_chat.id
    update.message.reply_text("⏳ Starting download, please wait…")

    try:
        url, cookies, text = fetch_playlist_and_cookies(page_url)
        logger.info("Using media playlist: %s", url)
        video = download_segments_and_concat(url, cookies, text)
    except PWTimeout:
        return update.message.reply_text("❌ Timeout loading player.")
    except Exception as e:
        logger.exception("Download failed")
        return update.message.reply_text(f"❌ Error: {e}")

    with open(video, "rb") as f:
        context.bot.send_video(chat_id=chat_id, video=f)
    os.remove(video)

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
