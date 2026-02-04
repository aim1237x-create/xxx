from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from PIL import Image, ImageDraw, ImageFont
import random
import os

BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"

ARAB_CODES = [
    "+20", "+966", "+971", "+965", "+974", "+973", "+968",
    "+212", "+213", "+216", "+218", "+221", "+222", "+223",
    "+224", "+225", "+226", "+227", "+228", "+229",
    "+249", "+252", "+253", "+269", "+970", "+962",
    "+964", "+963", "+961", "+967"
]

user_codes = {}
user_points = {}

# ───────── start ─────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btn = KeyboardButton("📱 شارك رقمك", request_contact=True)
    kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True)
    await update.message.reply_text(
        "مرحبًا 👋\n\nيرجى مشاركة رقم هاتفك للمتابعة:",
        reply_markup=kb
    )

# ───────── استلام الرقم ─────────
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.contact.phone_number

    if not any(phone.startswith(code) for code in ARAB_CODES):
        await update.message.reply_text("❌ الرقم غير تابع لدولة عربية")
        return

    user_points[user_id] = 20

    code = random.randint(1000, 9999)
    user_codes[user_id] = code

    image_path = create_code_image(code)

    await update.message.reply_text("✅ تم قبول الرقم\n🎯 حصلت على 20 نقطة")
    await update.message.reply_photo(
        photo=open(image_path, "rb"),
        caption="✏️ اكتب الرقم الظاهر داخل الصورة للتأكيد:"
    )

    os.remove(image_path)

# ───────── التحقق من الكود ─────────
async def verify_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if user_id not in user_codes:
        return

    if text == str(user_codes[user_id]):
        await update.message.reply_text(
            f"✅ تم التأكيد بنجاح\n🏆 نقاطك: {user_points[user_id]}"
        )
        del user_codes[user_id]
    else:
        await update.message.reply_text("❌ الرقم غير صحيح، حاول مرة أخرى")

# ───────── إنشاء صورة ─────────
def create_code_image(code):
    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 80)
    except:
        font = ImageFont.load_default()

    text = str(code)
    w, h = draw.textsize(text, font=font)
    draw.text(((400 - w) / 2, (200 - h) / 2), text, fill="black", font=font)

    path = f"code_{code}.png"
    img.save(path)
    return path

# ───────── تشغيل البوت ─────────
app = Application.builder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, verify_code))

app.run_polling()
