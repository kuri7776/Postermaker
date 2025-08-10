import os
import re
import textwrap
import traceback
from io import BytesIO
from typing import Optional

import aiohttp
import requests
from dotenv import load_dotenv
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from pymongo import MongoClient
from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery,
    InputMediaPhoto,
)

# Load environment
load_dotenv()

# MongoDB setup
mongo_client = MongoClient(os.getenv("MONGO_URI"))
db = mongo_client["tmdb_poster_bot"]
user_sessions = db["user_sessions"]
bot_settings = db["bot_settings"]
user_thumbnails = db["user_thumbnails"]

if not bot_settings.find_one({"setting": "branding"}):
    bot_settings.insert_one({"setting": "branding", "channel_name": "A-Hub"})

branding = bot_settings.find_one({"setting": "branding"})
CHANNEL_NAME = branding["channel_name"]

# Pyrogram client
app = Client(
    "tmdb_poster_bot",
    bot_token=os.getenv("BOT_TOKEN"),
    api_id=os.getenv("API_ID"),
    api_hash=os.getenv("API_HASH"),
)

# TMDB config
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"

# Poster layout constants
POSTER_WIDTH = 1280
POSTER_HEIGHT = 720
BLUR_AREA_WIDTH = int(POSTER_WIDTH * 0.60)
IMAGE_AREA_WIDTH = POSTER_WIDTH - BLUR_AREA_WIDTH
THUMBNAIL_SIZE = (80, 80)
BOTTOM_SECTION_HEIGHT = 150
GENRE_PADDING = 10
GENRE_SPACING = 10

FONT_DIR = "fonts"
TITLE_FONT_PATH = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")
SUBTITLE_FONT_PATH = os.path.join(FONT_DIR, "Montserrat-Medium.ttf")
DETAIL_FONT_PATH = os.path.join(FONT_DIR, "Montserrat-Regular.ttf")
RATING_FONT_PATH = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
GENRE_FONT_PATH = os.path.join(FONT_DIR, "Montserrat-Medium.ttf")
CHANNEL_FONT_PATH = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")

if not os.path.exists(FONT_DIR):
    os.makedirs(FONT_DIR, exist_ok=True)

from PIL import ImageFont


def load_font(path: str, size: int):
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    return ImageFont.load_default()


title_font = load_font(TITLE_FONT_PATH, 48)
subtitle_font = load_font(SUBTITLE_FONT_PATH, 28)
detail_font = load_font(DETAIL_FONT_PATH, 28)
genre_font = load_font(GENRE_FONT_PATH, 23)
rating_font = load_font(RATING_FONT_PATH, 60)
channel_font = load_font(CHANNEL_FONT_PATH, 36)


async def search_tmdb(query: str, page: int = 1, media_type: str = "multi") -> dict:
    """Search TMDB. media_type can be 'movie', 'tv' or 'multi'."""
    params = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "page": page,
        "include_adult": "false",
    }
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/search/{media_type}"
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            return data


async def get_tmdb_details(media_id: int, media_type: str = "movie") -> dict:
    """Get details for a movie or tv show."""
    params = {"api_key": TMDB_API_KEY}
    async with aiohttp.ClientSession() as session:
        url = f"{TMDB_BASE_URL}/{media_type}/{media_id}"
        async with session.get(url, params=params) as resp:
            return await resp.json()


def download_image(url: str) -> Image.Image:
    """Download image (blocking)."""
    if not url:
        raise ValueError("No image URL provided")
    response = requests.get(url, stream=True, timeout=20)
    response.raw.decode_content = True
    img = Image.open(response.raw)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


async def download_telegram_image(file_id: str) -> Image.Image:
    """Download Telegram image by file ID into PIL Image."""
    file_bytes = await app.download_media(file_id, in_memory=True)
    # download_media returns a BytesIO when in_memory=True
    if hasattr(file_bytes, "getvalue"):
        return Image.open(BytesIO(file_bytes.getvalue())).convert("RGB")
    # fallback: if it returned a path
    return Image.open(file_bytes).convert("RGB")


def get_text_dimensions(text_string: str, font) -> tuple:
    """Accurate text size measurement."""
    if hasattr(font, "getbbox"):
        bbox = font.getbbox(text_string)
        return (bbox[2] - bbox[0], bbox[3] - bbox[1])
    else:
        return (len(text_string) * 10, 20)


def create_custom_poster(media_data: dict, media_type: str = "movie", thumbnail_img: Optional[Image.Image] = None) -> BytesIO:
    """Create custom poster using TMDB fields."""
    try:
        # Compose image URLs
        poster_path = media_data.get("poster_path")
        backdrop_path = media_data.get("backdrop_path")
        cover_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None
        banner_url = f"{TMDB_IMAGE_BASE}{backdrop_path}" if backdrop_path else cover_url

        if not cover_url and not banner_url:
            raise ValueError("No poster or backdrop available to create image.")

        cover_img = download_image(cover_url or banner_url)
        banner_img = download_image(banner_url or cover_url)

        poster = Image.new("RGB", (POSTER_WIDTH, POSTER_HEIGHT), (15, 15, 25))
        draw = ImageDraw.Draw(poster)

        # Right side: cover image
        cover_resized = cover_img.resize((IMAGE_AREA_WIDTH, POSTER_HEIGHT))
        poster.paste(cover_resized, (BLUR_AREA_WIDTH, 0))

        # Left side: blurred banner background
        blurred_bg = banner_img.copy()
        banner_aspect = blurred_bg.width / blurred_bg.height
        target_height = POSTER_HEIGHT
        target_width = int(target_height * banner_aspect)
        if target_width < BLUR_AREA_WIDTH:
            target_width = BLUR_AREA_WIDTH
            target_height = int(target_width / banner_aspect)

        blurred_bg = blurred_bg.resize((target_width, target_height))
        left = (target_width - BLUR_AREA_WIDTH) // 2
        top = (target_height - POSTER_HEIGHT) // 2
        right = left + BLUR_AREA_WIDTH
        bottom = top + POSTER_HEIGHT
        blurred_bg = blurred_bg.crop((left, top, right, bottom))
        blurred_bg = blurred_bg.filter(ImageFilter.GaussianBlur(15))

        overlay = Image.new("RGBA", (BLUR_AREA_WIDTH, POSTER_HEIGHT), (0, 0, 0, 180))
        blurred_bg.paste(overlay, (0, 0), overlay)
        poster.paste(blurred_bg, (0, 0))

        # Title and details
        title = media_data.get("title") or media_data.get("name") or "No Title"
        release_date = media_data.get("release_date") or media_data.get("first_air_date") or ""
        year = release_date[:4] if release_date else "N/A"
        episodes = media_data.get("number_of_episodes", "N/A") if media_type == "tv" else "N/A"
        media_type_display = "Movie" if media_type == "movie" else "TV Series"
        vote_avg = media_data.get("vote_average") or 0
        rating = int(vote_avg * 10) if isinstance(vote_avg, (int, float)) else "N/A"
        genres = [g["name"] for g in media_data.get("genres", [])] if media_data.get("genres") else []

        title_x = 50
        title_y = 60
        max_title_width = BLUR_AREA_WIDTH - 100

        # Wrap title into up to 3 lines
        title_lines = []
        words = str(title).split()
        current_line = ""
        for word in words:
            test_line = f"{current_line} {word}".strip() if current_line else word
            test_width, _ = get_text_dimensions(test_line, title_font)
            if test_width <= max_title_width and len(title_lines) < 3:
                current_line = test_line
            else:
                if current_line:
                    title_lines.append(current_line)
                current_line = word
                if len(title_lines) >= 3:
                    break
        if current_line and len(title_lines) < 3:
            title_lines.append(current_line)

        for line in title_lines:
            draw.text((title_x, title_y), line, font=title_font, fill="white")
            title_y += get_text_dimensions(line, title_font)[1] + 10

        details_y = title_y + 20
        details = [f"Year: {year}", f"Episodes: {episodes}", f"Type: {media_type_display}"]
        for detail in details:
            draw.text((title_x, details_y), detail, font=detail_font, fill="#d3d3d3")
            details_y += get_text_dimensions(detail, detail_font)[1] + 18

        rating_y = details_y + 10
        rating_text = f"{rating}%" if rating != "N/A" else "N/A"
        star_width, star_height = get_text_dimensions("★", rating_font)
        draw.text((title_x, rating_y), "★", font=rating_font, fill=(255, 215, 0))
        draw.text((title_x + star_width + 10, rating_y), rating_text, font=rating_font, fill="white")

        genres_y = rating_y + star_height + 40
        genre_x = title_x
        fill_color = (100, 100, 100)

        for genre in genres[:4]:
            text_bbox = draw.textbbox((0, 0), genre, font=genre_font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            box_width = text_width + 2 * GENRE_PADDING
            box_height = text_height + 2 * GENRE_PADDING

            if genre_x + box_width > BLUR_AREA_WIDTH - 50:
                genre_x = title_x
                genres_y += box_height + GENRE_SPACING

            draw.rounded_rectangle([genre_x, genres_y, genre_x + box_width, genres_y + box_height], radius=10, fill=fill_color, outline=fill_color)
            text_x = genre_x + (box_width - text_width) / 2 - text_bbox[0]
            text_y = genres_y + (box_height - text_height) / 2 - text_bbox[1]
            draw.text((text_x, text_y), genre, font=genre_font, fill="white")
            genre_x += box_width + GENRE_SPACING

        # Join/channel section
        join_text = "Join Our Telegram Channel"
        join_font = load_font(SUBTITLE_FONT_PATH, 28)
        join_width, join_height = get_text_dimensions(join_text, join_font)
        join_x = (BLUR_AREA_WIDTH - join_width) // 2
        join_y = POSTER_HEIGHT - BOTTOM_SECTION_HEIGHT + 20
        draw.text((join_x, join_y), join_text, font=join_font, fill="white")

        # Thumbnail and channel name
        if thumbnail_img:
            try:
                thumbnail_resized = thumbnail_img.resize(THUMBNAIL_SIZE)
                mask = Image.new("L", THUMBNAIL_SIZE, 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.ellipse((0, 0, THUMBNAIL_SIZE[0], THUMBNAIL_SIZE[1]), fill=255)
                channel_width, channel_height = get_text_dimensions(CHANNEL_NAME, channel_font)
                group_width = THUMBNAIL_SIZE[0] + 20 + channel_width
                group_x = (BLUR_AREA_WIDTH - group_width) // 2
                group_y = join_y + join_height + 20

                poster.paste(thumbnail_resized, (group_x, group_y), mask)
                channel_x = group_x + THUMBNAIL_SIZE[0] + 20
                channel_y = group_y + (THUMBNAIL_SIZE[1] - channel_height) // 2
                draw.text((channel_x, channel_y), CHANNEL_NAME, font=channel_font, fill="white")
            except Exception as e:
                print(f"Error processing thumbnail: {e}")
                text_width, text_height = get_text_dimensions(CHANNEL_NAME, channel_font)
                draw.text(((BLUR_AREA_WIDTH - text_width) // 2, join_y + join_height + 20), CHANNEL_NAME, font=channel_font, fill="white")
        else:
            text_width, text_height = get_text_dimensions(CHANNEL_NAME, channel_font)
            draw.text(((BLUR_AREA_WIDTH - text_width) // 2, join_y + join_height + 20), CHANNEL_NAME, font=channel_font, fill="white")

        bio = BytesIO()
        poster.save(bio, format="JPEG", quality=95)
        bio.seek(0)
        return bio

    except Exception as e:
        print(f"Error in create_custom_poster: {e}")
        traceback.print_exc()
        raise


async def update_branding():
    global CHANNEL_NAME
    branding = bot_settings.find_one({"setting": "branding"})
    if branding:
        CHANNEL_NAME = branding["channel_name"]


# ----- Bot Commands and Handlers -----
@app.on_message(filters.command(["search", "s"]))
async def search_command(_, message: Message):
    query = " ".join(message.command[1:])
    if not query:
        await message.reply("Please provide a search query.\nExample: `/search Inception`")
        return

    session_id = f"{message.from_user.id}-{message.id}"
    session_data = {
        "session_id": session_id,
        "query": query,
        "page": 1,
        "chat_id": message.chat.id,
        "message_id": None,
        "current_index": 0,
    }
    user_sessions.insert_one(session_data)
    await perform_search(session_id)


async def perform_search(session_id: str):
    session = user_sessions.find_one({"session_id": session_id})
    if not session:
        return

    results = await search_tmdb(session["query"], session["page"], media_type="multi")
    if not results or not results.get("results"):
        await app.send_message(session["chat_id"], "❌ No results found!")
        user_sessions.delete_one({"session_id": session_id})
        return

    page = results.get("page", 1)
    total_pages = results.get("total_pages", 1)

    user_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "results": results["results"],
                "page_info": {"currentPage": page, "hasNextPage": page < total_pages},
                "current_index": 0,
            }
        },
    )

    await show_result(session_id)


async def show_result(session_id: str):
    session = user_sessions.find_one({"session_id": session_id})
    if not session or not session.get("results"):
        return

    media = session["results"][session["current_index"]]
    # TMDB search/multi results include a "media_type" key
    media_type = media.get("media_type") or ("movie" if media.get("title") else "tv")
    title = media.get("title") or media.get("name") or "Unknown Title"
    image_url = None
    if media.get("poster_path"):
        image_url = f"{TMDB_IMAGE_BASE}{media.get('poster_path')}"
    elif media.get("backdrop_path"):
        image_url = f"{TMDB_IMAGE_BASE}{media.get('backdrop_path')}"

    buttons = []
    total_results = len(session["results"])
    page_info = session.get("page_info", {})

    if session["current_index"] > 0:
        buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"result_prev_{session_id}"))

    buttons.append(InlineKeyboardButton(f"{session['current_index'] + 1}/{total_results}", callback_data="noop"))

    if session["current_index"] < total_results - 1:
        buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"result_next_{session_id}"))

    page_buttons = []
    if page_info.get("currentPage", 0) > 1:
        page_buttons.append(InlineKeyboardButton("⏪ Prev Page", callback_data=f"page_prev_{session_id}"))

    # poster callback includes media id and type
    page_buttons.append(InlineKeyboardButton("🎨 Create Poster", callback_data=f"poster_{session_id}_{media['id']}_{media_type}"))
    page_buttons.append(InlineKeyboardButton("❌ Close", callback_data=f"close_{session_id}"))
    if page_info.get("hasNextPage", False):
        page_buttons.append(InlineKeyboardButton("Next Page ⏩", callback_data=f"page_next_{session_id}"))

    keyboard = InlineKeyboardMarkup([buttons, page_buttons])

    caption = f"**{title}**"
    if session.get("message_id") and image_url:
        try:
            await app.edit_message_media(
                chat_id=session["chat_id"],
                message_id=session["message_id"],
                media=InputMediaPhoto(media=image_url, caption=caption),
                reply_markup=keyboard,
            )
            return
        except Exception:
            pass

    # send new photo
    msg = await app.send_photo(chat_id=session["chat_id"], photo=image_url or "", caption=caption, reply_markup=keyboard)
    user_sessions.update_one({"session_id": session_id}, {"$set": {"message_id": msg.id}})


@app.on_callback_query(filters.regex(r"^result_(prev|next)_(.+)"))
async def result_nav_handler(_, callback: CallbackQuery):
    action = callback.matches[0].group(1)
    session_id = callback.matches[0].group(2)

    session = user_sessions.find_one({"session_id": session_id})
    if not session:
        await callback.answer("Session expired!")
        return

    new_index = session["current_index"]
    if action == "prev" and session["current_index"] > 0:
        new_index -= 1
    elif action == "next" and session["current_index"] < len(session["results"]) - 1:
        new_index += 1

    user_sessions.update_one({"session_id": session_id}, {"$set": {"current_index": new_index}})
    await callback.answer()
    await show_result(session_id)


@app.on_callback_query(filters.regex(r"^page_(prev|next)_(.+)"))
async def page_nav_handler(_, callback: CallbackQuery):
    action = callback.matches[0].group(1)
    session_id = callback.matches[0].group(2)

    session = user_sessions.find_one({"session_id": session_id})
    if not session:
        await callback.answer("Session expired!")
        return

    new_page = session["page"]
    if action == "prev" and session["page"] > 1:
        new_page -= 1
    elif action == "next":
        new_page += 1

    user_sessions.update_one({"session_id": session_id}, {"$set": {"page": new_page}})
    await callback.answer("Loading new page...")
    await perform_search(session_id)


@app.on_callback_query(filters.regex(r"^close_(.+)"))
async def close_handler(_, callback: CallbackQuery):
    session_id = callback.matches[0].group(1)
    session = user_sessions.find_one({"session_id": session_id})
    if session and session.get("message_id"):
        try:
            await app.delete_messages(chat_id=session["chat_id"], message_ids=[session["message_id"]])
        except Exception:
            pass
    user_sessions.delete_one({"session_id": session_id})
    await callback.answer("Search closed")


@app.on_callback_query(filters.regex(r"^poster_(.+)_(\d+)_(movie|tv)"))
async def create_poster_handler(_, callback: CallbackQuery):
    session_id = callback.matches[0].group(1)
    media_id = int(callback.matches[0].group(2))
    media_type = callback.matches[0].group(3)
    user_id = callback.from_user.id

    await callback.answer("Creating your custom poster...")

    media_data = await get_tmdb_details(media_id, media_type=media_type)
    if not media_data:
        await callback.message.reply("❌ Failed to get media details")
        return

    thumbnail_img = None
    thumbnail = user_thumbnails.find_one({"user_id": user_id})
    if thumbnail and thumbnail.get("thumbnail_id"):
        try:
            thumbnail_img = await download_telegram_image(thumbnail["thumbnail_id"])
        except Exception as e:
            print(f"Error downloading thumbnail: {e}")

    try:
        await update_branding()
        poster_bio = create_custom_poster(media_data, media_type=media_type, thumbnail_img=thumbnail_img)
        title = media_data.get("title") or media_data.get("name") or "Unknown Title"

        await callback.message.reply_photo(photo=poster_bio, caption=f"**🎨 Custom Poster for {title}**")
        poster_bio.close()
    except Exception as e:
        print(f"Error creating poster: {e}")
        traceback.print_exc()
        await callback.message.reply("❌ Failed to create poster. Please try another title.")


@app.on_callback_query(filters.regex(r"^noop$"))
async def noop_handler(_, callback: CallbackQuery):
    await callback.answer()


@app.on_message(filters.command(["cname", "setchannel"]))
async def set_channel_name(_, message: Message):
    """Set custom channel name for posters"""
    if len(message.command) < 2:
        await message.reply("Please provide a channel name.\nExample: `/cname MovieHub`")
        return

    new_name = " ".join(message.command[1:])
    bot_settings.update_one({"setting": "branding"}, {"$set": {"channel_name": new_name}}, upsert=True)
    global CHANNEL_NAME
    CHANNEL_NAME = new_name
    await message.reply(f"✅ Channel name updated to: **{new_name}**")


@app.on_message(filters.command(["sl", "setlogo"]))
async def set_thumbnail(_, message: Message):
    """Set custom thumbnail from replied photo"""
    if not message.reply_to_message or not message.reply_to_message.photo:
        return await message.reply_text("Reply to a photo to set it as your custom thumbnail.")

    user_id = message.from_user.id
    photo_id = message.reply_to_message.photo.file_id

    user_thumbnails.update_one({"user_id": user_id}, {"$set": {"thumbnail_id": photo_id}}, upsert=True)
    await message.reply("✅ logo set successfully! It will be used in your posters.")


@app.on_message(filters.command(["rml", "removelogo"]))
async def clear_thumbnail(_, message: Message):
    user_id = message.from_user.id
    user_thumbnails.delete_one({"user_id": user_id})
    await message.reply("✅ logo removed successfully!")


@app.on_message(filters.command(["ml", "mylogo"]))
async def show_thumbnail(_, message: Message):
    user_id = message.from_user.id
    thumbnail = user_thumbnails.find_one({"user_id": user_id})

    if thumbnail and thumbnail.get("thumbnail_id"):
        try:
            await message.reply_photo(photo=thumbnail["thumbnail_id"], caption="Your current logo:")
        except Exception:
            await message.reply("❌ Failed to display logo. It may have expired.")
    else:
        await message.reply("You haven't set a custom logo yet.")


if __name__ == "__main__":
    print("TMDB Poster Bot started!")
    app.run()
    
