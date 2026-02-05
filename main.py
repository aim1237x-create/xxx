import logging
import sqlite3
import html
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import json

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    User,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ConversationHandler
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"  # ضع توكن البوت
ADMIN_ID = 8287678319  # ⚠️ ضع الآيدي الخاص بك هنا لتتحكم بالبوت

# مراحل المحادثات (Conversation States)
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_CREATE_CODE = range(2)
STATE_REDEEM_CODE = range(2)
STATE_CHANNEL_ID, STATE_CHANNEL_LINK = range(2, 4)
STATE_BROADCAST_MESSAGE, STATE_BROADCAST_MEDIA = range(4, 6)
STATE_USER_SEARCH, STATE_USER_MANAGE = range(6, 8)
STATE_SETTINGS_MENU = range(8, 9)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات (Database Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseManager:
    def __init__(self, db_name="bot_data.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.init_settings()

    def create_tables(self):
        # جدول المستخدمين
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT,
                points INTEGER DEFAULT 0,
                referrer_id INTEGER,
                last_daily_bonus TEXT,
                joined_date TEXT,
                is_banned INTEGER DEFAULT 0
            )
        ''')
        # جدول العمليات (السجل)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,  -- 'bonus', 'transfer_in', 'transfer_out', 'buy', 'code', 'attack'
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
        # جدول استخدام الأكواد (لمنع الاستخدام المتكرر)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_usage (
                user_id INTEGER,
                code TEXT,
                PRIMARY KEY (user_id, code)
            )
        ''')
        # جدول الإعدادات العامة
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        # جدول القنوات الإجبارية
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS forced_channels (
                channel_id TEXT PRIMARY KEY,
                channel_link TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        # جدول عمليات الدفع بالنجوم
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS star_payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER,
                stars INTEGER,
                points INTEGER,
                timestamp TEXT,
                status TEXT DEFAULT 'completed'
            )
        ''')
        # جدول الإذاعات
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                media_type TEXT,
                sent_to INTEGER,
                failed_to INTEGER,
                pinned INTEGER DEFAULT 0,
                timestamp TEXT
            )
        ''')
        self.conn.commit()

    def init_settings(self):
        # القيم الافتراضية
        default_settings = {
            "tax_percent": "25",
            "show_leaderboard": "1",  # 1 = True, 0 = False
            "maintenance_mode": "0",  # 0 = False, 1 = True
            "daily_bonus_amount": "5",
            "referral_points": "10",
            "min_transfer": "10",
            "welcome_points": "20"
        }
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
            welcome_points = int(self.get_setting("welcome_points") or 20)
            self.cursor.execute(
                "INSERT INTO users (user_id, username, full_name, phone, points, referrer_id, joined_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, full_name, phone, welcome_points, referrer_id, date)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()

    def get_user_by_username(self, username):
        self.cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        return self.cursor.fetchone()

    def update_points(self, user_id, amount, reason, details=""):
        # amount can be positive or negative
        self.cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        
        tx_type = "unknown"
        if reason == "bonus": tx_type = "🎁 مكافأة"
        elif reason == "transfer_in": tx_type = "📥 استلام"
        elif reason == "transfer_out": tx_type = "📤 تحويل"
        elif reason == "buy": tx_type = "💳 شراء"
        elif reason == "code": tx_type = "🎫 كود"
        elif reason == "attack": tx_type = "🎯 رشق"
        elif reason == "referral": tx_type = "👥 إحالة"
        elif reason == "admin_add": tx_type = "👑 إضافة من الأدمن"
        elif reason == "admin_deduct": tx_type = "👑 خصم من الأدمن"

        self.cursor.execute(
            "INSERT INTO transactions (user_id, amount, type, details, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, tx_type, details, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        self.conn.commit()

    def ban_user(self, user_id):
        self.cursor.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def unban_user(self, user_id):
        self.cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def is_banned(self, user_id):
        user = self.get_user(user_id)
        return user and user[8] == 1

    def get_history(self, user_id, limit=5):
        self.cursor.execute(
            "SELECT amount, type, details, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?", 
            (user_id, limit)
        )
        return self.cursor.fetchall()

    def get_top_referrers(self, limit=3):
        self.cursor.execute('''
            SELECT referrer_id, COUNT(*) as count 
            FROM users 
            WHERE referrer_id IS NOT NULL 
            GROUP BY referrer_id 
            ORDER BY count DESC 
            LIMIT ?
        ''', (limit,))
        top_ids = self.cursor.fetchall()
        
        results = []
        for uid, count in top_ids:
            user = self.get_user(uid)
            if user:
                results.append((user, count))
        return results

    # --- قنوات الإشتراك الإجباري ---
    def add_channel(self, channel_id, channel_link):
        try:
            self.cursor.execute(
                "INSERT INTO forced_channels (channel_id, channel_link) VALUES (?, ?)",
                (channel_id, channel_link)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def update_channel(self, channel_id, channel_link):
        self.cursor.execute(
            "UPDATE forced_channels SET channel_link = ? WHERE channel_id = ?",
            (channel_link, channel_id)
        )
        self.conn.commit()

    def toggle_channel(self, channel_id, active):
        self.cursor.execute(
            "UPDATE forced_channels SET is_active = ? WHERE channel_id = ?",
            (1 if active else 0, channel_id)
        )
        self.conn.commit()

    def get_channels(self):
        self.cursor.execute("SELECT channel_id, channel_link, is_active FROM forced_channels")
        return self.cursor.fetchall()

    def delete_channel(self, channel_id):
        self.cursor.execute("DELETE FROM forced_channels WHERE channel_id = ?", (channel_id,))
        self.conn.commit()

    # --- عمليات الأدمن والإعدادات ---
    def get_setting(self, key):
        self.cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_setting(self, key, value):
        self.cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
        self.conn.commit()

    def get_all_users(self):
        self.cursor.execute("SELECT user_id, username, full_name, points FROM users WHERE is_banned = 0")
        return self.cursor.fetchall()

    def get_new_users_stats(self, days=1):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self.cursor.execute(
            "SELECT COUNT(*) FROM users WHERE joined_date > ? AND is_banned = 0",
            (cutoff,)
        )
        return self.cursor.fetchone()[0]

    def get_global_stats(self):
        users_count = self.cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0").fetchone()[0]
        total_points = self.cursor.execute("SELECT SUM(points) FROM users WHERE is_banned = 0").fetchone()[0] or 0
        total_tx = self.cursor.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        
        # النجوم المشتراة
        self.cursor.execute("SELECT SUM(stars) FROM star_payments WHERE status = 'completed'")
        total_stars = self.cursor.fetchone()[0] or 0
        
        # العمليات في آخر 24 ساعة
        cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        self.cursor.execute("SELECT COUNT(*) FROM transactions WHERE timestamp > ?", (cutoff,))
        last_24h_tx = self.cursor.fetchone()[0]
        
        return users_count, total_points, total_tx, total_stars, last_24h_tx

    def get_top_rich_users(self, limit=10):
        self.cursor.execute(
            "SELECT user_id, username, full_name, points FROM users WHERE is_banned = 0 ORDER BY points DESC LIMIT ?",
            (limit,)
        )
        return self.cursor.fetchall()

    # --- نظام الدفع بالنجوم ---
    def add_star_payment(self, payment_id, user_id, stars, points):
        self.cursor.execute(
            "INSERT INTO star_payments (payment_id, user_id, stars, points, timestamp) VALUES (?, ?, ?, ?, ?)",
            (payment_id, user_id, stars, points, datetime.now().isoformat())
        )
        self.conn.commit()

    def get_star_payment(self, payment_id):
        self.cursor.execute("SELECT * FROM star_payments WHERE payment_id = ?", (payment_id,))
        return self.cursor.fetchone()

    # --- نظام الإذاعة ---
    def add_broadcast(self, message, media_type, sent_to, failed_to, pinned=0):
        self.cursor.execute(
            "INSERT INTO broadcasts (message, media_type, sent_to, failed_to, pinned, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            (message, media_type, sent_to, failed_to, pinned, datetime.now().isoformat())
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def create_promo_code(self, code, points, max_uses):
        try:
            self.cursor.execute(
                "INSERT INTO promo_codes (code, points, max_uses) VALUES (?, ?, ?)",
                (code, points, max_uses)
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def redeem_promo_code(self, user_id, code):
        self.cursor.execute("SELECT points, max_uses, current_uses, active FROM promo_codes WHERE code = ?", (code,))
        res = self.cursor.fetchone()
        if not res: return "not_found"
        
        points, max_uses, current_uses, active = res
        
        if not active or current_uses >= max_uses: return "expired"
        
        self.cursor.execute("SELECT * FROM code_usage WHERE user_id = ? AND code = ?", (user_id, code))
        if self.cursor.fetchone(): return "used"
        
        self.cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (code,))
        self.cursor.execute("INSERT INTO code_usage (user_id, code) VALUES (?, ?)", (user_id, code))
        self.update_points(user_id, points, "code", f"Code: {code}")
        self.conn.commit()
        return points

db = DatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ أدوات مساعدة وتنسيق
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_link(user_id, name):
    return f"<a href='tg://user?id={user_id}'>{html.escape(name)}</a>"

def get_main_keyboard(user_id):
    btns = [
        [InlineKeyboardButton("🎯 رشق", callback_data="attack_menu")],
        [InlineKeyboardButton("🔄 تجميع النقاط", callback_data="collect_points")],
        [InlineKeyboardButton("💸 تحويل النقاط", callback_data="transfer_start")],
        [InlineKeyboardButton("📜 سجل العمليات", callback_data="history"), 
         InlineKeyboardButton("📞 الدعم الفني", callback_data="support")]
    ]
    if user_id == ADMIN_ID:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def check_maintenance_mode(user_id):
    if user_id == ADMIN_ID:
        return False
    return db.get_setting("maintenance_mode") == "1"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات الرئيسية (Handlers)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # التحقق من وضع الصيانة
    if check_maintenance_mode(user.id):
        await update.message.reply_text(
            "🔧 البوت قيد الصيانة حاليًا.\n"
            "سيتم فتحه قريبًا بإذن الله.\n"
            "شكرًا لتفهمكم."
        )
        return
    
    args = context.args
    
    db_user = db.get_user(user.id)
    if not db_user:
        referrer_id = None
        if args and args[0].startswith("invite_"):
            try:
                inviter = int(args[0].split("_")[1])
                if inviter != user.id:
                    referrer_id = inviter
            except:
                pass
        
        db.add_user(user.id, user.username, user.first_name, "None", referrer_id)
        
        if referrer_id:
            referral_points = int(db.get_setting("referral_points") or 10)
            db.update_points(referrer_id, referral_points, "referral", f"دعوة: {user.first_name}")
            try:
                msg = f"🔔 <b>إحالة جديدة!</b>\nحصلت على {referral_points} نقاط لدعوة {user.first_name}"
                await context.bot.send_message(referrer_id, msg, parse_mode="HTML")
            except: pass

    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    
    # التحقق من وضع الصيانة
    if check_maintenance_mode(user.id):
        if update.callback_query:
            await update.callback_query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    db_user = db.get_user(user.id)
    points = db_user[4] if db_user else 0
    
    text = (
        f"مرحباً بك {get_user_link(user.id, user.first_name)} 👋\n\n"
        f"🆔 الآيدي الخاص بك: <code>{user.id}</code>\n"
        f"🏆 رصيدك الحالي: <b>{points} نقطة</b>\n"
        f"────────────────\n"
        f"👇 اختر من القائمة أدناه للتحكم:"
    )
    
    kb = get_main_keyboard(user.id)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💫 نظام الدفع التلقائي بالنجوم (Telegram Stars)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def buy_stars_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    await query.answer()
    
    if data == "buy_5":
        stars = 5
        points = 50
        title = "5 نجوم (50 نقطة)"
    elif data == "buy_10":
        stars = 10
        points = 120
        title = "10 نجوم (120 نقطة)"
    else:
        return
    
    # إنشاء فاتورة
    prices = [LabeledPrice(f"{points} نقطة", stars * 100)]  # النجوم بالسنتات
    
    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=f"شراء {points} نقطة مقابل {stars} نجوم",
            payload=f"stars_{stars}_{points}_{user_id}",
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="XTR",  # عملة النجوم
            prices=prices,
            start_parameter="stars_payment",
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False
        )
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ: {str(e)}")
        logger.error(f"Payment error: {e}")

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    
    # التحقق من الصحة
    if not query.invoice_payload.startswith("stars_"):
        await query.answer(ok=False, error_message="فاتورة غير صالحة")
        return
    
    await query.answer(ok=True)

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    
    try:
        # تحليل البايلود: stars_5_50_123456
        parts = payload.split("_")
        if len(parts) < 4:
            return
        
        stars = int(parts[1])
        points = int(parts[2])
        user_id = int(parts[3])
        
        # إضافة النقاط للمستخدم
        db.update_points(user_id, points, "buy", f"شراء بالنجوم: {stars} نجمة")
        
        # تسجيل العملية
        db.add_star_payment(
            payment_id=payment.provider_payment_id,
            user_id=user_id,
            stars=stars,
            points=points
        )
        
        # إشعار الأدمن
        user = update.effective_user
        admin_msg = (
            f"💰 <b>عملية شراء ناجحة!</b>\n\n"
            f"👤 المستخدم: {get_user_link(user.id, user.first_name)}\n"
            f"🆔 الآيدي: <code>{user.id}</code>\n"
            f"⭐ النجوم: {stars}\n"
            f"🎯 النقاط: {points}\n"
            f"💳 المبلغ: {payment.total_amount / 100} نجوم"
        )
        try:
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")
        except:
            pass
        
        # تأكيد للمستخدم
        await update.message.reply_text(
            f"✅ <b>تمت العملية بنجاح!</b>\n\n"
            f"تم إضافة <b>{points} نقطة</b> لحسابك.\n"
            f"شكراً لثقتك!",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Payment processing error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ لوحة تحكم الأدمن المتقدمة (Master Admin Panel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على الإحصائيات
    users_count, total_points, total_tx, total_stars, last_24h_tx = db.get_global_stats()
    new_users_today = db.get_new_users_stats(1)
    new_users_week = db.get_new_users_stats(7)
    
    maintenance_status = "🔴 معطل" if db.get_setting("maintenance_mode") == "0" else "🟢 مفعل"
    
    text = (
        f"⚙️ <b>لوحة التحكم الشاملة</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 👥 المستخدمين: {users_count}\n"
        f"• 📈 مستخدمين اليوم: {new_users_today}\n"
        f"• 📆 مستخدمين الأسبوع: {new_users_week}\n"
        f"• 💰 النقاط الكلية: {total_points}\n"
        f"• ⭐ النجوم المشتراة: {total_stars}\n"
        f"• 📊 العمليات (24س): {last_24h_tx}\n"
        f"• 🔧 وضع الصيانة: {maintenance_status}\n\n"
        f"👇 اختر القسم المطلوب:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels"),
         InlineKeyboardButton("👤 إدارة النقاط", callback_data="admin_points")],
        [InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data="admin_settings")],
        [InlineKeyboardButton("📤 نظام الإذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📈 الإحصائيات المتقدمة", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔧 وضع الصيانة", callback_data="admin_toggle_maintenance")],
        [InlineKeyboardButton("🔙 خروج", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📢 إدارة القنوات (Force Join)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    channels = db.get_channels()
    text = "📢 <b>إدارة القنوات الإجبارية</b>\n\n"
    
    if channels:
        for i, (channel_id, link, active) in enumerate(channels, 1):
            status = "🟢 مفعل" if active else "🔴 معطل"
            text += f"{i}. {link} ({channel_id}) - {status}\n"
    else:
        text += "لا توجد قنوات مضافة.\n"
    
    kb_buttons = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_channel")],
        [InlineKeyboardButton("🔄 تعديل قناة", callback_data="admin_edit_channel")]
    ]
    
    if channels:
        kb_buttons.append([InlineKeyboardButton("🔧 تفعيل/تعطيل", callback_data="admin_toggle_channel")])
        kb_buttons.append([InlineKeyboardButton("🗑️ حذف قناة", callback_data="admin_delete_channel")])
    
    kb_buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 <b>إضافة قناة جديدة</b>\n\n"
        "أرسل الآن <b>آيدي القناة</b> (مثال: @channel_name أو -1001234567890):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_channels")]])
    )
    return STATE_CHANNEL_ID

async def admin_get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_id = update.message.text.strip()
    context.user_data['new_channel_id'] = channel_id
    
    await update.message.reply_text(
        "✅ تم حفظ الآيدي.\n"
        "الآن أرسل <b>رابط القناة</b> (مثال: https://t.me/channel_name):",
        parse_mode="HTML"
    )
    return STATE_CHANNEL_LINK

async def admin_get_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_link = update.message.text.strip()
    channel_id = context.user_data['new_channel_id']
    
    if db.add_channel(channel_id, channel_link):
        await update.message.reply_text(f"✅ تمت إضافة القناة بنجاح!\n🆔: {channel_id}\n🔗: {channel_link}")
    else:
        await update.message.reply_text("❌ القناة موجودة مسبقاً!")
    
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👤 إدارة النقاط (Points Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "👤 <b>إدارة نقاط المستخدمين</b>\n\n"
        "أرسل الآن <b>آيدي المستخدم</b> أو <b>اسم المستخدم</b> (بدون @):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
    )
    return STATE_USER_SEARCH

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_input = update.message.text.strip()
    
    try:
        # محاولة البحث بالآيدي
        user_id = int(search_input)
        user = db.get_user(user_id)
    except ValueError:
        # البحث باسم المستخدم
        if search_input.startswith("@"):
            search_input = search_input[1:]
        user = db.get_user_by_username(search_input)
    
    if not user:
        await update.message.reply_text("❌ المستخدم غير موجود!")
        return STATE_USER_SEARCH
    
    context.user_data['managed_user'] = user[0]  # user_id
    context.user_data['managed_user_name'] = user[2]  # full_name
    
    text = (
        f"✅ <b>تم العثور على المستخدم:</b>\n\n"
        f"👤 الاسم: {user[2]}\n"
        f"🆔 الآيدي: <code>{user[0]}</code>\n"
        f"📛 اليوزر: @{user[1] or 'لا يوجد'}\n"
        f"💰 النقاط: {user[4]}\n"
        f"📅 تاريخ التسجيل: {user[7][:10]}\n"
        f"🚫 الحالة: {'محظور' if user[8] == 1 else 'نشط'}"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_add_points"),
         InlineKeyboardButton("➖ خصم نقاط", callback_data="admin_deduct_points")],
        [InlineKeyboardButton("🚫 حظر", callback_data="admin_ban_user"),
         InlineKeyboardButton("✅ فك الحظر", callback_data="admin_unban_user")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    return STATE_USER_MANAGE

async def admin_add_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['action'] = 'add'
    await query.edit_message_text(
        "💰 <b>إضافة نقاط</b>\n\n"
        "أرسل عدد النقاط التي تريد إضافتها:",
        parse_mode="HTML"
    )
    return STATE_USER_MANAGE

async def admin_deduct_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    context.user_data['action'] = 'deduct'
    await query.edit_message_text(
        "💰 <b>خصم نقاط</b>\n\n"
        "أرسل عدد النقاط التي تريد خصمها:",
        parse_mode="HTML"
    )
    return STATE_USER_MANAGE

async def admin_process_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ الرقم غير صالح!")
        return STATE_USER_MANAGE
    
    user_id = context.user_data['managed_user']
    user_name = context.user_data['managed_user_name']
    action = context.user_data.get('action')
    
    if action == 'add':
        db.update_points(user_id, amount, "admin_add", f"إضافة من الأدمن")
        message = f"✅ تم إضافة {amount} نقطة للمستخدم {user_name}"
    elif action == 'deduct':
        db.update_points(user_id, -amount, "admin_deduct", f"خصم من الأدمن")
        message = f"✅ تم خصم {amount} نقطة من المستخدم {user_name}"
    else:
        await update.message.reply_text("❌ حدث خطأ!")
        return
    
    await update.message.reply_text(message)
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = context.user_data['managed_user']
    user_name = context.user_data['managed_user_name']
    
    db.ban_user(user_id)
    
    await query.edit_message_text(f"✅ تم حظر المستخدم {user_name}")
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = context.user_data['managed_user']
    user_name = context.user_data['managed_user_name']
    
    db.unban_user(user_id)
    
    await query.edit_message_text(f"✅ تم فك حظر المستخدم {user_name}")
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ تعديل الإعدادات (Global Settings)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tax = db.get_setting("tax_percent")
    daily_bonus = db.get_setting("daily_bonus_amount")
    referral_points = db.get_setting("referral_points")
    min_transfer = db.get_setting("min_transfer")
    welcome_points = db.get_setting("welcome_points")
    
    text = (
        f"⚙️ <b>الإعدادات العامة</b>\n\n"
        f"📊 <b>الإعدادات الحالية:</b>\n"
        f"• 📉 نسبة الضريبة: {tax}%\n"
        f"• 🎁 المكافأة اليومية: {daily_bonus} نقطة\n"
        f"• 👥 نقاط الإحالة: {referral_points} نقطة\n"
        f"• 💸 الحد الأدنى للتحويل: {min_transfer} نقطة\n"
        f"• 👋 نقاط الترحيب: {welcome_points} نقطة\n\n"
        f"اختر الإعداد الذي تريد تعديله:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📉 نسبة الضريبة", callback_data="admin_set_tax"),
         InlineKeyboardButton("🎁 المكافأة اليومية", callback_data="admin_set_daily")],
        [InlineKeyboardButton("👥 نقاط الإحالة", callback_data="admin_set_referral"),
         InlineKeyboardButton("💸 حد التحويل", callback_data="admin_set_min")],
        [InlineKeyboardButton("👋 نقاط الترحيب", callback_data="admin_set_welcome")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    return STATE_SETTINGS_MENU

async def admin_change_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    
    setting_map = {
        "admin_set_tax": ("tax_percent", "📉 نسبة الضريبة", "نسبة مئوية"),
        "admin_set_daily": ("daily_bonus_amount", "🎁 المكافأة اليومية", "نقاط"),
        "admin_set_referral": ("referral_points", "👥 نقاط الإحالة", "نقاط"),
        "admin_set_min": ("min_transfer", "💸 الحد الأدنى للتحويل", "نقاط"),
        "admin_set_welcome": ("welcome_points", "👋 نقاط الترحيب", "نقاط")
    }
    
    if data not in setting_map:
        return
    
    key, name, unit = setting_map[data]
    context.user_data['setting_to_change'] = key
    
    await query.edit_message_text(
        f"⚙️ <b>تعديل {name}</b>\n\n"
        f"أرسل القيمة الجديدة ({unit}):",
        parse_mode="HTML"
    )
    return STATE_SETTINGS_MENU

async def admin_save_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = int(update.message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ القيمة غير صالحة! يجب أن تكون رقماً موجباً.")
        return STATE_SETTINGS_MENU
    
    key = context.user_data['setting_to_change']
    db.set_setting(key, str(value))
    
    await update.message.reply_text(f"✅ تم تحديث الإعداد بنجاح!")
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 نظام الإذاعة المتطور (Advanced Broadcast)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📤 <b>نظام الإذاعة المتطور</b>\n\n"
        "يمكنك إرسال رسالة لجميع المستخدمين مع خيارات متقدمة:\n"
        "1. 📝 نص فقط\n"
        "2. 🖼️ صورة مع نص\n"
        "3. 🎬 فيديو مع نص\n"
        "4. 📁 ملف مع نص\n\n"
        "مع إمكانية تثبيت الرسالة عند المستخدمين!"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إذاعة نصية", callback_data="broadcast_text")],
        [InlineKeyboardButton("🖼️ إذاعة بالصورة", callback_data="broadcast_photo")],
        [InlineKeyboardButton("🎬 إذاعة بالفيديو", callback_data="broadcast_video")],
        [InlineKeyboardButton("📁 إذاعة بملف", callback_data="broadcast_document")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    
    media_type = data.replace("broadcast_", "")
    context.user_data['broadcast_media'] = media_type
    
    await query.edit_message_text(
        "📝 <b>إرسال الرسالة</b>\n\n"
        "أرسل الآن نص الرسالة:\n"
        "(يمكنك استخدام HTML للتنسيق)",
        parse_mode="HTML"
    )
    return STATE_BROADCAST_MESSAGE

async def admin_get_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    context.user_data['broadcast_message'] = message
    
    media_type = context.user_data['broadcast_media']
    
    if media_type == "text":
        # مباشرة للإرسال
        await admin_send_broadcast(update, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            f"✅ تم حفظ النص.\n"
            f"الآن أرسل الـ{media_type}:\n"
            f"(الصورة / الفيديو / الملف)"
        )
        return STATE_BROADCAST_MEDIA

async def admin_get_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    media_type = context.user_data['broadcast_media']
    
    if media_type == "photo" and update.message.photo:
        context.user_data['broadcast_file_id'] = update.message.photo[-1].file_id
    elif media_type == "video" and update.message.video:
        context.user_data['broadcast_file_id'] = update.message.video.file_id
    elif media_type == "document" and update.message.document:
        context.user_data['broadcast_file_id'] = update.message.document.file_id
    else:
        await update.message.reply_text("❌ نوع الملف غير صحيح!")
        return STATE_BROADCAST_MEDIA
    
    # سؤال عن التثبيت
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، ثبت الرسالة", callback_data="broadcast_pin_yes"),
         InlineKeyboardButton("❌ لا، لا تثبت", callback_data="broadcast_pin_no")]
    ])
    
    await update.message.reply_text(
        "📌 هل تريد تثبيت الرسالة عند المستخدمين؟",
        reply_markup=kb
    )
    return ConversationHandler.END

async def admin_send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    
    message = context.user_data.get('broadcast_message', '')
    media_type = context.user_data.get('broadcast_media', 'text')
    file_id = context.user_data.get('broadcast_file_id')
    
    # تحديد إذا كان تثبيت
    pin_message = False
    if query and query.data == "broadcast_pin_yes":
        pin_message = True
    
    # الحصول على جميع المستخدمين
    all_users = db.get_all_users()
    total_users = len(all_users)
    
    if total_users == 0:
        if query:
            await query.edit_message_text("❌ لا يوجد مستخدمين لإرسال الرسالة لهم!")
        else:
            await update.message.reply_text("❌ لا يوجد مستخدمين لإرسال الرسالة لهم!")
        return ConversationHandler.END
    
    # إعداد الرسالة التقدمية
    if query:
        progress_msg = await query.edit_message_text("⏳ جاري إرسال الرسالة...\n0% (0/{})".format(total_users))
    else:
        progress_msg = await update.message.reply_text("⏳ جاري إرسال الرسالة...\n0% (0/{})".format(total_users))
    
    sent_count = 0
    failed_count = 0
    failed_users = []
    
    # إرسال الرسالة لكل مستخدم
    for i, (user_id, username, full_name, points) in enumerate(all_users, 1):
        try:
            if media_type == "text":
                msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    await context.bot.pin_chat_message(chat_id=user_id, message_id=msg.message_id)
                    
            elif media_type == "photo":
                msg = await context.bot.send_photo(
                    chat_id=user_id,
                    photo=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    await context.bot.pin_chat_message(chat_id=user_id, message_id=msg.message_id)
                    
            elif media_type == "video":
                msg = await context.bot.send_video(
                    chat_id=user_id,
                    video=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    await context.bot.pin_chat_message(chat_id=user_id, message_id=msg.message_id)
                    
            elif media_type == "document":
                msg = await context.bot.send_document(
                    chat_id=user_id,
                    document=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    await context.bot.pin_chat_message(chat_id=user_id, message_id=msg.message_id)
            
            sent_count += 1
            
        except Exception as e:
            failed_count += 1
            failed_users.append(f"{full_name} ({user_id}) - {str(e)}")
        
        # تحديث الرسالة التقدمية كل 10 مستخدمين
        if i % 10 == 0 or i == total_users:
            progress = int((i / total_users) * 100)
            await progress_msg.edit_text(
                f"⏳ جاري إرسال الرسالة...\n"
                f"{progress}% ({i}/{total_users})\n"
                f"✅ تم إرسال: {sent_count}\n"
                f"❌ فشل: {failed_count}"
            )
    
    # تسجيل الإذاعة في قاعدة البيانات
    db.add_broadcast(
        message=message[:100],  # تخزين أول 100 حرف فقط
        media_type=media_type,
        sent_to=sent_count,
        failed_to=failed_count,
        pinned=1 if pin_message else 0
    )
    
    # عرض النتائج النهائية
    result_text = (
        f"📊 <b>نتائج الإذاعة:</b>\n\n"
        f"✅ تم الإرسال بنجاح: {sent_count}\n"
        f"❌ فشل في الإرسال: {failed_count}\n"
        f"📌 تم التثبيت: {'نعم' if pin_message else 'لا'}\n\n"
    )
    
    if failed_users and failed_count <= 10:  # عرض فقط إذا كانوا قليلين
        result_text += "<b>المستخدمين الذين فشل الإرسال لهم:</b>\n"
        for user_info in failed_users[:10]:
            result_text += f"• {user_info}\n"
    
    await progress_msg.edit_text(result_text, parse_mode="HTML")
    
    # تنظيف البيانات المؤقتة
    context.user_data.pop('broadcast_message', None)
    context.user_data.pop('broadcast_media', None)
    context.user_data.pop('broadcast_file_id', None)
    
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📈 الإحصائيات المتقدمة (Deep Analytics)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_analytics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # الحصول على الإحصائيات
    users_count, total_points, total_tx, total_stars, last_24h_tx = db.get_global_stats()
    new_users_today = db.get_new_users_stats(1)
    new_users_week = db.get_new_users_stats(7)
    
    # أكثر 10 مستخدمين غنى
    rich_users = db.get_top_rich_users(10)
    
    # الحصول على أعلى المشيرين
    top_referrers = db.get_top_referrers(5)
    
    text = (
        f"📈 <b>الإحصائيات المتقدمة</b>\n\n"
        f"📊 <b>النظرة العامة:</b>\n"
        f"• 👥 إجمالي المستخدمين: {users_count}\n"
        f"• 📈 مستخدمين اليوم: {new_users_today}\n"
        f"• 📆 مستخدمين الأسبوع: {new_users_week}\n"
        f"• 💰 النقاط الكلية: {total_points}\n"
        f"• ⭐ النجوم المشتراة: {total_stars}\n"
        f"• 📊 العمليات (24س): {last_24h_tx}\n\n"
    )
    
    # عرض الأغنياء
    if rich_users:
        text += f"🏆 <b>أكثر 10 مستخدمين ثراءً:</b>\n"
        for i, (user_id, username, full_name, points) in enumerate(rich_users, 1):
            name_display = full_name or username or f"User {user_id}"
            text += f"{i}. {name_display[:20]} - {points:,} نقطة\n"
        text += "\n"
    
    # عرض أفضل المشيرين
    if top_referrers:
        text += f"👥 <b>أفضل 5 مشيرين:</b>\n"
        for i, (user_data, count) in enumerate(top_referrers, 1):
            name_display = user_data[2] or user_data[1] or f"User {user_data[0]}"
            text += f"{i}. {name_display[:20]} - {count} إحالة\n"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="admin_analytics")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 وضع الصيانة (Maintenance Mode)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    current = db.get_setting("maintenance_mode")
    new_val = "0" if current == "1" else "1"
    db.set_setting("maintenance_mode", new_val)
    
    status = "مفعل" if new_val == "1" else "معطل"
    await query.edit_message_text(f"✅ تم {status} وضع الصيانة.")
    
    # إذا تم تفعيل وضع الصيانة، إرسال إشعار لجميع المستخدمين النشطين
    if new_val == "1":
        all_users = db.get_all_users()
        for user_id, _, full_name, _ in all_users:
            try:
                await context.bot.send_message(
                    user_id,
                    "🔧 <b>إشعار هام</b>\n\n"
                    "البوت سيدخل في وضع الصيانة لفترة قصيرة.\n"
                    "سيعود للعمل قريبًا بإذن الله.\n"
                    "شكرًا لتفهمكم.",
                    parse_mode="HTML"
                )
                time.sleep(0.1)  # لتجنب حظر التلغرام
            except:
                continue
    
    await admin_panel(update, context)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي (Main Execution)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # التحقق من وضع الصيانة قبل أي أمر
    async def maintenance_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if check_maintenance_mode(user_id) and user_id != ADMIN_ID:
            if update.message:
                await update.message.reply_text(
                    "🔧 البوت قيد الصيانة حاليًا.\n"
                    "سيتم فتحه قريبًا بإذن الله.\n"
                    "شكرًا لتفهمكم."
                )
            elif update.callback_query:
                await update.callback_query.answer("البوت قيد الصيانة حالياً", show_alert=True)
            return True
        return False
    
    # إضافة middleware للتحقق من الصيانة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, maintenance_check), group=-1)

    # Conversation: Transfer Points
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)],
        },
        fallbacks=[CallbackQueryHandler(cancel_transfer, pattern="^cancel_transfer$")]
    )

    # Conversation: Redeem Code
    redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")],
        states={
            STATE_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)]
        },
        fallbacks=[CallbackQueryHandler(cancel_redeem, pattern="^cancel_redeem$")]
    )

    # Conversation: Create Code (Admin)
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_start_create_code, pattern="^admin_create_code$")],
        states={
            STATE_CREATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_code)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel_code, pattern="^admin_cancel_code$")]
    )

    # Conversation: إدارة القنوات
    channels_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_channel_start, pattern="^admin_add_channel$")],
        states={
            STATE_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_id)],
            STATE_CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_link)]
        },
        fallbacks=[CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$")]
    )

    # Conversation: إدارة النقاط
    points_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_points_menu, pattern="^admin_points$")],
        states={
            STATE_USER_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_user)],
            STATE_USER_MANAGE: [
                CallbackQueryHandler(admin_add_points_callback, pattern="^admin_add_points$"),
                CallbackQueryHandler(admin_deduct_points_callback, pattern="^admin_deduct_points$"),
                CallbackQueryHandler(admin_ban_user_callback, pattern="^admin_ban_user$"),
                CallbackQueryHandler(admin_unban_user_callback, pattern="^admin_unban_user$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_points)
            ]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )

    # Conversation: تعديل الإعدادات
    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_settings_menu, pattern="^admin_settings$")],
        states={
            STATE_SETTINGS_MENU: [
                CallbackQueryHandler(admin_change_setting, pattern="^admin_set_(tax|daily|referral|min|welcome)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_setting)
            ]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )

    # Conversation: الإذاعة المتطورة
    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_start_broadcast, pattern="^broadcast_(text|photo|video|document)$")
        ],
        states={
            STATE_BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_broadcast_message)],
            STATE_BROADCAST_MEDIA: [
                MessageHandler(filters.PHOTO, admin_get_broadcast_media),
                MessageHandler(filters.VIDEO, admin_get_broadcast_media),
                MessageHandler(filters.Document.ALL, admin_get_broadcast_media)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(admin_send_broadcast, pattern="^broadcast_pin_(yes|no)$"),
            CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast$")
        ]
    )

    # Handlers Registration
    application.add_handler(CommandHandler("start", start))
    
    # نظام الدفع بالنجوم
    if PAYMENT_PROVIDER_TOKEN:
        application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    
    # Register Conversations
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    application.add_handler(create_code_conv)
    application.add_handler(channels_conv)
    application.add_handler(points_conv)
    application.add_handler(settings_conv)
    application.add_handler(broadcast_conv)
    
    # Callback Handlers (General & Admin)
    application.add_handler(CallbackQueryHandler(main_callback_handler, pattern="^(main_menu|attack_menu|collect_points|referral_page|daily_bonus|buy_points_menu|buy_manual_.*|history|support)$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_lb, pattern="^admin_toggle_lb$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_maintenance, pattern="^admin_toggle_maintenance$"))
    application.add_handler(CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_analytics_menu, pattern="^admin_analytics$"))
    
    # معالجات الدفع بالنجوم (إذا كان توكن الدفع متوفراً)
    if PAYMENT_PROVIDER_TOKEN:
        application.add_handler(CallbackQueryHandler(buy_stars_handler, pattern="^buy_(5|10)$"))

    print(f"🤖 البوت يعمل بكفاءة عالية... (Admin: {ADMIN_ID})")
    print(f"🔧 وضع الصيانة: {'مفعل' if db.get_setting('maintenance_mode') == '1' else 'معطل'}")
    print(f"⭐ نظام الدفع: {'مفعل' if PAYMENT_PROVIDER_TOKEN else 'معطل'}")
    
    application.run_polling()

if __name__ == "__main__":
    main()