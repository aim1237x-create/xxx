import logging
import sqlite3
import html
import time
import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any, Union
import aiosqlite
from concurrent.futures import ThreadPoolExecutor
import threading
from collections import defaultdict

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
    ConversationHandler,
    CallbackContext
)
from telegram.error import Forbidden, BadRequest, TimedOut, NetworkError

# إعدادات البوت
BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"
ADMIN_ID = 8287678319
PAYMENT_PROVIDER_TOKEN = ""

# مراحل المحادثات
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_REDEEM_CODE = 2
STATE_CREATE_CODE = 3
STATE_CHANNEL_ID, STATE_CHANNEL_LINK = range(4, 6)
STATE_BROADCAST_MESSAGE, STATE_BROADCAST_MEDIA = range(6, 8)
STATE_USER_SEARCH, STATE_USER_MANAGE = range(8, 10)
STATE_SETTINGS_MENU = 10
STATE_SUPPORT_TICKET = 14
STATE_CODE_EXPIRY = 16
STATE_POINTS_AMOUNT = 17
STATE_CONFIRM_ACTION = 18
STATE_ADD_POINTS, STATE_DEDUCT_POINTS = range(19, 21)

# إعدادات النظام
CHECK_CHANNELS_INTERVAL = 300
BROADCAST_DELAY_MIN = 0.1
CACHE_TTL = 120
RATE_LIMIT_WINDOW = 1
MAX_REQUESTS_PER_WINDOW = 5
DATABASE_CONNECTION_TIMEOUT = 30

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# نظام قاعدة البيانات
class AsyncDatabaseManager:
    def __init__(self, db_name="bot_data.db"):
        self.db_name = db_name
        self.cache = {}
        self.cache_timestamps = {}
        self.rate_limit_data = defaultdict(list)
        
    def init_database_sync(self):
        """تهيئة قاعدة البيانات"""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False, timeout=30)
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            
            # إنشاء الجداول الأساسية
            tables = [
                '''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    points INTEGER DEFAULT 0,
                    referrer_id INTEGER,
                    last_daily_bonus TEXT,
                    joined_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    is_banned INTEGER DEFAULT 0,
                    last_active TEXT,
                    total_earned INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    warnings INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )''',
                
                '''CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    amount INTEGER,
                    type TEXT,
                    details TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    related_user_id INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )''',
                
                '''CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    points INTEGER,
                    max_uses INTEGER,
                    current_uses INTEGER DEFAULT 0,
                    active INTEGER DEFAULT 1,
                    created_by INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT,
                    description TEXT
                )''',
                
                '''CREATE TABLE IF NOT EXISTS forced_channels (
                    channel_id TEXT PRIMARY KEY,
                    channel_link TEXT,
                    is_active INTEGER DEFAULT 1,
                    added_by INTEGER,
                    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    channel_name TEXT,
                    bot_is_admin INTEGER DEFAULT 0
                )''',
                
                '''CREATE TABLE IF NOT EXISTS star_payments (
                    payment_id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    stars INTEGER,
                    points INTEGER,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'completed',
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )''',
                
                '''CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT,
                    media_type TEXT,
                    media_file_id TEXT,
                    sent_to INTEGER DEFAULT 0,
                    failed_to INTEGER DEFAULT 0,
                    total_users INTEGER DEFAULT 0,
                    sent_by INTEGER,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    completed INTEGER DEFAULT 0
                )''',
                
                '''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    description TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )''',
                
                '''CREATE TABLE IF NOT EXISTS support_tickets (
                    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    subject TEXT,
                    message TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    admin_reply TEXT,
                    replied_by INTEGER,
                    replied_at TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )'''
            ]
            
            for table_sql in tables:
                cursor.execute(table_sql)
            
            # إنشاء indices
            indices = [
                "CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)",
                "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_star_payments_user ON star_payments(user_id)"
            ]
            
            for index_sql in indices:
                cursor.execute(index_sql)
            
            # إعدادات افتراضية
            default_settings = [
                ("welcome_points", "20", "نقاط الترحيب"),
                ("referral_points", "10", "نقاط الإحالة"),
                ("min_transfer", "10", "الحد الأدنى للتحويل"),
                ("daily_bonus_amount", "5", "قيمة المكافأة اليومية"),
                ("maintenance_mode", "0", "وضع الصيانة"),
                ("force_channel_subscription", "1", "إجبار الاشتراك في القنوات"),
                ("points_per_star", "10", "النقاط مقابل كل نجمة"),
                ("broadcast_delay", "0.1", "التأخير بين الإرسالات")
            ]
            
            for key, val, desc in default_settings:
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
                    (key, val, desc)
                )
            
            conn.commit()
            conn.close()
            logger.info("✅ قاعدة البيانات مهيأة بنجاح")
        except Exception as e:
            logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
            raise
    
    async def get_connection(self):
        """الحصول على اتصال قاعدة البيانات"""
        try:
            conn = await aiosqlite.connect(self.db_name, timeout=DATABASE_CONNECTION_TIMEOUT)
            await conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = aiosqlite.Row
            return conn
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء اتصال قاعدة البيانات: {e}")
            raise
    
    async def execute_query(self, query: str, params: tuple = (), commit: bool = False):
        """تنفيذ استعلام"""
        try:
            conn = await self.get_connection()
            async with conn:
                async with conn.execute(query, params) as cursor:
                    result = await cursor.fetchall()
                    if commit:
                        await conn.commit()
                    return result
        except Exception as e:
            logger.error(f"❌ خطأ في قاعدة البيانات: {e}")
            raise
    
    async def execute_query_one(self, query: str, params: tuple = (), commit: bool = False):
        """تنفيذ استعلام وإرجاع صف واحد"""
        try:
            conn = await self.get_connection()
            async with conn:
                async with conn.execute(query, params) as cursor:
                    result = await cursor.fetchone()
                    if commit:
                        await conn.commit()
                    return result
        except Exception as e:
            logger.error(f"❌ خطأ في قاعدة البيانات: {e}")
            raise
    
    async def execute_update(self, query: str, params: tuple = ()):
        """تنفيذ استعلام تحديث"""
        try:
            conn = await self.get_connection()
            async with conn:
                async with conn.execute(query, params) as cursor:
                    await conn.commit()
                    return cursor.rowcount
        except Exception as e:
            logger.error(f"❌ خطأ في قاعدة البيانات: {e}")
            raise
    
    # عمليات المستخدم
    async def add_user(self, user_id: int, username: str, full_name: str, referrer_id: int = None) -> bool:
        """إضافة مستخدم جديد"""
        try:
            welcome_points = int(await self.get_setting("welcome_points") or 20)
            date = datetime.now().isoformat()
            
            await self.execute_update(
                """INSERT INTO users 
                (user_id, username, full_name, points, referrer_id, joined_date, last_active) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, full_name, welcome_points, referrer_id, date, date),
                commit=True
            )
            
            await self.execute_update(
                """INSERT INTO transactions 
                (user_id, amount, type, details) 
                VALUES (?, ?, ?, ?)""",
                (user_id, welcome_points, "🎁 مكافأة", "نقاط ترحيب"),
                commit=True
            )
            
            if referrer_id:
                referral_points = int(await self.get_setting("referral_points") or 10)
                await self.execute_update(
                    "UPDATE users SET points = points + ? WHERE user_id = ?",
                    (referral_points, referrer_id),
                    commit=True
                )
                
                await self.execute_update(
                    """INSERT INTO transactions 
                    (user_id, amount, type, details, related_user_id) 
                    VALUES (?, ?, ?, ?, ?)""",
                    (referrer_id, referral_points, "👥 إحالة", f"دعوة: {full_name}", user_id),
                    commit=True
                )
            
            logger.info(f"✅ تم إضافة مستخدم جديد: {user_id} - {full_name}")
            return True
                
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المستخدم {user_id}: {e}")
            return False
    
    async def get_user(self, user_id: int):
        """الحصول على بيانات مستخدم"""
        try:
            result = await self.execute_query_one(
                """SELECT user_id, username, full_name, points, referrer_id, 
                is_banned, last_active, total_earned, total_spent, warnings
                FROM users WHERE user_id = ?""",
                (user_id,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على بيانات المستخدم {user_id}: {e}")
            return None
    
    async def update_points(self, user_id: int, amount: int, reason: str, details: str = "", related_user_id: int = None):
        """تحديث نقاط المستخدم"""
        try:
            await self.execute_update(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (amount, user_id),
                commit=True
            )
            
            if amount > 0:
                await self.execute_update(
                    "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                    (amount, user_id),
                    commit=True
                )
            else:
                await self.execute_update(
                    "UPDATE users SET total_spent = total_spent + ABS(?) WHERE user_id = ?",
                    (amount, user_id),
                    commit=True
                )
            
            await self.execute_update(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id),
                commit=True
            )
            
            tx_type_map = {
                "bonus": "🎁 مكافأة",
                "transfer_in": "📥 استلام",
                "transfer_out": "📤 تحويل",
                "buy": "💳 شراء",
                "code": "🎫 كود",
                "referral": "👥 إحالة",
                "admin_add": "👑 إضافة من الأدمن",
                "admin_deduct": "👑 خصم من الأدمن"
            }
            
            tx_type = tx_type_map.get(reason, "❓ غير معروف")
            
            await self.execute_update(
                """INSERT INTO transactions 
                (user_id, amount, type, details, related_user_id) 
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, amount, tx_type, details, related_user_id),
                commit=True
            )
            
            logger.info(f"✅ تم تحديث نقاط المستخدم {user_id}: {amount:+d} ({reason})")
                
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث نقاط المستخدم {user_id}: {e}")
            raise
    
    async def ban_user(self, user_id: int, reason: str = "", banned_by: int = None):
        """حظر مستخدم"""
        try:
            await self.execute_update(
                "UPDATE users SET is_banned = 1, is_active = 0 WHERE user_id = ?",
                (user_id,),
                commit=True
            )
            logger.info(f"✅ تم حظر المستخدم {user_id} - السبب: {reason}")
        except Exception as e:
            logger.error(f"❌ خطأ في حظر المستخدم {user_id}: {e}")
    
    async def unban_user(self, user_id: int, unbanned_by: int = None):
        """فك حظر مستخدم"""
        try:
            await self.execute_update(
                "UPDATE users SET is_banned = 0, is_active = 1 WHERE user_id = ?",
                (user_id,),
                commit=True
            )
            logger.info(f"✅ تم فك حظر المستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في فك حظر المستخدم {user_id}: {e}")
    
    async def is_banned(self, user_id: int) -> bool:
        """التحقق إذا كان المستخدم محظوراً"""
        try:
            user = await self.get_user(user_id)
            return user and user['is_banned'] == 1
        except Exception as e:
            logger.error(f"خطأ في التحقق من حظر المستخدم {user_id}: {e}")
            return False
    
    # نظام القنوات
    async def check_channel_subscription(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
        """التحقق من اشتراك المستخدم في القنوات الإجبارية"""
        try:
            force_subscription = await self.get_setting("force_channel_subscription")
            if not force_subscription or force_subscription != "1":
                return (True, "")
            
            channels = await self.get_channels(active_only=True)
            if not channels:
                return (True, "")
            
            unsubscribed_channels = []
            for channel in channels:
                channel_id = channel['channel_id']
                try:
                    chat_member = await context.bot.get_chat_member(channel_id, user_id)
                    if chat_member.status in ['left', 'kicked']:
                        channel_link = channel['channel_link']
                        channel_name = channel['channel_name'] or "القناة"
                        unsubscribed_channels.append(f"• {channel_name}: {channel_link}")
                except Exception:
                    continue
            
            if unsubscribed_channels:
                message = (
                    "⚠️ <b>يجب الاشتراك في القنوات التالية أولاً:</b>\n\n"
                    + "\n".join(unsubscribed_channels) +
                    "\n\n✅ بعد الاشتراك، أرسل /start"
                )
                return (False, message)
            else:
                return (True, "")
            
        except Exception as e:
            logger.error(f"خطأ في التحقق من اشتراك القنوات للمستخدم {user_id}: {e}")
            return (True, "")
    
    async def add_channel(self, channel_id: str, channel_link: str, added_by: int, channel_name: str = "") -> bool:
        """إضافة قناة جديدة"""
        try:
            await self.execute_update(
                """INSERT OR REPLACE INTO forced_channels 
                (channel_id, channel_link, added_by, added_at, channel_name) 
                VALUES (?, ?, ?, ?, ?)""",
                (channel_id, channel_link, added_by, datetime.now().isoformat(), channel_name),
                commit=True
            )
            logger.info(f"✅ تم إضافة قناة: {channel_id} - {channel_name}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة القناة {channel_id}: {e}")
            return False
    
    async def get_channels(self, active_only: bool = False):
        """الحصول على جميع القنوات"""
        try:
            query = "SELECT channel_id, channel_link, is_active, channel_name FROM forced_channels"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY added_at DESC"
            
            result = await self.execute_query(query)
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على القنوات: {e}")
            return []
    
    # نظام الدفع بالنجوم
    async def add_star_payment(self, payment_id: str, user_id: int, stars: int, points: int, status: str = "completed") -> bool:
        """إضافة عملية دفع بالنجوم"""
        try:
            await self.execute_update(
                """INSERT INTO star_payments 
                (payment_id, user_id, stars, points, timestamp, status) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (payment_id, user_id, stars, points, datetime.now().isoformat(), status),
                commit=True
            )
            
            logger.info(f"✅ تم تسجيل عملية دفع: {payment_id} - {stars} نجوم -> {points} نقطة")
            return True
            
        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل عملية الدفع {payment_id}: {e}")
            return False
    
    # نظام الأكواد
    async def create_promo_code(self, code: str, points: int, max_uses: int, created_by: int, 
                               expires_days: int = 30, description: str = "") -> bool:
        """إنشاء كود جديد"""
        try:
            expires_at = None
            if expires_days > 0:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
            
            await self.execute_update(
                """INSERT INTO promo_codes 
                (code, points, max_uses, created_by, expires_at, description) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (code, points, max_uses, created_by, expires_at, description),
                commit=True
            )
            logger.info(f"✅ تم إنشاء كود: {code} - {points} نقطة")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء الكود {code}: {e}")
            return False
    
    async def redeem_promo_code(self, user_id: int, code: str) -> Union[int, str]:
        """استبدال كود"""
        try:
            # التحقق من وجود الكود
            res = await self.execute_query_one(
                """SELECT points, max_uses, current_uses, active, expires_at 
                FROM promo_codes WHERE code = ?""",
                (code,)
            )
            
            if not res:
                return "not_found"
            
            points = res['points']
            max_uses = res['max_uses']
            current_uses = res['current_uses']
            active = res['active']
            expires_at = res['expires_at']
            
            # التحقق من الصلاحية
            if not active:
                return "expired"
            
            if current_uses >= max_uses:
                return "expired"
            
            # التحقق من تاريخ الانتهاء
            if expires_at:
                try:
                    expires_date = datetime.fromisoformat(expires_at)
                    if expires_date < datetime.now():
                        return "expired"
                except ValueError:
                    return "error"
            
            # التحقق من الاستخدام السابق
            usage = await self.execute_query_one(
                "SELECT id FROM code_usage WHERE user_id = ? AND code = ?",
                (user_id, code)
            )
            if usage:
                return "used"
            
            # تنفيذ العملية
            await self.execute_update(
                "UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?",
                (code,),
                commit=True
            )
            
            await self.execute_update(
                "INSERT INTO code_usage (user_id, code, points_received) VALUES (?, ?, ?)",
                (user_id, code, points),
                commit=True
            )
            
            # إضافة النقاط
            await self.update_points(user_id, points, "code", f"كود: {code}")
            
            logger.info(f"✅ تم استبدال الكود {code} للمستخدم {user_id}")
            return points
                
        except Exception as e:
            logger.error(f"❌ خطأ في استبدال الكود {code}: {e}")
            return "error"
    
    async def get_promo_code(self, code: str):
        """الحصول على معلومات كود"""
        try:
            result = await self.execute_query_one(
                """SELECT code, points, max_uses, current_uses, active, 
                created_at, expires_at, description
                FROM promo_codes WHERE code = ?""",
                (code,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على معلومات الكود {code}: {e}")
            return None
    
    # إحصائيات
    async def get_global_stats(self) -> tuple:
        """الحصول على إحصائيات عامة"""
        try:
            users_result = await self.execute_query_one("SELECT COUNT(*) as count FROM users WHERE is_banned = 0")
            users_count = users_result['count'] if users_result else 0
            
            points_result = await self.execute_query_one("SELECT SUM(points) as total FROM users WHERE is_banned = 0")
            total_points = points_result['total'] if points_result else 0
            
            tx_result = await self.execute_query_one("SELECT COUNT(*) as count FROM transactions")
            total_tx = tx_result['count'] if tx_result else 0
            
            stars_result = await self.execute_query_one("SELECT SUM(stars) as total FROM star_payments WHERE status = 'completed'")
            total_stars = stars_result['total'] if stars_result else 0
            
            return users_count, total_points, total_tx, total_stars, 0, 0, 0
            
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإحصائيات: {e}")
            return 0, 0, 0, 0, 0, 0, 0
    
    async def get_all_users(self, exclude_banned: bool = True, limit: int = None, offset: int = 0):
        """الحصول على جميع المستخدمين"""
        try:
            query = "SELECT user_id, username, full_name, points, is_banned FROM users"
            if exclude_banned:
                query += " WHERE is_banned = 0"
            query += " ORDER BY user_id"
            
            if limit:
                query += " LIMIT ? OFFSET ?"
                result = await self.execute_query(query, (limit, offset))
            else:
                result = await self.execute_query(query)
            
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على جميع المستخدمين: {e}")
            return []
    
    # إدارة الإعدادات
    async def get_setting(self, key: str, default: str = None):
        """الحصول على إعداد"""
        try:
            result = await self.execute_query_one(
                "SELECT value FROM settings WHERE key = ?",
                (key,)
            )
            if result:
                return result['value']
            return default
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإعداد {key}: {e}")
            return default
    
    async def set_setting(self, key: str, value: str):
        """تحديث إعداد"""
        try:
            await self.execute_update(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (str(value), datetime.now().isoformat(), key),
                commit=True
            )
        except Exception as e:
            logger.error(f"خطأ في تحديث الإعداد {key}: {e}")
    
    # نظام Rate Limiting
    async def check_rate_limit(self, user_id: int) -> tuple:
        """التحقق من Rate Limiting"""
        try:
            now = time.time()
            window_start = now - RATE_LIMIT_WINDOW
            
            # تنظيف البيانات القديمة
            self.rate_limit_data[user_id] = [t for t in self.rate_limit_data[user_id] if t > window_start]
            
            # إضافة الطلب الحالي
            self.rate_limit_data[user_id].append(now)
            
            # التحقق من الحد
            if len(self.rate_limit_data[user_id]) > MAX_REQUESTS_PER_WINDOW:
                remaining_time = RATE_LIMIT_WINDOW - (now - self.rate_limit_data[user_id][0])
                return False, f"⏱️ تجاوزت الحد المسموح. يرجى الانتظار {remaining_time:.1f} ثانية"
            
            return True, ""
        except Exception as e:
            logger.error(f"خطأ في نظام Rate Limiting: {e}")
            return True, ""

# تهيئة قاعدة البيانات
db = AsyncDatabaseManager()

# أدوات مساعدة
def get_user_link(user_id: int, name: str) -> str:
    """إنشاء رابط للمستخدم"""
    safe_name = html.escape(name) if name else "مستخدم"
    return f"<a href='tg://user?id={user_id}'>{safe_name}</a>"

def get_main_keyboard(user_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """لوحة المفاتيح الرئيسية"""
    btns = [
        [InlineKeyboardButton("💸 تحويل النقاط", callback_data="transfer_start"),
         InlineKeyboardButton("🎫 استبدال كود", callback_data="redeem_code_start")],
        [InlineKeyboardButton("⭐ شراء النقاط", callback_data="buy_points_menu"),
         InlineKeyboardButton("📞 الدعم الفني", callback_data="support")],
    ]
    if is_admin:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """لوحة المفاتيح الإدارية"""
    btns = [
        [InlineKeyboardButton("📊 لوحة التحكم", callback_data="admin_panel")],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels"),
         InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("📤 نظام الإذاعة", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎫 إدارة الأكواد", callback_data="admin_codes")],
        [InlineKeyboardButton("⚙️ إعدادات النظام", callback_data="admin_settings")]
    ]
    return InlineKeyboardMarkup(btns)

async def check_maintenance_mode(user_id: int) -> bool:
    """التحقق من وضع الصيانة"""
    if user_id == ADMIN_ID:
        return False
    
    maintenance_mode = await db.get_setting("maintenance_mode")
    return bool(maintenance_mode)

async def check_rate_limit(user_id: int) -> tuple:
    """التحقق من Rate Limiting"""
    return await db.check_rate_limit(user_id)

def is_admin(user_id: int) -> bool:
    """التحقق إذا كان المستخدم أدمن"""
    return user_id == ADMIN_ID

def format_number(num: int) -> str:
    """تنسيق الأرقام"""
    return f"{num:,}" if num else "0"

# نظام المحادثات
class ConversationManager:
    def __init__(self):
        self.active_conversations = {}
        
    async def start_conversation(self, user_id: int, state: int, data: dict = None):
        """بدء محادثة جديدة"""
        self.active_conversations[user_id] = {
            'state': state,
            'data': data or {}
        }
        
    async def update_conversation(self, user_id: int, state: int = None, data: dict = None):
        """تحديث حالة المحادثة"""
        if user_id in self.active_conversations:
            if state is not None:
                self.active_conversations[user_id]['state'] = state
            if data is not None:
                self.active_conversations[user_id]['data'].update(data)
    
    async def end_conversation(self, user_id: int):
        """إنهاء محادثة"""
        if user_id in self.active_conversations:
            del self.active_conversations[user_id]
    
    async def get_conversation_state(self, user_id: int):
        """الحصول على حالة المحادثة"""
        return self.active_conversations.get(user_id, {}).get('state')
    
    async def get_conversation_data(self, user_id: int, key: str = None):
        """الحصول على بيانات المحادثة"""
        data = self.active_conversations.get(user_id, {}).get('data', {})
        return data.get(key) if key else data

conv_manager = ConversationManager()

# المعالجات الرئيسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start"""
    user = update.effective_user
    
    allowed, message = await check_rate_limit(user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return
    
    await conv_manager.end_conversation(user.id)
    
    if await check_maintenance_mode(user.id):
        await update.message.reply_text("🔧 البوت قيد الصيانة حاليًا.")
        return
    
    subscribed, message = await db.check_channel_subscription(user.id, context)
    if not subscribed:
        await update.message.reply_text(message, parse_mode="HTML")
        return
    
    if await db.is_banned(user.id):
        await update.message.reply_text("🚫 حسابك محظور!")
        return
    
    db_user = await db.get_user(user.id)
    if not db_user:
        referrer_id = None
        if context.args and context.args[0].startswith("invite_"):
            try:
                inviter = int(context.args[0].split("_")[1])
                if inviter != user.id:
                    referrer_id = inviter
            except:
                pass
        
        await db.add_user(user.id, user.username or "", user.full_name or "مستخدم", referrer_id)
    
    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """إرسال لوحة التحكم"""
    user = update.effective_user
    
    allowed, message = await check_rate_limit(user.id)
    if not allowed:
        if update.callback_query:
            await update.callback_query.answer(message, show_alert=True)
        return
    
    await conv_manager.end_conversation(user.id)
    
    if await check_maintenance_mode(user.id):
        if update.callback_query:
            await update.callback_query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    subscribed, message = await db.check_channel_subscription(user.id, context)
    if not subscribed:
        if update.callback_query:
            await update.callback_query.edit_message_text(message, parse_mode="HTML")
        return
    
    if await db.is_banned(user.id):
        ban_message = "🚫 حسابك محظور!"
        if update.callback_query:
            await update.callback_query.edit_message_text(ban_message, parse_mode="HTML")
        return
    
    db_user = await db.get_user(user.id)
    if not db_user:
        await start(update, context)
        return
    
    points = db_user['points']
    username = db_user['username'] or "لا يوجد"
    full_name = db_user['full_name'] or user.first_name
    
    text = (
        f"مرحباً بك {get_user_link(user.id, full_name)} 👋\n\n"
        f"📊 <b>معلومات حسابك:</b>\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"📛 اليوزر: @{username}\n"
        f"🏆 الرصيد: <b>{format_number(points)} نقطة</b>\n\n"
        f"👇 اختر من القائمة أدناه:"
    )
    
    kb = get_main_keyboard(user.id, is_admin(user.id))
    
    try:
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        elif update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ في إرسال لوحة التحكم: {e}")

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة للقائمة الرئيسية"""
    query = update.callback_query
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    await conv_manager.end_conversation(query.from_user.id)
    await send_dashboard(update, context, edit=True)

# نظام الدفع بالنجوم
async def buy_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة شراء النقاط"""
    query = update.callback_query
    user_id = query.from_user.id
    
    allowed, message = await check_rate_limit(user_id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(user_id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    text = "💰 <b>شراء النقاط</b>\n\n"
    
    if PAYMENT_PROVIDER_TOKEN:
        text += "⭐ <b>الدفع بالنجوم:</b>\n"
        text += "• 5 نجوم ← 50 نقطة\n"
        text += "• 10 نجوم ← 120 نقطة\n\n"
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ 5 نجوم (50 نقطة)", callback_data="buy_5"),
            InlineKeyboardButton("⭐⭐ 10 نجوم (120 نقطة)", callback_data="buy_10")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
    else:
        text += "نظام الدفع غير متاح حالياً."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def buy_stars_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج شراء النجوم"""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    allowed, message = await check_rate_limit(user_id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(user_id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    packages = {
        "buy_5": {"stars": 5, "points": 50, "title": "5 نجوم (50 نقطة)"},
        "buy_10": {"stars": 10, "points": 120, "title": "10 نجوم (120 نقطة)"}
    }
    
    if data not in packages:
        await query.edit_message_text("❌ الباقة المطلوبة غير موجودة.")
        return
    
    package = packages[data]
    
    if not PAYMENT_PROVIDER_TOKEN:
        await query.edit_message_text("❌ نظام الدفع غير مفعل حالياً.")
        return
    
    prices = [LabeledPrice(f"{package['points']} نقطة", package['stars'] * 100)]
    
    try:
        payload = f"stars_{package['stars']}_{package['points']}_{user_id}_{int(time.time())}"
        
        await context.bot.send_invoice(
            chat_id=user_id,
            title=package['title'],
            description=f"شراء {package['points']} نقطة مقابل {package['stars']} نجوم",
            payload=payload,
            provider_token=PAYMENT_PROVIDER_TOKEN,
            currency="XTR",
            prices=prices,
            start_parameter="stars_payment"
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ في إنشاء الفاتورة: {str(e)[:100]}")

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الدفع"""
    query = update.pre_checkout_query
    
    try:
        if not query.invoice_payload.startswith("stars_"):
            await query.answer(ok=False, error_message="فاتورة غير صالحة")
            return
        
        await query.answer(ok=True)
        
    except Exception as e:
        await query.answer(ok=False, error_message="حدث خطأ في التحقق من الدفع")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الدفع الناجح"""
    try:
        payment = update.message.successful_payment
        payload = payment.invoice_payload
        
        parts = payload.split("_")
        if len(parts) != 5:
            raise ValueError("بايلود غير صالح")
        
        stars = int(parts[1])
        points = int(parts[2])
        user_id = int(parts[3])
        
        if update.effective_user.id != user_id:
            await update.message.reply_text("❌ هذه الفاتورة لا تنتمي إليك!")
            return
        
        success = await db.add_star_payment(
            payment_id=payment.provider_payment_id,
            user_id=user_id,
            stars=stars,
            points=points
        )
        
        if not success:
            raise Exception("فشل في تسجيل عملية الدفع")
        
        await db.update_points(user_id, points, "buy", f"شراء بالنجوم: {stars} نجمة")
        
        user_data = await db.get_user(user_id)
        new_balance = user_data['points'] if user_data else points
        
        await update.message.reply_text(
            f"✅ تمت العملية بنجاح!\n\n"
            f"🎉 تم إضافة <b>{points} نقطة</b> لحسابك.\n"
            f"💰 رصيدك الحالي: <b>{format_number(new_balance)} نقطة</b>\n"
            f"⭐ النجوم المستخدمة: {stars}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الدفع الناجح: {e}")
        await update.message.reply_text("❌ حدث خطأ في معالجة الدفع.")

# لوحة تحكم الأدمن
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم الأدمن"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    users_count, total_points, total_tx, total_stars, _, _, _ = await db.get_global_stats()
    
    text = (
        f"⚙️ <b>لوحة التحكم</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 👥 المستخدمين: {format_number(users_count)}\n"
        f"• 💰 النقاط الكلية: {format_number(total_points)}\n"
        f"• ⭐ النجوم المشتراة: {format_number(total_stars)}\n\n"
        f"👇 اختر القسم المطلوب:"
    )
    
    kb = get_admin_keyboard()
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# إدارة القنوات
async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة القنوات"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    channels = await db.get_channels()
    text = "📢 <b>إدارة القنوات الإجبارية</b>\n\n"
    
    if channels:
        for i, channel in enumerate(channels, 1):
            status = "🟢 مفعل" if channel['is_active'] else "🔴 معطل"
            name = channel['channel_name'] or "بدون اسم"
            text += f"{i}. {name} - {status}\n"
    else:
        text += "لا توجد قنوات مضافة.\n"
    
    text += "\n👇 اختر الإجراء المطلوب:"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_channel")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة قناة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_CHANNEL_ID)
    
    await query.edit_message_text(
        "📝 <b>إضافة قناة جديدة</b>\n\n"
        "أرسل الآن <b>آيدي القناة</b>:\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آيدي القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_id = update.message.text.strip()
    
    try:
        chat = await context.bot.get_chat(channel_id)
        channel_name = chat.title
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_CHANNEL_LINK,
            {'channel_id': channel_id, 'channel_name': channel_name}
        )
        
        await update.message.reply_text(
            f"✅ تم التعرف على القناة: <b>{channel_name}</b>\n\n"
            "الآن أرسل <b>رابط القناة</b>:\n\n"
            "❌ للإلغاء، أرسل /cancel",
            parse_mode="HTML"
        )
        return STATE_CHANNEL_LINK
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الوصول للقناة: {str(e)[:100]}")
        return STATE_CHANNEL_ID

async def admin_get_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على رابط القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_link = update.message.text.strip()
    
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    channel_id = conv_data.get('channel_id')
    channel_name = conv_data.get('channel_name', 'قناة')
    
    if await db.add_channel(channel_id, channel_link, update.effective_user.id, channel_name):
        success_msg = (
            f"✅ <b>تمت إضافة القناة بنجاح!</b>\n\n"
            f"📢 القناة: <b>{channel_name}</b>\n"
            f"🆔 الآيدي: <code>{channel_id}</code>\n"
            f"🔗 الرابط: {channel_link}"
        )
        await update.message.reply_text(success_msg, parse_mode="HTML")
    else:
        await update.message.reply_text("❌ فشل في إضافة القناة!")
    
    await conv_manager.end_conversation(update.effective_user.id)
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_cancel_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إضافة قناة"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء عملية إضافة القناة.")
    await admin_channels_menu(update, context)
    return ConversationHandler.END

# إدارة المستخدمين
async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة المستخدمين"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    users_count = (await db.get_global_stats())[0]
    
    text = (
        f"👤 <b>إدارة المستخدمين</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 👥 إجمالي المستخدمين: {format_number(users_count)}\n\n"
        f"🔍 <b>طرق البحث:</b>"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بحث بالآيدي", callback_data="admin_search_by_id")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_search_by_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث بالآيدي"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_USER_SEARCH, {'search_type': 'id'})
    
    await query.edit_message_text(
        "🔍 <b>البحث عن مستخدم بالآيدي</b>\n\n"
        "أرسل الآن <b>آيدي المستخدم</b>:\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن مستخدم"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_USER_SEARCH
    
    search_input = update.message.text.strip()
    
    try:
        user_id = int(search_input)
        user = await db.get_user(user_id)
    
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_USER_SEARCH
    
    if not user:
        await update.message.reply_text("❌ المستخدم غير موجود!")
        return STATE_USER_SEARCH
    
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_USER_MANAGE,
        {
            'managed_user_id': user['user_id'],
            'managed_user_name': user['full_name'],
            'managed_user_data': dict(user)
        }
    )
    
    await show_user_management_panel(update, context, user)
    return STATE_USER_MANAGE

async def show_user_management_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data):
    """عرض لوحة إدارة المستخدم"""
    user_id = user_data['user_id']
    full_name = user_data['full_name'] or 'غير معروف'
    username = user_data['username'] or 'لا يوجد'
    points = user_data['points']
    is_banned = user_data['is_banned']
    
    text = (
        f"✅ <b>تم العثور على المستخدم:</b>\n\n"
        f"👤 <b>معلومات أساسية:</b>\n"
        f"• الاسم: {full_name}\n"
        f"• 🆔 الآيدي: <code>{user_id}</code>\n"
        f"• 📛 اليوزر: @{username}\n"
        f"• 🎯 النقاط: {format_number(points)}\n"
        f"• 🚫 الحالة: {'محظور' if is_banned else 'نشط'}\n\n"
        f"👇 اختر الإجراء المطلوب:"
    )
    
    kb_buttons = []
    
    if not is_banned:
        kb_buttons.append([
            InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_add_points"),
            InlineKeyboardButton("➖ خصم نقاط", callback_data="admin_deduct_points")
        ])
        kb_buttons.append([
            InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_ban_user")
        ])
    else:
        kb_buttons.append([
            InlineKeyboardButton("✅ فك الحظر", callback_data="admin_unban_user")
        ])
    
    kb_buttons.append([InlineKeyboardButton("🔙 رجوع للبحث", callback_data="admin_users")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_add_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة نقاط للمستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    
    await conv_manager.update_conversation(
        query.from_user.id,
        STATE_ADD_POINTS,
        {'action': 'add_points', 'target_user_id': user_id}
    )
    
    await query.edit_message_text(
        "➕ <b>إضافة نقاط للمستخدم</b>\n\n"
        "أرسل <b>عدد النقاط</b> التي تريد إضافتها:\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_ADD_POINTS

async def admin_process_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إضافة/خصم النقاط"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        current_state = await conv_manager.get_conversation_state(update.effective_user.id)
        return current_state
    
    try:
        points = int(update.message.text.strip())
        
        if points <= 0:
            await update.message.reply_text("❌ عدد النقاط يجب أن يكون أكبر من صفر!")
            current_state = await conv_manager.get_conversation_state(update.effective_user.id)
            return current_state
        
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        action = conv_data.get('action')
        user_id = conv_data.get('target_user_id')
        user_name = conv_data.get('managed_user_name', 'مستخدم')
        
        if action == 'add_points':
            await db.update_points(user_id, points, "admin_add", f"إضافة بواسطة الأدمن")
            result_text = f"✅ تمت إضافة {points} نقطة للمستخدم {user_name}"
            
        elif action == 'deduct_points':
            user_data = await db.get_user(user_id)
            if user_data and user_data['points'] < points:
                await update.message.reply_text(f"❌ رصيد المستخدم غير كافي!")
                current_state = await conv_manager.get_conversation_state(update.effective_user.id)
                return current_state
            
            await db.update_points(user_id, -points, "admin_deduct", f"خصم بواسطة الأدمن")
            result_text = f"✅ تم خصم {points} نقطة من المستخدم {user_name}"
        
        else:
            await update.message.reply_text("❌ إجراء غير معروف!")
            await conv_manager.end_conversation(update.effective_user.id)
            return ConversationHandler.END
        
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        user_data = conv_data.get('managed_user_data')
        
        if user_data:
            await update.message.reply_text(result_text)
            await show_user_management_panel(update, context, user_data)
            await conv_manager.end_conversation(update.effective_user.id)
            return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        current_state = await conv_manager.get_conversation_state(update.effective_user.id)
        return current_state

async def admin_ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حظر مستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await db.ban_user(user_id, "حظر يدوي", query.from_user.id)
    
    await query.edit_message_text(
        f"✅ <b>تم حظر المستخدم بنجاح!</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>",
        parse_mode="HTML"
    )
    
    user_data = await db.get_user(user_id)
    if user_data:
        await show_user_management_panel(update, context, user_data)

async def admin_unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فك حظر مستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await db.unban_user(user_id, query.from_user.id)
    
    await query.edit_message_text(
        f"✅ <b>تم فك حظر المستخدم بنجاح!</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>",
        parse_mode="HTML"
    )
    
    user_data = await db.get_user(user_id)
    if user_data:
        await show_user_management_panel(update, context, user_data)

async def admin_cancel_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إدارة المستخدم"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء العملية.")
    await admin_users_menu(update, context)
    return ConversationHandler.END

# نظام الأكواد
async def admin_codes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة الأكواد"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    text = "🎫 <b>إدارة الأكواد الترويجية</b>\n\n👇 اختر الإجراء المطلوب:"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء كود جديد", callback_data="admin_create_code")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_create_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إنشاء كود جديد"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_CREATE_CODE)
    
    await query.edit_message_text(
        "🎫 <b>إنشاء كود ترويجي جديد</b>\n\n"
        "أرسل <b>اسم الكود</b> (بدون مسافات، بالإنجليزية):\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الكود الجديد"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CREATE_CODE
    
    code = update.message.text.strip().upper()
    
    if not code.isalnum():
        await update.message.reply_text("❌ الكود يجب أن يحتوي على أحرف وأرقام فقط!")
        return STATE_CREATE_CODE
    
    existing_code = await db.get_promo_code(code)
    if existing_code:
        await update.message.reply_text(f"❌ الكود موجود مسبقاً!")
        return STATE_CREATE_CODE
    
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_CREATE_CODE,
        {'new_code': code}
    )
    
    await update.message.reply_text(
        f"✅ الكود <code>{code}</code> مقبول.\n\n"
        "الآن أرسل <b>عدد النقاط</b> التي يعطيها الكود:"
    )
    return STATE_POINTS_AMOUNT

async def admin_get_code_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على عدد نقاط الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_POINTS_AMOUNT
    
    try:
        points = int(update.message.text.strip())
        
        if points <= 0:
            await update.message.reply_text("❌ عدد النقاط يجب أن يكون أكبر من صفر!")
            return STATE_POINTS_AMOUNT
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_POINTS_AMOUNT,
            {'code_points': points}
        )
        
        await update.message.reply_text(
            f"✅ تم تعيين النقاط: {points}\n\n"
            "الآن أرسل <b>الحد الأقصى لعدد المستخدمين</b>:"
        )
        return STATE_CODE_EXPIRY
    
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_POINTS_AMOUNT

async def admin_get_code_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على صلاحية الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CODE_EXPIRY
    
    try:
        max_uses = int(update.message.text.strip())
        
        if max_uses < 0:
            await update.message.reply_text("❌ العدد يجب أن يكون 0 أو أكثر!")
            return STATE_CODE_EXPIRY
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_CODE_EXPIRY,
            {'code_max_uses': max_uses}
        )
        
        await update.message.reply_text(
            f"✅ الحد الأقصى: {max_uses if max_uses > 0 else 'غير محدود'}\n\n"
            "الآن أرسل <b>عدد أيام الصلاحية</b> (0 لدائم):"
        )
        return STATE_CONFIRM_ACTION
    
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_CODE_EXPIRY

async def admin_finish_code_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء إنشاء الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CONFIRM_ACTION
    
    try:
        expiry_days = int(update.message.text.strip())
        
        if expiry_days < 0:
            await update.message.reply_text("❌ عدد الأيام يجب أن يكون 0 أو أكثر!")
            return STATE_CONFIRM_ACTION
        
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        code = conv_data.get('new_code')
        points = conv_data.get('code_points')
        max_uses = conv_data.get('code_max_uses', 1)
        
        success = await db.create_promo_code(
            code=code,
            points=points,
            max_uses=max_uses if max_uses > 0 else 999999,
            created_by=update.effective_user.id,
            expires_days=expiry_days if expiry_days > 0 else 0
        )
        
        if success:
            expiry_text = f"{expiry_days} يوم" if expiry_days > 0 else "دائم"
            uses_text = f"{max_uses} مستخدم" if max_uses > 0 else "غير محدود"
            
            success_msg = (
                f"✅ <b>تم إنشاء الكود بنجاح!</b>\n\n"
                f"🎫 <b>تفاصيل الكود:</b>\n"
                f"• الكود: <code>{code}</code>\n"
                f"• النقاط: {format_number(points)}\n"
                f"• الحد الأقصى: {uses_text}\n"
                f"• الصلاحية: {expiry_text}"
            )
            
            await update.message.reply_text(success_msg, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ فشل في إنشاء الكود!")
        
        await conv_manager.end_conversation(update.effective_user.id)
        await admin_codes_menu(update, context)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_CONFIRM_ACTION

async def admin_cancel_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إنشاء كود"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء إنشاء الكود.")
    await admin_codes_menu(update, context)
    return ConversationHandler.END

# نظام التحويل
async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية تحويل النقاط"""
    query = update.callback_query
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    subscribed, message = await db.check_channel_subscription(query.from_user.id, context)
    if not subscribed:
        await query.edit_message_text(message, parse_mode="HTML")
        return
    
    await conv_manager.start_conversation(query.from_user.id, STATE_TRANSFER_ID)
    
    await query.edit_message_text(
        "💸 <b>تحويل النقاط</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> الذي تريد التحويل له:\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_TRANSFER_ID

async def get_transfer_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آيدي المستخدم للتحويل"""
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_TRANSFER_ID
    
    try:
        receiver_id = int(update.message.text.strip())
        
        if receiver_id == update.effective_user.id:
            await update.message.reply_text("❌ لا يمكن التحويل لنفسك!")
            return STATE_TRANSFER_ID
        
        receiver = await db.get_user(receiver_id)
        if not receiver:
            await update.message.reply_text("❌ المستخدم غير موجود!")
            return STATE_TRANSFER_ID
        
        if receiver['is_banned'] == 1:
            await update.message.reply_text("❌ المستخدم محظور!")
            return STATE_TRANSFER_ID
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_TRANSFER_AMOUNT,
            {'receiver_id': receiver_id, 'receiver_name': receiver['full_name']}
        )
        
        await update.message.reply_text(
            f"✅ المستخدم: {receiver['full_name']}\n\n"
            "أرسل <b>عدد النقاط</b> التي تريد تحويلها:\n\n"
            "❌ للإلغاء، أرسل /cancel"
        )
        return STATE_TRANSFER_AMOUNT
        
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_TRANSFER_ID

async def get_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على مبلغ التحويل"""
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_TRANSFER_AMOUNT
    
    try:
        amount = int(update.message.text.strip())
        
        min_transfer = await db.get_setting("min_transfer", 10)
        if amount < min_transfer:
            await update.message.reply_text(f"❌ الحد الأدنى للتحويل هو {min_transfer} نقطة!")
            return STATE_TRANSFER_AMOUNT
        
        sender = await db.get_user(update.effective_user.id)
        if not sender or sender['points'] < amount:
            await update.message.reply_text(f"❌ رصيدك غير كافي!")
            return STATE_TRANSFER_AMOUNT
        
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        receiver_id = conv_data.get('receiver_id')
        receiver_name = conv_data.get('receiver_name', 'مستخدم')
        
        try:
            await db.update_points(update.effective_user.id, -amount, "transfer_out", 
                                 f"تحويل إلى: {receiver_name}", receiver_id)
            
            await db.update_points(receiver_id, amount, "transfer_in", 
                                 f"استلام من: {sender['full_name']}", update.effective_user.id)
            
            await update.message.reply_text(
                f"✅ <b>تم التحويل بنجاح!</b>\n\n"
                f"👤 المستقبل: {receiver_name}\n"
                f"💰 المبلغ المحول: {amount:,} نقطة\n"
                f"📊 رصيدك الحالي: {sender['points'] - amount:,} نقطة",
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"خطأ في تنفيذ التحويل: {e}")
            await update.message.reply_text("❌ حدث خطأ في التحويل!")
        
        await conv_manager.end_conversation(update.effective_user.id)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!")
        return STATE_TRANSFER_AMOUNT

async def cancel_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية التحويل"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء عملية التحويل.")
    await send_dashboard(update, context)
    return ConversationHandler.END

# نظام استبدال الأكواد
async def start_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية استبدال الكود"""
    query = update.callback_query
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    subscribed, message = await db.check_channel_subscription(query.from_user.id, context)
    if not subscribed:
        await query.edit_message_text(message, parse_mode="HTML")
        return
    
    await conv_manager.start_conversation(query.from_user.id, STATE_REDEEM_CODE)
    
    await query.edit_message_text(
        "🎫 <b>استبدال الكود</b>\n\n"
        "أرسل <b>الكود</b> الذي تريد استبداله:\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_REDEEM_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الكود المدخل"""
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_REDEEM_CODE
    
    code = update.message.text.strip().upper()
    
    result = await db.redeem_promo_code(update.effective_user.id, code)
    
    if isinstance(result, int):
        user_data = await db.get_user(update.effective_user.id)
        await update.message.reply_text(
            f"✅ <b>تم استبدال الكود بنجاح!</b>\n\n"
            f"🎫 الكود: <code>{code}</code>\n"
            f"🎯 النقاط: {result:,}\n"
            f"💰 رصيدك الحالي: {user_data['points']:,} نقطة",
            parse_mode="HTML"
        )
    else:
        error_messages = {
            "not_found": "❌ الكود غير موجود!",
            "expired": "❌ الكود منتهي الصلاحية!",
            "used": "❌ لقد استخدمت هذا الكود مسبقاً!",
            "error": "❌ حدث خطأ في معالجة الكود!"
        }
        
        error_msg = error_messages.get(result, "❌ حدث خطأ غير معروف!")
        await update.message.reply_text(error_msg)
    
    await conv_manager.end_conversation(update.effective_user.id)
    await send_dashboard(update, context)
    return ConversationHandler.END

async def cancel_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية استبدال الكود"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء استبدال الكود.")
    await send_dashboard(update, context)
    return ConversationHandler.END

# نظام الدعم
async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الدعم الفني"""
    query = update.callback_query
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    text = (
        "📞 <b>مركز الدعم الفني</b>\n\n"
        "للتواصل مع الإدارة:\n"
        "👤 تواصل مباشر مع الأدمن"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 الرجوع", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# إعدادات النظام
async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإعدادات"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_SETTINGS_MENU)
    
    text = (
        "⚙️ <b>إدارة الإعدادات</b>\n\n"
        "لتعديل إعداد، أرسل اسم الإعداد والقيمة الجديدة:\n"
        "<code>welcome_points 50</code>\n\n"
        "❌ للإلغاء، أرسل /cancel"
    )
    
    await query.edit_message_text(text, parse_mode="HTML")
    return STATE_SETTINGS_MENU

async def admin_save_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الإعداد"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_SETTINGS_MENU
    
    input_text = update.message.text.strip()
    parts = input_text.split(maxsplit=1)
    
    if len(parts) != 2:
        await update.message.reply_text("❌ تنسيق غير صحيح!")
        return STATE_SETTINGS_MENU
    
    key, value = parts
    
    await db.set_setting(key, value)
    
    await update.message.reply_text(
        f"✅ تم تحديث الإعداد <code>{key}</code> إلى <code>{value}</code>"
    )
    
    return STATE_SETTINGS_MENU

async def admin_cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء تعديل الإعدادات"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء تعديل الإعدادات.")
    await admin_panel(update, context)
    return ConversationHandler.END

# معالج الأخطاء
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء"""
    logger.error(f"حدث خطأ: {context.error}", exc_info=context.error)
    
    if update and update.effective_user:
        error_msg = "❌ حدث خطأ غير متوقع"
        try:
            if update.callback_query:
                await update.callback_query.message.reply_text(error_msg)
            elif update.message:
                await update.message.reply_text(error_msg)
        except:
            pass

# التشغيل الرئيسي
async def main():
    """الدالة الرئيسية لتشغيل البوت"""
    
    if not BOT_TOKEN:
        logger.error("❌ لم يتم تعيين BOT_TOKEN!")
        return
    
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_error_handler(error_handler)
    
    # محادثة تحويل النقاط
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_transfer), CommandHandler("start", start)]
    )
    
    # محادثة استبدال الأكواد
    redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")],
        states={
            STATE_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel_redeem), CommandHandler("start", start)]
    )
    
    # محادثة إنشاء الأكواد
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_create_code_start, pattern="^admin_create_code$")],
        states={
            STATE_CREATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_code)],
            STATE_POINTS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_code_points)],
            STATE_CODE_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_code_expiry)],
            STATE_CONFIRM_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_finish_code_creation)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_code), CommandHandler("start", start)]
    )
    
    # محادثة إدارة القنوات
    channels_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_channel_start, pattern="^admin_add_channel$")],
        states={
            STATE_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_id)],
            STATE_CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_link)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_channel), CommandHandler("start", start)]
    )
    
    # محادثة إدارة المستخدمين
    users_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_search_by_id_start, pattern="^admin_search_by_id$")],
        states={
            STATE_USER_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_user)],
            STATE_ADD_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_points)],
            STATE_DEDUCT_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_process_points)],
        },
        fallbacks=[
            CallbackQueryHandler(admin_ban_user_callback, pattern="^admin_ban_user$"),
            CallbackQueryHandler(admin_unban_user_callback, pattern="^admin_unban_user$"),
            CommandHandler("cancel", admin_cancel_user_management),
            CommandHandler("start", start)
        ]
    )
    
    # محادثة تعديل الإعدادات
    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_settings_menu, pattern="^admin_settings$")],
        states={
            STATE_SETTINGS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_setting)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_settings), CommandHandler("start", start)]
    )
    
    # تسجيل المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    application.add_handler(create_code_conv)
    application.add_handler(channels_conv)
    application.add_handler(users_conv)
    application.add_handler(settings_conv)
    
    # معالجات الأزرار العامة
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(support_handler, pattern="^support$"))
    application.add_handler(CallbackQueryHandler(buy_points_menu, pattern="^buy_points_menu$"))
    
    # معالجات الأزرار الإدارية
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$"))
    application.add_handler(CallbackQueryHandler(admin_users_menu, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_codes_menu, pattern="^admin_codes$"))
    
    # معالجات الدفع بالنجوم
    if PAYMENT_PROVIDER_TOKEN:
        application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
        application.add_handler(CallbackQueryHandler(buy_stars_handler, pattern="^buy_(5|10)$"))
    
    # معلومات التشغيل
    print("\n" + "="*60)
    print("🤖 بوت النقاط المتطور - الإصدار المختصر")
    print("="*60)
    print(f"🆔 الأدمن: {ADMIN_ID}")
    print("="*60)
    print("✅ البوت يعمل بكفاءة عالية...")
    print("="*60 + "\n")
    
    # تشغيل البوت
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try:
        # تهيئة قاعدة البيانات
        print("⏳ جاري تهيئة قاعدة البيانات...")
        db.init_database_sync()
        print("✅ تم تهيئة قاعدة البيانات بنجاح")
            
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 تم إيقاف البوت")
    except Exception as e:
        logger.error(f"خطأ فادح: {e}")
        print(f"❌ خطأ فادح: {e}")