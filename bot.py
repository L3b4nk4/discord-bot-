"""
Manga Discord Bot - Main Entry Point
A voice-enabled AI assistant for Discord.
"""

from cogs.auth_cog import setup_global_check
from cogs import VoiceCog, ChatCog, AuthCog, HelpCog, AgentCog
from voice import VoiceHandler
from services import AIService, TTSService, SpeechRecognitionService, LLMAgentService
import discord
import discord.opus
from discord.ext import commands
import os
import re
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Ensure Opus is loaded for voice playback


def _load_opus():
    if discord.opus.is_loaded():
        return True
    candidates = [
        "libopus.so.0",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/local/lib/libopus.so.0",
        "opus.dll",
        "libopus.dylib",
    ]
    for lib in candidates:
        try:
            discord.opus.load_opus(lib)
            print(f"✅ Loaded Opus library: {lib}")
            return True
        except Exception:
            continue
    print("⚠️ Opus library not found. Voice playback may fail.")
    return False


_load_opus()

# Import services

# Import voice handler

# Import cogs


class MangaBot(commands.Bot):
    """Main bot class that coordinates all components."""

    def __init__(self):
        # Setup intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None  # We use custom help
        )
        # Explicitly ensure help command is removed to avoid overrides/conflicts
        self.remove_command('help')

        # Auto-delete bot logs in configured channels (default: 3 hours).
        self.log_auto_delete_seconds = max(
            0, int(os.getenv("LOG_AUTO_DELETE_SECONDS", "10800")))
        self.log_auto_delete_channels = {
            name.strip().lower()
            for name in os.getenv("LOG_AUTO_DELETE_CHANNELS", "manga-logs,logs").split(",")
            if name.strip()
        }
        self.ai_conversations = {}
        self.ai_conversation_ttl = max(
            60, int(os.getenv("AI_CONVERSATION_TTL", "1800")))
        self.ai_conversation_max_turns = max(
            4, int(os.getenv("AI_CONVERSATION_MAX_TURNS", "12")))

        # Initialize services
        print("📦 Initializing services...")
        self.ai_service = AIService()
        self.tts_service = TTSService()
        self.speech_service = SpeechRecognitionService()
        self.agent_service = LLMAgentService()

        # Initialize voice handler
        self.voice_handler = VoiceHandler(
            self,
            self.ai_service,
            self.tts_service,
            self.speech_service
        )

        print("✅ Services initialized")

    def _strip_bot_mention(self, text: str) -> str:
        if not self.user:
            return (text or "").strip()
        return re.sub(
            rf"<@!?{self.user.id}>", "", text or "").strip()

    def _conversation_key(self, channel_id: int, user_id: int) -> str:
        return f"{channel_id}:{user_id}"

    def get_cached_ai_history(self, channel_id: int, user_id: int):
        entry = self.ai_conversations.get(self._conversation_key(channel_id, user_id))
        if not entry:
            return []

        if time.monotonic() - entry.get("updated_at", 0) > self.ai_conversation_ttl:
            self.ai_conversations.pop(self._conversation_key(channel_id, user_id), None)
            return []

        history = entry.get("history", [])
        if not isinstance(history, list):
            return []
        return [dict(item) for item in history[-self.ai_conversation_max_turns:]]

    def remember_ai_exchange(self, message: discord.Message, user_text: str, bot_text: str, base_history=None):
        if not message or not getattr(message, "channel", None):
            return

        history = []
        for item in (base_history or []):
            if not isinstance(item, dict):
                continue
            content = (item.get("content") or "").strip()
            if not content:
                continue
            history.append({
                "role": str(item.get("role", "user")).strip().lower(),
                "name": str(item.get("name", "")).strip(),
                "content": content,
            })

        clean_user_text = (user_text or "").strip()
        if clean_user_text:
            history.append({
                "role": "user",
                "name": message.author.display_name,
                "content": clean_user_text,
            })

        clean_bot_text = (bot_text or "").strip()
        if clean_bot_text:
            history.append({
                "role": "assistant",
                "name": self.user.name if self.user else "Manga",
                "content": clean_bot_text,
            })

        history = history[-self.ai_conversation_max_turns:]
        self.ai_conversations[self._conversation_key(message.channel.id, message.author.id)] = {
            "updated_at": time.monotonic(),
            "history": history,
        }

    async def _resolve_referenced_message(self, message: discord.Message):
        reference = getattr(message, "reference", None)
        if not reference or not reference.message_id:
            return None

        resolved = getattr(reference, "resolved", None)
        if isinstance(resolved, discord.Message):
            return resolved

        channel = getattr(message, "channel", None)
        if not channel or not hasattr(channel, "fetch_message"):
            return None

        try:
            return await channel.fetch_message(reference.message_id)
        except Exception:
            return None

    def _message_text_for_ai(self, message: discord.Message, *, strip_bot_mention: bool = False) -> str:
        content = (message.content or "").strip()
        if strip_bot_mention:
            content = self._strip_bot_mention(content)
        return content

    async def build_ai_reply_history(self, message: discord.Message, limit: int = 8):
        """Build reply-chain history for user<->bot conversations only."""
        if not message or not self.user or not message.reference:
            return []

        chain = []
        cursor = message
        seen = {message.id}

        while len(chain) < limit:
            parent = await self._resolve_referenced_message(cursor)
            if not parent or parent.id in seen:
                break
            seen.add(parent.id)
            chain.append(parent)
            if not parent.reference:
                break
            cursor = parent

        chain.reverse()

        history = []
        has_bot_turn = False
        for item in chain:
            if item.author.id == self.user.id:
                has_bot_turn = True
                role = "assistant"
                name = self.user.name
                content = self._message_text_for_ai(item)
            elif item.author.id == message.author.id:
                role = "user"
                name = item.author.display_name
                content = self._message_text_for_ai(item, strip_bot_mention=True)
            else:
                # Keep reply-context limited to a single user talking to the bot.
                return []

            if not content:
                continue

            history.append({
                "role": role,
                "name": name,
                "content": content,
            })

        if not has_bot_turn:
            return []
        return history

    async def setup_hook(self):
        """Called when bot is starting up - load cogs."""
        print("📦 Loading cogs...")

        # Add cogs with their dependencies
        await self.add_cog(VoiceCog(self, self.voice_handler))
        # await self.add_cog(ChatCog(self, self.ai_service))
        auth_cog = AuthCog(self)
        await self.add_cog(auth_cog)
        setup_global_check(self, auth_cog)
        await self.add_cog(AgentCog(self, self.agent_service))
        await self.add_cog(HelpCog(self))

        # Load other cogs
        from cogs import TrollCog, FunCog, UtilityCog, AdminCog
        await self.add_cog(TrollCog(self, self.voice_handler))
        await self.add_cog(FunCog(self, self.ai_service))
        await self.add_cog(UtilityCog(self, self.ai_service))
        await self.add_cog(AdminCog(self))

        print("✅ Cogs loaded")

    async def on_ready(self):
        """Called when bot is connected and ready."""
        print(f"\n{'='*50}")
        print(f"🤖 {self.user.name} is now online!")
        print(f"📊 Connected to {len(self.guilds)} server(s)")
        print(f"{'='*50}\n")

        # Set presence
        await self.change_presence(
            activity=discord.CustomActivity(name="coded by l3b4nk4")
        )

    async def on_message(self, message):
        """Handle incoming messages."""
        # Auto-clean bot log messages in log channels.
        if message.author == self.user:
            channel_name = getattr(message.channel, "name", None)
            if (
                self.log_auto_delete_seconds > 0
                and isinstance(channel_name, str)
                and channel_name.lower() in self.log_auto_delete_channels
            ):
                try:
                    await message.delete(delay=self.log_auto_delete_seconds)
                except Exception:
                    pass
            return

        mentioned = self.user.mentioned_in(message)
        reply_history = []
        if not message.content.startswith("!"):
            try:
                reply_history = await self.build_ai_reply_history(message)
            except Exception as e:
                print(f"⚠️ Failed to build reply history: {e}")
                reply_history = []
        cached_history = []
        if not reply_history and not message.content.startswith("!"):
            try:
                cached_history = self.get_cached_ai_history(
                    message.channel.id, message.author.id)
            except Exception as e:
                print(f"⚠️ Failed to read cached AI history: {e}")
                cached_history = []
        conversation_history = reply_history or cached_history
        is_reply_continuation = bool(reply_history)
        auth_cog = self.get_cog("Auth") or self.get_cog("AuthCog")
        only_me_user_id = getattr(
            auth_cog, "only_me_user_id", None) if auth_cog else None
        is_onlyme_allowed = True
        if only_me_user_id is not None:
            if auth_cog and hasattr(auth_cog, "can_use_locked_mode"):
                try:
                    is_onlyme_allowed = bool(
                        auth_cog.can_use_locked_mode(message.guild, message.author.id)
                    )
                except Exception:
                    is_onlyme_allowed = message.author.id == only_me_user_id
            else:
                is_onlyme_allowed = message.author.id == only_me_user_id

        # Natural assistant actions are mention-only.
        if mentioned and is_onlyme_allowed:
            try:
                agent_cog = self.get_cog("Agent")
                if agent_cog and hasattr(agent_cog, "handle_natural_request"):
                    handled = await agent_cog.handle_natural_request(message)
                    if handled:
                        return
            except Exception as e:
                print(f"⚠️ Natural assistant handler error: {e}")

        # Process commands
        await self.process_commands(message)

        # Respond when mentioned (if not a command)
        if (mentioned or is_reply_continuation) and not message.content.startswith("!"):
            if not is_onlyme_allowed:
                return
            clean_text = self._strip_bot_mention(message.content)

            if clean_text and self.ai_service.enabled:
                async with message.channel.typing():
                    response = await self.ai_service.chat_response(
                        message.author.display_name,
                        clean_text,
                        history=conversation_history,
                    )
                    self.remember_ai_exchange(
                        message,
                        clean_text,
                        response,
                        base_history=conversation_history,
                    )
                    await message.reply(response, mention_author=False)
            elif clean_text:
                await message.reply(
                    "❌ AI is not configured. Set `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or `GROQ_API_KEY`.",
                    mention_author=False,
                )

    async def on_command_error(self, ctx, error):
        """Handle command errors."""
        if isinstance(error, commands.CommandNotFound):
            # Ignore unknown commands
            return
        elif isinstance(error, commands.CheckFailure):
            # Permission denials are handled by check functions with custom text.
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Missing argument: `{error.param.name}`")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send(f"❌ Member not found: `{error.argument}`")
        else:
            print(f"❌ Command error: {error}")
            await ctx.send(f"❌ An error occurred: {error}")


def main():
    """Main entry point."""
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found!")
        print("Create a .env file with: DISCORD_TOKEN=your_token_here")
        return

    print("🚀 Starting Manga Bot...")
    print(
        f"📍 AI Enabled: {'Yes (OpenRouter)' if OPENROUTER_API_KEY else 'No - Set OPENROUTER_API_KEY'}")

    bot = MangaBot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
