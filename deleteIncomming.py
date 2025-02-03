import discord
import asyncio
import os
import re
import time
from PIL import Image
import imagehash
import imageio
import requests
from alive_progress import alive_bar
import sys
# Configuration
TOKEN = sys.argv[1]
BANNED_FOLDER = "bannedImages"
BANNED_MEDIA_FILE = "bannedMedia.txt"
DOWNLOAD_DIR = "downloadTemp"
LAST_CLEAR_FILE = "lastClear.txt"
SCAN_OLD_MESSAGES = True  # Toggle between True/False to switch modes
BANNED_YOUTUBE_KEYWORDS = ["squid", "456", "player 456", "games", "fin"]
# Ensure directories exist
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
import logging  # Add this import at the top of the file
from logging.handlers import RotatingFileHandler

# Configure logging for debugging purposes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler('discord_bot_errors.log', maxBytes=1024, backupCount=3)
    ]
)

def setup_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler('discord_bot_errors.log', maxBytes=1024, backupCount=3)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

def is_youtube_link(url):
    youtube_patterns = [
        r'(https?://)?(www\.)?youtube\.com/watch\?v=',
        r'(https?://)?(www\.)?youtube\.com/shorts/',
        r'(https?://)?youtu\.be/'
    ]
    return any(re.search(pattern, url) for pattern in youtube_patterns)

def contains_youtube_keywords(text):
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in BANNED_YOUTUBE_KEYWORDS)

def check_embed_for_keywords(embed):
    # Check embed title
    if embed.title and contains_youtube_keywords(embed.title):
        return True
    
    # Check embed description
    if embed.description and contains_youtube_keywords(embed.description):
        return True
    
    # Check embed author
    if embed.author and embed.author.name and contains_youtube_keywords(embed.author.name):
        return True
    
    # Check embed fields
    for field in embed.fields:
        if contains_youtube_keywords(field.name) or contains_youtube_keywords(field.value):
            return True
    
    return False
# --- Last Scan Tracking ---
def get_last_clear_id():
    try:
        with open(LAST_CLEAR_FILE, 'r') as f:
            content = f.read().strip()
            if content:
                return int(content)
    except (FileNotFoundError, ValueError):
        pass
    return None

def update_last_clear_id(message_id):
    with open(LAST_CLEAR_FILE, 'w') as f:
        f.write(str(message_id))

# --- Image Processing Functions ---
def get_image_hash(image_path):
    try:
        with Image.open(image_path) as img:
            return imagehash.phash(img)
    except Exception as e:
        logger.error(f"Error downloading image at : {str(e)}")
        raise
        print(f"Error hashing image: {e}")
        return None

def compare_to_banned_images(input_path, threshold=15):
    input_hash = get_image_hash(input_path)
    if not input_hash:
        return False

    for filename in os.listdir(BANNED_FOLDER):
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            continue

        banned_path = os.path.join(BANNED_FOLDER, filename)
        
        try:
            if filename.lower().endswith('.gif'):
                gif = imageio.mimread(banned_path)
                for frame in gif:
                    pil_frame = Image.fromarray(frame)
                    frame_hash = imagehash.phash(pil_frame)
                    if (input_hash - frame_hash) < threshold:
                        return True
            else:
                banned_hash = get_image_hash(banned_path)
                if banned_hash and (input_hash - banned_hash) < threshold:
                    return True
        except Exception as e:
            logger.error(f"Error comparing image at {filename}: {str(e)}")
            print(f"Error comparing {filename}: {e}")
    
    return False

# --- File Handling ---
def download_image(url, message_id, index):
    try:
        if not any(url.lower().endswith(ext) for ext in ('.png', '.jpg', '.jpeg', '.gif')):
            return None

        sanitized = re.sub(r'\W+', '', os.path.basename(url.split('?')[0]))
        filename = f"{message_id}_{index}_{sanitized[:50]}"
        filepath = os.path.join(DOWNLOAD_DIR, filename)

        response = requests.get(url, stream=True)
        response.raise_for_status()

        if not response.headers.get('Content-Type', '').startswith('image/'):
            return None

        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(8192):
                f.write(chunk)

        return filepath
    except Exception as e:
        print(f"Download failed: {e}")
        return None

# --- Message Processing ---
async def process_new_message(message):
    for url in re.findall(r'https?://\S+', message.content):
        if any(banned in url for banned in get_banned_urls()):
            await message.delete()
            print(f"Deleted message with banned URL: {url}")
            return

    for embed in message.embeds:
        if embed.url and is_youtube_link(embed.url):
            if check_embed_for_keywords(embed):
                await message.delete()
                print(f"deleted Youtube emebed with banned keywords: {embed.url}")
    
    
    for idx, attachment in enumerate(message.attachments):
        if not attachment.content_type.startswith('image/'):
            continue

        filepath = download_image(attachment.url, message.id, idx)
        if not filepath:
            continue

        try:
            if compare_to_banned_images(filepath):
                await message.delete()
                print(f"Deleted banned image: {attachment.filename}")
                return
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)
            print("now only working on current messages")    

def get_banned_urls():
    with open(BANNED_MEDIA_FILE, 'r') as f:
        return set(line.strip() for line in f if line.strip())

async def process_old_messages(guild_id):
    guild = client.get_guild(guild_id)
    if not guild:
        return
    
    last_message_id = get_last_clear_id()
    newest_message_id = last_message_id

    for channel in guild.text_channels:
        print(f"Reading channel: {channel}")
        if not channel.permissions_for(guild.me).read_message_history:
            continue

        with alive_bar(spinner="waves", title=str(channel)) as bar:
            try:
                async for message in channel.history(
                    limit=None,
                    after=discord.Object(id=last_message_id) if last_message_id else None
                ):
                    await process_new_message(message)
                    # Track newest message ID
                    if newest_message_id is None or message.id > newest_message_id:
                        newest_message_id = message.id
                    bar()
            except Exception as e:
                print(f"Error processing channel {channel}: {e}")

    # Update last scan position
    if newest_message_id and newest_message_id != last_message_id:
        update_last_clear_id(newest_message_id)
        print(f"Updated last scan position to message ID: {newest_message_id}")

# --- Discord Client ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

client = discord.Client(intents=intents)
logger = setup_logger()
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    if SCAN_OLD_MESSAGES:
        try:
            await process_old_messages(1253811202912682078)
        except Exception as e:
            logger.error(f"Error processing old messages: {str(e)}") 
    print("now scanning incomming messages")    

@client.event
async def on_message(message):
    if message.author.bot or int(message.author.id) == 672459887741108238:
        return
    print("message recieved")
    await process_new_message(message)

client.run(TOKEN)
