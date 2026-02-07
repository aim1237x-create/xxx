import html
import asyncio
import logging
from datetime import datetime
from typing import Any

from telegram import Update
from telegram.error import Forbidden, BadRequest, TimedOut, NetworkError
from telegram.ext import ContextTypes

from database import AsyncDatabaseManager
from config import CACHE_TTL

logger = logging.getLogger(__name__)

db = AsyncDatabaseManager()

async def check_maintenance_mode(user_id: int) -> bool:
    """التحقق من وضع الصيانة مع التخزين المؤقت"""
    if is_admin(user_id):
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
                           reply_markup=None, parse_mode: str = "HTML"):
    """تعديل رسالة بأمان"""
    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, 
                reply_markup=reply_markup, 
                parse_mode=parse_mode
            )
        elif update.message:
            await update.message.edit_text(
                text, 
                reply_markup=reply_markup, 
                parse_mode=parse_mode
            )
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

def is_admin(user_id: int) -> bool:
    """التحقق إذا كان المستخدم أدمن"""
    from config import ADMIN_ID
    return user_id == ADMIN_ID