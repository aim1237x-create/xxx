import logging
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters

from config import *
from database import AsyncDatabaseManager
from keyboards import get_main_keyboard, get_user_link
from utils import check_rate_limit, check_maintenance_mode, format_number, format_datetime
from conversations import conv_manager

logger = logging.getLogger(__name__)
db = AsyncDatabaseManager()

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
    from utils import safe_api_call
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
    
    from keyboards import is_admin
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