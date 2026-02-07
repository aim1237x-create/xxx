import logging
import time
from telegram import Update, LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler, MessageHandler, filters

from config import *
from database import AsyncDatabaseManager
from keyboards import get_user_link
from utils import check_rate_limit, check_maintenance_mode, safe_api_call, format_number

logger = logging.getLogger(__name__)
db = AsyncDatabaseManager()

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