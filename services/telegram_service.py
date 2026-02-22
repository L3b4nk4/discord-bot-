"""
Telegram Service - Handles Telegram bot integration.
Allows remote control of the Discord bot and AI chat via Telegram.
"""
import os
import asyncio
import logging
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from telegram.request import HTTPXRequest
from telegram.error import Conflict

class TelegramService:
    def __init__(self, discord_bot, ai_service):
        self.discord_bot = discord_bot
        self.ai_service = ai_service
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.app = None
        self.chat_id = None
        self.username_map = {} # user_id -> username
        
        if self.token:
            print("📦 Telegram Service: Initializing...")
        else:
            print("⚠️ Telegram Service: No token found (TELEGRAM_TOKEN)")

    async def start(self):
        """Start the Telegram bot."""
        if not self.token: return

        try:
            req = HTTPXRequest(connection_pool_size=8)
            self.app = ApplicationBuilder().token(self.token).request(req).build()
            
            # Register handlers
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.app.add_handler(MessageHandler(filters.COMMAND, self.handle_command))
            
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling()
            print("✅ Telegram Service: Connected and polling")
        except Conflict:
            print("⚠️ Telegram Conflict: Helper terminated by other instance. (Normal if running multiple bots)")
            # Clean up app so stop() doesn't fail
            if self.app:
                await self.app.shutdown()
                self.app = None
        except Exception as e:
            print(f"❌ Telegram Service Error: {e}")

    async def stop(self):
        """Stop the Telegram bot."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    async def handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Telegram commands."""
        text = update.message.text
        cmd = text.split()[0].lower()
        chat_id = update.effective_chat.id
        self.chat_id = chat_id # Store for notifications
        
        resp = "Unknown command."
        
        if cmd == "/start":
            resp = "👋 Connected to Manga Bot! Type /help for commands."
        
        elif cmd == "/help":
            resp = """🤖 Controls:
/status - Bot status
/join - Join Discord VC (first available)
/leave - Leave VC
/say <text> - Speak in VC
/ping - Check latency"""

        elif cmd == "/status":
            guilds = len(self.discord_bot.guilds)
            vcs = len(self.discord_bot.voice_clients)
            resp = f"📊 Connected to {guilds} Guilds | 🔊 In {vcs} Voice Channels"

        elif cmd == "/ping":
            lat = round(self.discord_bot.latency * 1000)
            resp = f"🏓 Discord Latency: {lat}ms"

        elif cmd == "/join":
            # logic to join first available VC
            if self.discord_bot.guilds:
                guild = self.discord_bot.guilds[0] # Simplification
                vc = next((c for c in guild.voice_channels if c.members), None)
                if vc:
                    # Trigger the voice cog join logic? 
                    # Or just tell user to use discord command.
                    # For now, let's just report status
                    resp = f"ℹ️ Found active channel: {vc.name}. Please use Discord !join command (Remote join WIP)."
                    
                    # Implementation of remote join is complex due to context requirements of Cogs
                    # We can try to access the VoiceCog manually
                    voice_cog = self.discord_bot.get_cog("Voice")
                    if voice_cog and guild.voice_client is None:
                         # Requires rewriting VoiceCog to allow non-ctx joins or mocking ctx
                         pass
                else:
                    resp = "❌ No active voice channels found with people."
            else:
                resp = "❌ Bot is not in any Discord servers."

        elif cmd == "/leave":
            for vc in self.discord_bot.voice_clients:
                await vc.disconnect()
            resp = "👋 Disconnected from all Voice Channels."

        await context.bot.send_message(chat_id=chat_id, text=resp)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular messages (Chat with AI)."""
        text = update.message.text
        user = update.effective_user.first_name
        self.chat_id = update.effective_chat.id
        
        if self.ai_service.enabled:
            # Use 'Groq' or 'Gemini' via AI Service
            response = await self.ai_service.chat_response(user, text)
            await context.bot.send_message(chat_id=self.chat_id, text=response)
        else:
            await context.bot.send_message(chat_id=self.chat_id, text="ℹ️ AI is currently disabled.")
