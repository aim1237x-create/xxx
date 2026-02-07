import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

from config import *
from database import AsyncDatabaseManager
from keyboards import get_user_link, is_admin
from utils import check_rate_limit, format_number, safe_edit_message
from conversations import conv_manager

logger = logging.getLogger(__name__)
db = AsyncDatabaseManager()

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

# باقي دوال الأدمن سيتم نقلها بشكل مشابه...