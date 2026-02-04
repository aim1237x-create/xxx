import logging
import sqlite3
import html
import time
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    User
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
PAYMENT_PROVIDER_TOKEN = ""  # توكن الدفع (اختياري للنجوم التلقائية)

# أكواد الدول العربية المسموح بها
ARAB_CODES = [
    "20", "966", "971", "965", "974", "973", "968",
"212", "213", "216", "218", "221", "222", "223",
"224", "225", "226", "227", "228", "229",
"249", "252", "253", "269", "970", "962",
"964", "963", "961", "967"
]

# مراحل المحادثات (Conversation States)
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_CREATE_CODE = range(2)
STATE_REDEEM_CODE = range(2)

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
                joined_date TEXT
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
        self.conn.commit()

    def init_settings(self):
        # القيم الافتراضية
        default_settings = {
            "tax_percent": "25",
            "show_leaderboard": "1"  # 1 = True, 0 = False
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

        self.cursor.execute(
            "INSERT INTO transactions (user_id, amount, type, details, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, tx_type, details, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        self.conn.commit()

    def get_history(self, user_id, limit=5):
        self.cursor.execute(
            "SELECT amount, type, details, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?", 
            (user_id, limit)
        )
        return self.cursor.fetchall()

    def get_top_referrers(self, limit=3):
        # جلب أكثر الأشخاص دعوةً بناءً على عدد مرات تكرارهم في عمود referrer_id
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
                results.append((user, count)) # user tuple contains all info
        return results

    # --- عمليات الأدمن والإعدادات ---
    def get_setting(self, key):
        self.cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_setting(self, key, value):
        self.cursor.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
        self.conn.commit()

    def get_global_stats(self):
        users_count = self.cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_points = self.cursor.execute("SELECT SUM(points) FROM users").fetchone()[0] or 0
        total_tx = self.cursor.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        return users_count, total_points, total_tx

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
        # التحقق من وجود الكود وصلاحيته
        self.cursor.execute("SELECT points, max_uses, current_uses, active FROM promo_codes WHERE code = ?", (code,))
        res = self.cursor.fetchone()
        if not res: return "not_found"
        
        points, max_uses, current_uses, active = res
        
        if not active or current_uses >= max_uses: return "expired"
        
        # التحقق من الاستخدام السابق
        self.cursor.execute("SELECT * FROM code_usage WHERE user_id = ? AND code = ?", (user_id, code))
        if self.cursor.fetchone(): return "used"
        
        # تنفيذ العملية
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات الرئيسية (Handlers)
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
                if inviter != user.id:
                    referrer_id = inviter
            except:
                pass
        
        # تسجيل تلقائي (نضع كلمة None مكان الهاتف)
        db.add_user(user.id, user.username, user.first_name, "None", referrer_id)
        
        if referrer_id:
            db.update_points(referrer_id, 10, "referral", f"دعوة: {user.first_name}")
            try:
                msg = f"🔔 <b>إحالة جديدة!</b>\nحصلت على 10 نقاط لدعوة {user.first_name}"
                await context.bot.send_message(referrer_id, msg, parse_mode="HTML")
            except: pass

    await send_dashboard(update, context)


async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    db_user = db.get_user(user.id)
    points = db_user[4] # index 4 is points
    
    text += (
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
# 🔄 التنقل والقوائم الفرعية (Sub-Menus)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    await query.answer()

    # --- الرجوع للرئيسية ---
    if data == "main_menu":
        await send_dashboard(update, context, edit=True)

    # --- قائمة الرشق (Placeholder) ---
    elif data == "attack_menu":
        user_points = db.get_user(user_id)[4]
        text = (
            f"🎯 <b>قسم الرشق وزيادة التفاعل</b>\n"
            f"💰 رصيدك: <b>{user_points}</b>\n\n"
            "هذا القسم تحت الصيانة حالياً لضمان أعلى جودة.\n"
            "سيتم تفعيله قريباً!"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    # --- قائمة تجميع النقاط ---
    elif data == "collect_points":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="referral_page")],
            [InlineKeyboardButton("📅 المكافأة اليومية", callback_data="daily_bonus")],
            [InlineKeyboardButton("🎫 استبدال كود", callback_data="redeem_code_start")],
            [InlineKeyboardButton("💳 شراء نقاط", callback_data="buy_points_menu")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
        await query.edit_message_text(
            "🔄 <b>قسم تجميع النقاط</b>\nاختر الطريقة الأنسب لك لزيادة رصيدك:",
            reply_markup=kb, parse_mode="HTML"
        )

    # --- صفحة الإحالة ---
    elif data == "referral_page":
        link = f"https://t.me/{context.bot.username}?start=invite_{user_id}"
        
        # لوحة الشرف
        leaderboard_text = ""
        if db.get_setting("show_leaderboard") == "1":
            top_users = db.get_top_referrers()
            if top_users:
                leaderboard_text = "\n\n🏆 <b>أكثر الأعضاء تميزاً:</b>\n"
                for idx, (u_data, count) in enumerate(top_users, 1):
                    name_link = get_user_link(u_data[0], u_data[2]) # u_data[0]=id, u_data[2]=fullname
                    leaderboard_text += f"{idx}. {name_link} ⇦ {count} دعوة\n"

        text = (
            f"🎁 <b>نظام الإحالة والمكافآت</b>\n\n"
            f"شارك الرابط أدناه واربح <b>10 نقاط</b> عن كل صديق!\n\n"
            f"🔗 رابطك:\n<code>{link}</code>\n"
            f"{leaderboard_text}"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    # --- المكافأة اليومية ---
    elif data == "daily_bonus":
        u_data = db.get_user(user_id)
        last_bonus = u_data[6] # index 6
        now = datetime.now()
        
        can_claim = True
        if last_bonus:
            last_date = datetime.fromisoformat(last_bonus)
            if now - last_date < timedelta(hours=24):
                can_claim = False
                remaining = timedelta(hours=24) - (now - last_date)
                hours, remainder = divmod(remaining.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
        
        if can_claim:
            bonus = 5 # قيمة المكافأة
            db.update_points(user_id, bonus, "bonus")
            # تحديث وقت آخر مكافأة
            db.cursor.execute("UPDATE users SET last_daily_bonus = ? WHERE user_id = ?", (now.isoformat(), user_id))
            db.conn.commit()
            
            await query.edit_message_text(
                f"✅ <b>تم استلام المكافأة!</b>\n🎁 حصلت على {bonus} نقاط.\nعد غداً للمزيد.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]]),
                parse_mode="HTML"
            )
        else:
            await query.answer(f"⏳ تبقى {hours} ساعة و {minutes} دقيقة للمكافأة القادمة", show_alert=True)

    # --- شراء النقاط ---
    elif data == "buy_points_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ 5 نجوم (50 نقطة)", callback_data="buy_5"),
             InlineKeyboardButton("⭐ 10 نجوم (120 نقطة)", callback_data="buy_10")],
            [InlineKeyboardButton("⭐ 20 (250 نقطة - يدوي)", callback_data="buy_manual_20")],
            [InlineKeyboardButton("⭐ 50 (مؤبد - يدوي)", callback_data="buy_manual_50")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]
        ])
        await query.edit_message_text(
            "💳 <b>متجر النقاط (Telegram Stars)</b>\n"
            "اختر الباقة المناسبة للدفع:",
            reply_markup=kb, parse_mode="HTML"
        )
    
    # --- التعليمات اليدوية ---
    elif data in ["buy_manual_20", "buy_manual_50"]:
        stars = "20" if "20" in data else "50"
        reward = "250 نقطة" if "20" in data else "اشتراك مدى الحياة"
        text = (
            f"⚠️ <b>شراء يدوي ({stars} نجمة)</b>\n\n"
            f"للحصول على {reward}، اتبع الخطوات بدقة:\n"
            f"1️⃣ اضغط على اسم الحساب: @MO_3MK\n"
            f"2️⃣ أرسل له هدية بقيمة <b>{stars} نجوم</b>.\n"
            f"3️⃣ انسخ الآيدي الخاص بك: <code>{user_id}</code>\n"
            f"4️⃣ أرسل الآيدي + صورة الإيصال للمالك.\n\n"
            "⏳ سيتم الشحن خلال دقائق."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="buy_points_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

    # --- السجل ---
    elif data == "history":
        history = db.get_history(user_id)
        if not history:
            msg = "📭 لا توجد عمليات حديثة."
        else:
            msg = "📜 <b>آخر 5 عمليات:</b>\n\n"
            for amount, type_str, details, time_str in history:
                sign = "+" if amount > 0 else ""
                msg += f"▪️ <b>{type_str}</b> ({sign}{amount})\n   └ <i>{time_str}</i> | {details}\n\n"
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")
    
    # --- الدعم ---
    elif data == "support":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 مراسلة الدعم", url=f"tg://user?id={ADMIN_ID}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
        await query.edit_message_text("📞 <b>مركز الدعم الفني</b>\nاضغط الزر أدناه للتحدث مع المطور.", reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💸 نظام تحويل النقاط (Conversation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tax = db.get_setting("tax_percent")
    await query.edit_message_text(
        f"💸 <b>تحويل النقاط</b>\n\n"
        f"⚠️ <b>ملاحظة هامة:</b> سيتم خصم عمولة تشغيلية قدرها <b>{tax}%</b> من المبلغ المحول.\n\n"
        "👇 أرسل الآن <b>الآيدي (ID)</b> للشخص الذي تريد التحويل له:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_transfer")]])
    )
    return STATE_TRANSFER_ID

async def get_transfer_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ الآيدي يجب أن يكون أرقاماً فقط.")
        return STATE_TRANSFER_ID

    if target_id == update.effective_user.id:
        await update.message.reply_text("❌ لا يمكنك التحويل لنفسك!")
        return STATE_TRANSFER_ID

    target_user = db.get_user(target_id)
    if not target_user:
        await update.message.reply_text("❌ هذا المستخدم غير مسجل في البوت.")
        return STATE_TRANSFER_ID

    context.user_data['transfer_to'] = target_id
    context.user_data['target_name'] = target_user[2] # Full name

    await update.message.reply_text(
        f"✅ تم تحديد المستلم: <b>{target_user[2]}</b>\n"
        "🔢 أرسل الآن المبلغ المراد تحويله:",
        parse_mode="HTML"
    )
    return STATE_TRANSFER_AMOUNT

async def get_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ المبلغ يجب أن يكون رقماً صحيحاً.")
        return STATE_TRANSFER_AMOUNT

    if amount < 10:
        await update.message.reply_text("❌ الحد الأدنى للتحويل هو 10 نقاط.")
        return STATE_TRANSFER_AMOUNT

    user_balance = db.get_user(user_id)[4]
    if amount > user_balance:
        await update.message.reply_text(f"❌ رصيدك غير كافٍ. لديك {user_balance} فقط.")
        return STATE_TRANSFER_AMOUNT

    # الحسابات
    tax_percent = int(db.get_setting("tax_percent"))
    tax_amount = int(amount * (tax_percent / 100))
    final_amount = amount - tax_amount
    target_id = context.user_data['transfer_to']

    # التنفيذ
    db.update_points(user_id, -amount, "transfer_out", f"إلى: {target_id}")
    db.update_points(target_id, final_amount, "transfer_in", f"من: {user_id}")

    # رسالة للمرسل
    await update.message.reply_text(
        f"✅ <b>تم التحويل بنجاح!</b>\n"
        f"📤 المبلغ المخصوم: {amount}\n"
        f"📉 العمولة ({tax_percent}%): {tax_amount}\n"
        f"📥 وصل للمستلم: {final_amount}",
        parse_mode="HTML"
    )
    
    # رسالة للمستلم (إشعار ذكي)
    try:
        sender_link = get_user_link(user_id, update.effective_user.first_name)
        await context.bot.send_message(
            target_id,
            f"💰 <b>حوالة واردة!</b>\nاستلمت <b>{final_amount} نقطة</b> من {sender_link}",
            parse_mode="HTML"
        )
    except:
        pass

    # العودة للداشبورد
    await send_dashboard(update, context)
    return ConversationHandler.END

async def cancel_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تم الإلغاء")
    await send_dashboard(update, context, edit=True)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎫 نظام استبدال الأكواد (Conversation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎫 <b>استبدال الكود</b>\n\nأرسل الكود الخاص بك الآن:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_redeem")]])
    )
    return STATE_REDEEM_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    user_id = update.effective_user.id
    
    result = db.redeem_promo_code(user_id, code)
    
    if result == "not_found":
        await update.message.reply_text("❌ الكود غير صحيح.")
    elif result == "expired":
        await update.message.reply_text("❌ الكود منتهي الصلاحية أو تم استخدامه بالكامل.")
    elif result == "used":
        await update.message.reply_text("❌ لقد استخدمت هذا الكود مسبقاً.")
    else:
        # result is points amount
        await update.message.reply_text(f"🎉 <b>مبارك!</b>\nتم إضافة <b>{result} نقطة</b> لحسابك.", parse_mode="HTML")
        await send_dashboard(update, context)
        return ConversationHandler.END
        
    return STATE_REDEEM_CODE # Allow retry

async def cancel_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_dashboard(update, context, edit=True)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ لوحة تحكم الأدمن (Admin Panel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    u_count, total_pts, total_tx = db.get_global_stats()
    leaderboard_status = "✅ مفعل" if db.get_setting("show_leaderboard") == "1" else "❌ معطل"
    tax = db.get_setting("tax_percent")
    
    text = (
        f"⚙️ <b>لوحة التحكم الخاصة</b>\n\n"
        f"👥 المستخدمين: {u_count}\n"
        f"💰 النقاط الكلية: {total_pts}\n"
        f"📊 العمليات: {total_tx}\n"
        f"🏆 لوحة الشرف: {leaderboard_status}\n"
        f"📉 الضريبة: {tax}%\n"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء كود", callback_data="admin_create_code")],
        [InlineKeyboardButton("🏆 تفعيل/تعطيل الشرف", callback_data="admin_toggle_lb")],
        [InlineKeyboardButton("🔙 خروج", callback_data="main_menu")]
    ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_toggle_lb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = db.get_setting("show_leaderboard")
    new_val = "0" if current == "1" else "1"
    db.set_setting("show_leaderboard", new_val)
    await admin_panel(update, context) # Refresh

async def admin_start_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 <b>إنشاء كود جديد</b>\n\n"
        "أرسل البيانات بالترتيب التالي (كل معلومة في سطر):\n"
        "<code>اسم_الكود\nعدد_النقاط\nعدد_المستخدمين</code>\n\n"
        "مثال:\nEID2024\n100\n50",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_cancel_code")]])
    )
    return STATE_CREATE_CODE

async def admin_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        lines = text.split('\n')
        if len(lines) < 3: raise ValueError
        
        code_name = lines[0].strip()
        points = int(lines[1].strip())
        max_users = int(lines[2].strip())
        
        if db.create_promo_code(code_name, points, max_users):
            await update.message.reply_text(
                f"✅ تم إنشاء الكود بنجاح!\n🎫 الكود: <code>{code_name}</code>", 
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ الكود موجود مسبقاً، اختر اسماً آخر.")
            return STATE_CREATE_CODE
            
    except ValueError:
        await update.message.reply_text("❌ التنسيق خطأ! تأكد من الأسطر والأرقام.")
        return STATE_CREATE_CODE

    await send_dashboard(update, context)
    return ConversationHandler.END

async def admin_cancel_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي (Main Execution)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    application = Application.builder().token(BOT_TOKEN).build()

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

    # Handlers Registration
    application.add_handler(CommandHandler("start", start))
        
    # Register Conversations
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    application.add_handler(create_code_conv)
    
    # Callback Handlers (General & Admin)
    application.add_handler(CallbackQueryHandler(main_callback_handler, pattern="^(main_menu|attack_menu|collect_points|referral_page|daily_bonus|buy_points_menu|buy_manual_.*|history|support)$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_lb, pattern="^admin_toggle_lb$"))

    # Invoice Placeholders (للدفع التلقائي مستقبلاً)
    # application.add_handler(CallbackQueryHandler(buy_stars_handler, pattern="^buy_(5|10)$"))

    print(f"🤖 البوت يعمل بكفاءة عالية... (Admin: {ADMIN_ID})")
    application.run_polling()

if __name__ == "__main__":
    main()
