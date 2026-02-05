import logging
import sqlite3
import html
import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any
import json
import aiosqlite
from concurrent.futures import ThreadPoolExecutor

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

BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"
ADMIN_ID = 8287678319
PAYMENT_PROVIDER_TOKEN = ""

# مراحل المحادثات (Conversation States) - إصلاح التضارب
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_REDEEM_CODE = 2
STATE_CREATE_CODE = 3
STATE_CHANNEL_ID, STATE_CHANNEL_LINK = range(4, 6)
STATE_BROADCAST_MESSAGE, STATE_BROADCAST_MEDIA = range(6, 8)
STATE_USER_SEARCH, STATE_USER_MANAGE = range(8, 10)
STATE_SETTINGS_MENU = 10
STATE_EDIT_CHANNEL = 11
STATE_DELETE_CHANNEL = 12
STATE_TOGGLE_CHANNEL = 13

# الحد الأقصى للمحاولات في المحادثات
MAX_RETRIES = 3

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات المحسّن (Enhanced Database Manager)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseManager:
    def __init__(self, db_name="bot_data.db"):
        self.db_name = db_name
        self.conn = None
        self.cursor = None
        self.init_database()
        self.executor = ThreadPoolExecutor(max_workers=5)
        
    def init_database(self):
        """تهيئة قاعدة البيانات مع إضافة indices وتحسينات الأداء"""
        try:
            self.conn = sqlite3.connect(self.db_name, check_same_thread=False, timeout=30)
            self.cursor = self.conn.cursor()
            self.create_tables()
            self.create_indices()
            self.init_settings()
            logger.info("✅ قاعدة البيانات مهيأة بنجاح")
        except Exception as e:
            logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
            raise
    
    def create_tables(self):
        """إنشاء الجداول مع تحسينات"""
        tables = [
            # جدول المستخدمين مع تحسينات
            '''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                phone TEXT DEFAULT 'None',
                points INTEGER DEFAULT 0,
                referrer_id INTEGER,
                last_daily_bonus TEXT,
                joined_date TEXT DEFAULT CURRENT_TIMESTAMP,
                is_banned INTEGER DEFAULT 0,
                last_active TEXT,
                total_earned INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0,
                FOREIGN KEY (referrer_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول العمليات مع تحسينات
            '''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                details TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                related_user_id INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الأكواد
            '''
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                points INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT
            )
            ''',
            
            # جدول استخدام الأكواد
            '''
            CREATE TABLE IF NOT EXISTS code_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT,
                used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, code),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (code) REFERENCES promo_codes(code)
            )
            ''',
            
            # جدول الإعدادات العامة
            '''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                description TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''',
            
            # جدول القنوات الإجبارية
            '''
            CREATE TABLE IF NOT EXISTS forced_channels (
                channel_id TEXT PRIMARY KEY,
                channel_link TEXT,
                is_active INTEGER DEFAULT 1,
                added_by INTEGER,
                added_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_check TEXT
            )
            ''',
            
            # جدول عمليات الدفع بالنجوم
            '''
            CREATE TABLE IF NOT EXISTS star_payments (
                payment_id TEXT PRIMARY KEY,
                user_id INTEGER,
                stars INTEGER,
                points INTEGER,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'completed',
                provider TEXT,
                amount_currency TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الإذاعات
            '''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT,
                media_type TEXT,
                media_file_id TEXT,
                sent_to INTEGER DEFAULT 0,
                failed_to INTEGER DEFAULT 0,
                total_users INTEGER DEFAULT 0,
                pinned INTEGER DEFAULT 0,
                sent_by INTEGER,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                completed INTEGER DEFAULT 0
            )
            ''',
            
            # جدول إحصائيات البوت
            '''
            CREATE TABLE IF NOT EXISTS bot_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                new_users INTEGER DEFAULT 0,
                total_points_earned INTEGER DEFAULT 0,
                total_stars_purchased INTEGER DEFAULT 0,
                total_transactions INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        ]
        
        for table_sql in tables:
            try:
                self.cursor.execute(table_sql)
            except Exception as e:
                logger.error(f"خطأ في إنشاء الجدول: {e}")
        
        self.conn.commit()
    
    def create_indices(self):
        """إنشاء indices لتحسين أداء الاستعلامات"""
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_users_referrer ON users(referrer_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_banned ON users(is_banned)",
            "CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_code_usage_user ON code_usage(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_star_payments_user ON star_payments(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_star_payments_status ON star_payments(status)"
        ]
        
        for index_sql in indices:
            try:
                self.cursor.execute(index_sql)
            except Exception as e:
                logger.error(f"خطأ في إنشاء index: {e}")
        
        self.conn.commit()
    
    def init_settings(self):
        """تهيئة الإعدادات الافتراضية"""
        default_settings = [
            ("tax_percent", "25", "نسبة الضريبة على التحويلات"),
            ("show_leaderboard", "1", "عرض لوحة المتصدرين"),
            ("maintenance_mode", "0", "وضع الصيانة"),
            ("daily_bonus_amount", "5", "قيمة المكافأة اليومية"),
            ("referral_points", "10", "نقاط الإحالة"),
            ("min_transfer", "10", "الحد الأدنى للتحويل"),
            ("welcome_points", "20", "نقاط الترحيب"),
            ("max_transfer_per_day", "1000", "الحد الأقصى للتحويل يومياً"),
            ("broadcast_delay", "0.1", "التأخير بين الإرسالات في الإذاعة"),
            ("max_broadcast_users", "50", "الحد الأقصى للمستخدمين في الإذاعة الواحدة")
        ]
        
        for key, val, desc in default_settings:
            try:
                self.cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
                    (key, val, desc)
                )
            except Exception as e:
                logger.error(f"خطأ في إضافة الإعداد: {e}")
        
        self.conn.commit()
    
    # --- تحسينات الأداء والسلامة ---
    
    def execute_query(self, query: str, params: tuple = (), commit: bool = False):
        """تنفيذ استعلام بأمان"""
        try:
            result = self.cursor.execute(query, params)
            if commit:
                self.conn.commit()
            return result
        except sqlite3.Error as e:
            logger.error(f"خطأ في قاعدة البيانات: {e} - الاستعلام: {query}")
            self.conn.rollback()
            raise
    
    def begin_transaction(self):
        """بدء معاملة"""
        self.cursor.execute("BEGIN TRANSACTION")
    
    def commit_transaction(self):
        """إتمام المعاملة"""
        self.conn.commit()
    
    def rollback_transaction(self):
        """تراجع عن المعاملة"""
        self.conn.rollback()
    
    # --- عمليات المستخدم المحسنة ---
    
    def add_user(self, user_id: int, username: str, full_name: str, phone: str = "None", referrer_id: int = None) -> bool:
        """إضافة مستخدم جديد بأمان"""
        try:
            self.begin_transaction()
            
            # التحقق من عدم وجود المستخدم مسبقاً
            if self.get_user(user_id):
                return False
            
            welcome_points = int(self.get_setting("welcome_points") or 20)
            date = datetime.now().isoformat()
            
            self.execute_query(
                """INSERT INTO users 
                (user_id, username, full_name, phone, points, referrer_id, joined_date, last_active) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, username, full_name, phone, welcome_points, referrer_id, date, date),
                commit=False
            )
            
            # تسجيل عملية الترحيب
            self.execute_query(
                """INSERT INTO transactions 
                (user_id, amount, type, details) 
                VALUES (?, ?, ?, ?)""",
                (user_id, welcome_points, "🎁 مكافأة", "نقاط ترحيب"),
                commit=False
            )
            
            # تحديث إحصائيات المستخدم
            self.execute_query(
                "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                (welcome_points, user_id),
                commit=False
            )
            
            self.commit_transaction()
            logger.info(f"✅ تم إضافة مستخدم جديد: {user_id} - {full_name}")
            return True
            
        except Exception as e:
            self.rollback_transaction()
            logger.error(f"❌ خطأ في إضافة المستخدم {user_id}: {e}")
            return False
    
    def get_user(self, user_id: int):
        """الحصول على بيانات مستخدم"""
        try:
            self.cursor.execute(
                """SELECT user_id, username, full_name, phone, points, referrer_id, 
                last_daily_bonus, joined_date, is_banned, last_active, 
                total_earned, total_spent 
                FROM users WHERE user_id = ?""",
                (user_id,)
            )
            return self.cursor.fetchone()
        except Exception as e:
            logger.error(f"خطأ في الحصول على بيانات المستخدم {user_id}: {e}")
            return None
    
    def update_points(self, user_id: int, amount: int, reason: str, details: str = "", related_user_id: int = None):
        """تحديث نقاط المستخدم بأمان"""
        try:
            self.begin_transaction()
            
            # التحقق من وجود المستخدم
            user = self.get_user(user_id)
            if not user:
                raise ValueError(f"المستخدم {user_id} غير موجود")
            
            # التحقق من عدم وجود سالب إذا كان الخصم
            if amount < 0 and user[4] + amount < 0:
                raise ValueError("رصيد المستخدم غير كافي")
            
            # تحديث النقاط
            self.execute_query(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (amount, user_id),
                commit=False
            )
            
            # تحديث الإحصائيات
            if amount > 0:
                self.execute_query(
                    "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                    (amount, user_id),
                    commit=False
                )
            else:
                self.execute_query(
                    "UPDATE users SET total_spent = total_spent + ABS(?) WHERE user_id = ?",
                    (amount, user_id),
                    commit=False
                )
            
            # تسجيل العملية
            tx_type_map = {
                "bonus": "🎁 مكافأة",
                "transfer_in": "📥 استلام",
                "transfer_out": "📤 تحويل",
                "buy": "💳 شراء",
                "code": "🎫 كود",
                "attack": "🎯 رشق",
                "referral": "👥 إحالة",
                "admin_add": "👑 إضافة من الأدمن",
                "admin_deduct": "👑 خصم من الأدمن"
            }
            
            tx_type = tx_type_map.get(reason, "❓ غير معروف")
            
            self.execute_query(
                """INSERT INTO transactions 
                (user_id, amount, type, details, related_user_id) 
                VALUES (?, ?, ?, ?, ?)""",
                (user_id, amount, tx_type, details, related_user_id),
                commit=False
            )
            
            # تحديث وقت النشاط الأخير
            self.execute_query(
                "UPDATE users SET last_active = ? WHERE user_id = ?",
                (datetime.now().isoformat(), user_id),
                commit=False
            )
            
            self.commit_transaction()
            logger.info(f"✅ تم تحديث نقاط المستخدم {user_id}: {amount:+d} ({reason})")
            
        except Exception as e:
            self.rollback_transaction()
            logger.error(f"❌ خطأ في تحديث نقاط المستخدم {user_id}: {e}")
            raise
    
    def ban_user(self, user_id: int, reason: str = ""):
        """حظر مستخدم"""
        try:
            self.execute_query(
                "UPDATE users SET is_banned = 1 WHERE user_id = ?",
                (user_id,),
                commit=True
            )
            logger.info(f"✅ تم حظر المستخدم {user_id} - السبب: {reason}")
        except Exception as e:
            logger.error(f"❌ خطأ في حظر المستخدم {user_id}: {e}")
    
    def unban_user(self, user_id: int):
        """فك حظر مستخدم"""
        try:
            self.execute_query(
                "UPDATE users SET is_banned = 0 WHERE user_id = ?",
                (user_id,),
                commit=True
            )
            logger.info(f"✅ تم فك حظر المستخدم {user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في فك حظر المستخدم {user_id}: {e}")
    
    def is_banned(self, user_id: int) -> bool:
        """التحقق إذا كان المستخدم محظوراً"""
        try:
            user = self.get_user(user_id)
            return user and user[8] == 1
        except Exception as e:
            logger.error(f"خطأ في التحقق من حظر المستخدم {user_id}: {e}")
            return False
    
    def get_history(self, user_id: int, limit: int = 10):
        """الحصول على سجل العمليات"""
        try:
            self.cursor.execute(
                """SELECT amount, type, details, timestamp 
                FROM transactions 
                WHERE user_id = ? 
                ORDER BY id DESC 
                LIMIT ?""",
                (user_id, limit)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"خطأ في الحصول على سجل المستخدم {user_id}: {e}")
            return []
    
    # --- قنوات الإشتراك الإجباري المحسنة ---
    
    def add_channel(self, channel_id: str, channel_link: str, added_by: int) -> bool:
        """إضافة قناة جديدة"""
        try:
            self.execute_query(
                """INSERT OR REPLACE INTO forced_channels 
                (channel_id, channel_link, added_by, added_at) 
                VALUES (?, ?, ?, ?)""",
                (channel_id, channel_link, added_by, datetime.now().isoformat()),
                commit=True
            )
            logger.info(f"✅ تم إضافة قناة: {channel_id}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة القناة {channel_id}: {e}")
            return False
    
    def update_channel(self, channel_id: str, channel_link: str) -> bool:
        """تحديث رابط القناة"""
        try:
            self.execute_query(
                "UPDATE forced_channels SET channel_link = ? WHERE channel_id = ?",
                (channel_link, channel_id),
                commit=True
            )
            logger.info(f"✅ تم تحديث القناة: {channel_id}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث القناة {channel_id}: {e}")
            return False
    
    def toggle_channel(self, channel_id: str, active: bool) -> bool:
        """تفعيل/تعطيل القناة"""
        try:
            self.execute_query(
                "UPDATE forced_channels SET is_active = ? WHERE channel_id = ?",
                (1 if active else 0, channel_id),
                commit=True
            )
            status = "تفعيل" if active else "تعطيل"
            logger.info(f"✅ تم {status} القناة: {channel_id}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في {status} القناة {channel_id}: {e}")
            return False
    
    def get_channels(self):
        """الحصول على جميع القنوات"""
        try:
            self.cursor.execute(
                "SELECT channel_id, channel_link, is_active FROM forced_channels ORDER BY added_at DESC"
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"خطأ في الحصول على القنوات: {e}")
            return []
    
    def delete_channel(self, channel_id: str) -> bool:
        """حذف قناة"""
        try:
            self.execute_query(
                "DELETE FROM forced_channels WHERE channel_id = ?",
                (channel_id,),
                commit=True
            )
            logger.info(f"✅ تم حذف القناة: {channel_id}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في حذف القناة {channel_id}: {e}")
            return False
    
    # --- نظام الدفع بالنجوم المحسن ---
    
    def add_star_payment(self, payment_id: str, user_id: int, stars: int, points: int, 
                        provider: str = "telegram", status: str = "completed") -> bool:
        """إضافة عملية دفع بالنجوم"""
        try:
            self.begin_transaction()
            
            self.execute_query(
                """INSERT INTO star_payments 
                (payment_id, user_id, stars, points, timestamp, status, provider) 
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (payment_id, user_id, stars, points, datetime.now().isoformat(), status, provider),
                commit=False
            )
            
            self.commit_transaction()
            logger.info(f"✅ تم تسجيل عملية دفع: {payment_id} - {stars} نجوم")
            return True
            
        except Exception as e:
            self.rollback_transaction()
            logger.error(f"❌ خطأ في تسجيل عملية الدفع {payment_id}: {e}")
            return False
    
    # --- نظام الإذاعة المحسن ---
    
    def add_broadcast(self, message: str, media_type: str, media_file_id: str, 
                     sent_by: int, total_users: int) -> int:
        """إضافة إذاعة جديدة"""
        try:
            self.execute_query(
                """INSERT INTO broadcasts 
                (message, media_type, media_file_id, sent_by, total_users, timestamp) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (message[:500], media_type, media_file_id, sent_by, total_users, datetime.now().isoformat()),
                commit=True
            )
            broadcast_id = self.cursor.lastrowid
            logger.info(f"✅ تم إنشاء إذاعة #{broadcast_id}")
            return broadcast_id
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء إذاعة: {e}")
            return -1
    
    def update_broadcast_stats(self, broadcast_id: int, sent_count: int, failed_count: int):
        """تحديث إحصائيات الإذاعة"""
        try:
            self.execute_query(
                """UPDATE broadcasts 
                SET sent_to = ?, failed_to = ?, completed = 1 
                WHERE id = ?""",
                (sent_count, failed_count, broadcast_id),
                commit=True
            )
        except Exception as e:
            logger.error(f"خطأ في تحديث إحصائيات الإذاعة #{broadcast_id}: {e}")
    
    # --- إحصائيات وتحليلات متقدمة ---
    
    def get_global_stats(self) -> tuple:
        """الحصول على إحصائيات عامة"""
        try:
            # عدد المستخدمين النشطين
            self.cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 0")
            users_count = self.cursor.fetchone()[0] or 0
            
            # مجموع النقاط
            self.cursor.execute("SELECT SUM(points) FROM users WHERE is_banned = 0")
            total_points = self.cursor.fetchone()[0] or 0
            
            # عدد العمليات
            self.cursor.execute("SELECT COUNT(*) FROM transactions")
            total_tx = self.cursor.fetchone()[0]
            
            # النجوم المشتراة
            self.cursor.execute("SELECT SUM(stars) FROM star_payments WHERE status = 'completed'")
            total_stars = self.cursor.fetchone()[0] or 0
            
            # العمليات في آخر 24 ساعة
            cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            self.cursor.execute("SELECT COUNT(*) FROM transactions WHERE timestamp > ?", (cutoff,))
            last_24h_tx = self.cursor.fetchone()[0]
            
            return users_count, total_points, total_tx, total_stars, last_24h_tx
            
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإحصائيات: {e}")
            return 0, 0, 0, 0, 0
    
    def get_new_users_stats(self, days: int = 1) -> int:
        """الحصول على عدد المستخدمين الجدد"""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            self.cursor.execute(
                "SELECT COUNT(*) FROM users WHERE joined_date > ? AND is_banned = 0",
                (cutoff,)
            )
            return self.cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"خطأ في الحصول على إحصائيات المستخدمين الجدد: {e}")
            return 0
    
    def get_top_rich_users(self, limit: int = 10):
        """الحصول على أغنى المستخدمين"""
        try:
            self.cursor.execute(
                """SELECT user_id, username, full_name, points 
                FROM users 
                WHERE is_banned = 0 
                ORDER BY points DESC 
                LIMIT ?""",
                (limit,)
            )
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"خطأ في الحصول على أغنى المستخدمين: {e}")
            return []
    
    def get_all_users(self, exclude_banned: bool = True):
        """الحصول على جميع المستخدمين"""
        try:
            query = "SELECT user_id, username, full_name, points FROM users"
            if exclude_banned:
                query += " WHERE is_banned = 0"
            
            self.cursor.execute(query)
            return self.cursor.fetchall()
        except Exception as e:
            logger.error(f"خطأ في الحصول على جميع المستخدمين: {e}")
            return []
    
    # --- إدارة الإعدادات ---
    
    def get_setting(self, key: str):
        """الحصول على إعداد"""
        try:
            self.cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            result = self.cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإعداد {key}: {e}")
            return None
    
    def set_setting(self, key: str, value: str):
        """تحديث إعداد"""
        try:
            self.execute_query(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (str(value), datetime.now().isoformat(), key),
                commit=True
            )
        except Exception as e:
            logger.error(f"خطأ في تحديث الإعداد {key}: {e}")
    
    # --- نظام الأكواد ---
    
    def create_promo_code(self, code: str, points: int, max_uses: int, created_by: int, expires_days: int = 30) -> bool:
        """إنشاء كود جديد"""
        try:
            expires_at = None
            if expires_days > 0:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
            
            self.execute_query(
                """INSERT INTO promo_codes 
                (code, points, max_uses, created_by, expires_at) 
                VALUES (?, ?, ?, ?, ?)""",
                (code, points, max_uses, created_by, expires_at),
                commit=True
            )
            logger.info(f"✅ تم إنشاء كود: {code} - {points} نقطة")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء الكود {code}: {e}")
            return False
    
    def redeem_promo_code(self, user_id: int, code: str):
        """استبدال كود"""
        try:
            self.begin_transaction()
            
            # التحقق من وجود الكود
            self.cursor.execute(
                """SELECT points, max_uses, current_uses, active, expires_at 
                FROM promo_codes WHERE code = ?""",
                (code,)
            )
            res = self.cursor.fetchone()
            
            if not res:
                return "not_found"
            
            points, max_uses, current_uses, active, expires_at = res
            
            # التحقق من الصلاحية
            if not active:
                return "expired"
            
            if current_uses >= max_uses:
                return "expired"
            
            if expires_at and datetime.fromisoformat(expires_at) < datetime.now():
                return "expired"
            
            # التحقق من الاستخدام السابق
            self.cursor.execute(
                "SELECT id FROM code_usage WHERE user_id = ? AND code = ?",
                (user_id, code)
            )
            if self.cursor.fetchone():
                return "used"
            
            # تنفيذ العملية
            self.execute_query(
                "UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?",
                (code,),
                commit=False
            )
            
            self.execute_query(
                "INSERT INTO code_usage (user_id, code) VALUES (?, ?)",
                (user_id, code),
                commit=False
            )
            
            # إضافة النقاط
            self.update_points(user_id, points, "code", f"كود: {code}")
            
            self.commit_transaction()
            logger.info(f"✅ تم استبدال الكود {code} للمستخدم {user_id}")
            return points
            
        except Exception as e:
            self.rollback_transaction()
            logger.error(f"❌ خطأ في استبدال الكود {code}: {e}")
            return "error"
    
    def cleanup_old_data(self):
        """تنظيف البيانات القديمة"""
        try:
            # حذف الأكواد المنتهية
            cutoff = datetime.now().isoformat()
            self.execute_query(
                "DELETE FROM promo_codes WHERE expires_at < ? AND expires_at IS NOT NULL",
                (cutoff,),
                commit=True
            )
            
            # حذف سجلات الدفع القديمة (أكثر من 90 يوم)
            old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            self.execute_query(
                "DELETE FROM star_payments WHERE timestamp < ?",
                (old_date,),
                commit=True
            )
            
            logger.info("✅ تم تنظيف البيانات القديمة")
        except Exception as e:
            logger.error(f"خطأ في تنظيف البيانات: {e}")

db = DatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ أدوات مساعدة محسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_link(user_id: int, name: str) -> str:
    """إنشاء رابط للمستخدم"""
    return f"<a href='tg://user?id={user_id}'>{html.escape(name)}</a>"

def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """إنشاء لوحة المفاتيح الرئيسية"""
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

def check_maintenance_mode(user_id: int) -> bool:
    """التحقق من وضع الصيانة"""
    if user_id == ADMIN_ID:
        return False
    return db.get_setting("maintenance_mode") == "1"

def is_admin(user_id: int) -> bool:
    """التحقق إذا كان المستخدم أدمن"""
    return user_id == ADMIN_ID

def format_number(num: int) -> str:
    """تنسيق الأرقام"""
    return f"{num:,}"

def clean_context_data(context: ContextTypes.DEFAULT_TYPE, keys: list = None):
    """تنظيف البيانات من context"""
    if keys:
        for key in keys:
            context.user_data.pop(key, None)
    else:
        context.user_data.clear()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات الرئيسية المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start"""
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
    
    # التحقق من وجود المستخدم
    db_user = db.get_user(user.id)
    if not db_user:
        referrer_id = None
        if args and args[0].startswith("invite_"):
            try:
                inviter = int(args[0].split("_")[1])
                if inviter != user.id:
                    referrer_id = inviter
            except (ValueError, IndexError):
                pass
        
        # تسجيل المستخدم
        success = db.add_user(user.id, user.username or "", user.first_name or "مستخدم", "None", referrer_id)
        
        if success and referrer_id:
            referral_points = int(db.get_setting("referral_points") or 10)
            db.update_points(referrer_id, referral_points, "referral", f"دعوة: {user.first_name}")
            
            # إرسال إشعار للمشير
            try:
                msg = f"🔔 <b>إحالة جديدة!</b>\nحصلت على {referral_points} نقاط لدعوة {user.first_name}"
                await context.bot.send_message(referrer_id, msg, parse_mode="HTML")
            except Exception:
                pass
    
    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """إرسال لوحة التحكم"""
    user = update.effective_user
    
    # التحقق من وضع الصيانة
    if check_maintenance_mode(user.id):
        if update.callback_query:
            await update.callback_query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    # الحصول على بيانات المستخدم
    db_user = db.get_user(user.id)
    if not db_user:
        await start(update, context)
        return
    
    points = db_user[4]
    username = db_user[1] or "لا يوجد"
    
    text = (
        f"مرحباً بك {get_user_link(user.id, user.first_name)} 👋\n\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"📛 اليوزر: @{username}\n"
        f"🏆 الرصيد: <b>{format_number(points)} نقطة</b>\n"
        f"📅 تاريخ الانضمام: {db_user[7][:10] if db_user[7] else 'غير معروف'}\n"
        f"────────────────\n"
        f"👇 اختر من القائمة أدناه:"
    )
    
    kb = get_main_keyboard(user.id)
    
    try:
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ في إرسال لوحة التحكم: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💫 نظام الدفع التلقائي المحسن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def buy_stars_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج شراء النجوم"""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    if check_maintenance_mode(user_id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    # تعريف الباقات
    packages = {
        "buy_5": {"stars": 5, "points": 50, "title": "5 نجوم (50 نقطة)"},
        "buy_10": {"stars": 10, "points": 120, "title": "10 نجوم (120 نقطة)"}
    }
    
    if data not in packages:
        return
    
    package = packages[data]
    
    if not PAYMENT_PROVIDER_TOKEN:
        await query.edit_message_text(
            "❌ نظام الدفع غير مفعل حالياً.\n"
            "يرجى التواصل مع الإدارة للشراء اليدوي.",
            parse_mode="HTML"
        )
        return
    
    # إنشاء فاتورة
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
            start_parameter="stars_payment",
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False
        )
        
        logger.info(f"فاتورة إنشأت للمستخدم {user_id}: {package['stars']} نجوم")
        
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ: {str(e)[:100]}")
        logger.error(f"خطأ في إنشاء الفاتورة: {e}")

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الدفع"""
    query = update.pre_checkout_query
    
    try:
        # التحقق من صحة البايلود
        if not query.invoice_payload.startswith("stars_"):
            await query.answer(ok=False, error_message="فاتورة غير صالحة")
            return
        
        # التحقق من عدم تكرار الدفع
        payment_id = query.invoice_payload
        existing = db.get_star_payment(payment_id)
        if existing:
            await query.answer(ok=False, error_message="تم استخدام هذه الفاتورة مسبقاً")
            return
        
        await query.answer(ok=True)
        
    except Exception as e:
        logger.error(f"خطأ في التحقق من الدفع: {e}")
        await query.answer(ok=False, error_message="حدث خطأ في التحقق")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الدفع الناجح"""
    try:
        payment = update.message.successful_payment
        payload = payment.invoice_payload
        
        # تحليل البايلود
        parts = payload.split("_")
        if len(parts) < 5:
            raise ValueError("بايلود غير صالح")
        
        stars = int(parts[1])
        points = int(parts[2])
        user_id = int(parts[3])
        
        # التحقق من المستخدم الفعلي
        if update.effective_user.id != user_id:
            logger.warning(f"مستخدم {update.effective_user.id} يحاول استخدام فاتورة لـ {user_id}")
            return
        
        # تسجيل عملية الدفع
        success = db.add_star_payment(
            payment_id=payment.provider_payment_id,
            user_id=user_id,
            stars=stars,
            points=points,
            provider="telegram"
        )
        
        if not success:
            raise Exception("فشل في تسجيل عملية الدفع")
        
        # إضافة النقاط للمستخدم
        db.update_points(user_id, points, "buy", f"شراء بالنجوم: {stars} نجمة")
        
        # إشعار الأدمن
        try:
            admin_msg = (
                f"💰 <b>عملية شراء ناجحة!</b>\n\n"
                f"👤 المستخدم: {get_user_link(user_id, update.effective_user.first_name)}\n"
                f"🆔 الآيدي: <code>{user_id}</code>\n"
                f"⭐ النجوم: {stars}\n"
                f"🎯 النقاط: {points}\n"
                f"💳 المبلغ: {payment.total_amount / 100} نجوم\n"
                f"📊 الرصيد الجديد: {db.get_user(user_id)[4]:,} نقطة"
            )
            await context.bot.send_message(ADMIN_ID, admin_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن: {e}")
        
        # تأكيد للمستخدم
        await update.message.reply_text(
            f"✅ <b>تمت العملية بنجاح!</b>\n\n"
            f"تم إضافة <b>{points} نقطة</b> لحسابك.\n"
            f"رصيدك الحالي: <b>{db.get_user(user_id)[4]:,} نقطة</b>\n\n"
            f"شكراً لثقتك! 🎉",
            parse_mode="HTML"
        )
        
        logger.info(f"دفع ناجح للمستخدم {user_id}: {stars} نجوم -> {points} نقطة")
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الدفع الناجح: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ في معالجة الدفع.\n"
            "يرجى التواصل مع الإدارة.",
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ لوحة تحكم الأدمن المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم الأدمن"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على الإحصائيات
    stats = db.get_global_stats()
    new_users_today = db.get_new_users_stats(1)
    new_users_week = db.get_new_users_stats(7)
    
    maintenance_status = "🔴 معطل" if db.get_setting("maintenance_mode") == "0" else "🟢 مفعل"
    
    text = (
        f"⚙️ <b>لوحة التحكم الشاملة</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 👥 المستخدمين: {format_number(stats[0])}\n"
        f"• 📈 مستخدمين اليوم: {format_number(new_users_today)}\n"
        f"• 📆 مستخدمين الأسبوع: {format_number(new_users_week)}\n"
        f"• 💰 النقاط الكلية: {format_number(stats[1])}\n"
        f"• ⭐ النجوم المشتراة: {format_number(stats[3])}\n"
        f"• 📊 العمليات (24س): {format_number(stats[4])}\n"
        f"• 🔧 وضع الصيانة: {maintenance_status}\n\n"
        f"👇 اختر القسم المطلوب:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels"),
         InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data="admin_settings"),
         InlineKeyboardButton("💰 إدارة النقاط", callback_data="admin_points")],
        [InlineKeyboardButton("📤 نظام الإذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📈 الإحصائيات المتقدمة", callback_data="admin_analytics"),
         InlineKeyboardButton("🎫 إدارة الأكواد", callback_data="admin_codes")],
        [InlineKeyboardButton("🔧 وضع الصيانة", callback_data="admin_toggle_maintenance"),
         InlineKeyboardButton("🧹 تنظيف البيانات", callback_data="admin_cleanup")],
        [InlineKeyboardButton("🔙 خروج", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📢 إدارة القنوات المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة القنوات"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    channels = db.get_channels()
    text = "📢 <b>إدارة القنوات الإجبارية</b>\n\n"
    
    if channels:
        for i, (channel_id, link, active) in enumerate(channels, 1):
            status = "🟢 مفعل" if active else "🔴 معطل"
            text += f"{i}. {link} (<code>{channel_id}</code>) - {status}\n"
    else:
        text += "لا توجد قنوات مضافة.\n"
    
    kb_buttons = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_channel")],
        [InlineKeyboardButton("🔄 تعديل قناة", callback_data="admin_edit_channel_menu"),
         InlineKeyboardButton("🔧 تفعيل/تعطيل", callback_data="admin_toggle_channel_menu")]
    ]
    
    if channels:
        kb_buttons.append([InlineKeyboardButton("🗑️ حذف قناة", callback_data="admin_delete_channel_menu")])
    
    kb_buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة قناة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    await query.edit_message_text(
        "📝 <b>إضافة قناة جديدة</b>\n\n"
        "أرسل الآن <b>آيدي القناة</b> (مثال: @channel_name أو -1001234567890):\n\n"
        "⚠️ ملاحظة: يجب أن يكون البوت أدمن في القناة!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_channels")]])
    )
    return STATE_CHANNEL_ID

async def admin_get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آيدي القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_id = update.message.text.strip()
    
    # التحقق من صحة الآيدي
    if not channel_id.startswith('@') and not channel_id.startswith('-100'):
        await update.message.reply_text(
            "❌ صيغة الآيدي غير صحيحة!\n"
            "يجب أن يبدأ بـ @ أو -100\n\n"
            "أعد إرسال الآيدي:"
        )
        return STATE_CHANNEL_ID
    
    context.user_data['new_channel_id'] = channel_id
    
    await update.message.reply_text(
        "✅ تم حفظ الآيدي.\n"
        "الآن أرسل <b>رابط القناة</b> (مثال: https://t.me/channel_name):",
        parse_mode="HTML"
    )
    return STATE_CHANNEL_LINK

async def admin_get_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على رابط القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_link = update.message.text.strip()
    channel_id = context.user_data.get('new_channel_id')
    
    # التحقق من صحة الرابط
    if not channel_link.startswith('https://t.me/'):
        await update.message.reply_text(
            "❌ الرابط غير صحيح!\n"
            "يجب أن يبدأ بـ https://t.me/\n\n"
            "أعد إرسال الرابط:"
        )
        return STATE_CHANNEL_LINK
    
    # إضافة القناة
    if db.add_channel(channel_id, channel_link, update.effective_user.id):
        await update.message.reply_text(
            f"✅ تمت إضافة القناة بنجاح!\n\n"
            f"🆔: <code>{channel_id}</code>\n"
            f"🔗: {channel_link}",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ فشل في إضافة القناة!")
    
    clean_context_data(context, ['new_channel_id'])
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👤 إدارة المستخدمين المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة المستخدمين"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    text = (
        "👤 <b>إدارة المستخدمين</b>\n\n"
        "اختر طريقة البحث عن المستخدم:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بحث بالآيدي", callback_data="admin_search_by_id"),
         InlineKeyboardButton("🔍 بحث بالاسم", callback_data="admin_search_by_name")],
        [InlineKeyboardButton("📊 عرض جميع المستخدمين", callback_data="admin_list_users")],
        [InlineKeyboardButton("📈 عرض الأغنياء", callback_data="admin_show_rich")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_search_by_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث بالآيدي"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    await query.edit_message_text(
        "🔍 <b>البحث عن مستخدم بالآيدي</b>\n\n"
        "أرسل الآن <b>آيدي المستخدم</b> (أرقام فقط):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_users")]])
    )
    return STATE_USER_SEARCH

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن مستخدم"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    search_input = update.message.text.strip()
    
    try:
        # البحث بالآيدي
        user_id = int(search_input)
        user = db.get_user(user_id)
        
        if not user:
            # البحث بالاسم
            all_users = db.get_all_users()
            for u in all_users:
                if search_input.lower() in (u[2] or "").lower():
                    user = u
                    break
    
    except ValueError:
        # البحث بالاسم
        all_users = db.get_all_users()
        user = None
        for u in all_users:
            if search_input.lower() in (u[2] or "").lower():
                user = u
                break
    
    if not user:
        await update.message.reply_text("❌ المستخدم غير موجود!")
        return STATE_USER_SEARCH
    
    # حفظ بيانات المستخدم
    context.user_data['managed_user'] = user[0]
    context.user_data['managed_user_name'] = user[2]
    context.user_data['managed_user_data'] = user
    
    # عرض بيانات المستخدم
    text = (
        f"✅ <b>تم العثور على المستخدم:</b>\n\n"
        f"👤 الاسم: {user[2] or 'غير معروف'}\n"
        f"🆔 الآيدي: <code>{user[0]}</code>\n"
        f"📛 اليوزر: @{user[1] or 'لا يوجد'}\n"
        f"💰 النقاط: {format_number(user[4])}\n"
        f"📅 تاريخ التسجيل: {user[7][:10] if user[7] else 'غير معروف'}\n"
        f"🚫 الحالة: {'محظور' if user[8] == 1 else 'نشط'}\n"
        f"💎 مجموع المكتسب: {format_number(user[10])}\n"
        f"💸 مجموع المنفق: {format_number(user[11])}"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_add_points"),
         InlineKeyboardButton("➖ خصم نقاط", callback_data="admin_deduct_points")],
        [InlineKeyboardButton("🚫 حظر", callback_data="admin_ban_user"),
         InlineKeyboardButton("✅ فك الحظر", callback_data="admin_unban_user")],
        [InlineKeyboardButton("📜 عرض السجل", callback_data="admin_view_history"),
         InlineKeyboardButton("🔄 تحديث البيانات", callback_data="admin_refresh_user")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_users")]
    ])
    
    await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    return STATE_USER_MANAGE

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 نظام الإذاعة المتطور المحسن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإذاعة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    text = (
        "📤 <b>نظام الإذاعة المتطور</b>\n\n"
        "يمكنك إرسال رسالة لجميع المستخدمين مع خيارات متقدمة:\n\n"
        "🔸 <b>خيارات الإرسال:</b>\n"
        "• 📝 نص فقط\n"
        "• 🖼️ صورة مع نص\n"
        "• 🎬 فيديو مع نص\n"
        "• 📁 ملف مع نص\n\n"
        "🔸 <b>ميزات إضافية:</b>\n"
        "• 📌 تثبيت الرسالة عند المستخدمين\n"
        "• ⏱️ تأخير ذكي بين الإرسالات\n"
        "• 📊 متابعة الإحصائيات فورياً"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إذاعة نصية", callback_data="broadcast_text"),
         InlineKeyboardButton("🖼️ إذاعة بالصورة", callback_data="broadcast_photo")],
        [InlineKeyboardButton("🎬 إذاعة بالفيديو", callback_data="broadcast_video"),
         InlineKeyboardButton("📁 إذاعة بملف", callback_data="broadcast_document")],
        [InlineKeyboardButton("📊 إحصائيات الإذاعات", callback_data="broadcast_stats"),
         InlineKeyboardButton("⚙️ إعدادات الإذاعة", callback_data="broadcast_settings")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء الإذاعة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    media_type = query.data.replace("broadcast_", "")
    context.user_data['broadcast_media'] = media_type
    
    await query.edit_message_text(
        "📝 <b>إرسال الرسالة</b>\n\n"
        "أرسل الآن نص الرسالة:\n"
        "(يمكنك استخدام HTML للتنسيق)\n\n"
        "⚠️ <b>ملاحظات:</b>\n"
        "• يمكنك استخدام الوسوم: <b>عريض</b>, <i>مائل</i>, <code>كود</code>\n"
        "• يمكنك استخدام الروابط: <a href='رابط'>نص</a>\n"
        "• الحد الأقصى: 1000 حرف",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_broadcast")]])
    )
    return STATE_BROADCAST_MESSAGE

async def admin_get_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على نص الإذاعة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    message = update.message.text
    context.user_data['broadcast_message'] = message
    
    media_type = context.user_data.get('broadcast_media', 'text')
    
    if media_type == "text":
        # مباشرة لعرض خيارات الإرسال
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، أرسل الآن", callback_data="broadcast_send_yes"),
             InlineKeyboardButton("📌 نعم مع تثبيت", callback_data="broadcast_pin_yes")],
            [InlineKeyboardButton("❌ لا، عدل الرسالة", callback_data="broadcast_edit"),
             InlineKeyboardButton("🔙 إلغاء", callback_data="admin_broadcast")]
        ])
        
        await update.message.reply_text(
            f"📋 <b>معاينة الرسالة:</b>\n\n{message}\n\n"
            f"هل تريد إرسال هذه الرسالة لجميع المستخدمين؟",
            parse_mode="HTML",
            reply_markup=kb
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            f"✅ تم حفظ النص.\n"
            f"الآن أرسل الـ{media_type}:\n"
            f"(الصورة / الفيديو / الملف)\n\n"
            f"⚠️ <b>ملاحظة:</b>\n"
            f"• للصورة: أرسل صورة واحدة\n"
            f"• للفيديو: أرسل فيديو واحد\n"
            f"• للملف: أرسل ملف واحد"
        )
        return STATE_BROADCAST_MEDIA

async def admin_get_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على الوسائط للإذاعة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    media_type = context.user_data.get('broadcast_media')
    file_id = None
    
    try:
        if media_type == "photo" and update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif media_type == "video" and update.message.video:
            file_id = update.message.video.file_id
        elif media_type == "document" and update.message.document:
            file_id = update.message.document.file_id
        
        if not file_id:
            raise ValueError("نوع الملف غير صحيح")
        
        context.user_data['broadcast_file_id'] = file_id
        
        # عرض خيارات الإرسال
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، أرسل الآن", callback_data="broadcast_send_yes"),
             InlineKeyboardButton("📌 نعم مع تثبيت", callback_data="broadcast_pin_yes")],
            [InlineKeyboardButton("❌ لا، عدل الرسالة", callback_data="broadcast_edit"),
             InlineKeyboardButton("🔙 إلغاء", callback_data="admin_broadcast")]
        ])
        
        # معاينة الرسالة
        message_preview = context.user_data.get('broadcast_message', '')
        if len(message_preview) > 100:
            message_preview = message_preview[:97] + "..."
        
        await update.message.reply_text(
            f"📋 <b>معاينة الإذاعة:</b>\n\n"
            f"📝 النص: {message_preview}\n"
            f"📁 الوسائط: {media_type}\n\n"
            f"هل تريد إرسال هذه الإذاعة لجميع المستخدمين؟",
            parse_mode="HTML",
            reply_markup=kb
        )
        return ConversationHandler.END
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ نوع الملف غير صحيح!\n"
            f"يرجى إرسال {media_type} صالح.\n\n"
            f"أعد إرسال {media_type}:"
        )
        return STATE_BROADCAST_MEDIA

async def admin_send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال الإذاعة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على بيانات الإذاعة
    message = context.user_data.get('broadcast_message', '')
    media_type = context.user_data.get('broadcast_media', 'text')
    file_id = context.user_data.get('broadcast_file_id')
    
    # تحديد إذا كان تثبيت
    pin_message = query.data == "broadcast_pin_yes"
    
    # الحصول على جميع المستخدمين
    all_users = db.get_all_users()
    total_users = len(all_users)
    
    if total_users == 0:
        await query.edit_message_text("❌ لا يوجد مستخدمين لإرسال الرسالة لهم!")
        clean_context_data(context, ['broadcast_message', 'broadcast_media', 'broadcast_file_id'])
        return ConversationHandler.END
    
    # إنشاء سجل للإذاعة
    broadcast_id = db.add_broadcast(
        message=message,
        media_type=media_type,
        media_file_id=file_id or "",
        sent_by=query.from_user.id,
        total_users=total_users
    )
    
    if broadcast_id == -1:
        await query.edit_message_text("❌ فشل في إنشاء سجل الإذاعة!")
        clean_context_data(context, ['broadcast_message', 'broadcast_media', 'broadcast_file_id'])
        return ConversationHandler.END
    
    # إعداد الرسالة التقدمية
    progress_msg = await query.edit_message_text(
        f"⏳ <b>جاري إرسال الإذاعة...</b>\n\n"
        f"📊 الإحصائيات:\n"
        f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
        f"• ✅ تم إرسال: 0\n"
        f"• ❌ فشل: 0\n"
        f"• 📌 التثبيت: {'نعم' if pin_message else 'لا'}\n"
        f"• ⏱️ الوقت المتبقي: حساب...",
        parse_mode="HTML"
    )
    
    sent_count = 0
    failed_count = 0
    failed_users = []
    
    # حساب التأخير بين الإرسالات
    broadcast_delay = float(db.get_setting("broadcast_delay") or 0.1)
    max_users_per_broadcast = int(db.get_setting("max_broadcast_users") or 50)
    
    # إرسال الرسالة لكل مستخدم مع تأخير ذكي
    for i, (user_id, username, full_name, points) in enumerate(all_users, 1):
        try:
            # التحقق من حظر المستخدم
            if db.is_banned(user_id):
                failed_count += 1
                failed_users.append(f"{full_name} ({user_id}) - محظور")
                continue
            
            if media_type == "text":
                msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                if pin_message:
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=user_id,
                            message_id=msg.message_id,
                            disable_notification=True
                        )
                    except:
                        pass  # قد لا يملك البوت صلاحية التثبيت
                    
            elif media_type == "photo":
                msg = await context.bot.send_photo(
                    chat_id=user_id,
                    photo=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=user_id,
                            message_id=msg.message_id,
                            disable_notification=True
                        )
                    except:
                        pass
                    
            elif media_type == "video":
                msg = await context.bot.send_video(
                    chat_id=user_id,
                    video=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=user_id,
                            message_id=msg.message_id,
                            disable_notification=True
                        )
                    except:
                        pass
                    
            elif media_type == "document":
                msg = await context.bot.send_document(
                    chat_id=user_id,
                    document=file_id,
                    caption=message,
                    parse_mode="HTML"
                )
                if pin_message:
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=user_id,
                            message_id=msg.message_id,
                            disable_notification=True
                        )
                    except:
                        pass
            
            sent_count += 1
            
        except Exception as e:
            error_msg = str(e)
            if "Forbidden" in error_msg or "blocked" in error_msg.lower():
                error_msg = "المستخدم حظر البوت"
            elif "Chat not found" in error_msg:
                error_msg = "الدردشة غير موجودة"
            
            failed_count += 1
            failed_users.append(f"{full_name} ({user_id}) - {error_msg}")
        
        # تحديث الرسالة التقدمية كل 10 مستخدمين أو عند الانتهاء
        if i % 10 == 0 or i == total_users:
            progress = int((i / total_users) * 100)
            remaining = total_users - i
            estimated_time = remaining * broadcast_delay
            
            # تحويل الوقت المتبقي
            if estimated_time < 60:
                time_str = f"{int(estimated_time)} ثانية"
            elif estimated_time < 3600:
                minutes = int(estimated_time / 60)
                seconds = int(estimated_time % 60)
                time_str = f"{minutes} دقيقة {seconds} ثانية"
            else:
                hours = int(estimated_time / 3600)
                minutes = int((estimated_time % 3600) / 60)
                time_str = f"{hours} ساعة {minutes} دقيقة"
            
            await progress_msg.edit_text(
                f"⏳ <b>جاري إرسال الإذاعة...</b>\n\n"
                f"📊 الإحصائيات:\n"
                f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
                f"• ✅ تم إرسال: {format_number(sent_count)} ({progress}%)\n"
                f"• ❌ فشل: {format_number(failed_count)}\n"
                f"• 📌 التثبيت: {'نعم' if pin_message else 'لا'}\n"
                f"• ⏱️ الوقت المتبقي: {time_str if i < total_users else 'مكتمل'}",
                parse_mode="HTML"
            )
        
        # تأخير ذكي بين الإرسالات
        if i < total_users:
            await asyncio.sleep(broadcast_delay)
            
            # تقييد عدد الإرسالات المتزامنة
            if i % max_users_per_broadcast == 0 and i < total_users:
                await asyncio.sleep(2)  # استراحة قصيرة
    
    # تحديث إحصائيات الإذاعة
    db.update_broadcast_stats(broadcast_id, sent_count, failed_count)
    
    # عرض النتائج النهائية
    result_text = (
        f"✅ <b>تم إكمال الإذاعة!</b>\n\n"
        f"📊 <b>النتائج النهائية:</b>\n"
        f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
        f"• ✅ تم الإرسال بنجاح: {format_number(sent_count)}\n"
        f"• ❌ فشل في الإرسال: {format_number(failed_count)}\n"
        f"• 📌 تم التثبيت: {'نعم' if pin_message else 'لا'}\n"
        f"• 🆔 رقم الإذاعة: #{broadcast_id}\n\n"
    )
    
    if failed_users and failed_count <= 20:
        result_text += "<b>بعض المستخدمين الذين فشل الإرسال لهم:</b>\n"
        for j, user_info in enumerate(failed_users[:20], 1):
            result_text += f"{j}. {user_info}\n"
    
    # إضافة زر لإعادة المحاولة للمستخدمين الفاشلين
    kb_buttons = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_broadcast")]]
    
    if failed_count > 0:
        kb_buttons.insert(0, [InlineKeyboardButton("🔄 إعادة إرسال للفاشلين", callback_data=f"retry_failed_{broadcast_id}")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    
    await progress_msg.edit_text(result_text, reply_markup=kb, parse_mode="HTML")
    
    # تنظيف البيانات المؤقتة
    clean_context_data(context, ['broadcast_message', 'broadcast_media', 'broadcast_file_id'])
    
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📈 الإحصائيات المتقدمة المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_analytics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإحصائيات المتقدمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
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
        f"• 👥 إجمالي المستخدمين: {format_number(users_count)}\n"
        f"• 📈 مستخدمين اليوم: {format_number(new_users_today)}\n"
        f"• 📆 مستخدمين الأسبوع: {format_number(new_users_week)}\n"
        f"• 💰 النقاط الكلية: {format_number(total_points)}\n"
        f"• ⭐ النجوم المشتراة: {format_number(total_stars)}\n"
        f"• 📊 العمليات (24س): {format_number(last_24h_tx)}\n\n"
    )
    
    # عرض الأغنياء
    if rich_users:
        text += f"🏆 <b>أكثر 10 مستخدمين ثراءً:</b>\n"
        for i, (user_id, username, full_name, points) in enumerate(rich_users, 1):
            name_display = full_name or username or f"User {user_id}"
            text += f"{i}. {name_display[:20]} - {format_number(points)} نقطة\n"
        text += "\n"
    
    # عرض أفضل المشيرين
    if top_referrers:
        text += f"👥 <b>أفضل 5 مشيرين:</b>\n"
        for i, (user_data, count) in enumerate(top_referrers, 1):
            name_display = user_data[2] or user_data[1] or f"User {user_data[0]}"
            text += f"{i}. {name_display[:20]} - {count} إحالة\n"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث الإحصائيات", callback_data="admin_analytics")],
        [InlineKeyboardButton("📊 تفاصيل إضافية", callback_data="admin_detailed_stats")],
        [InlineKeyboardButton("📈 رسوم بيانية", callback_data="admin_charts")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 وضع الصيانة المحسن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل وضع الصيانة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    current = db.get_setting("maintenance_mode")
    new_val = "0" if current == "1" else "1"
    db.set_setting("maintenance_mode", new_val)
    
    status = "مفعل" if new_val == "1" else "معطل"
    await query.edit_message_text(f"✅ تم {status} وضع الصيانة.")
    
    # إذا تم تفعيل وضع الصيانة، إرسال إشعار لجميع المستخدمين النشطين
    if new_val == "1":
        all_users = db.get_all_users()
        notification_count = 0
        
        for user_id, _, full_name, _ in all_users:
            try:
                await context.bot.send_message(
                    user_id,
                    "🔧 <b>إشعار هام</b>\n\n"
                    "البوت سيدخل في وضع الصيانة لفترة قصيرة.\n"
                    "سيعود للعمل قريبًا بإذن الله.\n\n"
                    "شكرًا لتفهمكم. 🙏",
                    parse_mode="HTML"
                )
                notification_count += 1
                await asyncio.sleep(0.05)  # تأخير لتجنب حظر التلغرام
            except Exception as e:
                logger.error(f"فشل إرسال إشعار صيانة لـ {user_id}: {e}")
                continue
        
        logger.info(f"✅ تم إرسال {notification_count} إشعار صيانة")
    
    await admin_panel(update, context)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🧹 تنظيف البيانات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_cleanup_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظيف البيانات القديمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    await query.answer()
    
    try:
        # تنفيذ التنظيف
        db.cleanup_old_data()
        
        await query.edit_message_text(
            "✅ <b>تم تنظيف البيانات القديمة بنجاح!</b>\n\n"
            "تم حذف:\n"
            "• الأكواد المنتهية الصلاحية\n"
            "• سجلات الدفع القديمة (أكثر من 90 يوم)\n\n"
            "تم تحسين أداء قاعدة البيانات.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ حدث خطأ أثناء التنظيف: {str(e)}")
    
    await asyncio.sleep(2)
    await admin_panel(update, context)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي المحسن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    
    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة معالجة الأخطاء العامة
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالج الأخطاء العام"""
        logger.error(f"حدث خطأ: {context.error}", exc_info=context.error)
        
        try:
            # إرسال رسالة خطأ للمستخدم
            if update and update.effective_user:
                error_msg = (
                    "❌ حدث خطأ غير متوقع.\n"
                    "يرجى المحاولة مرة أخرى لاحقاً.\n\n"
                    "إذا استمر الخطأ، تواصل مع الدعم الفني."
                )
                
                if update.callback_query:
                    await update.callback_query.message.reply_text(error_msg)
                elif update.message:
                    await update.message.reply_text(error_msg)
        
        except Exception as e:
            logger.error(f"خطأ في معالجة الخطأ: {e}")
    
    application.add_error_handler(error_handler)
    
    # معالجات المحادثات
    
    # 1. محادثة تحويل النقاط
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)],
        },
        fallbacks=[CallbackQueryHandler(cancel_transfer, pattern="^cancel_transfer$")],
        allow_reentry=True
    )
    
    # 2. محادثة استبدال الأكواد
    redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")],
        states={
            STATE_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)]
        },
        fallbacks=[CallbackQueryHandler(cancel_redeem, pattern="^cancel_redeem$")],
        allow_reentry=True
    )
    
    # 3. محادثة إنشاء الأكواد (للأدمن)
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_start_create_code, pattern="^admin_create_code$")],
        states={
            STATE_CREATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_code)]
        },
        fallbacks=[CallbackQueryHandler(admin_cancel_code, pattern="^admin_cancel_code$")],
        allow_reentry=True
    )
    
    # 4. محادثة إدارة القنوات
    channels_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_channel_start, pattern="^admin_add_channel$")],
        states={
            STATE_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_id)],
            STATE_CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_link)]
        },
        fallbacks=[CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$")],
        allow_reentry=True
    )
    
    # 5. محادثة إدارة المستخدمين
    users_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_search_by_id_start, pattern="^admin_search_by_id$"),
            CallbackQueryHandler(admin_search_by_name_start, pattern="^admin_search_by_name$")
        ],
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
        fallbacks=[CallbackQueryHandler(admin_users_menu, pattern="^admin_users$")],
        allow_reentry=True
    )
    
    # 6. محادثة الإذاعة المتطورة
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
            CallbackQueryHandler(admin_send_broadcast, pattern="^broadcast_(send|pin)_yes$"),
            CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast$"),
            CallbackQueryHandler(admin_broadcast_menu, pattern="^broadcast_edit$"),
            CallbackQueryHandler(admin_broadcast_menu, pattern="^broadcast_cancel$")
        ],
        allow_reentry=True
    )
    
    # 7. محادثة تعديل الإعدادات
    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_settings_menu, pattern="^admin_settings$")],
        states={
            STATE_SETTINGS_MENU: [
                CallbackQueryHandler(admin_change_setting, pattern="^admin_set_(tax|daily|referral|min|welcome|broadcast|max_users)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_setting)
            ]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")],
        allow_reentry=True
    )
    
    # تسجيل المعالجات
    
    # الأمر الأساسي
    application.add_handler(CommandHandler("start", start))
    
    # محادثات المستخدمين
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    
    # محادثات الأدمن
    application.add_handler(create_code_conv)
    application.add_handler(channels_conv)
    application.add_handler(users_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(settings_conv)
    
    # معالجات الأزرار العامة
    application.add_handler(CallbackQueryHandler(main_callback_handler, 
        pattern="^(main_menu|attack_menu|collect_points|referral_page|daily_bonus|buy_points_menu|buy_manual_.*|history|support)$"))
    
    # معالجات الأزرار الإدارية
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$"))
    application.add_handler(CallbackQueryHandler(admin_users_menu, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_analytics_menu, pattern="^admin_analytics$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_maintenance, pattern="^admin_toggle_maintenance$"))
    application.add_handler(CallbackQueryHandler(admin_cleanup_data, pattern="^admin_cleanup$"))
    application.add_handler(CallbackQueryHandler(admin_codes_menu, pattern="^admin_codes$"))
    
    # معالجات الدفع بالنجوم
    if PAYMENT_PROVIDER_TOKEN:
        application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
        application.add_handler(CallbackQueryHandler(buy_stars_handler, pattern="^buy_(5|10)$"))
    
    # تشغيل البوت
    print("\n" + "="*50)
    print("🤖 بوت النقاط المتطور")
    print("="*50)
    print(f"🆔 الأدمن: {ADMIN_ID}")
    print(f"🔧 وضع الصيانة: {'🟢 مفعل' if db.get_setting('maintenance_mode') == '1' else '🔴 معطل'}")
    print(f"⭐ نظام الدفع: {'🟢 مفعل' if PAYMENT_PROVIDER_TOKEN else '🔴 معطل'}")
    print(f"📊 عدد المستخدمين: {db.get_global_stats()[0]:,}")
    print("="*50)
    print("✅ البوت يعمل بكفاءة عالية...")
    print("="*50 + "\n")
    
    # تشغيل البوت
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=0.5,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"خطأ فادح في تشغيل البوت: {e}")
        print(f"❌ خطأ فادح: {e}")