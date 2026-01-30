"""
Manga Discord Bot - Main Entry Point
A voice-enabled AI assistant for Discord.
"""

import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Import services
from services import AIService, TTSService, SpeechRecognitionService, LLMAgentService

# Import voice handler
from voice import VoiceHandler

# Import cogs
from cogs import VoiceCog, ChatCog, AuthCog, HelpCog, AgentCog


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
        await self.add_cog(AuthCog(self))
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
        # Ignore own messages
        if message.author == self.user:
            return
        
        # Process commands
        await self.process_commands(message)
        
        # Respond when mentioned (if not a command)
        if self.user.mentioned_in(message) and not message.content.startswith("!"):
            clean_text = message.content.replace(f"<@{self.user.id}>", "").strip()
            
            if clean_text and self.ai_service.enabled:
                async with message.channel.typing():
                    response = await self.ai_service.chat_response(
                        message.author.display_name,
                        clean_text
                    )
                    await message.reply(response)
    
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
