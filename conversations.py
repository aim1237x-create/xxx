import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from telegram.ext import Application

from database import AsyncDatabaseManager

logger = logging.getLogger(__name__)
db = AsyncDatabaseManager()

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
                from utils import safe_api_call
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