import os
import json
import base64
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Config
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ZIBONSATHI_API_URL = os.getenv("ZIBONSATHI_API_URL", "https://zibonsathi.com/api/webhook/import-biodata")
ZIBONSATHI_API_KEY = os.getenv("ZIBONSATHI_API_KEY")

# Store pending biodatas (chat_id -> biodata)
pending_biodatas = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🙏 স্বাগতম জিবনসাথী বায়োডাটা বটে!\n\n"
        "একটি বায়োডাটার ছবি পাঠান, আমি এক্সট্রাক্ট করে দেখাবো।\n"
        "তারপর আপনি সেভ করতে পারবেন।"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    
    # Send processing message
    processing_msg = await update.message.reply_text("⏳ বায়োডাটা প্রসেস হচ্ছে...")
    
    try:
        # Get the largest photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Download photo
        photo_bytes = await file.download_as_bytearray()
        base64_image = base64.b64encode(photo_bytes).decode('utf-8')
        
        # Call Gemini API
        biodata = await extract_biodata_gemini(base64_image)
        
        if biodata:
            # Store pending biodata
            pending_biodatas[chat_id] = biodata
            
            # Format preview
            preview = format_biodata_preview(biodata)
            
            # Create inline keyboard
            keyboard = [
                [
                    InlineKeyboardButton("✅ সেভ করুন", callback_data="save"),
                    InlineKeyboardButton("❌ বাতিল", callback_data="cancel")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await processing_msg.edit_text(preview, reply_markup=reply_markup)
        else:
            await processing_msg.edit_text("❌ বায়োডাটা এক্সট্রাক্ট করতে পারিনি। অন্য একটি ছবি পাঠান।")
            
    except Exception as e:
        print(f"Error: {e}")
        await processing_msg.edit_text(f"❌ ত্রুটি হয়েছে: {str(e)[:100]}")

async def extract_biodata_gemini(base64_image: str) -> dict:
    """Extract biodata using Gemini Vision API"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    
    prompt = """Extract biodata from this Bangladeshi matrimonial biodata image. 
Return ONLY a valid JSON object with these fields:
- name (string)
- gender (string: "male" or "female")
- dateOfBirth (string: YYYY-MM-DD format)
- division (string)
- district (string)
- upazila (string)
- education (string)
- occupation (string)
- monthlyIncome (string)
- maritalStatus (string: "never_married", "divorced", or "widowed")
- fatherAlive (string: "yes" or "no")
- motherAlive (string: "yes" or "no")
- siblings (number)
- prayerHabit (string)
- beardOrHijab (string)
- mazhab (string)
- aboutMe (string)
- partnerPreferences (string)
- phoneNumber (string)
- guardianPhone (string)

Use null for any missing fields. Return ONLY valid JSON, no other text."""

    payload = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}},
                {"text": prompt}
            ]
        }]
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": GEMINI_API_KEY
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        result = response.json()
        
        if "candidates" in result:
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                return json.loads(json_match.group())
    
    return None

def escape_markdown(text: str) -> str:
    """Escape special Markdown characters"""
    if not text:
        return "N/A"
    # Just return plain text, no escaping needed since we don't use parse_mode
    return str(text)

def format_biodata_preview(biodata: dict) -> str:
    """Format biodata for Telegram preview"""
    def get_field(key):
        val = biodata.get(key)
        return str(val) if val else "N/A"
    
    return f"""📋 এক্সট্রাক্ট করা বায়োডাটা:

👤 নাম: {get_field('name')}
⚧ লিঙ্গ: {get_field('gender')}
🎂 জন্মতারিখ: {get_field('dateOfBirth')}
📍 বিভাগ: {get_field('division')}
📍 জেলা: {get_field('district')}
📍 উপজেলা: {get_field('upazila')}
🎓 শিক্ষা: {get_field('education')}
💼 পেশা: {get_field('occupation')}
💰 আয়: {get_field('monthlyIncome')}
💍 বৈবাহিক: {get_field('maritalStatus')}
👨 বাবা: {get_field('fatherAlive')}
👩 মা: {get_field('motherAlive')}
👫 ভাইবোন: {get_field('siblings')}
🕌 নামাজ: {get_field('prayerHabit')}
📱 ফোন: {get_field('phoneNumber')}
📱 অভিভাবক: {get_field('guardianPhone')}

সেভ করতে নিচের বাটনে ক্লিক করুন"""

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    chat_id = query.message.chat_id
    
    await query.answer()
    
    if query.data == "save":
        biodata = pending_biodatas.get(chat_id)
        if biodata:
            # Save to Zibonsathi API
            result = await save_biodata(biodata)
            if result:
                await query.edit_message_text(
                    f"✅ বায়োডাটা সেভ হয়েছে!\n\n"
                    f"📋 ID: {result.get('biodataId', 'N/A')}\n"
                    f"👤 নাম: {result.get('name', biodata.get('name', 'N/A'))}"
                )
            else:
                await query.edit_message_text("❌ সেভ করতে সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।")
            # Clear pending
            pending_biodatas.pop(chat_id, None)
        else:
            await query.edit_message_text("❌ বায়োডাটা পাওয়া যায়নি। আবার ছবি পাঠান।")
    
    elif query.data == "cancel":
        pending_biodatas.pop(chat_id, None)
        await query.edit_message_text("❌ বাতিল করা হয়েছে।\n\nনতুন বায়োডাটার ছবি পাঠান।")

async def save_biodata(biodata: dict) -> dict:
    """Save biodata to Zibonsathi API"""
    payload = {
        "displayName": biodata.get("name"),
        "gender": biodata.get("gender"),
        "dateOfBirth": biodata.get("dateOfBirth"),
        "division": biodata.get("division"),
        "district": biodata.get("district"),
        "upazila": biodata.get("upazila"),
        "education": biodata.get("education"),
        "occupation": biodata.get("occupation"),
        "monthlyIncome": biodata.get("monthlyIncome"),
        "maritalStatus": biodata.get("maritalStatus"),
        "fatherAlive": biodata.get("fatherAlive"),
        "motherAlive": biodata.get("motherAlive"),
        "siblings": biodata.get("siblings"),
        "prayerHabit": biodata.get("prayerHabit"),
        "beardOrHijab": biodata.get("beardOrHijab"),
        "mazhab": biodata.get("mazhab"),
        "aboutMe": biodata.get("aboutMe"),
        "partnerPreferences": biodata.get("partnerPreferences"),
        "phoneNumber": biodata.get("phoneNumber"),
        "guardianPhone": biodata.get("guardianPhone"),
        "source": "telegram-bot"
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ZIBONSATHI_API_KEY}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(ZIBONSATHI_API_URL, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                return response.json().get("data", {})
    except Exception as e:
        print(f"Save error: {e}")
    
    return None

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    await update.message.reply_text(
        "দয়া করে একটি বায়োডাটার ছবি পাঠান।\n"
        "আমি ছবি থেকে তথ্য এক্সট্রাক্ট করে দেখাবো।"
    )

def main():
    """Start the bot"""
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_TOKEN not set")
        return
    if not GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY not set")
        return
    
    print("Starting Zibonsathi Biodata Bot...")
    
    # Create application
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    # Run bot
    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
