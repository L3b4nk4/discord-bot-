"""
Manga Discord Bot - Main Entry Point
A voice-enabled AI assistant for Discord.
"""

import discord
import discord.opus
from discord.ext import commands
import os
import re
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
from services import AIService, TTSService, SpeechRecognitionService, LLMAgentService

# Import voice handler
from voice import VoiceHandler

# Import cogs
from cogs import VoiceCog, ChatCog, AuthCog, HelpCog, AgentCog
from cogs.auth_cog import setup_global_check


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
        self.log_auto_delete_seconds = max(0, int(os.getenv("LOG_AUTO_DELETE_SECONDS", "10800")))
        self.log_auto_delete_channels = {
            name.strip().lower()
            for name in os.getenv("LOG_AUTO_DELETE_CHANNELS", "manga-logs,logs").split(",")
            if name.strip()
        }
        
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
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="!help | Voice Chat"
            )
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
        auth_cog = self.get_cog("Auth") or self.get_cog("AuthCog")
        only_me_user_id = getattr(auth_cog, "only_me_user_id", None) if auth_cog else None
        is_onlyme_allowed = True
        if only_me_user_id is not None:
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
        if mentioned and not message.content.startswith("!"):
            if not is_onlyme_allowed:
                return
            clean_text = re.sub(rf"<@!?{self.user.id}>", "", message.content or "").strip()
            
            if clean_text and self.ai_service.enabled:
                async with message.channel.typing():
                    response = await self.ai_service.chat_response(
                        message.author.display_name,
                        clean_text
                    )
                    await message.reply(response)
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
    print(f"📍 AI Enabled: {'Yes (OpenRouter)' if OPENROUTER_API_KEY else 'No - Set OPENROUTER_API_KEY'}")
    
    bot = MangaBot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
