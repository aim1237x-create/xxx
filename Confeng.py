import os
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Dict, Any, Union

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ⚙️ إعدادات البوت والتهيئة المحسنة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# توكن البوت
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

# إعدادات المسارات
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")
LOG_PATH = os.path.join(BASE_DIR, "bot.log")