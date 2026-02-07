import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from config import *
from database import AsyncDatabaseManager
from keyboards import get_user_link
from utils import check_rate_limit, check_maintenance_mode
from conversations import conv_manager

logger = logging.getLogger(__name__)
db = AsyncDatabaseManager()

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
    
    from keyboards import get_support_keyboard
    kb = get_support_keyboard()
    
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")

# باقي دوال الدعم سيتم نقلها بشكل مشابه...