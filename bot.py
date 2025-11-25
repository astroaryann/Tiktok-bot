import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import Forbidden
from apify_client import ApifyClient
from pymongo import MongoClient

# =========================================================================
# --- 1. CONFIGURATION ---
# =========================================================================

TELEGRAM_BOT_TOKEN = "8235693097:AAFJ9Dr2CdGyQceue6qCrSgWebRJ-l9x27I"
APIFY_TOKEN = "apify_api_O7h5l2DUoeLmssCI1gjyQgYT1ay4G52RDjGI"

# YOUR Personal User ID (To use the broadcast command)
# You can find this by talking to @userinfobot
ADMIN_IDS = [123456789] 

# Support Username (Where the support button goes)
SUPPORT_USERNAME = "player1522" 

# --- APIFY ACTORS ---
ACTOR_FULL_STATS = "v010Fa8JLkB0A5eIC" 
ACTOR_REGION = "4UB2bhV2zHNTpyHYe" 
ACTOR_DOWNLOADER = "wilcode/fast-tiktok-downloader-without-watermark"

# --- DATABASE ---
MONGO_CONN_STRING = "mongodb+srv://notaryan:aryan_patil152@cluster0.xaoenac.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "tiktok_bot_db"
COLLECTION_NAME = "users"

# =========================================================================
# --- 2. SETUP ---
# =========================================================================

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize DB
try:
    mongo_client = MongoClient(MONGO_CONN_STRING)
    db = mongo_client[DB_NAME]
    users_collection = db[COLLECTION_NAME]
    logger.info("✅ MongoDB Connected.")
except Exception as e:
    logger.error(f"❌ MongoDB Failed: {e}")
    users_collection = None

# =========================================================================
# --- 3. HELPER FUNCTIONS ---
# =========================================================================

def upsert_user(user_data):
    if users_collection is None: return
    users_collection.update_one(
        {"_id": user_data['id']},
        {
            "$set": {
                "username": user_data.get('username'),
                "first_name": user_data.get('first_name'),
                "last_active": datetime.now(),
            },
            "$setOnInsert": {
                "created_at": datetime.now(),
                "access_level": "free",
                "credits": {"download": 1, "info": 1},
                "is_admin": False,
            }
        },
        upsert=True
    )

def get_user_status(user_id):
    if users_collection is None: 
        return {"access": "free", "credits": {"download": 0, "info": 0}, "is_admin": False}
    doc = users_collection.find_one({"_id": user_id})
    if not doc: return {"access": "free", "credits": {"download": 0, "info": 0}, "is_admin": False}
    credits = doc.get("credits", {"download": 0, "info": 0})
    return {
        "access": doc.get("access_level", "free"),
        "credits": credits,
        "is_admin": doc.get("is_admin", False)
    }

def consume_credit(user_id, credit_type):
    if users_collection is None: return
    users_collection.update_one({"_id": user_id}, {"$inc": {f"credits.{credit_type}": -1}})

def run_apify(actor_id, run_input):
    try:
        client = ApifyClient(APIFY_TOKEN)
        run = client.actor(actor_id).call(run_input=run_input)
        if run.get('exitCode') != 0: return None
        dataset = client.dataset(run["defaultDatasetId"])
        items = list(dataset.iterate_items())
        return items[0] if items else None
    except Exception as e:
        logger.error(f"Apify Error: {e}")
        return None

# Formatting Helpers
def get_flag_emoji(cc): return "".join(chr(0x1F1E6 + (ord(c) - ord('A'))) for c in cc.upper()) if cc else "🌍"
def format_bool(val): return "Yes ✅" if val else "No ❌"
def format_ts(ts): 
    try: return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %H:%M:%S') if ts else "N/A"
    except: return "N/A"

# =========================================================================
# --- 4. KEYBOARDS ---
# =========================================================================

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🎬 Download Video", callback_data='req_download'),
         InlineKeyboardButton("📊 Profile & Country Info", callback_data='req_info')],
        [InlineKeyboardButton("💎 My Account & Plan", callback_data='my_account')],
        [InlineKeyboardButton("💳 Upgrade (Stripe)", url="https://stripe.com"), # Update later
         InlineKeyboardButton("🆘 Support", url=f"https://t.me/{SUPPORT_USERNAME}")], # NEW BUTTON
        [InlineKeyboardButton("⚙️ Admin Panel", callback_data='admin_panel')]
    ]
    return InlineKeyboardMarkup(keyboard)

# =========================================================================
# --- 5. BROADCAST COMMAND (NEW) ---
# =========================================================================

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if sender is in the Admin list (Defined at top)
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not an Admin.")
        return

    # Check if they typed a message
    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast Your Message Here`", parse_mode='Markdown')
        return

    message_text = " ".join(context.args)
    msg = await update.message.reply_text("📢 Starting broadcast...")

    users = users_collection.find({})
    success = 0
    blocked = 0

    for u in users:
        try:
            await context.bot.send_message(chat_id=u['_id'], text=f"📢 **Announcement:**\n\n{message_text}", parse_mode='Markdown')
            success += 1
            await asyncio.sleep(0.1) # Sleep to avoid hitting Telegram limits
        except Forbidden:
            blocked += 1 # User blocked bot
        except Exception as e:
            logger.error(f"Broadcast error: {e}")

    await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, 
                                        text=f"✅ **Broadcast Complete**\n\nSent: {success}\nBlocked/Failed: {blocked}", parse_mode='Markdown')

# =========================================================================
# --- 6. CORE HANDLERS ---
# =========================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(update.effective_user.to_dict())
    await update.message.reply_text("👋 **Welcome!** Select an option:", reply_markup=get_main_menu(), parse_mode='Markdown')

async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    status = get_user_status(query.from_user.id)
    
    if status['access'] == 'premium':
        txt = "👑 **Premium VIP**\n✅ Unlimited Access"
    else:
        dl = status['credits']['download']
        inf = status['credits']['info']
        txt = f"👤 **Free Tier**\n\n🎬 DL Credits: {dl}\n📊 Info Credits: {inf}\n\n💳 Upgrade for Unlimited!"
        
    await query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='back_home')]]), parse_mode='Markdown')

async def handle_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    status = get_user_status(user_id)
    is_premium = status['access'] == 'premium'
    
    if data == 'back_home':
        await query.edit_message_text("Choose an option:", reply_markup=get_main_menu())
        context.user_data['state'] = None
        return

    # Check Credits logic
    if data == 'req_download':
        if is_premium or status['credits']['download'] > 0:
            context.user_data['state'] = 'awaiting_dl_link'
            await query.edit_message_text("✅ **Send Video Link:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data='back_home')]]), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ **No Credits Left.** Upgrade now!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='back_home')]]))

    elif data == 'req_info':
        if is_premium or status['credits']['info'] > 0:
            context.user_data['state'] = 'awaiting_info_user'
            await query.edit_message_text("✅ **Send Username:**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Cancel", callback_data='back_home')]]), parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ **No Credits Left.** Upgrade now!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='back_home')]]))


# =========================================================================
# --- 7. TEXT INPUT HANDLER (SMART INPUT + DUAL SCRAPER + ANIMATION) ---
# =========================================================================

async def handle_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    state = context.user_data.get('state')
    
    if not state:
        await start(update, context)
        return

    status = get_user_status(user_id)
    is_premium = status['access'] == 'premium'

    # --- 1. VIDEO DOWNLOAD (Strict Link Check) ---
    if state == 'awaiting_dl_link':
        # VALIDATION: Downloads MUST be links
        if "tiktok.com" not in text:
            await update.message.reply_text("⚠️ **Invalid Link.**\nFor downloads, please send a full TikTok video link.")
            return

        if not is_premium and status['credits']['download'] <= 0:
            await update.message.reply_text("❌ Out of credits.")
            return

        # ANIMATION
        msg = await update.message.reply_text("⏳ **Initializing Download...**", parse_mode='Markdown')
        await asyncio.sleep(0.5)
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="📥 **Fetching Video Link...**", parse_mode='Markdown')

        result = await asyncio.get_event_loop().run_in_executor(None, lambda: run_apify(ACTOR_DOWNLOADER, {"url": text, "apiVersion": "v1"}))
        
        if result and result.get('result'):
            video_data = result['result'].get('video', {})
            dl_link = video_data.get('playAddr')
            title = result['result'].get('desc', 'TikTok Video')
            
            if dl_link:
                if not is_premium: consume_credit(user_id, 'download')
                
                response_text = (
                    f'🎉 <b>Ready!</b>\n\n'
                    f'📝 <b>Title:</b> {title}\n'
                    f'🔗 <a href="{dl_link}">Click Here to Download</a>\n\n'
                    f'👇 <b>Raw Link (Copy/Paste if button fails):</b>\n{dl_link}'
                )
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=response_text, parse_mode='HTML')
            else:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="❌ Link found but empty. Video might be private.")
        else:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="❌ Download failed. Invalid link or Private video.")
        
        context.user_data['state'] = None

    # --- 2. USER INFO (Smart Input + Dual Scraper) ---
    elif state == 'awaiting_info_user':
        # NOTE: No strict "tiktok.com" check here. We accept anything.

        if not is_premium and status['credits']['info'] <= 0:
            await update.message.reply_text("❌ Out of credits.")
            return

        # --- SMART USERNAME CLEANING ---
        # Logic: 
        # 1. If it's a URL (contains tiktok.com), grab the part after @
        # 2. If it's just @aryan, remove @
        # 3. If it's just aryan, keep it.
        
        raw_username = text
        if "tiktok.com" in text:
            # Handle URL: https://www.tiktok.com/@username?lang=en
            try:
                raw_username = text.split('@')[-1].split('?')[0].split('/')[0]
            except:
                raw_username = text # Fallback
        else:
            # Handle Username: @username or username
            raw_username = text.replace('@', '').strip()

        # Reconstruct URL for the Region Scraper (it needs a URL, not just a name)
        full_url = f"https://www.tiktok.com/@{raw_username}"
        
        # --- ANIMATION START ---
        msg = await update.message.reply_text(f"⏳ **Connecting to Database...**", parse_mode='Markdown')
        
        # STEP 1: RUN FULL STATS
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"🕵️ **Step 1/2:** Extracting Profile Data for @{raw_username}...", parse_mode='Markdown')
        full = await asyncio.get_event_loop().run_in_executor(None, lambda: run_apify(ACTOR_FULL_STATS, {"usernames": [raw_username]}))

        if full:
            # STEP 2: RUN REGION CHECK
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=f"🌍 **Step 2/2:** Detecting Region & Flag...", parse_mode='Markdown')
            
            region_input = {
                "url": full_url,
                "max_results": 0,
                "max_page": 1,
                "video_details": False,
                "proxy_settings": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                    "apifyProxyCountry": "US",
                }
            }
            # Using Actor: 4UB2bhV2zHNTpyHYe
            base = await asyncio.get_event_loop().run_in_executor(None, lambda: run_apify(ACTOR_REGION, region_input))

            # --- MERGE DATA ---
            u, s = full.get('user', {}), full.get('stats', {})
            
            # Extract Region
            region_code = "N/A"
            if base:
                region_code = base.get('region', base.get('locationCreated', 'N/A'))
            
            flag = get_flag_emoji(region_code) if region_code != "N/A" else "🌍"

            # --- FINAL REPORT ---
            report = (
                f"Username: {u.get('uniqueId')}\nID: {u.get('id')}\nName: {u.get('nickname')}\n"
                f"Followers: {s.get('followerCount')}\nFollowing: {s.get('followingCount')}\n"
                f"Friends: {s.get('friendCount')}\nLikes: {s.get('heartCount')}\nVideos: {s.get('videoCount')}\n"
                f"Account Created: {format_ts(u.get('createTime'))}\n"
                f"Username Modified: {format_ts(u.get('uniqueIdModifyTime'))}\n"
                f"Name Modified: {format_ts(u.get('nickNameModifyTime'))}\n"
                f"Country: {region_code} {flag}\n"
                f"Language: {u.get('language')}\nVerified Account: {format_bool(u.get('verified'))}\n"
                f"Download Settings: Everyone 🌍\nPrivate Account: {format_bool(u.get('privateAccount'))}\n"
                f"Secret Account: No ❌\nHas Store: No ❌\nSells on TikTok: No ❌\n"
                f"Followers Visibility: Everyone 🌍\nDuet Settings: Everyone 🌍\n"
                f"Publishing Settings: Everyone 🌍\nComment Settings: Everyone 🌍\n"
                f"Organization Account: {format_bool(u.get('isOrganization'))}\n"
                f"Account Relationship: {u.get('relation')}\nFavorites Open: No ❌\n"
                f"Embed Ban: {format_bool(not u.get('profileEmbedPermission', True))}\n"
                f"• Profile Tab Settings.\n"
                f"Show Music Tab: No ❌\nShow Q&A Tab: No ❌\nShow Playlist Tab: No ❌\n"
                f"Suggest Account Link?: No ❌\nCan Expand Playlists: Yes ✅\n"
                f"Profile Embed Permission: {format_bool(u.get('profileEmbedPermission'))}\n"
                f"FTC Compliant: No ❌\nVirtual Ads Account: No ❌\n"
                f"Bio: ({u.get('signature', '')})\nLink in Bio: ({u.get('bioLink', {}).get('link', 'None')})"
            )
            
            if not is_premium: consume_credit(user_id, 'info')
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=report)
        else:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="❌ User not found. Check the username.")
            
        context.user_data['state'] = None

    # --- 2. USER INFO (FULL JUNK DATA) ---
    elif state == 'awaiting_info_user':
        if not is_premium and status['credits']['info'] <= 0:
            await update.message.reply_text("❌ Out of credits.")
            return

        username = text.replace('https://www.tiktok.com/@', '').replace('@', '').split('?')[0].split('/')[0]
        full_url = f"https://www.tiktok.com/@{username}"
        
        msg = await update.message.reply_text(f"🕵️ **Scanning @{username}...**", parse_mode='Markdown')
        
        full = await asyncio.get_event_loop().run_in_executor(None, lambda: run_apify(ACTOR_FULL_STATS, {"usernames": [username]}))
        base = await asyncio.get_event_loop().run_in_executor(None, lambda: run_apify(ACTOR_REGION, {"url": full_url, "max_results": 1}))
        
        if full:
            u, s = full.get('user', {}), full.get('stats', {})
            region = base.get('region', 'Unknown').upper() if base else "Unknown"
            
            # THE MASSIVE REPORT (Junk Included)
            report = (
                f"Username: {u.get('uniqueId')}\nID: {u.get('id')}\nName: {u.get('nickname')}\n"
                f"Followers: {s.get('followerCount')}\nFollowing: {s.get('followingCount')}\n"
                f"Friends: {s.get('friendCount')}\nLikes: {s.get('heartCount')}\nVideos: {s.get('videoCount')}\n"
                f"Account Created: {format_ts(u.get('createTime'))}\n"
                f"Username Modified: {format_ts(u.get('uniqueIdModifyTime'))}\n"
                f"Name Modified: {format_ts(u.get('nickNameModifyTime'))}\n"
                f"Country: {region} {get_flag_emoji(region)}\n"
                f"• @tiktokinfobot 🔰\n"
                f"Language: {u.get('language')}\nVerified Account: {format_bool(u.get('verified'))}\n"
                f"Download Settings: Everyone 🌍\nPrivate Account: {format_bool(u.get('privateAccount'))}\n"
                f"Secret Account: No ❌\nHas Store: No ❌\nSells on TikTok: No ❌\n"
                f"Followers Visibility: Everyone 🌍\nDuet Settings: Everyone 🌍\n"
                f"Publishing Settings: Everyone 🌍\nComment Settings: Everyone 🌍\n"
                f"Organization Account: {format_bool(u.get('isOrganization'))}\n"
                f"Account Relationship: {u.get('relation')}\nFavorites Open: No ❌\n"
                f"Embed Ban: {format_bool(not u.get('profileEmbedPermission', True))}\n"
                f"• Profile Tab Settings.\n"
                f"Show Music Tab: No ❌\nShow Q&A Tab: No ❌\nShow Playlist Tab: No ❌\n"
                f"Suggest Account Link?: No ❌\nCan Expand Playlists: Yes ✅\n"
                f"Profile Embed Permission: {format_bool(u.get('profileEmbedPermission'))}\n"
                f"FTC Compliant: No ❌\nVirtual Ads Account: No ❌\n"
                f"Bio: ({u.get('signature', '')})\nLink in Bio: ({u.get('bioLink', {}).get('link', 'None')})"
            )
            
            if not is_premium: consume_credit(user_id, 'info')
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text=report)
        else:
            await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg.message_id, text="❌ User not found.")
            
        context.user_data['state'] = None

# =========================================================================
# --- 8. RUNNER ---
# =========================================================================

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast)) # NEW COMMAND
    app.add_handler(CallbackQueryHandler(my_account, pattern='^my_account$'))
    app.add_handler(CallbackQueryHandler(handle_requests))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_input))

    print("Bot is Running...")
    app.run_polling()