import logging
import sqlite3
import time
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any, Union
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes

from config import (
    DATABASE_CONNECTION_TIMEOUT, CACHE_TTL,
    RATE_LIMIT_WINDOW, MAX_REQUESTS_PER_WINDOW
)

logger = logging.getLogger(__name__)


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