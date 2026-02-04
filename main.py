from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)
import random
import asyncio
import logging

# ───────── إعدادات البوت ─────────
BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"
ARAB_CODES = [
    "20", "966", "971", "965", "974", "973", "968",
"212", "213", "216", "218", "221", "222", "223",
"224", "225", "226", "227", "228", "229",
"249", "252", "253", "269", "970", "962",
"964", "963", "961", "967"
]

# ───────── متغيرات إضافية ─────────
SUPPORT_USERNAME = "your_support_username"  # ضع اسم المستخدم للدعم هنا
ADMIN_USERNAME = "your_admin_username"     # ضع اسم المشرف هنا
BANK_NAME = "البنك العربي"
BANK_ACCOUNT = "123456789"
BANK_IBAN = "SA1234567890123456789012"
PHONE_NUMBER = "+966501234567"

# ───────── تخزين البيانات ─────────
user_codes = {}
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
    
    # معالجة رابط الدعوة
    if context.args and context.args[0].startswith('invite_'):
        inviter_id = int(context.args[0].split('_')[1])
        if user_id not in user_invites:
            user_invites[user_id] = inviter_id
            await update.message.reply_text(
                "🎉 مرحباً بك عبر رابط الدعوة!\n"
                "ستحصل على 10 نقاط إضافية بعد التسجيل."
            )
    
    btn = KeyboardButton("📱 شارك رقمك", request_contact=True)
    kb = ReplyKeyboardMarkup([[btn]], resize_keyboard=True)
    
    await update.message.reply_text(
        "مرحبًا بك في بوت الرشق! 👋\n\n"
        "🔹 *لتتمكن من استخدام البوت، يرجى مشاركة رقم هاتفك:*\n"
        "▫️ يجب أن يكون الرقم عربي\n"
        "▫️ ستتلقى 20 نقطة مجانية فوراً\n\n"
        "🎯 *مميزات البوت:*\n"
        "• رشق مباشر\n"
        "• كسب نقاط مجاني\n"
        "• شراء نقاط بأسعار مميزة\n"
        "• نظام دعوة مربح\n\n"
        "➖➖➖➖➖➖➖➖➖➖",
        reply_markup=kb,
        parse_mode='Markdown'
    )

# ───────── استلام الرقم ─────────
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.contact.phone_number
    
    # التحقق من الرقم العربي
    if not any(phone.startswith(code) for code in ARAB_CODES):
        await update.message.reply_text(
            "❌ *الرقم غير تابع لدولة عربية*\n\n"
            "▫️ يرجى مشاركة رقم هاتف عربي صحيح\n"
            "▫️ يجب أن يبدأ الرقم بأحد الرموز العربية",
            parse_mode='Markdown'
        )
        return
    
    # إعطاء النقاط الأولية
    user_points[user_id] = 20
    user_data[user_id] = {"verified": True, "phone": phone}
    
    # إعطاء نقاط الدعوة إذا كان عبر رابط
    if user_id in user_invites:
        inviter_id = user_invites[user_id]
        if inviter_id in user_points:
            user_points[inviter_id] += 10
            await context.bot.send_message(
                chat_id=user_chats.get(inviter_id),
                text="🎉 *تهانينا!*\n\n"
                     "حصلت على 10 نقاط إضافية\n"
                     "لأن أحد المدعوين قام بالتسجيل\n\n"
                     f"🏆 *إجمالي نقاطك الآن:* {user_points.get(inviter_id, 0)}",
                parse_mode='Markdown'
            )
        del user_invites[user_id]
    
    await update.message.reply_text(
        "✅ *تم التسجيل بنجاح!*\n\n"
        "🎁 *المكافآت التي حصلت عليها:*\n"
        f"• 20 نقطة مجانية\n"
        "• حسابك مفعل الآن\n"
        "🚀 يمكنك البدء باستخدام البوت\n\n"
        f"🏆 *إجمالي نقاطك الآن:* {user_points.get(user_id, 0)}\n\n"
        "👇 استخدم الأزرار للتنقل:",
        parse_mode='Markdown'
    )
    
    await asyncio.sleep(1)
    await main_menu(update, context)

# ───────── القائمة الرئيسية ─────────
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    
    # إنشاء اسم المستخدم مع رابط
    if user.username:
        name_link = f"https://t.me/{user.username}"
        name_display = f'<a href="{name_link}">{user.first_name}</a>'
    else:
        name_display = user.first_name
    
    points = user_points.get(user_id, 0)
    invite_count = len([uid for uid, inviter in user_invites.items() if inviter == user_id])
    
    # تصميم واجهة جميلة
    message_text = (
        f"🌟 <b>مرحباً {name_display}!</b>\n\n"
        f"🏆 <b>عدد نقاطك:</b> <code>{points}</code>\n"
        f"👥 <b>عدد الأيدي:</b> <code>{invite_count}</code>\n\n"
        "➖➖➖➖➖➖➖➖➖➖"
    )
    
    # أزرار القائمة الرئيسية
    keyboard = [
        [KeyboardButton("🎯 أرشق الآن")],
        [KeyboardButton("💰 كسب النقاط")],
        [KeyboardButton("📞 خدمة العملاء")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

# ───────── قائمة كسب النقاط ─────────
async def earn_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("🆓 كسب النقاط مجاناً")],
        [KeyboardButton("💳 كسب النقاط عن طريق الدفع")],
        [KeyboardButton("🔙 العودة للرئيسية")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        "💰 *خيارات كسب النقاط*\n\n"
        "اختر الطريقة المناسبة لك:\n\n"
        "🆓 *مجاناً:* عبر نظام الدعوة\n"
        "💳 *مدفوع:* شراء نقاط بأسعار مميزة\n\n"
        "➖➖➖➖➖➖➖➖➖➖",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ───────── كسب نقاط مجاني ─────────
async def free_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=invite_{user_id}"
    
    message_text = (
        "🎁 *كسب النقاط المجاني*\n\n"
        "▫️ *مميزات نظام الدعوة:*\n"
        "• تحصل على 10 نقاط لكل مدعو\n"
        "• المدعو يحصل على 20 نقطة\n"
        "• لا حدود لعدد الدعوات\n\n"
        "🔗 *رابط الدعوة الخاص بك:*\n\n"
        f"`{invite_link}`\n\n"
        "▫️ *طريقة الاستخدام:*\n"
        "1. انسخ الرابط أعلاه\n"
        "2. أرسله لأصدقائك\n"
        "3. عندما يسجلون\n"
        "4. تحصل أنت وهم على نقاط مجانية\n\n"
        "🎯 *ملاحظة:*\n"
        "اضغط على الرابط ليتم نسخه تلقائياً"
    )
    
    await update.message.reply_text(
        message_text,
        parse_mode='Markdown'
    )

# ───────── كسب نقاط مدفوع ─────────
async def paid_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("⭐ 5 نجوم - 50 نقطة", callback_data="buy_5"),
            InlineKeyboardButton("⭐⭐ 10 نجوم - 120 نقطة", callback_data="buy_10")
        ],
        [
            InlineKeyboardButton("⭐⭐⭐ 20 نجوم - 250 نقطة", callback_data="buy_20"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐ 50 نجوم - اشتراك دائم", callback_data="buy_50")
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_earn")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = (
        "💳 *كسب النقاط عن طريق الدفع*\n\n"
        "🎯 *الباقات المتاحة:*\n\n"
        "⭐ *5 نجوم:*\n"
        "• 50 نقطة\n"
        "• سعر مناسب للمبتدئين\n\n"
        "⭐⭐ *10 نجوم:*\n"
        "• 120 نقطة\n"
        "• أفضل قيمة للثمن\n\n"
        "⭐⭐⭐ *20 نجوم:*\n"
        "• 250 نقطة\n"
        "• خصم 20% للكميات\n\n"
        "⭐⭐⭐⭐⭐ *50 نجوم:*\n"
        "• اشتراك دائم مدى الحياة\n"
        "• إيداع مباشر في الحساب\n"
        "• أولوية في الخدمة\n\n"
        "💰 *طريقة الشراء:*\n"
        "1. اختر الباقة المناسبة\n"
        "2. سيتم إرسال تفاصيل الدفع\n"
        "3. بعد التأكد من الدفع\n"
        "4. تودع النقاط مباشرة في حسابك\n\n"
        f"⚠️ *ملاحظة هامة:*\n"
        f"• باقة 50 نجوم ترسل إلى الحساب الشخصي للمشرف @{ADMIN_USERNAME}"
    )
    
    await update.message.reply_text(
        message_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ───────── خدمة العملاء ─────────
async def support_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_profile_link = f"https://t.me/{user.username}" if user.username else "غير متوفر"
    
    message_text = (
        "📞 *خدمة العملاء والدعم الفني*\n\n"
        f"👤 *اسمك:* {user.first_name}\n"
        f"🔗 *رابط حسابك:* {user_profile_link}\n\n"
        "🎯 *طرق التواصل مع الدعم:*\n\n"
        f"🔹 *المشرف:* @{ADMIN_USERNAME}\n"
        f"🔹 *دعم فني:* @{SUPPORT_USERNAME}\n"
        f"📞 *هاتف:* {PHONE_NUMBER}\n\n"
        "💰 *تفاصيل الحساب البنكي للدفع:*\n"
        f"🏦 *اسم البنك:* {BANK_NAME}\n"
        f"🔢 *رقم الحساب:* {BANK_ACCOUNT}\n"
        f"💳 *IBAN:* `{BANK_IBAN}`\n\n"
        "🕒 *أوقات العمل:*\n"
        "• 24 ساعة / 7 أيام\n\n"
        "📝 *ملاحظات:*\n"
        "• عند التواصل أرسل اسم المستخدم الخاص بك\n"
        "• تأكد من حفظ إيصال الدفع\n"
        "• يتم الرد خلال 5 دقائق كحد أقصى"
    )
    
    await update.message.reply_text(
        message_text,
        parse_mode='Markdown'
    )

# ───────── معالج الأزرار الداخلية ─────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "back_earn":
        await query.edit_message_text(
            "تم العودة لقائمة كسب النقاط",
            parse_mode='Markdown'
        )
        await earn_points_menu(update, context)
    
    elif data.startswith("buy_"):
        packages = {
            "buy_5": {"stars": "5 نجوم", "points": 50, "price": "سعر 5 نجوم"},
            "buy_10": {"stars": "10 نجوم", "points": 120, "price": "سعر 10 نجوم"},
            "buy_20": {"stars": "20 نجوم", "points": 250, "price": "سعر 20 نجوم"},
            "buy_50": {"stars": "50 نجوم", "points": "اشتراك دائم", "price": "سعر 50 نجوم"}
        }
        
        package = packages[data]
        
        payment_text = (
            f"💳 *طلب شراء {package['stars']}*\n\n"
            f"🎯 *المزايا:*\n"
            f"• {package['points']} نقطة\n"
            f"• {package['price']}\n\n"
            "💰 *طريقة الدفع:*\n"
            f"1. ارسل المبلغ إلى الحساب البنكي\n"
            f"2. احفظ إيصال الدفع\n"
            f"3. تواصل مع المشرف @{ADMIN_USERNAME}\n"
            "4. أرسل له الإيصال\n"
            "5. ستضاف النقاط خلال 5 دقائق\n\n"
            f"🏦 *تفاصيل الحساب:*\n"
            f"• اسم البنك: {BANK_NAME}\n"
            f"• رقم الحساب: {BANK_ACCOUNT}\n"
            f"• IBAN: {BANK_IBAN}\n\n"
            f"📞 *للتواصل:*\n"
            f"• @{ADMIN_USERNAME}\n"
            f"• {PHONE_NUMBER}\n\n"
            "⚠️ *تنبيه:*\n"
            "• احتفظ بإيصال الدفع\n"
            "• النقاط تضاف بعد التأكد"
        )
        
        await query.edit_message_text(
            payment_text,
            parse_mode='Markdown'
        )

# ───────── معالج النصوص ─────────
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    # معالجة الأوامر النصية
    if text == "🎯 أرشق الآن":
        await attack_menu(update, context)
    
    elif text == "💰 كسب النقاط":
        await earn_points_menu(update, context)
    
    elif text == "📞 خدمة العملاء":
        await support_service(update, context)
    
    elif text == "🆓 كسب النقاط مجاناً":
        await free_points(update, context)
    
    elif text == "💳 كسب النقاط عن طريق الدفع":
        await paid_points(update, context)
    
    elif text == "🔙 العودة للرئيسية":
        await main_menu(update, context)
    
    elif text == "🔙 رجوع":
        await earn_points_menu(update, context)

# ───────── قائمة الرشق ─────────
async def attack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    points = user_points.get(user_id, 0)
    
    message_text = (
        "🎯 *قائمة الرشق*\n\n"
        f"🏆 *رصيدك الحالي:* {points} نقطة\n\n"
        "▫️ *تعليمات الرشق:*\n"
        "• أدخل الرقم المراد رشقه\n"
        "• اختر نوع الهجوم\n"
        "• اضغط على بدء الرشق\n"
        "• ستخصم النقاط تلقائياً\n\n"
        "⚠️ *تحذير:*\n"
        "الاستخدام الخاطئ قد يؤدي إلى إيقاف الحساب\n\n"
        "👇 *أرسل الرقم الآن:*"
    )
    
    await update.message.reply_text(
        message_text,
        parse_mode='Markdown'
    )

# ───────── تشغيل البوت ─────────
def main():
    # إنشاء تطبيق البوت
    app = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل الآن...")
    print("📊 حالة البوت: نشط")
    print("⚡ الإصدار: 3.0 (بدون صور)")
    print("🔗 رابط البوت: https://t.me/your_bot_username")
    print(f"👤 الدعم: @{SUPPORT_USERNAME}")
    print(f"👑 المشرف: @{ADMIN_USERNAME}")
    
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
