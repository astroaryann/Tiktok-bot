import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from apify_client import ApifyClient
from pymongo import MongoClient
from datetime import datetime
import requests 
import json     
import time 
import io       # Needed for BytesIO for file upload
import asyncio  # Needed for running blocking code in executor

# =========================================================================
# --- 1. CONFIGURATION & API KEYS ---
# =========================================================================

# 1. Telegram Bot Token (from user request)
TELEGRAM_BOT_TOKEN = "8235693097:AAFJ9Dr2CdGyQceue6qCrSgWebRJ-l9x27I"

# 2. Apify Client Token (UPDATED with new value)
APIFY_TOKEN = "apify_api_vpkYgqdt4UCIsuq9IshYRuXHmqyc17013l8r"

# --- APIRY ACTOR IDs (Dual-Actor Stable System) ---
USER_INFO_ACTOR_ID_FULL = "v010Fa8JLkB0A5eIC"                    # Full Stats & Timestamps
USER_INFO_ACTOR_ID_BASE = "ewLohp8vu0rtVK77c"                     # NEW: Dedicated Country Scraper
VIDEO_DOWNLOADER_ACTOR_ID = "wilcode/fast-tiktok-downloader-without-watermark" # Working Actor for Download
# -----------------------

# 3. MongoDB Connection Details
MONGO_DB_CONN_STRING = "mongodb+srv://notaryan:aryan_patil152@cluster0.xaoenac.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

DB_NAME = "tiktok_bot_db"
COLLECTION_NAME = "users"

# 4. Admin Configuration
ADMIN_COMMAND_PASSWORD = "tiktok101" 

# =========================================================================
# --- 2. INITIALIZATION & DATABASE SETUP ---
# =========================================================================

# Initialize MongoDB Client variables globally
mongo_client = None
db = None
users_collection = None

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Attempt MongoDB connection using the direct string
try:
    mongo_client = MongoClient(
        MONGO_DB_CONN_STRING,
        serverSelectionTimeoutMS=5000 
    )
    mongo_client.admin.command('ping') 
    db = mongo_client[COLLECTION_NAME]
    users_collection = db[COLLECTION_NAME]
    logging.info("MongoDB connection successful.")
except Exception as e:
    logging.error(f"MongoDB connection failed: {e}")

# =========================================================================
# --- 3. UTILITY FUNCTIONS (Access, Time, Formatting) ---
# =========================================================================

def get_flag_emoji(country_code: str) -> str:
    """Converts a two-letter country code (like 'US') to a flag emoji."""
    if not country_code or len(country_code) != 2:
        return ""
    # Convert upper-case country code letters to their regional indicator symbols
    return "".join(chr(0x1F1E6 + (ord(char) - ord('A'))) for char in country_code.upper())

def format_bool(value):
    """Formats boolean values to Yes ✅ or No ❌."""
    if isinstance(value, str):
        return "Yes ✅" if value.lower() == 'true' else "No ❌"
    return "Yes ✅" if value else "No ❌"

def format_timestamp_data(timestamp):
    """Formats Unix timestamp to readable date/time, returning N/A for 0 or invalid."""
    try:
        ts_int = int(timestamp or 0)
        if ts_int == 0:
            return "N/A"
        if datetime.fromtimestamp(ts_int).year >= 1980:
             return datetime.fromtimestamp(ts_int).strftime('%Y-%m-%d %H:%M:%S')
        return "N/A"
    except Exception: return "N/A"

 
def get_user_access_status(user_id):
    """Retrieves user access level, trial status, and admin status from MongoDB."""
    if users_collection is None:
        return {"access_level": "free", "trial_used": False, "is_admin": False}
        
    doc = users_collection.find_one({"_id": user_id})
    if doc is None:
        return {"access_level": "free", "trial_used": False, "is_admin": False}
    return {
        "access_level": doc.get("access_level", "free"),
        "trial_used": doc.get("trial_used", False),
        "is_admin": doc.get("is_admin", False)
    }

def consume_trial(user_id):
    """Sets the user's trial_used flag to True."""
    if users_collection is None: return

    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"trial_used": True}}
    )

def upsert_user(user_data):
    """
    Inserts or updates user data in MongoDB.
    """
    if users_collection is None: return

    user_id = user_data['id'] # Correctly extract user_id from the passed dictionary
    users_collection.update_one(
        {"_id": user_id}, # Use user_id here
        {
            "$set": {
                "username": user_data.get('username'),
                "first_name": user_data.get('first_name'),
                "is_premium": user_data.get('is_premium', False),
                "last_active": datetime.now(),
            },
            "$setOnInsert": {
                "created_at": datetime.now(),
                "access_level": "free",
                "trial_used": False,
                "is_admin": False,
            }
        },
        upsert=True
    )

# =========================================================================
# --- 4. API SCRAPER EXECUTION LOGIC (Core) ---
# =========================================================================

def execute_apify_run(actor_id: str, run_input: dict):
    """
    SYNCHRONOUS CORE: Executes the Apify Actor run and handles all retrieval logic.
    """
    try:
        # Re-initialize client inside sync function to ensure thread isolation
        client = ApifyClient(APIFY_TOKEN) 
        
        # 1. Adapt run_input structure based on the Actor ID
        final_run_input = run_input.copy()

        if actor_id == USER_INFO_ACTOR_ID_FULL:
            # v010Fa8JLkB0A5eIC requires input as a list of usernames
            url = run_input.get("url", "")
            username = url.split('@')[-1].split('/')[0] # Extract @username from the URL
            
            final_run_input = {
                "usernames": [username], # REQUIRED WORKING FORMAT for v010Fa8JLkB0A5eIC
            } 
        
        elif actor_id == USER_INFO_ACTOR_ID_BASE:
            # ewLohp8vu0rtVK77c requires input as a list of URL objects
            final_run_input = {
                "urls": [{"url": run_input["url"]}], # Use list of URL objects
                "proxy": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                    "apifyProxyCountry": "US",
                },
            }
        
        elif actor_id == VIDEO_DOWNLOADER_ACTOR_ID:
            # wilcode/fast-tiktok-downloader-without-watermark requires the URL in the 'url' field.
            pass


        # 2. Run the Actor (it will wait until SUCCEEDED)
        run = client.actor(actor_id).call(run_input=final_run_input)
        
        if run.get('exitCode', 1) != 0:
            logger.error(f"Apify Actor finished with non-zero exit code: {run.get('exitCode')}. Message: {run.get('statusMessage')}")
            return {"status": "error", "message": run.get('statusMessage', 'Actor failed to complete.')}

        dataset_id = run["defaultDatasetId"]
        dataset_client = client.dataset(dataset_id)
        
        # 3. Wait for dataset readiness (up to 30s)
        timeout = 30
        start_time = time.time()
        while time.time() - start_time < timeout:
            dataset_info = dataset_client.get()
            if dataset_info and dataset_info.get('itemCount', 0) > 0:
                logger.info("Dataset confirmed ready with item count > 0.")
                break
            time.sleep(2)
        else:
            logger.error(f"Apify dataset indexing timed out after {timeout} seconds or returned zero items.")
            return {"status": "error", "message": "API Error: Dataset not ready or empty."}
        
        # 4. Download the data using iteration (most reliable method)
        items = list(dataset_client.iterate_items())
        
        if not items: 
            return {"status": "error", "message": "API Error: No data retrieved after run succeeded."}

        # --- Process output based on Actor ID ---
        
        if actor_id == USER_INFO_ACTOR_ID_FULL:
            # Logic specific to v010Fa8JLkB0A5eIC (Full User Info)
            
            profile_data = items[0]
            user_data = profile_data.get('user', {})
            stats_data = profile_data.get('stats', {}) 
            
            # Combine relevant keys
            combined_data = {**user_data, **stats_data}

            return {"status": "success", "actor": "user_info_full", "data": combined_data}

            
        elif actor_id == USER_INFO_ACTOR_ID_BASE:
            # Logic specific to ewLohp8vu0rtVK77c (Base Info)
            
            # This actor returns the data structure at the root, potentially nested under 'author'
            user_data = items[0]
            
            # Check for author nesting (common in this scraper type)
            if 'author' in user_data:
                user_data = user_data['author']
            
            return {"status": "success", "actor": "user_info_base", "data": user_data}
            
        elif actor_id == VIDEO_DOWNLOADER_ACTOR_ID:
            # Logic specific to wilcode/fast-tiktok-downloader-without-watermark (Video Download)
            result_data = items[0].get("result", items[0])
            video_object = result_data.get("video")
            
            if video_object and "playAddr" in video_object:
                download_link = video_object.get("playAddr")
                if isinstance(download_link, list): 
                    download_link = download_link[0] if download_link else None
                
                if not download_link: return {"status": "error", "message": "API Error: No download link found."}
                
                return {"status": "success", 
                        "actor": "downloader",
                        "download_url": download_link, 
                        "title": result_data.get("desc", "Untitled Video")}
            else:
                return {"status": "error", "message": "API Error: Unexpected download data structure (Missing video/playAddr keys)."}
            
    except Exception as e:
        # --- DIAGNOSTIC LOGGING ---
        if hasattr(e, 'response') and e.response is not None:
             logger.error(f"APIFY DIAGNOSTIC: Status {e.response.status_code}, Content: {e.response.text[:250]}...")
        # --------------------------

        logger.error(f"Unexpected error during Apify call: {e}"); 
        if "User was not found or authentication token is not valid" in str(e) or "Actor was not found" in str(e):
             return {"status": "error", "message": "TikTok user/video not found OR Apify token is invalid/expired."}
        
        return {"status": "error", "message": f"Unexpected error: {e}"}

def run_apify_scraper_sync(actor_id: str, run_input: dict):
    """
    ASYNCHRONOUS COORDINATOR: Executes the synchronous Apify run logic in a thread pool.
    """
    return execute_apify_run(actor_id, run_input)


# --- Blocking I/O Functions (Used in download_video flow) ---

def blocking_download_and_upload(download_url: str, title: str, update: Update, status_message_id: int, was_trial: bool, user_id: int):
    """
    NOTE: This function is OBSOLETE as we are only posting the link, but kept for structural context.
    """
    try:
        raise Exception("Skipping direct download/upload attempt due to persistent Telegram file errors.")
        
    except Exception as e:
        # Prepare fallback message with the direct link
        response_text = (f"🎉 **Ready!** 🎉\n\n**Title:** {title}\n\n"
                         f"ℹ️ Couldn't upload automatically (Link may be expired or too large). "
                         f"Please download via the link below:\n\n`{download_url}`")
        
        return {"status": "failure", "message": response_text}


# =========================================================================
# --- 5. TELEGRAM MENU KEYBOARDS ---
# =========================================================================

def get_main_menu_keyboard():
    """Returns the main menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🎬 Download TikTok Video", callback_data='download_video'),
            InlineKeyboardButton("📊 Get TikTok User Info", callback_data='get_user_info')
        ],
        [
            InlineKeyboardButton("🌍 Get Country Info", callback_data='start_country_info'), # NEW BUTTON
            InlineKeyboardButton("🔒 Check My Access", callback_data='check_access')
        ],
        [InlineKeyboardButton("💳 Make a Payment (Stripe)", callback_data='make_payment_placeholder')],
        [InlineKeyboardButton("⚙️ Admin Panel", callback_data='admin_panel')] 
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel_keyboard():
    """Returns the admin panel inline keyboard."""
    keyboard = [
        [InlineKeyboardButton("👥 View Subscribers", callback_data='admin_view_subscribers')],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)


# =========================================================================
# --- 6. TELEGRAM HANDLERS (Commands & Callbacks) ---
# =========================================================================

async def admin_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Grants admin access if the command and password match."""
    user = update.effective_user
    full_command = update.message.text.strip()
    expected_command = f"/admin {ADMIN_COMMAND_PASSWORD}"

    if full_command == expected_command:
        if users_collection is None:
            await update.message.reply_text("❌ Cannot grant admin access: Database connection failed. Please fix your MongoDB connection.", parse_mode='Markdown')
            return

        users_collection.update_one(
            {"_id": user.id},
            {"$set": {"access_level": "premium", "is_admin": True}},
            upsert=True
        )
        
        await update.message.reply_text(
            f"👑 **Congratulations, Admin @{user.username or user.id}!** 👑\n"
            "You now have permanent Premium access and the Admin Panel button is functional. **Your status is updated.**",
            parse_mode='Markdown'
        )
        await start(update, context) 
    else:
        await update.message.reply_text("❌ Invalid administrator command or password.", parse_mode='Markdown')

async def view_subscribers(query: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Queries MongoDB and lists all premium/subscribed users."""
    user_id = query.from_user.id
    status = get_user_access_status(user_id)
    
    if not status['is_admin']:
        await query.edit_message_text("❌ Unauthorized access to subscriber list.", parse_mode='Markdown')
        return

    if users_collection is None:
        await query.edit_message_text("❌ Database is currently unreachable.", parse_mode='Markdown')
        return

    await query.edit_message_text("Fetching subscriber list, please wait...", parse_mode='Markdown')

    try:
        subscriber_docs = users_collection.find({"access_level": "premium"})
        subscriber_list = []
        for doc in subscriber_docs:
            user_handle = doc.get('username') or f"ID: {doc['_id']}" 
            is_admin_star = " (ADMIN ⭐)" if doc.get('is_admin') else ""
            subscriber_list.append(f"- @{user_handle} [ID: {doc['_id']}]{is_admin_star}")

        if subscriber_list:
            response_text = "👑 **Current Premium Subscribers:** 👑\n\n" + "\n".join(subscriber_list)
        else:
            response_text = "🤷‍♂️ **No Premium Subscribers Found.**"
            
    except Exception as e:
        logger.error(f"Error fetching subscribers: {e}")
        response_text = "❌ Error accessing subscriber database."

    await query.edit_message_text(
        response_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh List", callback_data='admin_view_subscribers')],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data='back_to_menu')]
        ]),
        parse_mode='Markdown'
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and the main menu."""
    user = update.effective_user
    
    try:
        if user:
            upsert_user(user.to_dict())

        welcome_text = (
            f"**Welcome to the TikTok Downloader Bot!** 🎉\n\n"
            "Your ultimate tool for downloading TikTok videos and getting user information. "
            "Choose an option below to get started:"
        )

        await update.message.reply_text(
            welcome_text,
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error sending start message (Network/Telegram API issue): {e}")
        if update.message:
            await update.message.reply_text("⚠️ **Network Error.** Could not connect to Telegram servers. Please check your internet connection and try again.", parse_mode='Markdown')


async def handle_trial_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the user confirming to use their free trial."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    action = query.data.replace('use_trial_for_', '')
    
    consume_trial(user_id)
    
    if action == 'download_video':
        await query.edit_message_text(
            "✅ Trial Started! Send me the **URL of the TikTok Video** now.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
            parse_mode='Markdown'
        )
        context.user_data['state'] = 'awaiting_video_url'
    
    elif action == 'get_user_info':
        await query.edit_message_text(
            "✅ Trial Started! Send me the **TikTok profile URL** or **username** now.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
            parse_mode='Markdown'
        )
        context.user_data['state'] = 'awaiting_user_info'

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles button presses from the main menu, including access checks."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    
    # --- 1. Handle Feature Clicks (Sets State) ---
    if data == 'start_country_info':
        await query.edit_message_text(
            "🌍 **Country Info**:\n\nPlease send the **TikTok username** (e.g., `@tiktok`) or a profile link to get the region/country.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
            parse_mode='Markdown'
        )
        context.user_data['state'] = 'awaiting_country_username' # NEW STATE
        return # Exit the function early

    if data in ('download_video', 'get_user_info'):
        
        status = get_user_access_status(user_id)
        action = data
        
        if status['access_level'] == 'premium':
            
            access_type = "Admin Access 👑" if status['is_admin'] else "Premium Access ✨"

            if action == 'download_video':
                await query.edit_message_text(
                    f"{access_type}: Send me the **URL of the TikTok Video** you want to download.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
                    parse_mode='Markdown'
                )
                context.user_data['state'] = 'awaiting_video_url'
            elif action == 'get_user_info':
                await query.edit_message_text(
                    f"{access_type}: Send me the **TikTok profile URL** or **username** for information.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
                    parse_mode='Markdown'
                )
                context.user_data['state'] = 'awaiting_user_info'
        
        elif not status['trial_used']:
            trial_text = (
                "⚠️ **Free Trial Available!** ⚠️\n\n"
                "You are a free user and have **one** single-use free trial available to try a feature (Download or Info).\n"
                "Do you want to use your free trial now to perform this action?"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Use Free Trial Now", callback_data=f'use_trial_for_{action}')],
                [InlineKeyboardButton("✨ Upgrade to Premium", callback_data='premium_info')],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]
            ])
            await query.edit_message_text(trial_text, reply_markup=keyboard, parse_mode='Markdown')

        else:
            await query.edit_message_text(
                "❌ **Access Denied.**\n\n"
                "You have used your free trial. Please purchase a Premium Subscription for unlimited access!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✨ Premium Subscription", callback_data='premium_info')],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]
                ]),
                parse_mode='Markdown'
            )
        
    # --- 2. Handle Admin/Access Clicks ---
    elif data == 'premium_info':
        await query.edit_message_text(
            "✨ **Premium Subscription Benefits:**\n\n"
            "- Unlimited high-quality video downloads.\n"
            "- Faster processing times.\n"
            "- Priority support.\n\n"
            "Press the 'Make a Payment' button to subscribe!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Make a Payment (Stripe)", callback_data='make_payment_placeholder')],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]
            ]),
            parse_mode='Markdown'
        )
    
    elif data == 'admin_panel':
        status = get_user_access_status(user_id)
        
        if status['is_admin']:
            await query.edit_message_text(
                "👑 **Admin Panel** 👑\n\n"
                "Welcome to the control center. Select an administrative task:",
                reply_markup=get_admin_panel_keyboard(),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text(
                "❌ **Access Denied.**\n\n"
                "This panel is restricted to administrators.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
                parse_mode='Markdown'
            )

    elif data == 'admin_view_subscribers':
        await view_subscribers(query, context)

    elif data == 'check_access':
        status = get_user_access_status(user_id)
        access_level = status['access_level']
        trial_status = "Used ❌" if status['trial_used'] else "Available ✅"
        admin_status = "Yes (👑)" if status['is_admin'] else "No"

        await query.edit_message_text(
            f"🔒 **Your Access Status:**\n\n"
            f"Current Level: **{access_level.upper()}**\n"
            f"Free Trial: **{trial_status}**\n"
            f"Admin Status: **{admin_status}**\n\n"
            "Thank you for using our bot!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
            parse_mode='Markdown'
        )
    
    elif data == 'make_payment_placeholder':
        await query.edit_message_text(
            "💳 **Payment Gateway (WIP)**\n\n"
            "The Stripe payment gateway is currently under construction. Once we get the payment ID, this button will initiate a payment. Please check back soon!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]]),
            parse_mode='Markdown'
        )

    elif data == 'back_to_menu':
        context.user_data['state'] = None
        await query.edit_message_text(
            "Welcome back to the main menu. Choose an option below to get started:",
            reply_markup=get_main_menu_keyboard()
        )

# NEW HANDLER DEFINITION for Country Button (Moved from Section 7)
async def get_country_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # Extract the username from callback data (e.g., 'get_country_for_tiktok')
    data = query.data
    username = data.replace('get_country_for_', '')
    
    if username == 'none':
         await query.edit_message_text("⚠️ Cannot get country info: Username was not found in the initial report.", parse_mode='Markdown')
         return

    # Convert username back to URL for the scraper
    user_url = f"https://www.tiktok.com/@{username}"
    
    # Use the original status message ID for editing
    status_message_id = query.message.message_id
    
    await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=status_message_id, 
                                        text=f"🌍 **Fetching Country Info** for @{username}...", parse_mode='Markdown')

    # Run BASE ACTOR to get only the reliable region data
    # CRITICAL FIX: Ensure Apify call runs in an executor thread
    apify_result_base = await asyncio.get_event_loop().run_in_executor(
        None,  
        lambda: execute_apify_run(USER_INFO_ACTOR_ID_BASE, {"url": user_url})
    )

    if apify_result_base["status"] == "success" and apify_result_base.get("actor") == "user_info_base":
         base_data = apify_result_base["data"]
         base_region = base_data.get('region')
         
         if base_region and base_region.upper() not in ['N/A', 'NONE', '']:
             flag = get_flag_emoji(base_region.upper())
             base_region_message = (
                 f"🌍 **Country Info for @{username}** 🌎\n\n"
                 f"**Region:** *{base_region}* {flag}"
             )
         else:
             base_region_message = (
                 f"❌ **Country Info Not Found** ❌\n\n"
                 f"Could not retrieve the region for @{username} from our secondary source. "
                 f"The account may be global or the data is protected."
             )
         
         # Send the dedicated country message
         await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=status_message_id,
             text=base_region_message,
             parse_mode='Markdown',
             reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]])
         )
         
    else:
        # Failure in the base actor run
        error_msg = apify_result_base.get('message', 'Failed to run secondary scraper.')
        await context.bot.edit_message_text(chat_id=query.message.chat_id, message_id=status_message_id, 
             text=f"❌ **Error Fetching Country Info:** {error_msg}", parse_mode='Markdown', 
             reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]])
         )

# =========================================================================
# --- 7. HANDLE USER INPUT (Download & Info Logic) ---
# =========================================================================

async def handle_url_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages (URLs/Usernames) based on the current state.)"""
    
    user_input = update.message.text.strip()
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    
    # Normalize input for user info scraping
    if not user_input.startswith(('http://', 'https://')) and state in ('awaiting_user_info', 'awaiting_country_username'):
        user_input = f"https://www.tiktok.com/@{user_input.lstrip('@')}"

    # Input validation (basic check)
    if state in ('awaiting_video_url', 'awaiting_user_info', 'awaiting_country_username') and 'tiktok.com' not in user_input:
        await update.message.reply_text(
            "That doesn't look like a valid TikTok link. Please try again or go back to the menu.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]])
        )
        return

    # A. Handle Video Download Request (wilcode/fast-tiktok-downloader-without-watermark)
    if state == 'awaiting_video_url':
        context.user_data['state'] = None
        
        status_message = await update.message.reply_text("⏳ Calling Apify API...")
        
        apify_result = await asyncio.get_event_loop().run_in_executor(
            None,  # Use default executor
            lambda: execute_apify_run(VIDEO_DOWNLOADER_ACTOR_ID, {"url": user_input})
        )
        
        if apify_result["status"] == "success" and apify_result.get("actor") == "downloader":
            download_url = apify_result["download_url"]
            title = apify_result["title"]
            was_trial = get_user_access_status(user_id)['trial_used'] 
            
            # --- Deliver Link Directly (Bypassing File Upload Error) ---
            
            caption = f"🎉 **Ready!** 🎉\n\n**Title:** {title}\n\n**Video Link:** [`{download_url}`]\n\n"
            caption += "ℹ️ **Note:** The link is provided directly to avoid Telegram upload errors. Download immediately as the link may expire!"
            if was_trial: caption += "\n\n*(Trial used.)*"

            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id, 
                                                text=caption, parse_mode='Markdown', disable_web_page_preview=True)

        else:
            response = f"❌ **FAILED**: {apify_result.get('message', 'Unknown Apify error.')}"
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id, 
                                                text=response, parse_mode='Markdown', disable_web_page_preview=True)
            
    # B. Handle Detailed User Info Request
    elif state == 'awaiting_user_info':
        context.user_data['state'] = None
        status_message = await update.message.reply_text("🕵️ Getting user information, please wait...")
        
        apify_result_full = await asyncio.get_event_loop().run_in_executor(
            None,  
            lambda: execute_apify_run(USER_INFO_ACTOR_ID_FULL, {"url": user_input})
        )
        
        final_user_data = {}
        
        if apify_result_full["status"] == "success" and apify_result_full.get("actor") == "user_info_full":
            final_user_data = apify_result_full["data"]
        
        if final_user_data.get('uniqueId'): 
            user_data = final_user_data
            
            # --- START USER INFO MAPPING (RESTORED DETAILED FORMAT) ---
            
            def format_bool(value):
                if isinstance(value, str): return "Yes ✅" if value.lower() == 'true' else "No ❌"
                return "Yes ✅" if value else "No ❌"

            def format_timestamp_data(timestamp):
                try:
                    ts_int = int(timestamp or 0)
                    if ts_int == 0: return "N/A"
                    if datetime.fromtimestamp(ts_int).year >= 1980: return datetime.fromtimestamp(ts_int).strftime('%Y-%m-%d %H:%M:%S')
                    return "N/A"
                except Exception: return "N/A"
            
            DEFAULT_NO_ICON = "No ❌"; DEFAULT_YES_ICON = "Yes ✅"; DEFAULT_EVERYONE = "Everyone 🌍"
            
            
            info_text = (
                f"📊 **TikTok User Info**\n\n"
                f"**Username:** @{user_data.get('uniqueId', user_data.get('username', 'N/A'))}\n"
                f"**ID:** {user_data.get('id', 'N/A')}\n"
                f"**Name:** {user_data.get('nickname', 'N/A')}\n"
                
                f"**Followers:** {user_data.get('followerCount', 0):,}\n" 
                f"**Following:** {user_data.get('followingCount', 0):,}\n" 
                f"**Friends:** {user_data.get('friendCount', 0):,}\n"
                f"**Likes:** {user_data.get('heartCount', 0):,}\n"
                f"**Videos:** {user_data.get('videoCount', 0):,}\n"
                
                f"**Account Created:** {format_timestamp_data(user_data.get('createTime'))}\n"
                f"**Username Modified:** {format_timestamp_data(user_data.get('uniqueIdModifyTime'))}\n"
                f"**Name Modified:** {format_timestamp_data(user_data.get('nickNameModifyTime'))}\n"
                
                f"**Country:** N/A\n" 
                f"**Language:** {user_data.get('language', 'Unknown language')}\n"
                f"**Verified Account:** {format_bool(user_data.get('verified', False))}\n" 
                f"**Download Settings:** {DEFAULT_EVERYONE}\n" 
                f"**Private Account:** {format_bool(user_data.get('privateAccount', False))}\n"
                f"**Secret Account:** {DEFAULT_NO_ICON}\n" 
                f"**Has Store:** {DEFAULT_NO_ICON}\n" 
                f"**Sells on TikTok:** {DEFAULT_NO_ICON}\n" 
                f"**Followers Visibility:** {DEFAULT_EVERYONE}\n" 
                f"**Duet Settings:** {DEFAULT_EVERYONE}\n" 
                f"**Publishing Settings:** {DEFAULT_EVERYONE}\n" 
                f"**Comment Settings:** {DEFAULT_EVERYONE}\n" 
                f"**Organization Account:** {format_bool(user_data.get('isOrganization'))}\n"
                f"**Account Relationship:** {user_data.get('relation', 0)}\n"
                f"**Favorites Open:** {DEFAULT_NO_ICON}\n" 
                f"**Embed Ban:** {format_bool(not user_data.get('profileEmbedPermission', True))}\n"

                f"\n• **Profile Tab Settings.**\n"
                f"**Show Music Tab:** {DEFAULT_NO_ICON}\n" 
                f"**Show Q&A Tab:** {DEFAULT_NO_ICON}\n" 
                f"**Show Playlist Tab:** {DEFAULT_NO_ICON}\n" 
                f"**Suggest Account Link?:** {DEFAULT_NO_ICON}\n" 
                f"**Can Expand Playlists:** {DEFAULT_YES_ICON}\n" 
                f"**Profile Embed Permission:** {format_bool(user_data.get('profileEmbedPermission', True))}\n" 
                f"**FTC Compliant:** {DEFAULT_NO_ICON}\n" 
                f"**Virtual Ads Account:** {DEFAULT_NO_ICON}\n" 
                
                f"**Bio:** {user_data.get('signature', 'N/A')}\n"
                f"**Link in Bio:** {user_data.get('bioLink', {}).get('link', 'None')}"
            )
            # --- END USER INFO MAPPING ---

            # Store the current input URL in context for the button handler to use later
            context.user_data['country_info_url'] = user_input

            # Send Message 1: Full Detailed Report
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id,
                text=info_text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌍 Get Country Info", callback_data=f'get_country_for_{user_data.get("uniqueId", "none")}')],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]
                ])
            )

        else:
            # Send failure message if no data was retrieved from the primary actor
            response = f"❌ **FAILED**: {apify_result_full.get('message', 'Could not retrieve user info.')}"
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id, 
                                                text=response, parse_mode='Markdown', disable_web_page_preview=True)
            
    # C. Handle Dedicated Country Info Input
    elif state == 'awaiting_country_username':
        context.user_data['state'] = None
        status_message = await update.message.reply_text(f"🌍 **Fetching Country Info** for @{user_input.lstrip('@')}...", parse_mode='Markdown')
        
        # Run BASE ACTOR to get only the reliable region data
        apify_result_base = await asyncio.get_event_loop().run_in_executor(
            None,  
            lambda: execute_apify_run(USER_INFO_ACTOR_ID_BASE, {"url": user_input})
        )

        if apify_result_base["status"] == "success" and apify_result_base.get("actor") == "user_info_base":
             base_data = apify_result_base["data"]
             base_region = base_data.get('region')
             username_display = base_data.get('uniqueId', user_input.lstrip('@'))
             
             if base_region and base_region.upper() not in ['N/A', 'NONE', '']:
                 flag = get_flag_emoji(base_region.upper())
                 base_region_message = (
                     f"✅ **Country Info for @{username_display}** 🌎\n\n"
                     f"**Region:** *{base_region}* {flag}"
                 )
             else:
                 base_region_message = (
                     f"❌ **Country Info Not Found** ❌\n\n"
                     f"Could not retrieve the region for @{username_display} from our secondary source. "
                     f"The account may be global or the data is protected."
                 )
             
             # Send the dedicated country message
             await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id,
                 text=base_region_message,
                 parse_mode='Markdown',
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]])
             )
        
        else:
            # Failure in the base actor run
            error_msg = apify_result_base.get('message', 'Failed to run scraper.')
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=status_message.message_id, 
                 text=f"❌ **Error Fetching Country Info:** {error_msg}", parse_mode='Markdown', 
                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Main Menu", callback_data='back_to_menu')]])
             )
            
    else:
        await update.message.reply_text(
            "I'm not sure how to handle that. Please use the menu buttons below.",
            reply_markup=get_main_menu_keyboard()
        )

# =========================================================================
# --- 8. MAIN EXECUTION ---
# =========================================================================

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_access)) 
    application.add_handler(CallbackQueryHandler(handle_trial_confirmation, pattern='^use_trial_for_'))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # NEW HANDLER: For the "Get Country Info" button press
    application.add_handler(CallbackQueryHandler(get_country_info, pattern='^get_country_for_'))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_input))

    logger.info("Bot started and polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
