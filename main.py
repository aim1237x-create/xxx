import logging
import sqlite3
import html
import time
import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any, Union
import json
import aiosqlite
from concurrent.futures import ThreadPoolExecutor
import threading
from collections import defaultdict

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
    ConversationHandler,
    CallbackContext
)
from telegram.error import Forbidden, BadRequest, TimedOut, NetworkError

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة المحسنة - القراءة من متغيرات البيئة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# توكن البوت موضوع مباشرة (بدون ENV)
BOT_TOKEN = "7637690071:AAE-MZYASnMZx3iq52aheHbDcq9yE2VQUjk"
ADMIN_ID = 8287678319
PAYMENT_PROVIDER_TOKEN = ""

# مراحل المحادثات (Conversation States)
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
STATE_SUPPORT_TICKET = 14
STATE_ADMIN_REPLY = 15
STATE_CODE_EXPIRY = 16
STATE_POINTS_AMOUNT = 17
STATE_CONFIRM_ACTION = 18
STATE_ADD_POINTS, STATE_DEDUCT_POINTS = range(19, 21)

# إعدادات التحقق من القنوات
CHECK_CHANNELS_INTERVAL = 300
CHANNEL_CHECK_TIMEOUT = 10

# إعدادات نظام الإذاعة المحسنة
BROADCAST_DELAY_MIN = 0.1
BROADCAST_DELAY_MAX = 0.3
BROADCAST_BATCH_SIZE = 30
BROADCAST_BATCH_DELAY = 1.0

# إعدادات نظام التخزين المؤقت
CACHE_TTL = 120  # 2 دقائق للتخزين المؤقت

# نظام Rate Limiting
RATE_LIMIT_WINDOW = 1  # ثانية واحدة
MAX_REQUESTS_PER_WINDOW = 5  # 5 طلبات في الثانية

# التحقق من الاتصال
DATABASE_CONNECTION_TIMEOUT = 30

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات المتقدم مع Connection Pool وWAL Mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AsyncDatabaseManager:
    def __init__(self, db_name="bot_data.db"):
        self.db_name = db_name
        self.connection_pool = []
        self.pool_size = 5
        self.pool_lock = threading.Lock()
        self.cache = {}
        self.cache_timestamps = {}
        self.executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="DBThread")
        self.user_last_activity = {}
        self.rate_limit_data = defaultdict(list)
        
    def init_database_sync(self):
        """تهيئة قاعدة البيانات بشكل متزامن مع WAL Mode"""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False, timeout=30)
            cursor = conn.cursor()
            
            # تفعيل WAL Mode لمنع مشاكل القفل
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-2000")  # 2MB cache
            cursor.execute("PRAGMA foreign_keys=ON")
            
            self.create_tables_sync(cursor)
            self.create_indices_sync(cursor)
            self.init_settings_sync(cursor)
            
            conn.commit()
            conn.close()
            logger.info("✅ قاعدة البيانات مهيأة بنجاح مع WAL Mode")
        except Exception as e:
            logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
            raise
    
    def create_tables_sync(self, cursor):
        """إنشاء الجداول مع تحسينات متقدمة"""
        tables = [
            # جدول المستخدمين مع تحسينات متقدمة
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
                warnings INTEGER DEFAULT 0,
                subscription_checked TEXT,
                language TEXT DEFAULT 'ar',
                privacy_level INTEGER DEFAULT 1,
                last_channel_check TEXT,
                is_active INTEGER DEFAULT 1,
                rate_limit_count INTEGER DEFAULT 0,
                last_rate_limit_reset TEXT,
                FOREIGN KEY (referrer_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول العمليات مع تحسينات متقدمة
            '''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                details TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                related_user_id INTEGER,
                status TEXT DEFAULT 'completed',
                ip_address TEXT,
                device_info TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الأكواد مع تحسينات متقدمة
            '''
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                points INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                description TEXT,
                min_points_required INTEGER DEFAULT 0,
                category TEXT DEFAULT 'general'
            )
            ''',
            
            # جدول استخدام الأكواد
            '''
            CREATE TABLE IF NOT EXISTS code_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                code TEXT,
                used_at TEXT DEFAULT CURRENT_TIMESTAMP,
                points_received INTEGER,
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
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                data_type TEXT DEFAULT 'string',
                options TEXT
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
                last_check TEXT,
                required_subscription INTEGER DEFAULT 1,
                channel_name TEXT,
                member_count INTEGER DEFAULT 0,
                bot_is_admin INTEGER DEFAULT 0
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
                invoice_payload TEXT,
                telegram_payment_charge_id TEXT,
                provider_payment_charge_id TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الإذاعات مع تحسينات متقدمة
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
                completed INTEGER DEFAULT 0,
                broadcast_type TEXT DEFAULT 'instant',
                scheduled_time TEXT,
                status TEXT DEFAULT 'sent',
                tags TEXT
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
                total_referrals INTEGER DEFAULT 0,
                daily_active_users INTEGER DEFAULT 0,
                revenue_estimate REAL DEFAULT 0.0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            ''',
            
            # جدول تذاكر الدعم
            '''
            CREATE TABLE IF NOT EXISTS support_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                message TEXT,
                status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                admin_reply TEXT,
                replied_by INTEGER,
                replied_at TEXT,
                category TEXT DEFAULT 'general',
                attachments TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الإشعارات
            '''
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                notification_type TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                related_id INTEGER,
                action_url TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول أنشطة البوت
            '''
            CREATE TABLE IF NOT EXISTS bot_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_type TEXT,
                user_id INTEGER,
                details TEXT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT
            )
            ''',
            
            # جدول الجلسات
            '''
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_activity TEXT,
                expires_at TEXT,
                device_info TEXT,
                ip_address TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول الإحالات المتقدم
            '''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                status TEXT DEFAULT 'active',
                points_earned INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                converted_at TEXT,
                conversion_value INTEGER DEFAULT 0,
                FOREIGN KEY (referrer_id) REFERENCES users(user_id),
                FOREIGN KEY (referred_id) REFERENCES users(user_id),
                UNIQUE(referrer_id, referred_id)
            )
            ''',
            
            # جدول التحويلات المحسنة
            '''
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER,
                receiver_id INTEGER,
                amount INTEGER,
                fee INTEGER DEFAULT 0,
                tax INTEGER DEFAULT 0,
                net_amount INTEGER,
                status TEXT DEFAULT 'completed',
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                transaction_hash TEXT,
                FOREIGN KEY (sender_id) REFERENCES users(user_id),
                FOREIGN KEY (receiver_id) REFERENCES users(user_id)
            )
            ''',
            
            # جدول Rate Limiting
            '''
            CREATE TABLE IF NOT EXISTS rate_limits (
                user_id INTEGER PRIMARY KEY,
                request_count INTEGER DEFAULT 0,
                last_reset TEXT,
                warning_count INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            '''
        ]
        
        for table_sql in tables:
            try:
                cursor.execute(table_sql)
            except Exception as e:
                logger.error(f"خطأ في إنشاء الجدول: {e}")
    
    def create_indices_sync(self, cursor):
        """إنشاء indices متقدمة لتحسين أداء الاستعلامات"""
        indices = [
            "CREATE INDEX IF NOT EXISTS idx_users_referrer ON users(referrer_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_banned ON users(is_banned)",
            "CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)",
            "CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type)",
            "CREATE INDEX IF NOT EXISTS idx_code_usage_user ON code_usage(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_code_usage_code ON code_usage(code)",
            "CREATE INDEX IF NOT EXISTS idx_star_payments_user ON star_payments(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_star_payments_status ON star_payments(status)",
            "CREATE INDEX IF NOT EXISTS idx_star_payments_timestamp ON star_payments(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_broadcasts_timestamp ON broadcasts(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_broadcasts_status ON broadcasts(status)",
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_user ON support_tickets(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status)",
            "CREATE INDEX IF NOT EXISTS idx_support_tickets_priority ON support_tickets(priority DESC)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read)",
            "CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)",
            "CREATE INDEX IF NOT EXISTS idx_referrals_referred ON referrals(referred_id)",
            "CREATE INDEX IF NOT EXISTS idx_transfers_sender ON transfers(sender_id)",
            "CREATE INDEX IF NOT EXISTS idx_transfers_receiver ON transfers(receiver_id)",
            "CREATE INDEX IF NOT EXISTS idx_transfers_timestamp ON transfers(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_bot_stats_date ON bot_stats(date)"
        ]
        
        for index_sql in indices:
            try:
                cursor.execute(index_sql)
            except Exception as e:
                logger.error(f"خطأ في إنشاء index: {e}")
    
    def init_settings_sync(self, cursor):
        """تهيئة الإعدادات الافتراضية المتقدمة"""
        default_settings = [
            ("tax_percent", "25", "نسبة الضريبة على التحويلات", "integer", "0,50"),
            ("show_leaderboard", "1", "عرض لوحة المتصدرين", "boolean", "0,1"),
            ("maintenance_mode", "0", "وضع الصيانة", "boolean", "0,1"),
            ("daily_bonus_amount", "5", "قيمة المكافأة اليومية", "integer", "0,1000"),
            ("referral_points", "10", "نقاط الإحالة", "integer", "0,1000"),
            ("min_transfer", "10", "الحد الأدنى للتحويل", "integer", "1,10000"),
            ("welcome_points", "20", "نقاط الترحيب", "integer", "0,1000"),
            ("max_transfer_per_day", "1000", "الحد الأقصى للتحويل يومياً", "integer", "100,100000"),
            ("broadcast_delay", "0.1", "التأخير بين الإرسالات في الإذاعة", "float", "0.05,2.0"),
            ("max_broadcast_users", "50", "الحد الأقصى للمستخدمين في الإذاعة الواحدة", "integer", "10,1000"),
            ("check_channels_interval", "300", "فترة التحقق من القنوات بالثواني", "integer", "60,3600"),
            ("conversation_timeout", "300", "مهلة المحادثات بالثواني", "integer", "60,1800"),
            ("max_warnings", "3", "الحد الأقصى للتحذيرات قبل الحظر", "integer", "1,10"),
            ("points_per_star", "10", "النقاط مقابل كل نجمة", "integer", "1,1000"),
            ("enable_star_payments", "1", "تفعيل الدفع بالنجوم", "boolean", "0,1"),
            ("force_channel_subscription", "1", "إجبار الاشتراك في القنوات", "boolean", "0,1"),
            ("enable_daily_bonus", "1", "تفعيل المكافأة اليومية", "boolean", "0,1"),
            ("enable_referral_system", "1", "تفعيل نظام الإحالة", "boolean", "0,1"),
            ("auto_cleanup_days", "90", "عدد أيام الاحتفاظ بالسجلات", "integer", "30,365"),
            ("backup_interval_hours", "24", "فترة النسخ الاحتياطي بالساعات", "integer", "6,168"),
            ("rate_limit_enabled", "1", "تفعيل نظام Rate Limiting", "boolean", "0,1"),
            ("inactive_user_days", "30", "عدد أيام عدم النشاط للحساب غير الفعال", "integer", "7,365"),
            ("max_points_per_user", "1000000", "الحد الأقصى للنقاط للمستخدم الواحد", "integer", "10000,10000000")
        ]
        
        for key, val, desc, data_type, options in default_settings:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO settings (key, value, description, data_type, options) VALUES (?, ?, ?, ?, ?)",
                    (key, val, desc, data_type, options)
                )
            except Exception as e:
                logger.error(f"خطأ في إضافة الإعداد: {e}")
    
    async def get_connection(self):
        """الحصول على اتصال من البركة مع إدارة الأخطاء"""
        try:
            conn = await aiosqlite.connect(self.db_name, timeout=DATABASE_CONNECTION_TIMEOUT)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = aiosqlite.Row
            return conn
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء اتصال قاعدة البيانات: {e}")
            raise
    
    async def execute_query(self, query: str, params: tuple = (), commit: bool = False, 
                          use_cache: bool = False, cache_key: str = None, retry_count: int = 0):
        """تنفيذ استعلام بأمان مع إعادة المحاولة"""
        if use_cache and cache_key:
            cached_data = self.get_cached_data(cache_key)
            if cached_data is not None:
                return cached_data
        
        max_retries = 3
        try:
            conn = await self.get_connection()
            async with conn:
                async with conn.execute(query, params) as cursor:
                    result = await cursor.fetchall()
                    if commit:
                        await conn.commit()
                    
                    if use_cache and cache_key:
                        self.set_cached_data(cache_key, result)
                    
                    return result
        except aiosqlite.OperationalError as e:
            if "database is locked" in str(e) and retry_count < max_retries:
                await asyncio.sleep(0.1 * (retry_count + 1))
                return await self.execute_query(query, params, commit, use_cache, cache_key, retry_count + 1)
            logger.error(f"❌ خطأ في قاعدة البيانات: {e} - الاستعلام: {query[:100]}")
            raise
        except Exception as e:
            logger.error(f"❌ خطأ في قاعدة البيانات: {e} - الاستعلام: {query[:100]}")
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
            logger.error(f"❌ خطأ في قاعدة البيانات: {e} - الاستعلام: {query[:100]}")
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
            logger.error(f"❌ خطأ في قاعدة البيانات: {e} - الاستعلام: {query[:100]}")
            raise
    
    # --- نظام التخزين المؤقت ---
    
    def get_cached_data(self, key: str):
        """الحصول على بيانات مخزنة مؤقتاً"""
        if key in self.cache:
            timestamp = self.cache_timestamps.get(key, 0)
            if time.time() - timestamp < CACHE_TTL:
                return self.cache[key]
            else:
                del self.cache[key]
                del self.cache_timestamps[key]
        return None
    
    def set_cached_data(self, key: str, data):
        """تخزين بيانات مؤقتاً"""
        self.cache[key] = data
        self.cache_timestamps[key] = time.time()
    
    def clear_cache(self, key: str = None):
        """مسح التخزين المؤقت"""
        if key:
            if key in self.cache:
                del self.cache[key]
            if key in self.cache_timestamps:
                del self.cache_timestamps[key]
        else:
            self.cache.clear()
            self.cache_timestamps.clear()
    
    # --- نظام Rate Limiting المتقدم ---
    
    async def check_rate_limit(self, user_id: int) -> tuple:
        """التحقق من Rate Limiting مع تحديث قاعدة البيانات"""
        try:
            if not await self.get_setting("rate_limit_enabled", 1):
                return True, ""
            
            now = time.time()
            window_start = now - RATE_LIMIT_WINDOW
            
            # تنظيف البيانات القديمة
            self.rate_limit_data[user_id] = [t for t in self.rate_limit_data[user_id] if t > window_start]
            
            # إضافة الطلب الحالي
            self.rate_limit_data[user_id].append(now)
            
            # التحقق من الحد
            if len(self.rate_limit_data[user_id]) > MAX_REQUESTS_PER_WINDOW:
                # تحديث تحذيرات المستخدم في قاعدة البيانات
                await self.execute_update(
                    "UPDATE users SET warnings = warnings + 1 WHERE user_id = ?",
                    (user_id,)
                )
                
                # تسجيل النشاط
                await self.execute_update(
                    """INSERT INTO bot_activities 
                    (activity_type, user_id, details, timestamp) 
                    VALUES (?, ?, ?, ?)""",
                    ("rate_limit_exceeded", user_id, f"تجاوز حد الطلبات: {len(self.rate_limit_data[user_id])} طلب", datetime.now().isoformat())
                )
                
                remaining_time = RATE_LIMIT_WINDOW - (now - self.rate_limit_data[user_id][0])
                return False, f"⏱️ تجاوزت الحد المسموح. يرجى الانتظار {remaining_time:.1f} ثانية"
            
            return True, ""
        except Exception as e:
            logger.error(f"خطأ في نظام Rate Limiting: {e}")
            return True, ""  # في حالة الخطأ، نسمح بالطلب
    
    async def reset_rate_limit(self, user_id: int):
        """إعادة تعيين Rate Limiting للمستخدم"""
        if user_id in self.rate_limit_data:
            self.rate_limit_data[user_id] = []
    
    # --- عمليات المستخدم المتقدمة ---
    
    async def add_user(self, user_id: int, username: str, full_name: str, phone: str = "None", referrer_id: int = None) -> bool:
        """إضافة مستخدم جديد بأمان مع معاملات متقدمة"""
        try:
            conn = await self.get_connection()
            async with conn:
                await conn.execute("BEGIN TRANSACTION")
                
                # التحقق من عدم وجود المستخدم مسبقاً
                existing_user = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
                existing = await existing_user.fetchone()
                if existing:
                    await conn.execute("ROLLBACK")
                    return False
                
                welcome_points = int(await self.get_setting("welcome_points") or 20)
                date = datetime.now().isoformat()
                
                await conn.execute(
                    """INSERT INTO users 
                    (user_id, username, full_name, phone, points, referrer_id, joined_date, last_active) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, username, full_name, phone, welcome_points, referrer_id, date, date)
                )
                
                # تسجيل عملية الترحيب
                await conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, details) 
                    VALUES (?, ?, ?, ?)""",
                    (user_id, welcome_points, "🎁 مكافأة", "نقاط ترحيب")
                )
                
                # تحديث إحصائيات المستخدم
                await conn.execute(
                    "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                    (welcome_points, user_id)
                )
                
                # إذا كان هناك مشير، تسجيل الإحالة
                if referrer_id:
                    referral_points = int(await self.get_setting("referral_points") or 10)
                    await conn.execute(
                        """INSERT INTO referrals 
                        (referrer_id, referred_id, status, points_earned) 
                        VALUES (?, ?, ?, ?)""",
                        (referrer_id, user_id, "active", referral_points)
                    )
                    
                    # إضافة نقاط للمشير
                    await conn.execute(
                        "UPDATE users SET points = points + ? WHERE user_id = ?",
                        (referral_points, referrer_id)
                    )
                    
                    await conn.execute(
                        """INSERT INTO transactions 
                        (user_id, amount, type, details, related_user_id) 
                        VALUES (?, ?, ?, ?, ?)""",
                        (referrer_id, referral_points, "👥 إحالة", f"دعوة: {full_name}", user_id)
                    )
                    
                    await conn.execute(
                        "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                        (referral_points, referrer_id)
                    )
                
                # تسجيل نشاط البوت
                await conn.execute(
                    """INSERT INTO bot_activities 
                    (activity_type, user_id, details, timestamp) 
                    VALUES (?, ?, ?, ?)""",
                    ("user_join", user_id, f"انضمام مستخدم جديد: {full_name}", date)
                )
                
                await conn.commit()
                logger.info(f"✅ تم إضافة مستخدم جديد: {user_id} - {full_name}")
                
                # مسح التخزين المؤقت
                self.clear_cache(f"user_{user_id}")
                self.clear_cache("users_count")
                self.clear_cache("new_users_today")
                
                return True
                
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المستخدم {user_id}: {e}")
            return False
    
    async def get_user(self, user_id: int, use_cache: bool = True):
        """الحصول على بيانات مستخدم مع التخزين المؤقت"""
        cache_key = f"user_{user_id}"
        if use_cache:
            cached_data = self.get_cached_data(cache_key)
            if cached_data is not None:
                return cached_data
        
        try:
            result = await self.execute_query_one(
                """SELECT user_id, username, full_name, phone, points, referrer_id, 
                last_daily_bonus, joined_date, is_banned, last_active, 
                total_earned, total_spent, warnings, subscription_checked,
                language, privacy_level, last_channel_check, is_active
                FROM users WHERE user_id = ?""",
                (user_id,)
            )
            
            if result and use_cache:
                self.set_cached_data(cache_key, result)
            
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على بيانات المستخدم {user_id}: {e}")
            return None
    
    async def update_points(self, user_id: int, amount: int, reason: str, details: str = "", related_user_id: int = None):
        """تحديث نقاط المستخدم بأمان مع معاملات متقدمة"""
        try:
            conn = await self.get_connection()
            async with conn:
                await conn.execute("BEGIN TRANSACTION")
                
                # التحقق من وجود المستخدم
                user_cursor = await conn.execute("SELECT points, is_banned FROM users WHERE user_id = ?", (user_id,))
                user = await user_cursor.fetchone()
                if not user:
                    await conn.execute("ROLLBACK")
                    raise ValueError(f"المستخدم {user_id} غير موجود")
                
                # التحقق من عدم وجود سالب إذا كان الخصم
                if amount < 0 and user['points'] + amount < 0:
                    await conn.execute("ROLLBACK")
                    raise ValueError("رصيد المستخدم غير كافي")
                
                # التحقق من حظر المستخدم
                if user['is_banned'] == 1 and amount > 0:
                    await conn.execute("ROLLBACK")
                    raise ValueError("المستخدم محظور")
                
                # تحديث النقاط
                await conn.execute(
                    "UPDATE users SET points = points + ? WHERE user_id = ?",
                    (amount, user_id)
                )
                
                # تحديث الإحصائيات
                if amount > 0:
                    await conn.execute(
                        "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                        (amount, user_id)
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET total_spent = total_spent + ABS(?) WHERE user_id = ?",
                        (amount, user_id)
                    )
                
                # تحديث وقت النشاط الأخير
                await conn.execute(
                    "UPDATE users SET last_active = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id)
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
                    "admin_deduct": "👑 خصم من الأدمن",
                    "withdrawal": "🏧 سحب",
                    "refund": "↩️ استرداد",
                    "penalty": "⚠️ غرامة",
                    "reward": "🏆 مكافأة",
                    "correction": "✏️ تصحيح"
                }
                
                tx_type = tx_type_map.get(reason, "❓ غير معروف")
                
                await conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, details, related_user_id) 
                    VALUES (?, ?, ?, ?, ?)""",
                    (user_id, amount, tx_type, details, related_user_id)
                )
                
                await conn.commit()
                logger.info(f"✅ تم تحديث نقاط المستخدم {user_id}: {amount:+d} ({reason})")
                
                # مسح التخزين المؤقت
                self.clear_cache(f"user_{user_id}")
                self.clear_cache(f"user_history_{user_id}")
                
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث نقاط المستخدم {user_id}: {e}")
            raise
    
    async def ban_user(self, user_id: int, reason: str = "", banned_by: int = None):
        """حظر مستخدم مع تسجيل السبب"""
        try:
            await self.execute_update(
                "UPDATE users SET is_banned = 1, is_active = 0 WHERE user_id = ?",
                (user_id,)
            )
            
            # تسجيل نشاط الحظر
            await self.execute_update(
                """INSERT INTO bot_activities 
                (activity_type, user_id, details, timestamp) 
                VALUES (?, ?, ?, ?)""",
                ("user_ban", user_id, f"حظر مستخدم - السبب: {reason} - المحظِر: {banned_by}", datetime.now().isoformat())
            )
            
            logger.info(f"✅ تم حظر المستخدم {user_id} - السبب: {reason}")
            self.clear_cache(f"user_{user_id}")
        except Exception as e:
            logger.error(f"❌ خطأ في حظر المستخدم {user_id}: {e}")
    
    async def unban_user(self, user_id: int, unbanned_by: int = None):
        """فك حظر مستخدم"""
        try:
            await self.execute_update(
                "UPDATE users SET is_banned = 0, is_active = 1 WHERE user_id = ?",
                (user_id,)
            )
            
            # تسجيل نشاط فك الحظر
            await self.execute_update(
                """INSERT INTO bot_activities 
                (activity_type, user_id, details, timestamp) 
                VALUES (?, ?, ?, ?)""",
                ("user_unban", user_id, f"فك حظر مستخدم - المفعِل: {unbanned_by}", datetime.now().isoformat())
            )
            
            logger.info(f"✅ تم فك حظر المستخدم {user_id}")
            self.clear_cache(f"user_{user_id}")
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
    
    async def get_history(self, user_id: int, limit: int = 10, offset: int = 0):
        """الحصول على سجل العمليات مع ترقيم الصفحات"""
        try:
            result = await self.execute_query(
                """SELECT amount, type, details, timestamp 
                FROM transactions 
                WHERE user_id = ? 
                ORDER BY id DESC 
                LIMIT ? OFFSET ?""",
                (user_id, limit, offset)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على سجل المستخدم {user_id}: {e}")
            return []
    
    # --- نظام التحقق من القنوات المتقدم مع التخزين المؤقت ---
    
    async def check_channel_subscription(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple:
        """التحقق من اشتراك المستخدم في القنوات الإجبارية مع التخزين المؤقت"""
        cache_key = f"channel_check_{user_id}"
        cached_result = self.get_cached_data(cache_key)
        if cached_result:
            return cached_result
        
        try:
            # التحقق من تفعيل النظام
            force_subscription = await self.get_setting("force_channel_subscription")
            if not force_subscription or force_subscription != "1":
                result = (True, "")
                self.set_cached_data(cache_key, result)
                return result
            
            channels = await self.get_channels(active_only=True)
            if not channels:
                result = (True, "")
                self.set_cached_data(cache_key, result)
                return result
            
            unsubscribed_channels = []
            for channel in channels:
                channel_id = channel['channel_id']
                try:
                    # التحقق من وجود البوت كأدمن في القناة
                    bot_is_admin = channel.get('bot_is_admin', 0)
                    if bot_is_admin == 0:
                        try:
                            bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
                            if bot_member.status in ['administrator', 'creator']:
                                await self.execute_update(
                                    "UPDATE forced_channels SET bot_is_admin = 1 WHERE channel_id = ?",
                                    (channel_id,)
                                )
                                bot_is_admin = 1
                            else:
                                await self.execute_update(
                                    "UPDATE forced_channels SET bot_is_admin = 0 WHERE channel_id = ?",
                                    (channel_id,)
                                )
                        except Exception as e:
                            logger.error(f"البوت ليس أدمن في القناة {channel_id}: {e}")
                            continue
                    
                    # التحقق من عضوية المستخدم
                    chat_member = await context.bot.get_chat_member(channel_id, user_id)
                    if chat_member.status in ['left', 'kicked']:
                        channel_link = channel['channel_link']
                        channel_name = channel['channel_name'] or "القناة"
                        unsubscribed_channels.append(f"• {channel_name}: {channel_link}")
                except Forbidden:
                    # المستخدم حظر البوت أو البوت ليس أدمن
                    logger.warning(f"لا يمكن الوصول للقناة {channel_id} - قد يكون البوت محظوراً")
                    continue
                except BadRequest as e:
                    logger.error(f"طلب غير صالح للقناة {channel_id}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"خطأ في التحقق من عضوية القناة {channel_id}: {e}")
                    continue
            
            if unsubscribed_channels:
                message = (
                    "⚠️ <b>يجب الاشتراك في القنوات التالية أولاً:</b>\n\n"
                    + "\n".join(unsubscribed_channels) +
                    "\n\n✅ بعد الاشتراك، أرسل /start"
                )
                result = (False, message)
            else:
                result = (True, "")
                
                # تحديث وقت آخر تحقق
                await self.execute_update(
                    "UPDATE users SET last_channel_check = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id)
                )
            
            # تخزين النتيجة مؤقتاً لمدة دقيقتين
            self.set_cached_data(cache_key, result)
            return result
            
        except Exception as e:
            logger.error(f"خطأ في التحقق من اشتراك القنوات للمستخدم {user_id}: {e}")
            # في حالة الخطأ، نسمح للمستخدم بالمتابعة لمنع حجب الخدمة
            return (True, "")
    
    async def add_channel(self, channel_id: str, channel_link: str, added_by: int, channel_name: str = "") -> bool:
        """إضافة قناة جديدة مع معلومات إضافية"""
        try:
            await self.execute_update(
                """INSERT OR REPLACE INTO forced_channels 
                (channel_id, channel_link, added_by, added_at, channel_name) 
                VALUES (?, ?, ?, ?, ?)""",
                (channel_id, channel_link, added_by, datetime.now().isoformat(), channel_name)
            )
            logger.info(f"✅ تم إضافة قناة: {channel_id} - {channel_name}")
            self.clear_cache("channels_all")
            self.clear_cache("channels_active")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة القناة {channel_id}: {e}")
            return False
    
    async def update_channel(self, channel_id: str, channel_link: str = None, channel_name: str = None) -> bool:
        """تحديث معلومات القناة"""
        try:
            updates = []
            params = []
            
            if channel_link:
                updates.append("channel_link = ?")
                params.append(channel_link)
            
            if channel_name:
                updates.append("channel_name = ?")
                params.append(channel_name)
            
            if not updates:
                return False
            
            params.append(channel_id)
            
            query = f"UPDATE forced_channels SET {', '.join(updates)} WHERE channel_id = ?"
            await self.execute_update(query, tuple(params))
            
            logger.info(f"✅ تم تحديث القناة: {channel_id}")
            self.clear_cache("channels_all")
            self.clear_cache("channels_active")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث القناة {channel_id}: {e}")
            return False
    
    async def toggle_channel(self, channel_id: str, active: bool) -> bool:
        """تفعيل/تعطيل القناة"""
        try:
            await self.execute_update(
                "UPDATE forced_channels SET is_active = ? WHERE channel_id = ?",
                (1 if active else 0, channel_id)
            )
            status = "تفعيل" if active else "تعطيل"
            logger.info(f"✅ تم {status} القناة: {channel_id}")
            self.clear_cache("channels_all")
            self.clear_cache("channels_active")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في {status} القناة {channel_id}: {e}")
            return False
    
    async def get_channels(self, active_only: bool = False):
        """الحصول على جميع القنوات"""
        try:
            query = "SELECT channel_id, channel_link, is_active, channel_name, bot_is_admin FROM forced_channels"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY added_at DESC"
            
            result = await self.execute_query(query)
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على القنوات: {e}")
            return []
    
    async def delete_channel(self, channel_id: str) -> bool:
        """حذف قناة"""
        try:
            await self.execute_update(
                "DELETE FROM forced_channels WHERE channel_id = ?",
                (channel_id,)
            )
            logger.info(f"✅ تم حذف القناة: {channel_id}")
            self.clear_cache("channels_all")
            self.clear_cache("channels_active")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في حذف القناة {channel_id}: {e}")
            return False
    
    # --- نظام الدفع بالنجوم المتقدم ---
    
    async def add_star_payment(self, payment_id: str, user_id: int, stars: int, points: int, 
                              provider: str = "telegram", status: str = "completed",
                              invoice_payload: str = "", telegram_payment_charge_id: str = "",
                              provider_payment_charge_id: str = "") -> bool:
        """إضافة عملية دفع بالنجوم مع معلومات مفصلة"""
        try:
            await self.execute_update(
                """INSERT INTO star_payments 
                (payment_id, user_id, stars, points, timestamp, status, provider,
                invoice_payload, telegram_payment_charge_id, provider_payment_charge_id) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payment_id, user_id, stars, points, datetime.now().isoformat(), status, provider,
                 invoice_payload, telegram_payment_charge_id, provider_payment_charge_id)
            )
            
            logger.info(f"✅ تم تسجيل عملية دفع: {payment_id} - {stars} نجوم -> {points} نقطة")
            self.clear_cache(f"user_{user_id}")
            self.clear_cache("total_stars")
            return True
            
        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل عملية الدفع {payment_id}: {e}")
            return False
    
    async def get_star_payment(self, payment_id: str):
        """الحصول على معلومات عملية دفع"""
        try:
            result = await self.execute_query_one(
                "SELECT * FROM star_payments WHERE payment_id = ?",
                (payment_id,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على معلومات الدفع {payment_id}: {e}")
            return None
    
    # --- نظام الإذاعة المتقدم ---
    
    async def add_broadcast(self, message: str, media_type: str, media_file_id: str, 
                           sent_by: int, total_users: int, broadcast_type: str = "instant",
                           scheduled_time: str = None, tags: str = None) -> int:
        """إضافة إذاعة جديدة مع خيارات متقدمة"""
        try:
            result = await self.execute_query_one(
                """INSERT INTO broadcasts 
                (message, media_type, media_file_id, sent_by, total_users, timestamp,
                broadcast_type, scheduled_time, tags) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (message[:1000], media_type, media_file_id, sent_by, total_users, 
                 datetime.now().isoformat(), broadcast_type, scheduled_time, tags)
            )
            
            # الحصول على المعرف
            last_id = await self.execute_query_one("SELECT last_insert_rowid()")
            broadcast_id = last_id[0] if last_id else -1
            
            logger.info(f"✅ تم إنشاء إذاعة #{broadcast_id}")
            return broadcast_id
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء إذاعة: {e}")
            return -1
    
    async def update_broadcast_stats(self, broadcast_id: int, sent_count: int, failed_count: int, status: str = "completed"):
        """تحديث إحصائيات الإذاعة"""
        try:
            await self.execute_update(
                """UPDATE broadcasts 
                SET sent_to = ?, failed_to = ?, completed = 1, status = ?
                WHERE id = ?""",
                (sent_count, failed_count, status, broadcast_id)
            )
        except Exception as e:
            logger.error(f"خطأ في تحديث إحصائيات الإذاعة #{broadcast_id}: {e}")
    
    async def get_broadcast_stats(self, broadcast_id: int):
        """الحصول على إحصائيات إذاعة"""
        try:
            result = await self.execute_query_one(
                "SELECT * FROM broadcasts WHERE id = ?",
                (broadcast_id,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على إحصائيات الإذاعة #{broadcast_id}: {e}")
            return None
    
    # --- نظام الأكواد المتقدم ---
    
    async def create_promo_code(self, code: str, points: int, max_uses: int, created_by: int, 
                               expires_days: int = 30, description: str = "", 
                               min_points_required: int = 0, category: str = "general") -> bool:
        """إنشاء كود جديد مع خيارات متقدمة"""
        try:
            expires_at = None
            if expires_days > 0:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
            
            await self.execute_update(
                """INSERT INTO promo_codes 
                (code, points, max_uses, created_by, expires_at, description,
                min_points_required, category) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (code, points, max_uses, created_by, expires_at, description,
                 min_points_required, category)
            )
            logger.info(f"✅ تم إنشاء كود: {code} - {points} نقطة")
            self.clear_cache("promo_codes_all")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء الكود {code}: {e}")
            return False
    
    async def redeem_promo_code(self, user_id: int, code: str) -> Union[int, str]:
        """استبدال كود مع معالجة أخطاء متقدمة"""
        try:
            conn = await self.get_connection()
            async with conn:
                await conn.execute("BEGIN TRANSACTION")
                
                # التحقق من وجود الكود
                code_cursor = await conn.execute(
                    """SELECT points, max_uses, current_uses, active, expires_at, 
                    min_points_required FROM promo_codes WHERE code = ?""",
                    (code,)
                )
                res = await code_cursor.fetchone()
                
                if not res:
                    await conn.execute("ROLLBACK")
                    return "not_found"
                
                points = res['points']
                max_uses = res['max_uses']
                current_uses = res['current_uses']
                active = res['active']
                expires_at = res['expires_at']
                min_points_required = res['min_points_required']
                
                # التحقق من الصلاحية
                if not active:
                    await conn.execute("ROLLBACK")
                    return "expired"
                
                if current_uses >= max_uses:
                    await conn.execute("ROLLBACK")
                    return "expired"
                
                # التحقق من تاريخ الانتهاء
                if expires_at:
                    try:
                        expires_date = datetime.fromisoformat(expires_at)
                        if expires_date < datetime.now():
                            await conn.execute("ROLLBACK")
                            return "expired"
                    except ValueError as e:
                        logger.error(f"خطأ في تنسيق تاريخ الانتهاء للكود {code}: {e}")
                        await conn.execute("ROLLBACK")
                        return "error"
                
                # التحقق من الحد الأدنى للنقاط المطلوبة
                if min_points_required > 0:
                    user_cursor = await conn.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
                    user_points = await user_cursor.fetchone()
                    if user_points and user_points['points'] < min_points_required:
                        await conn.execute("ROLLBACK")
                        return "min_points"
                
                # التحقق من الاستخدام السابق
                usage_cursor = await conn.execute(
                    "SELECT id FROM code_usage WHERE user_id = ? AND code = ?",
                    (user_id, code)
                )
                if await usage_cursor.fetchone():
                    await conn.execute("ROLLBACK")
                    return "used"
                
                # تنفيذ العملية
                await conn.execute(
                    "UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?",
                    (code,)
                )
                
                await conn.execute(
                    "INSERT INTO code_usage (user_id, code, points_received) VALUES (?, ?, ?)",
                    (user_id, code, points)
                )
                
                # إضافة النقاط
                await conn.execute(
                    "UPDATE users SET points = points + ? WHERE user_id = ?",
                    (points, user_id)
                )
                
                await conn.execute(
                    "UPDATE users SET total_earned = total_earned + ? WHERE user_id = ?",
                    (points, user_id)
                )
                
                # تسجيل العملية
                await conn.execute(
                    """INSERT INTO transactions 
                    (user_id, amount, type, details) 
                    VALUES (?, ?, ?, ?)""",
                    (user_id, points, "🎫 كود", f"كود: {code}")
                )
                
                # تحديث وقت النشاط الأخير
                await conn.execute(
                    "UPDATE users SET last_active = ? WHERE user_id = ?",
                    (datetime.now().isoformat(), user_id)
                )
                
                await conn.commit()
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
                created_at, expires_at, description, min_points_required, category
                FROM promo_codes WHERE code = ?""",
                (code,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على معلومات الكود {code}: {e}")
            return None
    
    async def get_all_promo_codes(self, active_only: bool = False):
        """الحصول على جميع الأكواد"""
        try:
            query = """SELECT code, points, max_uses, current_uses, active, 
                     created_at, expires_at, description 
                     FROM promo_codes"""
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY created_at DESC"
            
            result = await self.execute_query(query)
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على الأكواد: {e}")
            return []
    
    # --- إحصائيات وتحليلات متقدمة ---
    
    async def get_global_stats(self) -> tuple:
        """الحصول على إحصائيات عامة متقدمة"""
        try:
            # عدد المستخدمين النشطين
            users_result = await self.execute_query_one("SELECT COUNT(*) as count FROM users WHERE is_banned = 0 AND is_active = 1")
            users_count = users_result['count'] if users_result else 0
            
            # مجموع النقاط
            points_result = await self.execute_query_one("SELECT SUM(points) as total FROM users WHERE is_banned = 0")
            total_points = points_result['total'] if points_result else 0
            
            # عدد العمليات
            tx_result = await self.execute_query_one("SELECT COUNT(*) as count FROM transactions")
            total_tx = tx_result['count'] if tx_result else 0
            
            # النجوم المشتراة
            stars_result = await self.execute_query_one("SELECT SUM(stars) as total FROM star_payments WHERE status = 'completed'")
            total_stars = stars_result['total'] if stars_result else 0
            
            # العمليات في آخر 24 ساعة
            cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
            last_24h_result = await self.execute_query_one("SELECT COUNT(*) as count FROM transactions WHERE timestamp > ?", (cutoff,))
            last_24h_tx = last_24h_result['count'] if last_24h_result else 0
            
            # الإحالات النشطة
            referrals_result = await self.execute_query_one("SELECT COUNT(*) as count FROM referrals WHERE status = 'active'")
            total_referrals = referrals_result['count'] if referrals_result else 0
            
            # المستخدمين النشطين اليوم
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            active_users_result = await self.execute_query_one(
                "SELECT COUNT(DISTINCT user_id) as count FROM transactions WHERE timestamp > ?",
                (today_start,)
            )
            daily_active_users = active_users_result['count'] if active_users_result else 0
            
            return users_count, total_points, total_tx, total_stars, last_24h_tx, total_referrals, daily_active_users
            
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإحصائيات: {e}")
            return 0, 0, 0, 0, 0, 0, 0
    
    async def get_new_users_stats(self, days: int = 1) -> int:
        """الحصول على عدد المستخدمين الجدد"""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            result = await self.execute_query_one(
                "SELECT COUNT(*) as count FROM users WHERE joined_date > ? AND is_banned = 0 AND is_active = 1",
                (cutoff,)
            )
            return result['count'] if result else 0
        except Exception as e:
            logger.error(f"خطأ في الحصول على إحصائيات المستخدمين الجدد: {e}")
            return 0
    
    async def get_top_rich_users(self, limit: int = 10):
        """الحصول على أغنى المستخدمين"""
        try:
            result = await self.execute_query(
                """SELECT user_id, username, full_name, points 
                FROM users 
                WHERE is_banned = 0 AND is_active = 1
                ORDER BY points DESC 
                LIMIT ?""",
                (limit,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على أغنى المستخدمين: {e}")
            return []
    
    async def get_top_referrers(self, limit: int = 5):
        """الحصول على أفضل المشيرين"""
        try:
            result = await self.execute_query(
                """SELECT u.user_id, u.username, u.full_name, COUNT(r.referred_id) as referral_count
                FROM users u
                LEFT JOIN referrals r ON u.user_id = r.referrer_id
                WHERE u.is_banned = 0 AND u.is_active = 1
                GROUP BY u.user_id
                ORDER BY referral_count DESC
                LIMIT ?""",
                (limit,)
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على أفضل المشيرين: {e}")
            return []
    
    async def get_all_users(self, exclude_banned: bool = True, limit: int = None, offset: int = 0):
        """الحصول على جميع المستخدمين مع ترقيم الصفحات"""
        try:
            query = "SELECT user_id, username, full_name, points, is_banned, is_active FROM users"
            if exclude_banned:
                query += " WHERE is_banned = 0 AND is_active = 1"
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
    
    # --- إدارة الإعدادات المتقدمة ---
    
    async def get_setting(self, key: str, default: str = None):
        """الحصول على إعداد"""
        try:
            result = await self.execute_query_one(
                "SELECT value, data_type FROM settings WHERE key = ?",
                (key,)
            )
            if result:
                # تحويل القيمة بناءً على نوع البيانات
                data_type = result['data_type']
                value = result['value']
                
                if data_type == 'integer':
                    return int(value) if value else 0
                elif data_type == 'float':
                    return float(value) if value else 0.0
                elif data_type == 'boolean':
                    return value == "1"
                else:
                    return value
            
            return default
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإعداد {key}: {e}")
            return default
    
    async def set_setting(self, key: str, value: str):
        """تحديث إعداد"""
        try:
            await self.execute_update(
                "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                (str(value), datetime.now().isoformat(), key)
            )
            self.clear_cache(f"setting_{key}")
        except Exception as e:
            logger.error(f"خطأ في تحديث الإعداد {key}: {e}")
    
    async def get_all_settings(self):
        """الحصول على جميع الإعدادات"""
        try:
            result = await self.execute_query(
                "SELECT key, value, description, data_type, options FROM settings ORDER BY key"
            )
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على الإعدادات: {e}")
            return []
    
    # --- نظام الدعم المتقدم ---
    
    async def create_support_ticket(self, user_id: int, subject: str, message: str, category: str = "general") -> int:
        """إنشاء تذكرة دعم جديدة"""
        try:
            result = await self.execute_query_one(
                """INSERT INTO support_tickets 
                (user_id, subject, message, category, created_at, updated_at) 
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, subject, message, category, datetime.now().isoformat(), datetime.now().isoformat())
            )
            
            # الحصول على معرف التذكرة
            last_id = await self.execute_query_one("SELECT last_insert_rowid()")
            ticket_id = last_id[0] if last_id else -1
            
            logger.info(f"✅ تم إنشاء تذكرة دعم #{ticket_id} للمستخدم {user_id}")
            return ticket_id
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء تذكرة دعم: {e}")
            return -1
    
    async def get_support_tickets(self, status: str = None, user_id: int = None, limit: int = 50):
        """الحصول على تذاكر الدعم"""
        try:
            query = """SELECT t.*, u.username, u.full_name 
                      FROM support_tickets t
                      LEFT JOIN users u ON t.user_id = u.user_id
                      WHERE 1=1"""
            params = []
            
            if status:
                query += " AND t.status = ?"
                params.append(status)
            
            if user_id:
                query += " AND t.user_id = ?"
                params.append(user_id)
            
            query += " ORDER BY t.created_at DESC LIMIT ?"
            params.append(limit)
            
            result = await self.execute_query(query, tuple(params))
            return result
        except Exception as e:
            logger.error(f"خطأ في الحصول على تذاكر الدعم: {e}")
            return []
    
    async def update_ticket_status(self, ticket_id: int, status: str, admin_reply: str = None, replied_by: int = None):
        """تحديث حالة تذكرة الدعم"""
        try:
            updates = ["status = ?", "updated_at = ?"]
            params = [status, datetime.now().isoformat()]
            
            if admin_reply:
                updates.append("admin_reply = ?")
                updates.append("replied_by = ?")
                updates.append("replied_at = ?")
                params.append(admin_reply)
                params.append(replied_by)
                params.append(datetime.now().isoformat())
            
            params.append(ticket_id)
            
            query = f"UPDATE support_tickets SET {', '.join(updates)} WHERE ticket_id = ?"
            await self.execute_update(query, tuple(params))
            
            logger.info(f"✅ تم تحديث حالة التذكرة #{ticket_id} إلى {status}")
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في تحديث حالة التذكرة #{ticket_id}: {e}")
            return False
    
    # --- تنظيف البيانات المتقدم ---
    
    async def cleanup_old_data(self):
        """تنظيف البيانات القديمة بذكاء"""
        try:
            # الحصول على عدد أيام الاحتفاظ من الإعدادات
            auto_cleanup_days = await self.get_setting("auto_cleanup_days", 90)
            inactive_user_days = await self.get_setting("inactive_user_days", 30)
            cutoff_date = (datetime.now() - timedelta(days=auto_cleanup_days)).strftime("%Y-%m-%d")
            inactive_cutoff = (datetime.now() - timedelta(days=inactive_user_days)).strftime("%Y-%m-%d")
            
            # حذف الأكواد المنتهية
            deleted_codes = await self.execute_update(
                "DELETE FROM promo_codes WHERE expires_at < ? AND expires_at IS NOT NULL",
                (cutoff_date,)
            )
            logger.info(f"🧹 تم حذف {deleted_codes} كود منتهي الصلاحية")
            
            # حذف سجلات الدفع القديمة
            deleted_payments = await self.execute_update(
                "DELETE FROM star_payments WHERE timestamp < ?",
                (cutoff_date,)
            )
            logger.info(f"🧹 تم حذف {deleted_payments} سجل دفع قديم")
            
            # حذف السجلات القديمة (ولكن الاحتفاظ بالمستخدمين)
            deleted_transactions = await self.execute_update(
                "DELETE FROM transactions WHERE timestamp < ? AND type IN ('🎯 رشق', '🎁 مكافأة', '🎫 كود')",
                (cutoff_date,)
            )
            logger.info(f"🧹 تم حذف {deleted_transactions} سجل معاملة قديم")
            
            # تعطيل المستخدمين غير النشطين
            deactivated_users = await self.execute_update(
                "UPDATE users SET is_active = 0 WHERE last_active < ? AND is_banned = 0 AND is_active = 1",
                (inactive_cutoff,)
            )
            logger.info(f"🧹 تم تعطيل {deactivated_users} مستخدم غير نشط")
            
            # حذف الإشعارات القديمة
            deleted_notifications = await self.execute_update(
                "DELETE FROM notifications WHERE created_at < ? AND is_read = 1",
                (cutoff_date,)
            )
            logger.info(f"🧹 تم حذف {deleted_notifications} إشعار قديم")
            
            # حذف أنشطة البوت القديمة
            deleted_activities = await self.execute_update(
                "DELETE FROM bot_activities WHERE timestamp < ?",
                (cutoff_date,)
            )
            logger.info(f"🧹 تم حذف {deleted_activities} سجل نشاط قديم")
            
            # تحسين قاعدة البيانات
            await self.execute_update("VACUUM")
            
            logger.info(f"✅ تم تنظيف البيانات القديمة (أكثر من {auto_cleanup_days} يوم)")
            self.clear_cache()
            
        except Exception as e:
            logger.error(f"خطأ في تنظيف البيانات: {e}")

# تهيئة قاعدة البيانات
db = AsyncDatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ أدوات مساعدة متقدمة مع Rate Limiting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_link(user_id: int, name: str) -> str:
    """إنشاء رابط للمستخدم مع حماية من الـHTML"""
    safe_name = html.escape(name) if name else "مستخدم"
    return f"<a href='tg://user?id={user_id}'>{safe_name}</a>"

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """إنشاء لوحة المفاتيح الإدارية"""
    btns = [
        [InlineKeyboardButton("📊 لوحة التحكم", callback_data="admin_panel")],
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels"),
         InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data="admin_settings"),
         InlineKeyboardButton("💰 إدارة النقاط", callback_data="admin_points")],
        [InlineKeyboardButton("📤 نظام الإذاعة", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎫 إدارة الأكواد", callback_data="admin_codes")],
        [InlineKeyboardButton("📈 الإحصائيات", callback_data="admin_analytics"),
         InlineKeyboardButton("🎫 تذاكر الدعم", callback_data="admin_tickets")],
        [InlineKeyboardButton("🔧 الصيانة", callback_data="admin_maintenance"),
         InlineKeyboardButton("🧹 التنظيف", callback_data="admin_cleanup")]
    ]
    return InlineKeyboardMarkup(btns)

def get_main_keyboard(user_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """إنشاء لوحة المفاتيح الرئيسية"""
    btns = [
        [InlineKeyboardButton("🎯 رشق", callback_data="attack_menu")],
        [InlineKeyboardButton("🔄 تجميع النقاط", callback_data="collect_points")],
        [InlineKeyboardButton("💸 تحويل النقاط", callback_data="transfer_start"),
         InlineKeyboardButton("🎫 استبدال كود", callback_data="redeem_code_start")],
        [InlineKeyboardButton("📜 سجل العمليات", callback_data="history"), 
         InlineKeyboardButton("📞 الدعم الفني", callback_data="support")],
        [InlineKeyboardButton("⭐ شراء النقاط", callback_data="buy_points_menu"),
         InlineKeyboardButton("👥 نظام الإحالة", callback_data="referral_page")]
    ]
    if is_admin:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    return InlineKeyboardMarkup(btns)

def get_support_keyboard() -> InlineKeyboardMarkup:
    """إنشاء لوحة المفاتيح للدعم الفني"""
    btns = [
        [InlineKeyboardButton("📞 إنشاء تذكرة دعم", callback_data="create_ticket")],
        [InlineKeyboardButton("📋 تذاكري", callback_data="my_tickets"),
         InlineKeyboardButton("🗣️ تواصل مباشر", callback_data="direct_support")],
        [InlineKeyboardButton("🔙 الرجوع", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(btns)

async def check_maintenance_mode(user_id: int) -> bool:
    """التحقق من وضع الصيانة مع التخزين المؤقت"""
    if user_id == ADMIN_ID:
        return False
    
    cache_key = "maintenance_mode"
    cached = db.get_cached_data(cache_key)
    if cached is not None:
        return cached
    
    maintenance_mode = await db.get_setting("maintenance_mode")
    result = bool(maintenance_mode)
    db.set_cached_data(cache_key, result)
    return result

async def check_rate_limit(user_id: int) -> tuple:
    """التحقق من Rate Limiting مع التخزين المؤقت"""
    return await db.check_rate_limit(user_id)

async def safe_api_call(func, *args, **kwargs):
    """تنفيذ استدعاء API بأمان مع معالجة الأخطاء"""
    try:
        return await func(*args, **kwargs)
    except Forbidden as e:
        logger.warning(f"المستخدم حظر البوت أو ليس لديه إذن: {e}")
        return None
    except BadRequest as e:
        logger.error(f"طلب غير صالح: {e}")
        return None
    except TimedOut as e:
        logger.error(f"انتهت مهلة الطلب: {e}")
        return None
    except NetworkError as e:
        logger.error(f"خطأ في الشبكة: {e}")
        return None
    except Exception as e:
        logger.error(f"خطأ غير متوقع في API: {e}")
        return None

def is_admin(user_id: int) -> bool:
    """التحقق إذا كان المستخدم أدمن"""
    return user_id == ADMIN_ID

def format_number(num: int) -> str:
    """تنسيق الأرقام"""
    return f"{num:,}" if num else "0"

def format_datetime(dt_string: str) -> str:
    """تنسيق التاريخ والوقت"""
    if not dt_string:
        return "غير معروف"
    try:
        dt = datetime.fromisoformat(dt_string.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return dt_string[:19]

async def safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """حذف رسالة بأمان"""
    try:
        await safe_api_call(context.bot.delete_message, chat_id, message_id)
    except Exception as e:
        logger.error(f"خطأ في حذف الرسالة: {e}")

async def safe_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, 
                           reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML"):
    """تعديل رسالة بأمان"""
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.message:
            await update.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.error(f"خطأ في تعديل الرسالة: {e}")

def clean_context_data(context: ContextTypes.DEFAULT_TYPE, keys: list = None):
    """تنظيف البيانات من context مع التعامل الآمن"""
    try:
        if keys:
            for key in keys:
                if key in context.user_data:
                    del context.user_data[key]
        else:
            context.user_data.clear()
    except Exception as e:
        logger.error(f"خطأ في تنظيف context: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 نظام المحادثات المحسن مع Timeout وRate Limiting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ConversationManager:
    """مدير المحادثات مع دعم Timeout"""
    
    def __init__(self):
        self.active_conversations = {}
        self.timeout_task = None
        
    async def start_conversation(self, user_id: int, state: int, data: dict = None):
        """بدء محادثة جديدة"""
        self.active_conversations[user_id] = {
            'state': state,
            'data': data or {},
            'start_time': datetime.now(),
            'last_activity': datetime.now()
        }
        
    async def update_conversation(self, user_id: int, state: int = None, data: dict = None):
        """تحديث حالة المحادثة"""
        if user_id in self.active_conversations:
            if state is not None:
                self.active_conversations[user_id]['state'] = state
            if data is not None:
                self.active_conversations[user_id]['data'].update(data)
            self.active_conversations[user_id]['last_activity'] = datetime.now()
    
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
    
    async def check_timeouts(self, application: Application):
        """التحقق من المحادثات المنتهية الصلاحية"""
        timeout_seconds = await db.get_setting("conversation_timeout", 300)
        now = datetime.now()
        expired_users = []
        
        for user_id, conv in self.active_conversations.items():
            if (now - conv['last_activity']).total_seconds() > timeout_seconds:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            try:
                await self.end_conversation(user_id)
                # إرسال رسالة للمستخدم
                await safe_api_call(
                    application.bot.send_message,
                    user_id,
                    "⏰ <b>تم إغلاق المحادثة تلقائياً بسبب عدم النشاط.</b>\n\n"
                    "يمكنك البدء مرة أخرى باستخدام الأمر /start",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"خطأ في إنهاء المحادثة للمستخدم {user_id}: {e}")
    
    async def start_timeout_checker(self, application: Application):
        """بدء مدقق الـTimeout"""
        async def checker():
            while True:
                try:
                    await self.check_timeouts(application)
                except Exception as e:
                    logger.error(f"خطأ في مدقق الـTimeout: {e}")
                await asyncio.sleep(60)  # التحقق كل دقيقة
        
        self.timeout_task = asyncio.create_task(checker())

conv_manager = ConversationManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات الرئيسية المحسنة مع نظام المحادثات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج أمر /start مع تحسينات متقدمة"""
    user = update.effective_user
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}\n\nيرجى المحاولة بعد قليل.")
        return
    
    # إنهاء أي محادثة نشطة
    await conv_manager.end_conversation(user.id)
    
    # التحقق من وضع الصيانة
    if await check_maintenance_mode(user.id):
        await update.message.reply_text(
            "🔧 <b>البوت قيد الصيانة حاليًا.</b>\n\n"
            "سيتم فتحه قريبًا بإذن الله.\n"
            "شكرًا لتفهمكم. 🙏",
            parse_mode="HTML"
        )
        return
    
    # التحقق من اشتراك القنوات
    subscribed, message = await db.check_channel_subscription(user.id, context)
    if not subscribed:
        await update.message.reply_text(message, parse_mode="HTML")
        return
    
    args = context.args
    
    # التحقق من حظر المستخدم
    if await db.is_banned(user.id):
        await update.message.reply_text(
            "🚫 <b>حسابك محظور!</b>\n\n"
            "لا يمكنك استخدام البوت حالياً.\n"
            "للمزيد من المعلومات، تواصل مع الدعم الفني.",
            parse_mode="HTML"
        )
        return
    
    # التحقق من وجود المستخدم
    db_user = await db.get_user(user.id)
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
        success = await db.add_user(
            user.id, 
            user.username or "", 
            user.full_name or "مستخدم", 
            "None", 
            referrer_id
        )
        
        if success:
            # إرسال إشعار للمشير
            if referrer_id:
                try:
                    referral_points = await db.get_setting("referral_points", 10)
                    referrer = await db.get_user(referrer_id)
                    if referrer:
                        msg = (
                            f"🔔 <b>إحالة جديدة!</b>\n\n"
                            f"👤 المستخدم: {get_user_link(user.id, user.full_name)}\n"
                            f"🎯 النقاط: {referral_points}\n"
                            f"💰 رصيدك الحالي: {referrer['points']:,}"
                        )
                        await safe_api_call(context.bot.send_message, referrer_id, msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"خطأ في إرسال إشعار الإحالة: {e}")
    
    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    """إرسال لوحة التحكم مع معلومات متقدمة"""
    user = update.effective_user
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(user.id)
    if not allowed:
        if update.callback_query:
            await update.callback_query.answer(message, show_alert=True)
        return
    
    # إنهاء أي محادثة نشطة
    await conv_manager.end_conversation(user.id)
    
    # التحقق من وضع الصيانة
    if await check_maintenance_mode(user.id):
        if update.callback_query:
            await update.callback_query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    # التحقق من اشتراك القنوات
    subscribed, message = await db.check_channel_subscription(user.id, context)
    if not subscribed:
        if update.callback_query:
            await update.callback_query.edit_message_text(message, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(message, parse_mode="HTML")
        return
    
    # التحقق من حظر المستخدم
    if await db.is_banned(user.id):
        ban_message = "🚫 <b>حسابك محظور!</b>\n\nلا يمكنك استخدام البوت حالياً."
        if update.callback_query:
            await update.callback_query.edit_message_text(ban_message, parse_mode="HTML")
        elif update.message:
            await update.message.reply_text(ban_message, parse_mode="HTML")
        return
    
    # الحصول على بيانات المستخدم
    db_user = await db.get_user(user.id)
    if not db_user:
        await start(update, context)
        return
    
    points = db_user['points']
    username = db_user['username'] or "لا يوجد"
    full_name = db_user['full_name'] or user.first_name
    joined_date = format_datetime(db_user['joined_date'])
    last_active = format_datetime(db_user['last_active'])
    total_earned = db_user['total_earned']
    total_spent = db_user['total_spent']
    
    # الحصول على ترتيب المستخدم
    all_users = await db.get_all_users(exclude_banned=True, limit=1000)
    user_rank = 1
    for u in all_users:
        if u['user_id'] == user.id:
            break
        user_rank += 1
    
    text = (
        f"مرحباً بك {get_user_link(user.id, full_name)} 👋\n\n"
        f"📊 <b>معلومات حسابك:</b>\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"📛 اليوزر: @{username}\n"
        f"🏆 الرصيد: <b>{format_number(points)} نقطة</b>\n"
        f"📈 الترتيب: #{user_rank}\n"
        f"💰 إجمالي المكتسب: {format_number(total_earned)} نقطة\n"
        f"💸 إجمالي المنفق: {format_number(total_spent)} نقطة\n"
        f"📅 تاريخ الانضمام: {joined_date}\n"
        f"🕐 آخر نشاط: {last_active}\n"
        f"────────────────\n"
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
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    await conv_manager.end_conversation(query.from_user.id)
    await send_dashboard(update, context, edit=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💫 نظام الدفع التلقائي المحسن مع معالجة أخطاء مفصلة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def buy_points_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة شراء النقاط"""
    query = update.callback_query
    user_id = query.from_user.id
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(user_id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(user_id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    # التحقق من تفعيل نظام الدفع
    enable_star_payments = await db.get_setting("enable_star_payments", 1)
    
    text = "💰 <b>شراء النقاط</b>\n\n"
    
    if enable_star_payments and PAYMENT_PROVIDER_TOKEN:
        text += "⭐ <b>الدفع بالنجوم (تلقائي):</b>\n"
        text += "• 5 نجوم ← 50 نقطة\n"
        text += "• 10 نجوم ← 120 نقطة\n\n"
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ 5 نجوم (50 نقطة)", callback_data="buy_5"),
            InlineKeyboardButton("⭐⭐ 10 نجوم (120 نقطة)", callback_data="buy_10")],
            [InlineKeyboardButton("💳 الدفع اليدوي", callback_data="buy_manual")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
    else:
        text += "نظام الدفع التلقائي غير متاح حالياً.\n"
        text += "يمكنك الشراء يدوياً عبر التواصل مع الإدارة.\n\n"
        text += "📞 <b>تواصل مع:</b> @username"
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 طلب شراء يدوي", callback_data="buy_manual")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def buy_stars_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج شراء النجوم مع معالجة أخطاء مفصلة"""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(user_id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(user_id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    # تعريف الباقات
    packages = {
        "buy_5": {"stars": 5, "points": 50, "title": "5 نجوم (50 نقطة)"},
        "buy_10": {"stars": 10, "points": 120, "title": "10 نجوم (120 نقطة)"}
    }
    
    if data not in packages:
        logger.error(f"باقة غير معروفة: {data}")
        await query.edit_message_text("❌ الباقة المطلوبة غير موجودة.")
        return
    
    package = packages[data]
    
    if not PAYMENT_PROVIDER_TOKEN:
        logger.error("رمز مزود الدفع غير موجود")
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
        
        await safe_api_call(
            context.bot.send_invoice,
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
            need_shipping_address=False,
            is_flexible=False
        )
        
        logger.info(f"فاتورة إنشأت للمستخدم {user_id}: {package['stars']} نجوم")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"خطأ في إنشاء الفاتورة للمستخدم {user_id}: {error_msg}")
        
        # إرسال رسالة خطأ مفصلة
        user_error_msg = (
            "❌ <b>حدث خطأ في إنشاء الفاتورة</b>\n\n"
            "تفاصيل الخطأ:\n"
            f"{error_msg[:200]}\n\n"
            "يرجى المحاولة مرة أخرى أو التواصل مع الدعم."
        )
        
        await query.edit_message_text(user_error_msg, parse_mode="HTML")

async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الدفع مع معالجة مفصلة"""
    query = update.pre_checkout_query
    
    try:
        # التحقق من صحة البايلود
        if not query.invoice_payload.startswith("stars_"):
            logger.warning(f"بايلود غير صالح: {query.invoice_payload}")
            await query.answer(ok=False, error_message="فاتورة غير صالحة")
            return
        
        # تحليل البايلود
        parts = query.invoice_payload.split("_")
        if len(parts) != 5:
            logger.warning(f"تنسيق بايلود غير صحيح: {query.invoice_payload}")
            await query.answer(ok=False, error_message="تنسيق فاتورة غير صحيح")
            return
        
        # التحقق من عدم تكرار الدفع
        payment_id = query.id
        existing = await db.get_star_payment(payment_id)
        if existing:
            logger.warning(f"فاتورة مكررة: {payment_id}")
            await query.answer(ok=False, error_message="تم استخدام هذه الفاتورة مسبقاً")
            return
        
        await query.answer(ok=True)
        logger.info(f"التحقق من الدفع ناجح: {payment_id}")
        
    except Exception as e:
        logger.error(f"خطأ في التحقق من الدفع: {e}")
        await query.answer(ok=False, error_message="حدث خطأ في التحقق من الدفع")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الدفع الناجح مع معالجة مفصلة"""
    try:
        payment = update.message.successful_payment
        payload = payment.invoice_payload
        
        # تحليل البايلود
        parts = payload.split("_")
        if len(parts) != 5:
            raise ValueError(f"بايلود غير صالح: {payload}")
        
        stars = int(parts[1])
        points = int(parts[2])
        user_id = int(parts[3])
        
        # التحقق من المستخدم الفعلي
        if update.effective_user.id != user_id:
            logger.warning(f"مستخدم {update.effective_user.id} يحاول استخدام فاتورة لـ {user_id}")
            await update.message.reply_text("❌ هذه الفاتورة لا تنتمي إليك!")
            return
        
        # تسجيل عملية الدفع
        success = await db.add_star_payment(
            payment_id=payment.provider_payment_id,
            user_id=user_id,
            stars=stars,
            points=points,
            provider="telegram",
            invoice_payload=payload,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            provider_payment_charge_id=payment.provider_payment_charge_id
        )
        
        if not success:
            raise Exception("فشل في تسجيل عملية الدفع")
        
        # إضافة النقاط للمستخدم
        await db.update_points(user_id, points, "buy", f"شراء بالنجوم: {stars} نجمة")
        
        # الحصول على بيانات المستخدم المحدثة
        user_data = await db.get_user(user_id)
        new_balance = user_data['points'] if user_data else points
        
        # إشعار الأدمن
        try:
            admin_msg = (
                f"💰 <b>عملية شراء ناجحة!</b>\n\n"
                f"👤 المستخدم: {get_user_link(user_id, update.effective_user.full_name)}\n"
                f"🆔 الآيدي: <code>{user_id}</code>\n"
                f"⭐ النجوم: {stars}\n"
                f"🎯 النقاط: {points}\n"
                f"💳 المبلغ: {payment.total_amount / 100} نجوم\n"
                f"📊 الرصيد الجديد: {format_number(new_balance)} نقطة\n"
                f"🔗 معرِّف الدفع: {payment.provider_payment_id}"
            )
            await safe_api_call(context.bot.send_message, ADMIN_ID, admin_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن: {e}")
        
        # تأكيد للمستخدم
        await update.message.reply_text(
            f"✅ <b>تمت العملية بنجاح!</b>\n\n"
            f"🎉 تم إضافة <b>{points} نقطة</b> لحسابك.\n"
            f"💰 رصيدك الحالي: <b>{format_number(new_balance)} نقطة</b>\n"
            f"⭐ النجوم المستخدمة: {stars}\n\n"
            f"شكراً لثقتك! 🙏",
            parse_mode="HTML"
        )
        
        logger.info(f"دفع ناجح للمستخدم {user_id}: {stars} نجوم -> {points} نقطة")
        
    except ValueError as e:
        logger.error(f"خطأ في معالجة الدفع (ValueError): {e}")
        await update.message.reply_text(
            "❌ حدث خطأ في معالجة الدفع.\n"
            "يرجى التواصل مع الإدارة مع إرسال تفاصيل الدفع.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"خطأ في معالجة الدفع الناجح: {e}")
        await update.message.reply_text(
            "❌ حدث خطأ في معالجة الدفع.\n"
            "يرجى حفظ هذه الرسالة والتواصل مع الدعم:\n"
            f"معرِّف الدفع: {payment.provider_payment_id if 'payment' in locals() else 'غير معروف'}",
            parse_mode="HTML"
        )

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ لوحة تحكم الأدمن المحسنة مع ميزات متقدمة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة تحكم الأدمن مع إحصائيات متقدمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على الإحصائيات
    users_count, total_points, total_tx, total_stars, last_24h_tx, total_referrals, daily_active_users = await db.get_global_stats()
    new_users_today = await db.get_new_users_stats(1)
    new_users_week = await db.get_new_users_stats(7)
    
    maintenance_status = "🟢 مفعل" if await db.get_setting("maintenance_mode") else "🔴 معطل"
    star_payments_status = "🟢 مفعل" if PAYMENT_PROVIDER_TOKEN and await db.get_setting("enable_star_payments", 1) else "🔴 معطل"
    
    # الحصول على الإيرادات المقدرة
    revenue_estimate = total_stars * 0.01  # تقدير إيرادي
    
    text = (
        f"⚙️ <b>لوحة التحكم المتقدمة</b>\n\n"
        f"📊 <b>الإحصائيات العامة:</b>\n"
        f"• 👥 المستخدمين: {format_number(users_count)}\n"
        f"• 📈 مستخدمين اليوم: {format_number(new_users_today)}\n"
        f"• 📆 مستخدمين الأسبوع: {format_number(new_users_week)}\n"
        f"• 🎯 المستخدمين النشطين: {format_number(daily_active_users)}\n"
        f"• 💰 النقاط الكلية: {format_number(total_points)}\n"
        f"• ⭐ النجوم المشتراة: {format_number(total_stars)}\n"
        f"• 💵 الإيراد المقدر: ${revenue_estimate:.2f}\n"
        f"• 📊 العمليات (24س): {format_number(last_24h_tx)}\n"
        f"• 👥 الإحالات النشطة: {format_number(total_referrals)}\n\n"
        f"🔧 <b>حالة النظام:</b>\n"
        f"• وضع الصيانة: {maintenance_status}\n"
        f"• الدفع بالنجوم: {star_payments_status}\n\n"
        f"👇 اختر القسم المطلوب:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 إدارة القنوات", callback_data="admin_channels"),
         InlineKeyboardButton("👤 إدارة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data="admin_settings"),
         InlineKeyboardButton("💰 إدارة النقاط", callback_data="admin_points")],
        [InlineKeyboardButton("📤 نظام الإذاعة", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎫 إدارة الأكواد", callback_data="admin_codes")],
        [InlineKeyboardButton("📈 الإحصائيات المتقدمة", callback_data="admin_analytics"),
         InlineKeyboardButton("🎫 تذاكر الدعم", callback_data="admin_tickets")],
        [InlineKeyboardButton("🔧 الصيانة والإعدادات", callback_data="admin_maintenance"),
         InlineKeyboardButton("🧹 تنظيف البيانات", callback_data="admin_cleanup")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📢 إدارة القنوات المحسنة مع نظام التحقق المتقدم
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة القنوات مع معلومات مفصلة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
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
            bot_status = "✅ أدمن" if channel['bot_is_admin'] == 1 else "❌ ليس أدمن"
            name = channel['channel_name'] or "بدون اسم"
            text += f"{i}. {name}\n"
            text += f"   🔗 {channel['channel_link']}\n"
            text += f"   🆔 <code>{channel['channel_id']}</code>\n"
            text += f"   📊 {status} | {bot_status}\n\n"
    else:
        text += "لا توجد قنوات مضافة.\n"
    
    text += "👇 اختر الإجراء المطلوب:"
    
    kb_buttons = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data="admin_add_channel")],
        [InlineKeyboardButton("✏️ تعديل قناة", callback_data="admin_edit_channel_menu"),
         InlineKeyboardButton("🔧 تفعيل/تعطيل", callback_data="admin_toggle_channel_menu")],
        [InlineKeyboardButton("🔄 تحديث المعلومات", callback_data="admin_refresh_channels"),
         InlineKeyboardButton("📊 اختبار الاشتراكات", callback_data="admin_test_subscriptions")]
    ]
    
    if channels:
        kb_buttons.append([InlineKeyboardButton("🗑️ حذف قناة", callback_data="admin_delete_channel_menu")])
    
    kb_buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة قناة مع بدء محادثة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_CHANNEL_ID)
    
    await query.edit_message_text(
        "📝 <b>إضافة قناة جديدة</b>\n\n"
        "أرسل الآن <b>آيدي القناة</b>:\n\n"
        "📌 <b>ملاحظات مهمة:</b>\n"
        "• يمكن أن يكون الآيدي مثل @channel_name\n"
        "• أو آيدي رقمي مثل -1001234567890\n"
        "• يجب أن يكون البوت أدمن في القناة!\n"
        "• تأكد من أن القناة عامة\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آيدي القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_id = update.message.text.strip()
    
    # التحقق من صحة الآيدي
    if not channel_id.startswith('@') and not (channel_id.startswith('-100') and channel_id[1:].isdigit()):
        await update.message.reply_text(
            "❌ صيغة الآيدي غير صحيحة!\n"
            "يجب أن يبدأ بـ @ أو -100 متبوعاً بأرقام\n\n"
            "أعد إرسال الآيدي أو /cancel للإلغاء:"
        )
        return STATE_CHANNEL_ID
    
    # التحقق من وجود القناة
    try:
        chat = await safe_api_call(context.bot.get_chat, channel_id)
        if not chat:
            await update.message.reply_text(
                "❌ لا يمكن الوصول للقناة!\n"
                "تحقق من الآيدي وتأكد أن البوت عضو في القناة.\n\n"
                "أعد إرسال الآيدي أو /cancel للإلغاء:"
            )
            return STATE_CHANNEL_ID
            
        channel_name = chat.title
        
        # التحقق من وجود البوت كأدمن في القناة
        try:
            bot_member = await safe_api_call(context.bot.get_chat_member, channel_id, context.bot.id)
            if not bot_member or bot_member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    "❌ البوت ليس أدمن في هذه القناة!\n"
                    "يجب رفع البوت كأدمن أولاً.\n\n"
                    "أعد إرسال آيدي قناة أخرى أو /cancel للإلغاء:"
                )
                return STATE_CHANNEL_ID
        except Exception as e:
            await update.message.reply_text(
                f"❌ لا يمكن التحقق من صلاحية البوت: {str(e)[:100]}\n\n"
                "أعد إرسال آيدي قناة أخرى أو /cancel للإلغاء:"
            )
            return STATE_CHANNEL_ID
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_CHANNEL_LINK,
            {'channel_id': channel_id, 'channel_name': channel_name}
        )
        
        await update.message.reply_text(
            f"✅ تم التعرف على القناة: <b>{channel_name}</b>\n\n"
            "الآن أرسل <b>رابط القناة</b> (مثال: https://t.me/channel_name):\n\n"
            "❌ للإلغاء، أرسل /cancel",
            parse_mode="HTML"
        )
        return STATE_CHANNEL_LINK
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ خطأ في الوصول للقناة: {str(e)[:100]}\n\n"
            "أعد إرسال الآيدي أو /cancel للإلغاء:"
        )
        return STATE_CHANNEL_ID

async def admin_get_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على رابط القناة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    channel_link = update.message.text.strip()
    
    # التحقق من صحة الرابط
    if not channel_link.startswith('https://t.me/'):
        await update.message.reply_text(
            "❌ الرابط غير صحيح!\n"
            "يجب أن يبدأ بـ https://t.me/\n\n"
            "أعد إرسال الرابط أو /cancel للإلغاء:"
        )
        return STATE_CHANNEL_LINK
    
    # الحصول على بيانات المحادثة
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    channel_id = conv_data.get('channel_id')
    channel_name = conv_data.get('channel_name', 'قناة')
    
    # إضافة القناة
    if await db.add_channel(channel_id, channel_link, update.effective_user.id, channel_name):
        success_msg = (
            f"✅ <b>تمت إضافة القناة بنجاح!</b>\n\n"
            f"📢 القناة: <b>{channel_name}</b>\n"
            f"🆔 الآيدي: <code>{channel_id}</code>\n"
            f"🔗 الرابط: {channel_link}\n\n"
            f"⚠️ <b>تأكد من:</b>\n"
            f"• البوت أدمن في القناة\n"
            f"• القناة عامة\n"
            f"• تم تفعيل القناة تلقائياً"
        )
        await update.message.reply_text(success_msg, parse_mode="HTML")
    else:
        await update.message.reply_text("❌ فشل في إضافة القناة! قد تكون مضافة مسبقاً.")
    
    await conv_manager.end_conversation(update.effective_user.id)
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_cancel_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إضافة قناة"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء عملية إضافة القناة.")
    await admin_channels_menu(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👤 إدارة المستخدمين المحسنة مع بحث متقدم
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة المستخدمين مع خيارات متقدمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على إحصائيات سريعة
    users_count = (await db.get_global_stats())[0]
    banned_count = await db.execute_query_one("SELECT COUNT(*) as count FROM users WHERE is_banned = 1")
    banned_count = banned_count['count'] if banned_count else 0
    
    text = (
        f"👤 <b>إدارة المستخدمين</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 👥 إجمالي المستخدمين: {format_number(users_count)}\n"
        f"• 🚫 المستخدمين المحظورين: {format_number(banned_count)}\n\n"
        f"🔍 <b>طرق البحث:</b>"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بحث بالآيدي", callback_data="admin_search_by_id"),
         InlineKeyboardButton("🔍 بحث بالاسم", callback_data="admin_search_by_name")],
        [InlineKeyboardButton("📧 بحث باليوزر", callback_data="admin_search_by_username")],
        [InlineKeyboardButton("📊 عرض جميع المستخدمين", callback_data="admin_list_users")],
        [InlineKeyboardButton("📈 عرض الأغنياء", callback_data="admin_show_rich"),
         InlineKeyboardButton("👥 أفضل المشيرين", callback_data="admin_top_referrers")],
        [InlineKeyboardButton("🚫 عرض المحظورين", callback_data="admin_show_banned")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_search_by_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث بالآيدي"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_USER_SEARCH, {'search_type': 'id'})
    
    await query.edit_message_text(
        "🔍 <b>البحث عن مستخدم بالآيدي</b>\n\n"
        "أرسل الآن <b>آيدي المستخدم</b> (أرقام فقط):\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_search_by_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث بالاسم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_USER_SEARCH, {'search_type': 'name'})
    
    await query.edit_message_text(
        "🔍 <b>البحث عن مستخدم بالاسم</b>\n\n"
        "أرسل الآن <b>اسم المستخدم</b> (كامل أو جزء منه):\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_search_by_username_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث باليوزر"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_USER_SEARCH, {'search_type': 'username'})
    
    await query.edit_message_text(
        "🔍 <b>البحث عن مستخدم باليوزر</b>\n\n"
        "أرسل الآن <b>يوزر المستخدم</b> (بدون @):\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن مستخدم باستخدام طرق متعددة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_USER_SEARCH
    
    search_input = update.message.text.strip()
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    search_type = conv_data.get('search_type', 'id')
    
    user = None
    
    try:
        if search_type == 'id':
            # البحث بالآيدي
            user_id = int(search_input)
            user = await db.get_user(user_id)
        
        elif search_type == 'name':
            # البحث بالاسم
            all_users = await db.get_all_users(exclude_banned=False, limit=100)
            for u in all_users:
                if search_input.lower() in (u['full_name'] or "").lower():
                    user = u
                    break
        
        elif search_type == 'username':
            # البحث باليوزر
            all_users = await db.get_all_users(exclude_banned=False, limit=100)
            for u in all_users:
                if search_input.lower() in (u['username'] or "").lower():
                    user = u
                    break
    
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط للبحث بالآيدي!")
        return STATE_USER_SEARCH
    
    if not user:
        await update.message.reply_text("❌ المستخدم غير موجود!")
        return STATE_USER_SEARCH
    
    # حفظ بيانات المستخدم
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_USER_MANAGE,
        {
            'managed_user_id': user['user_id'],
            'managed_user_name': user['full_name'],
            'managed_user_data': dict(user)
        }
    )
    
    # عرض بيانات المستخدم
    await show_user_management_panel(update, context, user)
    return STATE_USER_MANAGE

async def show_user_management_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_data):
    """عرض لوحة إدارة المستخدم"""
    user_id = user_data['user_id']
    full_name = user_data['full_name'] or 'غير معروف'
    username = user_data['username'] or 'لا يوجد'
    points = user_data['points']
    is_banned = user_data['is_banned']
    warnings = user_data['warnings']
    total_earned = user_data['total_earned']
    total_spent = user_data['total_spent']
    joined_date = format_datetime(user_data['joined_date'])
    last_active = format_datetime(user_data['last_active'])
    is_active = user_data.get('is_active', 1)
    
    # الحصول على عدد الإحالات
    referrals_result = await db.execute_query_one(
        "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = ?",
        (user_id,)
    )
    referral_count = referrals_result['count'] if referrals_result else 0
    
    text = (
        f"✅ <b>تم العثور على المستخدم:</b>\n\n"
        f"👤 <b>معلومات أساسية:</b>\n"
        f"• الاسم: {full_name}\n"
        f"• 🆔 الآيدي: <code>{user_id}</code>\n"
        f"• 📛 اليوزر: @{username}\n"
        f"• 🎯 النقاط: {format_number(points)}\n"
        f"• ⚠️ التحذيرات: {warnings}\n"
        f"• 🚫 الحالة: {'محظور' if is_banned else 'نشط'}\n"
        f"• 📱 النشاط: {'نشط' if is_active == 1 else 'غير نشط'}\n\n"
        f"📊 <b>إحصائيات:</b>\n"
        f"• 💰 إجمالي المكتسب: {format_number(total_earned)}\n"
        f"• 💸 إجمالي المنفق: {format_number(total_spent)}\n"
        f"• 👥 عدد الإحالات: {referral_count}\n"
        f"• 📅 تاريخ التسجيل: {joined_date}\n"
        f"• 🕐 آخر نشاط: {last_active}\n\n"
        f"👇 اختر الإجراء المطلوب:"
    )
    
    kb_buttons = []
    
    if not is_banned:
        kb_buttons.append([
            InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_add_points"),
            InlineKeyboardButton("➖ خصم نقاط", callback_data="admin_deduct_points")
        ])
        
        kb_buttons.append([
            InlineKeyboardButton("⚠️ إضافة تحذير", callback_data="admin_add_warning"),
            InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_ban_user")
        ])
        
        if is_active == 0:
            kb_buttons.append([
                InlineKeyboardButton("✅ تفعيل الحساب", callback_data="admin_activate_user")
            ])
    else:
        kb_buttons.append([
            InlineKeyboardButton("✅ فك الحظر", callback_data="admin_unban_user")
        ])
    
    kb_buttons.append([
        InlineKeyboardButton("📜 عرض السجل", callback_data="admin_view_history"),
        InlineKeyboardButton("🔄 تحديث البيانات", callback_data="admin_refresh_user")
    ])
    
    kb_buttons.append([
        InlineKeyboardButton("📨 إرسال رسالة", callback_data="admin_message_user"),
        InlineKeyboardButton("👥 عرض الإحالات", callback_data="admin_view_referrals")
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
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await conv_manager.update_conversation(
        query.from_user.id,
        STATE_ADD_POINTS,
        {'action': 'add_points', 'target_user_id': user_id}
    )
    
    await query.edit_message_text(
        f"➕ <b>إضافة نقاط للمستخدم</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>\n\n"
        f"أرسل <b>عدد النقاط</b> التي تريد إضافتها:\n\n"
        f"📌 <b>ملاحظة:</b> أدخل أرقاماً فقط\n"
        f"❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_ADD_POINTS

async def admin_deduct_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء خصم نقاط من المستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await conv_manager.update_conversation(
        query.from_user.id,
        STATE_DEDUCT_POINTS,
        {'action': 'deduct_points', 'target_user_id': user_id}
    )
    
    await query.edit_message_text(
        f"➖ <b>خصم نقاط من المستخدم</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>\n\n"
        f"أرسل <b>عدد النقاط</b> التي تريد خصمها:\n\n"
        f"📌 <b>ملاحظة:</b> أدخل أرقاماً فقط\n"
        f"❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_DEDUCT_POINTS

async def admin_process_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إضافة/خصم النقاط"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        current_state = await conv_manager.get_conversation_state(update.effective_user.id)
        return current_state
    
    try:
        points = int(update.message.text.strip())
        
        if points <= 0:
            await update.message.reply_text("❌ عدد النقاط يجب أن يكون أكبر من صفر!\nأعد إرسال العدد:")
            current_state = await conv_manager.get_conversation_state(update.effective_user.id)
            return current_state
        
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        action = conv_data.get('action')
        user_id = conv_data.get('target_user_id')
        user_name = conv_data.get('managed_user_name', 'مستخدم')
        
        if action == 'add_points':
            # إضافة النقاط
            await db.update_points(user_id, points, "admin_add", f"إضافة بواسطة الأدمن: {update.effective_user.full_name}")
            
            # إرسال إشعار للمستخدم
            try:
                user_msg = f"✅ <b>تمت إضافة {points} نقطة لحسابك!</b>\n\n👤 الإداري: {update.effective_user.full_name}"
                await safe_api_call(context.bot.send_message, user_id, user_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار للمستخدم: {e}")
            
            result_text = f"✅ تمت إضافة {points} نقطة للمستخدم {user_name}"
            
        elif action == 'deduct_points':
            # التحقق من رصيد المستخدم
            user_data = await db.get_user(user_id)
            if user_data and user_data['points'] < points:
                await update.message.reply_text(f"❌ رصيد المستخدم غير كافي! الرصيد الحالي: {user_data['points']}\nأعد إرسال عدد أقل:")
                current_state = await conv_manager.get_conversation_state(update.effective_user.id)
                return current_state
            
            # خصم النقاط
            await db.update_points(user_id, -points, "admin_deduct", f"خصم بواسطة الأدمن: {update.effective_user.full_name}")
            
            # إرسال إشعار للمستخدم
            try:
                user_msg = f"⚠️ <b>تم خصم {points} نقطة من حسابك!</b>\n\n👤 الإداري: {update.effective_user.full_name}"
                await safe_api_call(context.bot.send_message, user_id, user_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار للمستخدم: {e}")
            
            result_text = f"✅ تم خصم {points} نقطة من المستخدم {user_name}"
        
        else:
            await update.message.reply_text("❌ إجراء غير معروف!")
            await conv_manager.end_conversation(update.effective_user.id)
            return ConversationHandler.END
        
        # العودة إلى لوحة إدارة المستخدم
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        user_data = conv_data.get('managed_user_data')
        
        if user_data:
            await update.message.reply_text(result_text)
            await show_user_management_panel(update, context, user_data)
            await conv_manager.end_conversation(update.effective_user.id)
            return ConversationHandler.END
        else:
            await update.message.reply_text("❌ حدث خطأ في استعادة بيانات المستخدم!")
            await conv_manager.end_conversation(update.effective_user.id)
            await admin_users_menu(update, context)
            return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال أرقام فقط!\nأعد إرسال العدد:")
        current_state = await conv_manager.get_conversation_state(update.effective_user.id)
        return current_state

async def admin_ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حظر مستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await db.ban_user(user_id, "حظر يدوي", query.from_user.id)
    
    # إرسال إشعار للمستخدم
    try:
        user_msg = "🚫 <b>تم حظر حسابك!</b>\n\nللمزيد من المعلومات، تواصل مع الدعم."
        await safe_api_call(context.bot.send_message, user_id, user_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ في إرسال إشعار الحظر: {e}")
    
    await query.edit_message_text(
        f"✅ <b>تم حظر المستخدم بنجاح!</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>\n"
        f"👤 المحظِر: {query.from_user.full_name}",
        parse_mode="HTML"
    )
    
    # تحديث بيانات المستخدم
    user_data = await db.get_user(user_id)
    if user_data:
        await show_user_management_panel(update, context, user_data)

async def admin_unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فك حظر مستخدم"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    conv_data = await conv_manager.get_conversation_data(query.from_user.id)
    user_id = conv_data.get('managed_user_id')
    user_name = conv_data.get('managed_user_name', 'مستخدم')
    
    await db.unban_user(user_id, query.from_user.id)
    
    # إرسال إشعار للمستخدم
    try:
        user_msg = "✅ <b>تم فك حظر حسابك!</b>\n\nيمكنك الآن استخدام البوت مرة أخرى."
        await safe_api_call(context.bot.send_message, user_id, user_msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"خطأ في إرسال إشعار فك الحظر: {e}")
    
    await query.edit_message_text(
        f"✅ <b>تم فك حظر المستخدم بنجاح!</b>\n\n"
        f"👤 المستخدم: {user_name}\n"
        f"🆔 الآيدي: <code>{user_id}</code>\n"
        f"👤 المفعِل: {query.from_user.full_name}",
        parse_mode="HTML"
    )
    
    # تحديث بيانات المستخدم
    user_data = await db.get_user(user_id)
    if user_data:
        await show_user_management_panel(update, context, user_data)

async def admin_cancel_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إدارة المستخدم"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء العملية.")
    await admin_users_menu(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📤 نظام الإذاعة المتطور المحسن مع إدارة Flood
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإذاعة مع خيارات متقدمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على إحصائيات الإذاعات
    broadcast_stats = await db.execute_query_one(
        "SELECT COUNT(*) as total, SUM(sent_to) as total_sent, SUM(failed_to) as total_failed FROM broadcasts"
    )
    
    total_broadcasts = broadcast_stats['total'] if broadcast_stats else 0
    total_sent = broadcast_stats['total_sent'] if broadcast_stats and broadcast_stats['total_sent'] else 0
    total_failed = broadcast_stats['total_failed'] if broadcast_stats and broadcast_stats['total_failed'] else 0
    
    text = (
        f"📤 <b>نظام الإذاعة المتطور</b>\n\n"
        f"📊 <b>إحصائيات الإذاعات:</b>\n"
        f"• 📨 عدد الإذاعات: {format_number(total_broadcasts)}\n"
        f"• ✅ تم الإرسال: {format_number(total_sent)}\n"
        f"• ❌ فشل الإرسال: {format_number(total_failed)}\n\n"
        f"🎯 <b>خيارات الإرسال:</b>\n"
        "• 📝 نص فقط\n"
        "• 🖼️ صورة مع نص\n"
        "• 🎬 فيديو مع نص\n"
        "• 📁 ملف مع نص\n\n"
        f"⚡ <b>ميزات متقدمة:</b>\n"
        "• 📌 تثبيت الرسالة\n"
        "• ⏱️ تأخير ذكي\n"
        "• 🎯 إرسال لمجموعات محددة\n"
        "• 📊 متابعة فورية\n"
        "• 💾 حفظ القوالب"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إذاعة نصية", callback_data="broadcast_text"),
         InlineKeyboardButton("🖼️ إذاعة بالصورة", callback_data="broadcast_photo")],
        [InlineKeyboardButton("🎬 إذاعة بالفيديو", callback_data="broadcast_video"),
         InlineKeyboardButton("📁 إذاعة بملف", callback_data="broadcast_document")],
        [InlineKeyboardButton("📊 إحصائيات الإذاعات", callback_data="broadcast_stats"),
         InlineKeyboardButton("💾 قوالب جاهزة", callback_data="broadcast_templates")],
        [InlineKeyboardButton("⚙️ إعدادات الإذاعة", callback_data="broadcast_settings"),
         InlineKeyboardButton("🔄 الإذاعات السابقة", callback_data="broadcast_history")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء الإذاعة مع بدء محادثة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    media_type = query.data.replace("broadcast_", "")
    
    await conv_manager.start_conversation(
        query.from_user.id,
        STATE_BROADCAST_MESSAGE,
        {'broadcast_media': media_type}
    )
    
    media_names = {
        'text': 'نصية',
        'photo': 'بالصورة',
        'video': 'بفيديو',
        'document': 'بملف'
    }
    
    media_name = media_names.get(media_type, 'نصية')
    
    instructions = {
        'text': "أرسل نص الرسالة فقط.",
        'photo': "أرسل نص الرسالة أولاً، ثم أرسل الصورة.",
        'video': "أرسل نص الرسالة أولاً، ثم أرسل الفيديو.",
        'document': "أرسل نص الرسالة أولاً، ثم أرسل الملف."
    }
    
    await query.edit_message_text(
        f"📤 <b>إعداد إذاعة {media_name}</b>\n\n"
        f"📝 <b>الخطوة 1/2:</b> أرسل نص الرسالة\n\n"
        f"{instructions[media_type]}\n\n"
        f"📌 <b>ملاحظات:</b>\n"
        "• يمكنك استخدام HTML للتنسيق\n"
        "• الوسوم المدعومة: <b>عريض</b>, <i>مائل</i>, <code>كود</code>\n"
        "• الروابط: <a href='رابط'>نص</a>\n"
        "• الحد الأقصى: 1000 حرف\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_get_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على نص الإذاعة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_BROADCAST_MESSAGE
    
    message_text = update.message.text
    
    if len(message_text) > 1000:
        await update.message.reply_text(
            "❌ النص طويل جداً! الحد الأقصى 1000 حرف.\n"
            "أعد إرسال نص أقصر:"
        )
        return STATE_BROADCAST_MESSAGE
    
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_BROADCAST_MESSAGE,
        {'broadcast_message': message_text}
    )
    
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    media_type = conv_data.get('broadcast_media', 'text')
    
    if media_type == 'text':
        # الانتقال مباشرة للخيارات
        await show_broadcast_options(update, context, message_text, media_type)
        await conv_manager.end_conversation(update.effective_user.id)
        return ConversationHandler.END
    else:
        media_names = {
            'photo': 'صورة',
            'video': 'فيديو',
            'document': 'ملف'
        }
        
        await update.message.reply_text(
            f"✅ تم حفظ النص ({len(message_text)} حرف).\n\n"
            f"📁 <b>الخطوة 2/2:</b> أرسل ال{media_names[media_type]}\n\n"
            f"❌ للإلغاء، أرسل /cancel"
        )
        return STATE_BROADCAST_MEDIA

async def admin_get_broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على الوسائط للإذاعة"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_BROADCAST_MEDIA
    
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    media_type = conv_data.get('broadcast_media')
    message_text = conv_data.get('broadcast_message', '')
    
    file_id = None
    
    try:
        if media_type == "photo" and update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif media_type == "video" and update.message.video:
            file_id = update.message.video.file_id
        elif media_type == "document" and update.message.document:
            file_id = update.message.document.file_id
        
        if not file_id:
            raise ValueError("نوع الملف غير مطابق")
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_BROADCAST_MEDIA,
            {'broadcast_file_id': file_id}
        )
        
        # عرض خيارات الإرسال
        await show_broadcast_options(update, context, message_text, media_type, file_id)
        await conv_manager.end_conversation(update.effective_user.id)
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"خطأ في معالجة الوسائط: {e}")
        
        media_names = {
            'photo': 'صورة',
            'video': 'فيديو',
            'document': 'ملف'
        }
        
        await update.message.reply_text(
            f"❌ لم يتم إرسال {media_names[media_type]}!\n"
            f"يرجى إرسال {media_names[media_type]} صالح.\n\n"
            f"أعد إرسال {media_names[media_type]}:"
        )
        return STATE_BROADCAST_MEDIA

async def show_broadcast_options(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                               message: str, media_type: str, file_id: str = None):
    """عرض خيارات الإرسال"""
    media_names = {
        'text': '📝 نص',
        'photo': '🖼️ صورة',
        'video': '🎬 فيديو',
        'document': '📁 ملف'
    }
    
    media_name = media_names.get(media_type, '📝 نص')
    
    # عرض معاينة
    preview_text = message[:100] + "..." if len(message) > 100 else message
    
    text = (
        f"📋 <b>معاينة الإذاعة</b>\n\n"
        f"📊 <b>نوع الإذاعة:</b> {media_name}\n"
        f"📝 <b>النص:</b> {preview_text}\n\n"
        f"👇 اختر خيار الإرسال:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ إرسال عادي", callback_data="broadcast_send_normal"),
         InlineKeyboardButton("📌 إرسال مع تثبيت", callback_data="broadcast_send_pin")],
        [InlineKeyboardButton("🎯 إرسال لمجموعة محددة", callback_data="broadcast_send_group")],
        [InlineKeyboardButton("✏️ تعديل النص", callback_data="broadcast_edit_text"),
         InlineKeyboardButton("🔄 تغيير الوسائط", callback_data="broadcast_edit_media")],
        [InlineKeyboardButton("💾 حفظ كقالب", callback_data="broadcast_save_template"),
         InlineKeyboardButton("❌ إلغاء", callback_data="admin_broadcast")]
    ])
    
    # حفظ البيانات في context للمرحلة القادمة
    context.user_data['broadcast_data'] = {
        'message': message,
        'media_type': media_type,
        'file_id': file_id
    }
    
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_send_broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ الإذاعة مع إدارة Flood"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على بيانات الإذاعة
    broadcast_data = context.user_data.get('broadcast_data', {})
    message = broadcast_data.get('message', '')
    media_type = broadcast_data.get('media_type', 'text')
    file_id = broadcast_data.get('file_id')
    
    # تحديد إذا كان تثبيت
    pin_message = query.data == "broadcast_send_pin"
    
    # الحصول على جميع المستخدمين
    all_users = await db.get_all_users(exclude_banned=True)
    total_users = len(all_users)
    
    if total_users == 0:
        await query.edit_message_text("❌ لا يوجد مستخدمين لإرسال الرسالة لهم!")
        clean_context_data(context, ['broadcast_data'])
        return
    
    # إنشاء سجل للإذاعة
    broadcast_id = await db.add_broadcast(
        message=message,
        media_type=media_type,
        media_file_id=file_id or "",
        sent_by=query.from_user.id,
        total_users=total_users,
        tags="normal" if not pin_message else "pinned"
    )
    
    if broadcast_id == -1:
        await query.edit_message_text("❌ فشل في إنشاء سجل الإذاعة!")
        clean_context_data(context, ['broadcast_data'])
        return
    
    # إعداد الرسالة التقدمية
    progress_msg = await query.edit_message_text(
        f"⏳ <b>جاري إرسال الإذاعة...</b>\n\n"
        f"📊 الإحصائيات الأولية:\n"
        f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
        f"• ✅ تم إرسال: 0\n"
        f"• ❌ فشل: 0\n"
        f"• 📌 التثبيت: {'نعم' if pin_message else 'لا'}\n"
        f"• ⏱️ الحالة: تجهيز...",
        parse_mode="HTML"
    )
    
    sent_count = 0
    failed_count = 0
    failed_users_details = []
    
    # إعدادات Flood Control
    broadcast_delay = await db.get_setting("broadcast_delay", 0.1)
    max_users_per_batch = await db.get_setting("max_broadcast_users", 50)
    batch_delay = 1.0  # تأخير بين الباتشات
    
    # تنفيذ الإرسال مع إدارة Flood
    for batch_start in range(0, total_users, max_users_per_batch):
        batch_end = min(batch_start + max_users_per_batch, total_users)
        batch_users = all_users[batch_start:batch_end]
        
        batch_sent = 0
        batch_failed = 0
        
        for user_data in batch_users:
            user_id = user_data['user_id']
            full_name = user_data['full_name'] or "مستخدم"
            
            try:
                if media_type == "text":
                    msg = await safe_api_call(
                        context.bot.send_message,
                        chat_id=user_id,
                        text=message,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    if pin_message and msg:
                        try:
                            await safe_api_call(context.bot.pin_chat_message, user_id, msg.message_id, disable_notification=True)
                        except:
                            pass
                
                elif media_type == "photo":
                    msg = await safe_api_call(
                        context.bot.send_photo,
                        chat_id=user_id,
                        photo=file_id,
                        caption=message,
                        parse_mode="HTML"
                    )
                    if pin_message and msg:
                        try:
                            await safe_api_call(context.bot.pin_chat_message, user_id, msg.message_id, disable_notification=True)
                        except:
                            pass
                
                elif media_type == "video":
                    msg = await safe_api_call(
                        context.bot.send_video,
                        chat_id=user_id,
                        video=file_id,
                        caption=message,
                        parse_mode="HTML"
                    )
                    if pin_message and msg:
                        try:
                            await safe_api_call(context.bot.pin_chat_message, user_id, msg.message_id, disable_notification=True)
                        except:
                            pass
                
                elif media_type == "document":
                    msg = await safe_api_call(
                        context.bot.send_document,
                        chat_id=user_id,
                        document=file_id,
                        caption=message,
                        parse_mode="HTML"
                    )
                    if pin_message and msg:
                        try:
                            await safe_api_call(context.bot.pin_chat_message, user_id, msg.message_id, disable_notification=True)
                        except:
                            pass
                
                if msg:
                    sent_count += 1
                    batch_sent += 1
                else:
                    failed_count += 1
                    batch_failed += 1
                    failed_users_details.append(f"{full_name} ({user_id}) - فشل في الإرسال")
                
            except Forbidden:
                failed_count += 1
                batch_failed += 1
                failed_users_details.append(f"{full_name} ({user_id}) - حظر البوت")
            except BadRequest as e:
                failed_count += 1
                batch_failed += 1
                failed_users_details.append(f"{full_name} ({user_id}) - {str(e)[:50]}")
            except TimedOut:
                failed_count += 1
                batch_failed += 1
                failed_users_details.append(f"{full_name} ({user_id}) - انتهت المهلة")
            except Exception as e:
                failed_count += 1
                batch_failed += 1
                failed_users_details.append(f"{full_name} ({user_id}) - {str(e)[:50]}")
            
            # تأخير بين الإرسالات داخل الباتش
            await asyncio.sleep(broadcast_delay)
        
        # تحديث الرسالة التقدمية بعد كل باتش
        progress = int((batch_end / total_users) * 100)
        remaining = total_users - batch_end
        
        await progress_msg.edit_text(
            f"⏳ <b>جاري إرسال الإذاعة...</b>\n\n"
            f"📊 الإحصائيات:\n"
            f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
            f"• ✅ تم إرسال: {format_number(sent_count)} ({progress}%)\n"
            f"• ❌ فشل: {format_number(failed_count)}\n"
            f"• 📌 التثبيت: {'نعم' if pin_message else 'لا'}\n"
            f"• 📦 الباتش الحالي: {batch_sent} ✅, {batch_failed} ❌\n"
            f"• ⏱️ المتبقي: {format_number(remaining)} مستخدم",
            parse_mode="HTML"
        )
        
        # تأخير بين الباتشات (Flood Control)
        if batch_end < total_users:
            await asyncio.sleep(batch_delay)
    
    # تحديث إحصائيات الإذاعة
    await db.update_broadcast_stats(broadcast_id, sent_count, failed_count)
    
    # النتائج النهائية
    success_rate = (sent_count / total_users * 100) if total_users > 0 else 0
    
    result_text = (
        f"✅ <b>تم إكمال الإذاعة!</b>\n\n"
        f"📊 <b>النتائج النهائية:</b>\n"
        f"• 👥 إجمالي المستخدمين: {format_number(total_users)}\n"
        f"• ✅ تم الإرسال بنجاح: {format_number(sent_count)}\n"
        f"• ❌ فشل في الإرسال: {format_number(failed_count)}\n"
        f"• 📈 نسبة النجاح: {success_rate:.1f}%\n"
        f"• 📌 تم التثبيت: {'نعم' if pin_message else 'لا'}\n"
        f"• 🆔 رقم الإذاعة: #{broadcast_id}\n"
        f"• ⏱️ وقت الإكمال: {datetime.now().strftime('%H:%M:%S')}\n\n"
    )
    
    if failed_users_details and failed_count <= 10:
        result_text += "<b>بعض المستخدمين الذين فشل الإرسال لهم:</b>\n"
        for i, detail in enumerate(failed_users_details[:10], 1):
            result_text += f"{i}. {detail}\n"
    
    kb_buttons = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_broadcast")]]
    
    if failed_count > 0:
        kb_buttons.insert(0, [InlineKeyboardButton("🔄 إعادة إرسال للفاشلين", callback_data=f"retry_failed_{broadcast_id}")])
    
    kb = InlineKeyboardMarkup(kb_buttons)
    
    await progress_msg.edit_text(result_text, reply_markup=kb, parse_mode="HTML")
    
    # تنظيف البيانات المؤقتة
    clean_context_data(context, ['broadcast_data'])

async def admin_cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء الإذاعة"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء عملية الإذاعة.")
    await admin_broadcast_menu(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎫 نظام الأكواد المحسن مع معالجة أخطاء متقدمة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_codes_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة إدارة الأكواد"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على إحصائيات الأكواد
    active_codes = await db.get_all_promo_codes(active_only=True)
    total_codes = await db.execute_query_one("SELECT COUNT(*) as count FROM promo_codes")
    total_codes_count = total_codes['count'] if total_codes else 0
    
    text = (
        f"🎫 <b>إدارة الأكواد الترويجية</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"• 🎫 إجمالي الأكواد: {format_number(total_codes_count)}\n"
        f"• 🟢 الأكواد النشطة: {format_number(len(active_codes))}\n\n"
        f"👇 اختر الإجراء المطلوب:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إنشاء كود جديد", callback_data="admin_create_code")],
        [InlineKeyboardButton("📋 عرض جميع الأكواد", callback_data="admin_list_codes"),
         InlineKeyboardButton("🔄 تحديث القائمة", callback_data="admin_codes")],
        [InlineKeyboardButton("🔍 بحث عن كود", callback_data="admin_search_code"),
         InlineKeyboardButton("📊 إحصائيات الأكواد", callback_data="admin_codes_stats")],
        [InlineKeyboardButton("🔧 إدارة الكود", callback_data="admin_manage_code"),
         InlineKeyboardButton("🗑️ حذف كود", callback_data="admin_delete_code")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_create_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إنشاء كود جديد"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_CREATE_CODE)
    
    await query.edit_message_text(
        "🎫 <b>إنشاء كود ترويجي جديد</b>\n\n"
        "أرسل <b>اسم الكود</b> (بدون مسافات، بالإنجليزية):\n\n"
        "📌 <b>مثال:</b> WELCOME2024\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )

async def admin_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الكود الجديد"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CREATE_CODE
    
    code = update.message.text.strip().upper()
    
    # التحقق من صحة الكود
    if not code.isalnum():
        await update.message.reply_text(
            "❌ الكود يجب أن يحتوي على أحرف وأرقام فقط!\n"
            "أعد إرسال الكود:"
        )
        return STATE_CREATE_CODE
    
    # التحقق من عدم تكرار الكود
    existing_code = await db.get_promo_code(code)
    if existing_code:
        await update.message.reply_text(
            f"❌ الكود <code>{code}</code> موجود مسبقاً!\n"
            "أعد إرسال كود مختلف:"
        )
        return STATE_CREATE_CODE
    
    # حفظ الكود مؤقتاً
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_CREATE_CODE,
        {'new_code': code}
    )
    
    await update.message.reply_text(
        f"✅ الكود <code>{code}</code> مقبول.\n\n"
        "الآن أرسل <b>عدد النقاط</b> التي يعطيها الكود (أرقام فقط):\n\n"
        "❌ للإلغاء، أرسل /cancel"
    )
    return STATE_POINTS_AMOUNT

async def admin_get_code_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على عدد نقاط الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_POINTS_AMOUNT
    
    try:
        points = int(update.message.text.strip())
        
        if points <= 0:
            await update.message.reply_text(
                "❌ عدد النقاط يجب أن يكون أكبر من صفر!\n"
                "أعد إرسال عدد النقاط:"
            )
            return STATE_POINTS_AMOUNT
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_POINTS_AMOUNT,
            {'code_points': points}
        )
        
        await update.message.reply_text(
            f"✅ تم تعيين النقاط: {points}\n\n"
            "الآن أرسل <b>الحد الأقصى لعدد المستخدمين</b> الذين يمكنهم استخدام الكود:\n\n"
            "📌 <b>ملاحظة:</b> اكتب 0 ليكون غير محدود\n\n"
            "❌ للإلغاء، أرسل /cancel"
        )
        return STATE_CODE_EXPIRY
    
    except ValueError:
        await update.message.reply_text(
            "❌ يجب إدخال أرقام فقط!\n"
            "أعد إرسال عدد النقاط:"
        )
        return STATE_POINTS_AMOUNT

async def admin_get_code_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على صلاحية الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CODE_EXPIRY
    
    try:
        max_uses = int(update.message.text.strip())
        
        if max_uses < 0:
            await update.message.reply_text(
                "❌ العدد يجب أن يكون 0 أو أكثر!\n"
                "أعد إرسال العدد:"
            )
            return STATE_CODE_EXPIRY
        
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_CODE_EXPIRY,
            {'code_max_uses': max_uses}
        )
        
        await update.message.reply_text(
            f"✅ الحد الأقصى: {max_uses if max_uses > 0 else 'غير محدود'}\n\n"
            "الآن أرسل <b>عدد أيام الصلاحية</b>:\n\n"
            "📌 <b>ملاحظة:</b> اكتب 0 ليكون الكود دائماً\n\n"
            "❌ للإلغاء، أرسل /cancel"
        )
        return STATE_CONFIRM_ACTION
    
    except ValueError:
        await update.message.reply_text(
            "❌ يجب إدخال أرقام فقط!\n"
            "أعد إرسال العدد:"
        )
        return STATE_CODE_EXPIRY

async def admin_finish_code_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء إنشاء الكود"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CONFIRM_ACTION
    
    try:
        expiry_days = int(update.message.text.strip())
        
        if expiry_days < 0:
            await update.message.reply_text(
                "❌ عدد الأيام يجب أن يكون 0 أو أكثر!\n"
                "أعد إرسال عدد الأيام:"
            )
            return STATE_CONFIRM_ACTION
        
        # الحصول على جميع بيانات الكود
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        code = conv_data.get('new_code')
        points = conv_data.get('code_points')
        max_uses = conv_data.get('code_max_uses', 1)
        
        # إنشاء الكود
        success = await db.create_promo_code(
            code=code,
            points=points,
            max_uses=max_uses if max_uses > 0 else 999999,
            created_by=update.effective_user.id,
            expires_days=expiry_days if expiry_days > 0 else 0,
            description=f"كود تم إنشاؤه بواسطة {update.effective_user.full_name}"
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
                f"• الصلاحية: {expiry_text}\n\n"
                f"📋 <b>للاستخدام:</b>\n"
                f"استخدم الأمر /redeem ثم أدخل الكود"
            )
            
            await update.message.reply_text(success_msg, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ فشل في إنشاء الكود!")
        
        await conv_manager.end_conversation(update.effective_user.id)
        await admin_codes_menu(update, context)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ يجب إدخال أرقام فقط!\n"
            "أعد إرسال عدد الأيام:"
        )
        return STATE_CONFIRM_ACTION

async def admin_cancel_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إنشاء كود"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء إنشاء الكود.")
    await admin_codes_menu(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📞 نظام الدعم الفني المحسن
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الدعم الفني"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
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
        "مرحباً بك في مركز الدعم. يمكنك:\n\n"
        "• 📨 إنشاء تذكرة دعم جديدة\n"
        "• 📋 متابعة تذاكرك السابقة\n"
        "• 🗣️ التواصل المباشر مع الدعم\n"
        "• ❓ الأسئلة الشائعة\n\n"
        "👇 اختر الخيار المناسب:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 إنشاء تذكرة جديدة", callback_data="create_ticket")],
        [InlineKeyboardButton("📋 تذاكري المفتوحة", callback_data="my_open_tickets"),
         InlineKeyboardButton("📁 تذاكري المغلقة", callback_data="my_closed_tickets")],
        [InlineKeyboardButton("❓ الأسئلة الشائعة", callback_data="faq"),
         InlineKeyboardButton("🗣️ تواصل مباشر", callback_data="direct_contact")],
        [InlineKeyboardButton("🔙 الرجوع", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def create_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إنشاء تذكرة دعم"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_SUPPORT_TICKET)
    
    text = (
        "📨 <b>إنشاء تذكرة دعم جديدة</b>\n\n"
        "الخطوة 1/2: اختر <b>فئة المشكلة</b>:\n\n"
        "• 🐛 مشكلة تقنية\n"
        "• 💰 مشكلة في الدفع\n"
        "• 🎯 مشكلة في النقاط\n"
        "• 👤 مشكلة في الحساب\n"
        "• 📢 اقتراح أو فكرة\n"
        "• ❓ استفسار عام\n\n"
        "أرسل رقم الفئة (1-6) أو اسم الفئة:\n\n"
        "❌ للإلغاء، أرسل /cancel"
    )
    
    await query.edit_message_text(text, parse_mode="HTML")

async def process_ticket_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة فئة التذكرة"""
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_SUPPORT_TICKET
    
    user_input = update.message.text.strip()
    
    category_map = {
        '1': 'technical', '🐛': 'technical', 'تقنية': 'technical',
        '2': 'payment', '💰': 'payment', 'دفع': 'payment',
        '3': 'points', '🎯': 'points', 'نقاط': 'points',
        '4': 'account', '👤': 'account', 'حساب': 'account',
        '5': 'suggestion', '📢': 'suggestion', 'اقتراح': 'suggestion',
        '6': 'general', '❓': 'general', 'عام': 'general'
    }
    
    category = category_map.get(user_input.lower())
    
    if not category:
        await update.message.reply_text(
            "❌ فئة غير صحيحة!\n"
            "أعد إرسال رقم الفئة (1-6) أو اسمها:\n\n"
            "❌ للإلغاء، أرسل /cancel"
        )
        return STATE_SUPPORT_TICKET
    
    category_names = {
        'technical': '🐛 مشكلة تقنية',
        'payment': '💰 مشكلة في الدفع',
        'points': '🎯 مشكلة في النقاط',
        'account': '👤 مشكلة في الحساب',
        'suggestion': '📢 اقتراح أو فكرة',
        'general': '❓ استفسار عام'
    }
    
    await conv_manager.update_conversation(
        update.effective_user.id,
        STATE_SUPPORT_TICKET,
        {'ticket_category': category}
    )
    
    await update.message.reply_text(
        f"✅ الفئة: {category_names[category]}\n\n"
        "الخطوة 2/2: اكتب <b>وصف المشكلة</b>:\n\n"
        "📌 <b>نصائح:</b>\n"
        "• كن واضحاً ومفصلاً\n"
        "• أرفق أية رسائل خطأ\n"
        "• اذكر خطوات تكرار المشكلة\n\n"
        "❌ للإلغاء، أرسل /cancel"
    )
    return STATE_CONFIRM_ACTION

async def finish_ticket_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنهاء إنشاء التذكرة"""
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_CONFIRM_ACTION
    
    description = update.message.text.strip()
    
    if len(description) < 10:
        await update.message.reply_text(
            "❌ الوصف قصير جداً! يجب أن يكون 10 أحرف على الأقل.\n"
            "أعد إرسال الوصف:"
        )
        return STATE_CONFIRM_ACTION
    
    conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
    category = conv_data.get('ticket_category', 'general')
    
    category_names = {
        'technical': 'مشكلة تقنية',
        'payment': 'مشكلة في الدفع',
        'points': 'مشكلة في النقاط',
        'account': 'مشكلة في الحساب',
        'suggestion': 'اقتراح أو فكرة',
        'general': 'استفسار عام'
    }
    
    subject = f"{category_names[category]} - {update.effective_user.full_name}"
    
    # إنشاء التذكرة
    ticket_id = await db.create_support_ticket(
        user_id=update.effective_user.id,
        subject=subject,
        message=description,
        category=category
    )
    
    if ticket_id != -1:
        # إرسال إشعار للأدمن
        try:
            admin_notification = (
                f"📨 <b>تذكرة دعم جديدة #{ticket_id}</b>\n\n"
                f"👤 المستخدم: {get_user_link(update.effective_user.id, update.effective_user.full_name)}\n"
                f"📝 الفئة: {category_names[category]}\n"
                f"📄 الوصف: {description[:200]}..."
            )
            await safe_api_call(context.bot.send_message, ADMIN_ID, admin_notification, parse_mode="HTML")
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن: {e}")
        
        await update.message.reply_text(
            f"✅ <b>تم إنشاء تذكرتك بنجاح!</b>\n\n"
            f"🎫 رقم التذكرة: <code>#{ticket_id}</code>\n"
            f"📝 الفئة: {category_names[category]}\n"
            f"⏱️ وقت الإنشاء: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"📌 <b>معلومات مهمة:</b>\n"
            f"• سيتم الرد على تذكرتك خلال 24 ساعة\n"
            f"• يمكنك متابعة التذكرة من قائمة الدعم\n"
            f"• لا تنشئ تذاكر متعددة لنفس المشكلة\n\n"
            f"شكراً لتواصلك معنا! 🙏",
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "❌ <b>فشل في إنشاء التذكرة!</b>\n\n"
            "يرجى المحاولة مرة أخرى لاحقاً أو التواصل مباشرة مع الإدارة.",
            parse_mode="HTML"
        )
    
    await conv_manager.end_conversation(update.effective_user.id)
    await send_dashboard(update, context)
    return ConversationHandler.END

async def cancel_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء إنشاء تذكرة"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء إنشاء التذكرة.")
    await support_handler(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔧 دوال الأدمن المكتملة التي كانت فارغة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_analytics_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإحصائيات المتقدمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على إحصائيات متقدمة
    users_count, total_points, total_tx, total_stars, last_24h_tx, total_referrals, daily_active_users = await db.get_global_stats()
    new_users_today = await db.get_new_users_stats(1)
    new_users_week = await db.get_new_users_stats(7)
    new_users_month = await db.get_new_users_stats(30)
    
    # إحصائيات الأكواد
    total_codes = await db.execute_query_one("SELECT COUNT(*) as count FROM promo_codes")
    total_codes_count = total_codes['count'] if total_codes else 0
    active_codes = await db.get_all_promo_codes(active_only=True)
    
    # إحصائيات القنوات
    total_channels = await db.execute_query_one("SELECT COUNT(*) as count FROM forced_channels")
    total_channels_count = total_channels['count'] if total_channels else 0
    
    # أفضل 5 مستخدمين
    top_users = await db.get_top_rich_users(5)
    
    # أفضل 3 مشيرين
    top_referrers = await db.get_top_referrers(3)
    
    # إيرادات مقدرة
    revenue_estimate = total_stars * 0.01
    
    text = (
        f"📈 <b>الإحصائيات المتقدمة</b>\n\n"
        
        f"👥 <b>المستخدمين:</b>\n"
        f"• إجمالي المستخدمين: {format_number(users_count)}\n"
        f"• مستخدمين اليوم: {format_number(new_users_today)}\n"
        f"• مستخدمين الأسبوع: {format_number(new_users_week)}\n"
        f"• مستخدمين الشهر: {format_number(new_users_month)}\n"
        f"• النشطين اليوم: {format_number(daily_active_users)}\n\n"
        
        f"💰 <b>النقاط والمالية:</b>\n"
        f"• النقاط الكلية: {format_number(total_points)}\n"
        f"• النجوم المشتراة: {format_number(total_stars)}\n"
        f"• الإيراد المقدر: ${revenue_estimate:.2f}\n"
        f"• العمليات (24س): {format_number(last_24h_tx)}\n"
        f"• الإحالات النشطة: {format_number(total_referrals)}\n\n"
        
        f"🎫 <b>الأكواد:</b>\n"
        f"• الأكواد الكلية: {format_number(total_codes_count)}\n"
        f"• الأكواد النشطة: {format_number(len(active_codes))}\n\n"
        
        f"📢 <b>القنوات:</b>\n"
        f"• القنوات الكلية: {format_number(total_channels_count)}\n\n"
    )
    
    if top_users:
        text += f"🏆 <b>أفضل 5 مستخدمين:</b>\n"
        for i, user in enumerate(top_users, 1):
            text += f"{i}. {user['full_name']} - {format_number(user['points'])} نقطة\n"
        text += "\n"
    
    if top_referrers:
        text += f"👥 <b>أفضل 3 مشيرين:</b>\n"
        for i, referrer in enumerate(top_referrers, 1):
            text += f"{i}. {referrer['full_name']} - {referrer['referral_count']} إحالة\n"
        text += "\n"
    
    text += "👇 اختر الإجراء المطلوب:"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 تحديث الإحصائيات", callback_data="admin_analytics"),
         InlineKeyboardButton("📈 رسوم بيانية", callback_data="admin_charts")],
        [InlineKeyboardButton("📤 تصدير البيانات", callback_data="admin_export_data"),
         InlineKeyboardButton("🔄 تحديث القنوات", callback_data="admin_update_channels")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

async def admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل وضع الصيانة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    current_mode = await db.get_setting("maintenance_mode")
    new_mode = "0" if current_mode else "1"
    
    await db.set_setting("maintenance_mode", new_mode)
    
    status = "تم تفعيل وضع الصيانة" if new_mode == "1" else "تم تعطيل وضع الصيانة"
    
    # مسح التخزين المؤقت
    db.clear_cache("maintenance_mode")
    
    await query.edit_message_text(
        f"🔧 <b>{status}</b>\n\n"
        f"📊 <b>حالة النظام الآن:</b>\n"
        f"• وضع الصيانة: {'🟢 مفعل' if new_mode == '1' else '🔴 معطل'}\n"
        f"• الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"👤 <b>المفعِل:</b> {query.from_user.full_name}",
        parse_mode="HTML"
    )

async def admin_cleanup_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنظيف البيانات القديمة"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    # عرض تحذير
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم، قم بالتنظيف", callback_data="admin_cleanup_confirm")],
        [InlineKeyboardButton("❌ لا، إلغاء", callback_data="admin_panel")]
    ])
    
    auto_cleanup_days = await db.get_setting("auto_cleanup_days", 90)
    inactive_user_days = await db.get_setting("inactive_user_days", 30)
    
    await query.edit_message_text(
        f"🧹 <b>تنظيف البيانات القديمة</b>\n\n"
        f"⚠️ <b>تحذير:</b> هذه العملية غير قابلة للتراجع!\n\n"
        f"📊 <b>سيتم حذف:</b>\n"
        f"• الأكواد المنتهية الصلاحية (أكثر من {auto_cleanup_days} يوم)\n"
        f"• سجلات الدفع القديمة (أكثر من {auto_cleanup_days} يوم)\n"
        f"• سجلات المعاملات القديمة (أكثر من {auto_cleanup_days} يوم)\n"
        f"• تعطيل المستخدمين غير النشطين (أكثر من {inactive_user_days} يوم)\n"
        f"• الإشعارات المقروءة القديمة\n"
        f"• سجلات أنشطة البوت القديمة\n\n"
        f"هل أنت متأكد من رغبتك في المتابعة؟",
        parse_mode="HTML",
        reply_markup=kb
    )

async def admin_cleanup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تأكيد تنظيف البيانات"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await query.edit_message_text("⏳ جاري تنظيف البيانات القديمة...")
    
    try:
        # تنفيذ التنظيف
        await db.cleanup_old_data()
        
        await query.edit_message_text(
            "✅ <b>تم تنظيف البيانات بنجاح!</b>\n\n"
            "• تم حذف الأكواد المنتهية\n"
            "• تم حذف السجلات القديمة\n"
            "• تم تعطيل المستخدمين غير النشطين\n"
            "• تم تحسين قاعدة البيانات\n\n"
            "📊 <b>حالة النظام الآن مثالية</b>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"خطأ في تنظيف البيانات: {e}")
        await query.edit_message_text(
            "❌ <b>حدث خطأ أثناء التنظيف!</b>\n\n"
            f"التفاصيل: {str(e)[:200]}\n\n"
            "يرجى المحاولة مرة أخرى لاحقاً.",
            parse_mode="HTML"
        )

async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإعدادات"""
    query = update.callback_query
    
    if not is_admin(query.from_user.id):
        await query.answer("❌ هذا القسم للأدمن فقط!", show_alert=True)
        return
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer()
    
    await conv_manager.start_conversation(query.from_user.id, STATE_SETTINGS_MENU)
    
    # الحصول على جميع الإعدادات
    settings = await db.get_all_settings()
    
    text = "⚙️ <b>إدارة الإعدادات</b>\n\n"
    text += "📋 <b>الإعدادات الحالية:</b>\n\n"
    
    # تصنيف الإعدادات
    categories = {
        "النظام العام": [],
        "النقاط والمكافآت": [],
        "الدفع والإحالة": [],
        "الإذاعة والقنوات": [],
        "الأمان والصيانة": []
    }
    
    for setting in settings:
        key = setting['key']
        value = setting['value']
        description = setting['description']
        
        # تصنيف الإعدادات
        if key in ["maintenance_mode", "conversation_timeout", "auto_cleanup_days", "backup_interval_hours", "rate_limit_enabled"]:
            categories["النظام العام"].append((key, value, description))
        elif key in ["welcome_points", "daily_bonus_amount", "min_transfer", "max_transfer_per_day", "max_points_per_user"]:
            categories["النقاط والمكافآت"].append((key, value, description))
        elif key in ["referral_points", "points_per_star", "enable_star_payments", "tax_percent"]:
            categories["الدفع والإحالة"].append((key, value, description))
        elif key in ["broadcast_delay", "max_broadcast_users", "force_channel_subscription", "check_channels_interval"]:
            categories["الإذاعة والقنوات"].append((key, value, description))
        elif key in ["max_warnings", "inactive_user_days", "show_leaderboard", "enable_daily_bonus", "enable_referral_system"]:
            categories["الأمان والصيانة"].append((key, value, description))
        else:
            categories["النظام العام"].append((key, value, description))
    
    # عرض الإعدادات
    for category_name, category_settings in categories.items():
        if category_settings:
            text += f"📌 <b>{category_name}:</b>\n"
            for key, value, description in category_settings:
                text += f"• <code>{key}</code>: {value} - {description}\n"
            text += "\n"
    
    text += "📝 <b>لتعديل إعداد:</b>\n"
    text += "أرسل اسم الإعداد والقيمة الجديدة مثل:\n"
    text += "<code>welcome_points 50</code>\n\n"
    text += "❌ للإلغاء، أرسل /cancel"
    
    await query.edit_message_text(text, parse_mode="HTML")
    return STATE_SETTINGS_MENU

async def admin_save_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ الإعداد"""
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_SETTINGS_MENU
    
    input_text = update.message.text.strip()
    parts = input_text.split(maxsplit=1)
    
    if len(parts) != 2:
        await update.message.reply_text(
            "❌ تنسيق غير صحيح!\n"
            "استخدم: <code>اسم_الإعداد القيمة_الجديدة</code>\n\n"
            "أعد إرسال الإعداد:"
        )
        return STATE_SETTINGS_MENU
    
    key, value = parts
    
    # التحقق من وجود الإعداد
    existing_setting = await db.execute_query_one(
        "SELECT data_type, options FROM settings WHERE key = ?",
        (key,)
    )
    
    if not existing_setting:
        await update.message.reply_text(
            f"❌ الإعداد <code>{key}</code> غير موجود!\n"
            "أعد إرسال اسم إعداد صحيح:"
        )
        return STATE_SETTINGS_MENU
    
    data_type = existing_setting['data_type']
    options = existing_setting['options']
    
    # التحقق من صحة القيمة بناءً على نوع البيانات
    try:
        if data_type == 'integer':
            int_value = int(value)
            if options:
                min_val, max_val = map(int, options.split(','))
                if not (min_val <= int_value <= max_val):
                    await update.message.reply_text(
                        f"❌ القيمة يجب أن تكون بين {min_val} و {max_val}!\n"
                        "أعد إرسال القيمة:"
                    )
                    return STATE_SETTINGS_MENU
        
        elif data_type == 'float':
            float_value = float(value)
            if options:
                min_val, max_val = map(float, options.split(','))
                if not (min_val <= float_value <= max_val):
                    await update.message.reply_text(
                        f"❌ القيمة يجب أن تكون بين {min_val} و {max_val}!\n"
                        "أعد إرسال القيمة:"
                    )
                    return STATE_SETTINGS_MENU
        
        elif data_type == 'boolean':
            if value not in ['0', '1']:
                await update.message.reply_text(
                    "❌ القيمة يجب أن تكون 0 أو 1!\n"
                    "أعد إرسال القيمة:"
                )
                return STATE_SETTINGS_MENU
    except ValueError:
        await update.message.reply_text(
            f"❌ القيمة غير صحيحة لنوع البيانات {data_type}!\n"
            "أعد إرسال القيمة:"
        )
        return STATE_SETTINGS_MENU
    
    # حفظ الإعداد
    await db.set_setting(key, value)
    
    await update.message.reply_text(
        f"✅ تم تحديث الإعداد <code>{key}</code> إلى <code>{value}</code>\n\n"
        f"📝 <b>لتعديل إعداد آخر:</b>\n"
        f"أرسل اسم الإعداد والقيمة الجديدة\n\n"
        f"❌ للإلغاء، أرسل /cancel"
    )
    
    return STATE_SETTINGS_MENU

async def admin_cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء تعديل الإعدادات"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء تعديل الإعدادات.")
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔄 دوال التحويل واستبدال الأكواد المكتملة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية تحويل النقاط"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    # التحقق من اشتراك القنوات
    subscribed, message = await db.check_channel_subscription(query.from_user.id, context)
    if not subscribed:
        await query.edit_message_text(message, parse_mode="HTML")
        return
    
    await conv_manager.start_conversation(query.from_user.id, STATE_TRANSFER_ID)
    
    await query.edit_message_text(
        "💸 <b>تحويل النقاط</b>\n\n"
        "الخطوة 1/2: أرسل <b>آيدي المستخدم</b> الذي تريد التحويل له:\n\n"
        "📌 <b>ملاحظات:</b>\n"
        "• يجب أن يكون المستخدم مسجلاً في البوت\n"
        "• لا يمكن التحويل لنفسك\n"
        "• الحد الأدنى للتحويل: 10 نقاط\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_TRANSFER_ID

async def get_transfer_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على آيدي المستخدم للتحويل"""
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_TRANSFER_ID
    
    try:
        receiver_id = int(update.message.text.strip())
        
        # التحقق من عدم التحويل لنفسه
        if receiver_id == update.effective_user.id:
            await update.message.reply_text(
                "❌ لا يمكن التحويل لنفسك!\n"
                "أعد إرسال آيدي مستخدم آخر:"
            )
            return STATE_TRANSFER_ID
        
        # التحقق من وجود المستخدم
        receiver = await db.get_user(receiver_id)
        if not receiver:
            await update.message.reply_text(
                "❌ المستخدم غير موجود في البوت!\n"
                "أعد إرسال آيدي مستخدم آخر:"
            )
            return STATE_TRANSFER_ID
        
        # التحقق من حظر المستخدم
        if receiver['is_banned'] == 1:
            await update.message.reply_text(
                "❌ المستخدم محظور ولا يمكن التحويل له!\n"
                "أعد إرسال آيدي مستخدم آخر:"
            )
            return STATE_TRANSFER_ID
        
        # حفظ بيانات التحويل
        await conv_manager.update_conversation(
            update.effective_user.id,
            STATE_TRANSFER_AMOUNT,
            {'receiver_id': receiver_id, 'receiver_name': receiver['full_name']}
        )
        
        await update.message.reply_text(
            f"✅ المستخدم: {receiver['full_name']}\n\n"
            "الخطوة 2/2: أرسل <b>عدد النقاط</b> التي تريد تحويلها:\n\n"
            "📌 <b>ملاحظات:</b>\n"
            f"• الحد الأدنى: 10 نقاط\n"
            f"• رصيدك الحالي: {(await db.get_user(update.effective_user.id))['points']:,} نقطة\n\n"
            "❌ للإلغاء، أرسل /cancel"
        )
        return STATE_TRANSFER_AMOUNT
        
    except ValueError:
        await update.message.reply_text(
            "❌ يجب إدخال أرقام فقط!\n"
            "أعد إرسال الآيدي:"
        )
        return STATE_TRANSFER_ID

async def get_transfer_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الحصول على مبلغ التحويل"""
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_TRANSFER_AMOUNT
    
    try:
        amount = int(update.message.text.strip())
        
        # التحقق من الحد الأدنى
        min_transfer = await db.get_setting("min_transfer", 10)
        if amount < min_transfer:
            await update.message.reply_text(
                f"❌ الحد الأدنى للتحويل هو {min_transfer} نقطة!\n"
                "أعد إرسال المبلغ:"
            )
            return STATE_TRANSFER_AMOUNT
        
        # التحقق من رصيد المستخدم
        sender = await db.get_user(update.effective_user.id)
        if not sender or sender['points'] < amount:
            await update.message.reply_text(
                f"❌ رصيدك غير كافي! الرصيد الحالي: {sender['points']:,} نقطة\n"
                "أعد إرسال مبلغ أقل:"
            )
            return STATE_TRANSFER_AMOUNT
        
        # الحصول على بيانات المستقبل
        conv_data = await conv_manager.get_conversation_data(update.effective_user.id)
        receiver_id = conv_data.get('receiver_id')
        receiver_name = conv_data.get('receiver_name', 'مستخدم')
        
        # حساب الضريبة
        tax_percent = await db.get_setting("tax_percent", 25)
        tax = int(amount * tax_percent / 100)
        net_amount = amount - tax
        
        # تنفيذ التحويل
        try:
            # خصم من المرسل مع الضريبة
            await db.update_points(update.effective_user.id, -amount, "transfer_out", 
                                 f"تحويل إلى: {receiver_name}", receiver_id)
            
            # إضافة للمستقبل بدون الضريبة
            await db.update_points(receiver_id, net_amount, "transfer_in", 
                                 f"استلام من: {sender['full_name']}", update.effective_user.id)
            
            # تسجيل الضريبة
            await db.execute_update(
                """INSERT INTO transactions 
                (user_id, amount, type, details, related_user_id) 
                VALUES (?, ?, ?, ?, ?)""",
                (update.effective_user.id, -tax, "ضريبة", f"ضريبة تحويل: {tax_percent}%", receiver_id)
            )
            
            # إرسال إشعار للمستقبل
            try:
                receiver_msg = (
                    f"💰 <b>تم استلام تحويل نقاط!</b>\n\n"
                    f"👤 المرسل: {sender['full_name']}\n"
                    f"🎯 المبلغ: {net_amount:,} نقطة\n"
                    f"📊 رصيدك الجديد: {(await db.get_user(receiver_id))['points']:,} نقطة"
                )
                await safe_api_call(context.bot.send_message, receiver_id, receiver_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"خطأ في إرسال إشعار للمستقبل: {e}")
            
            # تأكيد للمرسل
            await update.message.reply_text(
                f"✅ <b>تم التحويل بنجاح!</b>\n\n"
                f"👤 المستقبل: {receiver_name}\n"
                f"💰 المبلغ المحول: {amount:,} نقطة\n"
                f"💸 الضريبة ({tax_percent}%): {tax:,} نقطة\n"
                f"🎯 المبلغ المستلم: {net_amount:,} نقطة\n"
                f"📊 رصيدك الحالي: {sender['points'] - amount:,} نقطة\n\n"
                f"شكراً لاستخدامك خدمة التحويل! 🙏",
                parse_mode="HTML"
            )
            
            logger.info(f"تحويل ناجح: {update.effective_user.id} -> {receiver_id} : {amount} نقطة")
            
        except Exception as e:
            logger.error(f"خطأ في تنفيذ التحويل: {e}")
            await update.message.reply_text(
                "❌ <b>حدث خطأ في التحويل!</b>\n\n"
                "يرجى المحاولة مرة أخرى لاحقاً.",
                parse_mode="HTML"
            )
        
        await conv_manager.end_conversation(update.effective_user.id)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ يجب إدخال أرقام فقط!\n"
            "أعد إرسال المبلغ:"
        )
        return STATE_TRANSFER_AMOUNT

async def cancel_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية التحويل"""
    await conv_manager.end_conversation(update.effective_user.id)
    await update.message.reply_text("❌ تم إلغاء عملية التحويل.")
    await send_dashboard(update, context)
    return ConversationHandler.END

async def start_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية استبدال الكود"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    if await check_maintenance_mode(query.from_user.id):
        await query.answer("البوت قيد الصيانة حالياً", show_alert=True)
        return
    
    await query.answer()
    
    # التحقق من اشتراك القنوات
    subscribed, message = await db.check_channel_subscription(query.from_user.id, context)
    if not subscribed:
        await query.edit_message_text(message, parse_mode="HTML")
        return
    
    await conv_manager.start_conversation(query.from_user.id, STATE_REDEEM_CODE)
    
    await query.edit_message_text(
        "🎫 <b>استبدال الكود</b>\n\n"
        "أرسل <b>الكود</b> الذي تريد استبداله:\n\n"
        "📌 <b>ملاحظات:</b>\n"
        "• الكود يجب أن يكون باللغة الإنجليزية\n"
        "• الكود حساس لحالة الأحرف\n"
        "• كل كود يمكن استخدامه مرة واحدة فقط\n\n"
        "❌ للإلغاء، أرسل /cancel",
        parse_mode="HTML"
    )
    return STATE_REDEEM_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الكود المدخل"""
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(update.effective_user.id)
    if not allowed:
        await update.message.reply_text(f"⏱️ {message}")
        return STATE_REDEEM_CODE
    
    code = update.message.text.strip().upper()
    
    # استبدال الكود
    result = await db.redeem_promo_code(update.effective_user.id, code)
    
    if isinstance(result, int):
        # نجاح الاستبدال
        user_data = await db.get_user(update.effective_user.id)
        await update.message.reply_text(
            f"✅ <b>تم استبدال الكود بنجاح!</b>\n\n"
            f"🎫 الكود: <code>{code}</code>\n"
            f"🎯 النقاط: {result:,}\n"
            f"💰 رصيدك الحالي: {user_data['points']:,} نقطة\n\n"
            f"شكراً لاستخدامك الكود! 🙏",
            parse_mode="HTML"
        )
    else:
        # فشل الاستبدال
        error_messages = {
            "not_found": "❌ الكود غير موجود!",
            "expired": "❌ الكود منتهي الصلاحية أو تم استخدامه بالكامل!",
            "used": "❌ لقد استخدمت هذا الكود مسبقاً!",
            "min_points": "❌ لا تملك النقاط الكافية لاستخدام هذا الكود!",
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي المحسن مع إدارة متقدمة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج الأخطاء العام المحسن"""
    try:
        logger.error(f"حدث خطأ: {context.error}", exc_info=context.error)
        
        # تسجيل الخطأ في قاعدة البيانات
        try:
            error_details = str(context.error)[:500]
            await db.execute_update(
                """INSERT INTO bot_activities 
                (activity_type, user_id, details, timestamp) 
                VALUES (?, ?, ?, ?)""",
                ("system_error", 0, error_details, datetime.now().isoformat())
            )
        except Exception as db_error:
            logger.error(f"خطأ في تسجيل الخطأ في قاعدة البيانات: {db_error}")
        
        # إرسال رسالة خطأ للمستخدم
        if update and update.effective_user:
            error_msg = (
                "❌ <b>حدث خطأ غير متوقع</b>\n\n"
                "نعتذر للإزعاج. تم تسجيل الخطأ وسيتم إصلاحه قريباً.\n\n"
                "📌 <b>يمكنك:</b>\n"
                "• المحاولة مرة أخرى بعد قليل\n"
                "• استخدام الأمر /start للبدء من جديد\n"
                "• التواصل مع الدعم إذا تكرر الخطأ\n\n"
                "شكراً لتفهمك. 🙏"
            )
            
            try:
                if update.callback_query:
                    await update.callback_query.message.reply_text(error_msg, parse_mode="HTML")
                elif update.message:
                    await update.message.reply_text(error_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"خطأ في إرسال رسالة الخطأ: {e}")
        
        # إرسال إشعار للأدمن
        try:
            user_info = ""
            if update and update.effective_user:
                user_info = f"المستخدم: {update.effective_user.full_name} ({update.effective_user.id})"
            
            admin_msg = (
                f"🚨 <b>حدث خطأ في البوت!</b>\n\n"
                f"{user_info}\n"
                f"📝 الخطأ: {str(context.error)[:200]}\n"
                f"⏱️ الوقت: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await safe_api_call(context.bot.send_message, ADMIN_ID, admin_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"خطأ في إرسال إشعار الأدمن: {e}")
            
    except Exception as e:
        logger.error(f"خطأ في معالج الأخطاء نفسه: {e}")

async def periodic_cleanup():
    """تنظيف دوري للبيانات"""
    while True:
        try:
            await asyncio.sleep(3600)  # كل ساعة
            await db.cleanup_old_data()
            logger.info("✅ تم التنظيف الدوري للبيانات")
        except Exception as e:
            logger.error(f"خطأ في التنظيف الدوري: {e}")

async def daily_rate_limit_reset():
    """إعادة تعيين Rate Limiting يومياً"""
    while True:
        try:
            await asyncio.sleep(86400)  # كل 24 ساعة
            db.rate_limit_data.clear()
            logger.info("✅ تم إعادة تعيين Rate Limiting")
        except Exception as e:
            logger.error(f"خطأ في إعادة تعيين Rate Limiting: {e}")

async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج للكولباك غير المعروف"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
    allowed, message = await check_rate_limit(query.from_user.id)
    if not allowed:
        await query.answer(message, show_alert=True)
        return
    
    await query.answer("❌ هذا الزر لم يتم برمجته بعد!", show_alert=True)

async def main():
    """الدالة الرئيسية لتشغيل البوت مع تحسينات متقدمة"""
    
    # التحقق من التوكنات
    if not BOT_TOKEN:
        logger.error("❌ لم يتم تعيين BOT_TOKEN!")
        print("❌ خطأ: يجب تعيين متغير البيئة TELEGRAM_BOT_TOKEN")
        return
    
    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة معالجة الأخطاء
    application.add_error_handler(error_handler)
    
    # محادثة تحويل النقاط
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_transfer), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة استبدال الأكواد
    redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")],
        states={
            STATE_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel_redeem), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة إنشاء الأكواد (للأدمن)
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_create_code_start, pattern="^admin_create_code$")],
        states={
            STATE_CREATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_code)],
            STATE_POINTS_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_code_points)],
            STATE_CODE_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_code_expiry)],
            STATE_CONFIRM_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_finish_code_creation)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_code), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة إدارة القنوات
    channels_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_channel_start, pattern="^admin_add_channel$")],
        states={
            STATE_CHANNEL_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_id)],
            STATE_CHANNEL_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_channel_link)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_channel), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة إدارة المستخدمين
    users_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_search_by_id_start, pattern="^admin_search_by_id$"),
            CallbackQueryHandler(admin_search_by_name_start, pattern="^admin_search_by_name$"),
            CallbackQueryHandler(admin_search_by_username_start, pattern="^admin_search_by_username$")
        ],
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
        ],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة الإذاعة
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
            CallbackQueryHandler(admin_send_broadcast_execute, pattern="^broadcast_send_(normal|pin|group)$"),
            CommandHandler("cancel", admin_cancel_broadcast),
            CommandHandler("start", start)
        ],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة الدعم الفني
    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_ticket_start, pattern="^create_ticket$")],
        states={
            STATE_SUPPORT_TICKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_ticket_category)],
            STATE_CONFIRM_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, finish_ticket_creation)],
        },
        fallbacks=[CommandHandler("cancel", cancel_ticket), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # محادثة تعديل الإعدادات
    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_settings_menu, pattern="^admin_settings$")],
        states={
            STATE_SETTINGS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_setting)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel_settings), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=await db.get_setting("conversation_timeout", 300)
    )
    
    # تسجيل المعالجات
    
    # الأمر الأساسي
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    
    # محادثات المستخدمين
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    application.add_handler(support_conv)
    
    # محادثات الأدمن
    application.add_handler(create_code_conv)
    application.add_handler(channels_conv)
    application.add_handler(users_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(settings_conv)
    
    # معالجات الأزرار العامة
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(support_handler, pattern="^support$"))
    application.add_handler(CallbackQueryHandler(buy_points_menu, pattern="^buy_points_menu$"))
    application.add_handler(CallbackQueryHandler(send_dashboard, pattern="^collect_points$"))
    
    # معالجات الأزرار الإدارية
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_channels_menu, pattern="^admin_channels$"))
    application.add_handler(CallbackQueryHandler(admin_users_menu, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_menu, pattern="^admin_broadcast$"))
    application.add_handler(CallbackQueryHandler(admin_analytics_menu, pattern="^admin_analytics$"))
    application.add_handler(CallbackQueryHandler(admin_codes_menu, pattern="^admin_codes$"))
    application.add_handler(CallbackQueryHandler(admin_toggle_maintenance, pattern="^admin_maintenance$"))
    application.add_handler(CallbackQueryHandler(admin_cleanup_data, pattern="^admin_cleanup$"))
    application.add_handler(CallbackQueryHandler(admin_cleanup_confirm, pattern="^admin_cleanup_confirm$"))
    
    # معالجات الدفع بالنجوم
    if PAYMENT_PROVIDER_TOKEN:
        application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
        application.add_handler(CallbackQueryHandler(buy_stars_handler, pattern="^buy_(5|10)$"))
    
    # معالجات عامة
    application.add_handler(CallbackQueryHandler(unknown_callback, pattern=".*"))
    
    # معلومات التشغيل
    print("\n" + "="*60)
    print("🤖 بوت النقاط المتطور - الإصدار المحسن للإنتاج")
    print("="*60)
    print(f"🆔 الأدمن: {ADMIN_ID}")
    print("="*60)
    print("✅ البوت يعمل بكفاءة عالية مع جميع التحسينات...")
    print("="*60 + "\n")
    
    # بدء المهام المتكررة
    asyncio.create_task(periodic_cleanup())
    asyncio.create_task(daily_rate_limit_reset())
    asyncio.create_task(conv_manager.start_timeout_checker(application))
    
    # تشغيل البوت
    await application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        poll_interval=0.5,
        timeout=30,
        drop_pending_updates=True,
        close_loop=False
    )

if __name__ == "__main__":
    try:
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"خطأ فادح في تشغيل البوت: {e}")
        print(f"❌ خطأ فادح: {e}")
