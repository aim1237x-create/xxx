import logging
import asyncio
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler

from config import *
from database import AsyncDatabaseManager
from conversations import ConversationManager
from keyboards import is_admin
from handlers.user import start, send_dashboard, main_menu_callback
from handlers.payment import buy_points_menu, buy_stars_handler, precheckout_handler, successful_payment_handler
from handlers.admin import admin_panel
from handlers.support import support_handler

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تهيئة قاعدة البيانات
db = AsyncDatabaseManager()

# مدير المحادثات
conv_manager = ConversationManager()

async def error_handler(update: Update, context):
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

async def unknown_callback(update: Update, context):
    """معالج للكولباك غير المعروف"""
    query = update.callback_query
    
    # التحقق من Rate Limiting
    from utils import check_rate_limit
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
        print("❌ خطأ: يجب تعيين توكن البوت في ملف config.py")
        return
    
    # إنشاء التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة معالجة الأخطاء
    application.add_error_handler(error_handler)
    
    # تسجيل المعالجات الأساسية
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    
    # معالجات الأزرار العامة
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(support_handler, pattern="^support$"))
    application.add_handler(CallbackQueryHandler(buy_points_menu, pattern="^buy_points_menu$"))
    application.add_handler(CallbackQueryHandler(send_dashboard, pattern="^collect_points$"))
    
    # معالجات الأزرار الإدارية
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    
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
        # تهيئة قاعدة البيانات بشكل متزامن
        db.init_database_sync()
        
        # تشغيل البوت
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n🛑 تم إيقاف البوت بواسطة المستخدم")
    except Exception as e:
        logger.error(f"خطأ فادح في تشغيل البوت: {e}")
        print(f"❌ خطأ فادح: {e}")