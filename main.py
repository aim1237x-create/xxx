from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    PreCheckoutQueryHandler
)
import logging

# ───────── إعدادات البوت ─────────
BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"
PROVIDER_TOKEN = "YOUR_PROVIDER_TOKEN_HERE"  # ← ضع توكن الدفع هنا

ARAB_CODES = [
    "20", "966", "971", "965", "974", "973", "968",
"212", "213", "216", "218", "221", "222", "223",
"224", "225", "226", "227", "228", "229",
"249", "252", "253", "269", "970", "962",
"964", "963", "961", "967"
]

# ───────── تخزين البيانات ─────────
user_points = {}
user_invites = {}
user_chats = {}
user_data = {}

# ───────── إعداد التسجيل ─────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ───────── دالة البداية ─────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_chats[user_id] = update.effective_chat.id
    
    if context.args and context.args[0].startswith('invite_'):
        inviter_id = int(context.args[0].split('_')[1])
        if user_id not in user_invites:
            user_invites[user_id] = inviter_id
            await update.message.reply_text("🎉 مرحباً عبر رابط الدعوة!")
    
    btn = KeyboardButton("📱 مشاركة الرقم", request_contact=True)
    kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True)
    
    await update.message.reply_text(
        "مرحباً 👋\n\n"
        "شارك رقمك العربي للحصول على 20 نقطة مجانية.",
        reply_markup=kb
    )

# ───────── استلام الرقم ─────────
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.contact.phone_number
    
    if not any(phone.startswith(code) for code in ARAB_CODES):
        await update.message.reply_text("❌ الرقم غير عربي")
        return
    
    user_points[user_id] = 20
    user_data[user_id] = {"verified": True, "phone": phone}
    
    if user_id in user_invites:
        inviter_id = user_invites[user_id]
        if inviter_id in user_points:
            user_points[inviter_id] += 10
            await context.bot.send_message(
                chat_id=user_chats.get(inviter_id),
                text=f"🎉 +10 نقاط\n🏆 رصيدك: {user_points.get(inviter_id, 0)}"
            )
        del user_invites[user_id]
    
    await update.message.reply_text("✅ تم التسجيل")
    await main_menu(update, context)

# ───────── القائمة الرئيسية ─────────
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points = user_points.get(user_id, 0)
    
    keyboard = [
        [KeyboardButton("🎯 رشق")],
        [KeyboardButton("💰 شراء نقاط")],
        [KeyboardButton("❌ إنهاء"), KeyboardButton("📞 الدعم")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"🏆 نقاطك: {points}\n👇 اختر:",
        reply_markup=reply_markup
    )

# ───────── إنهاء الجلسة ─────────
async def end_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_keyboard = ReplyKeyboardMarkup([[KeyboardButton("/start")]], resize_keyboard=True)
    await update.message.reply_text("✅ تم إنهاء الجلسة", reply_markup=remove_keyboard)

# ───────── الدعم الفني ─────────
async def contact_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    
    keyboard = [[InlineKeyboardButton("💬 تواصل مع الدعم", url=f"tg://user?id={user_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📞 للتواصل مع الدعم:\nاضغط الزر أدناه",
        reply_markup=reply_markup
    )

# ───────── قائمة الشراء ─────────
async def buy_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("⭐ 5 نجوم - 50 نقطة", callback_data="buy_5"),
            InlineKeyboardButton("⭐⭐ 10 نجوم - 120 نقطة", callback_data="buy_10")
        ],
        [
            InlineKeyboardButton("⭐⭐⭐ 20 نجوم - 250 نقطة", callback_data="buy_20_manual"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐ 50 نجوم - اشتراك دائم", callback_data="buy_50_manual")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "💰 اختر الباقة:\n\n"
        "⭐ 5 نجوم = 50 نقطة\n"
        "⭐⭐ 10 نجوم = 120 نقطة\n"
        "⭐⭐⭐ 20 نجوم = 250 نقطة\n"
        "⭐⭐⭐⭐⭐ 50 نجوم = اشتراك دائم",
        reply_markup=reply_markup
    )

# ───────── معالج الأزرار الداخلية ─────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "buy_5":
        await send_invoice(query, 5, 50, "buy_5_stars")
    elif data == "buy_10":
        await send_invoice(query, 10, 120, "buy_10_stars")
    elif data == "buy_20_manual":
        await manual_payment(query, 20, 250)
    elif data == "buy_50_manual":
        await manual_payment(query, 50, "اشتراك دائم")

# ───────── إرسال فاتورة الدفع ─────────
async def send_invoice(query, stars, points, payload):
    prices = [LabeledPrice(f"{stars} ⭐", stars * 100)]  # 1 نجمة = 100 وحدة
    
    await query.message.reply_invoice(
        title=f"{stars} نجمة - {points} نقطة",
        description=f"شراء {points} نقطة مقابل {stars} نجوم",
        payload=payload,
        provider_token=PROVIDER_TOKEN,
        currency="XTR",
        prices=prices,
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False
    )

# ───────── دفع يدوي (لـ 20 و50 نجمة) ─────────
async def manual_payment(query, stars, points):
    await query.edit_message_text(
        f"💰 دفع يدوي لـ {stars} نجمة\n\n"
        f"المكافأة: {points}\n\n"
        "📩 ارسل النجوم مباشرة إلى حساب المالك:\n"
        "👤 @MO_3MK\n\n"
        "⚠️ بعد الإرسال:\n"
        "1. احفظ إيصال الدفع\n"
        "2. تواصل مع المالك @MO_3MK\n"
        "3. أرسل الإيصال مع ID حسابك\n"
        "4. ستضاف النقاط خلال 5 دقائق"
    )

# ───────── معالجة طلب الدفع المسبق ─────────
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

# ───────── معالجة الدفع الناجح ─────────
async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    
    if payload == "buy_5_stars":
        points_to_add = 50
    elif payload == "buy_10_stars":
        points_to_add = 120
    else:
        points_to_add = 0
    
    if user_id not in user_points:
        user_points[user_id] = 0
    
    user_points[user_id] += points_to_add
    
    await update.message.reply_text(
        f"✅ تمت العملية بنجاح!\n"
        f"🎁 تم إضافة {points_to_add} نقطة\n"
        f"🏆 رصيدك الآن: {user_points[user_id]}"
    )

# ───────── معالج النصوص ─────────
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "🎯 رشق":
        await attack_menu(update, context)
    elif text == "💰 شراء نقاط":
        await buy_points(update, context)
    elif text == "❌ إنهاء":
        await end_session(update, context)
    elif text == "📞 الدعم":
        await contact_support(update, context)
    elif text == "/start":
        await start(update, context)

# ───────── قائمة الرشق ─────────
async def attack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points = user_points.get(user_id, 0)
    
    await update.message.reply_text(
        f"🎯 الرشق\n🏆 رصيدك: {points}\n\n"
        "أرسل الرقم المراد رشقه:"
    )

# ───────── تشغيل البوت ─────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    print("🤖 البوت يعمل...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
