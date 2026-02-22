"""
Voice Commands Cog - Extended voice-related bot commands.
"""
import discord
from discord.ext import commands
import random
import os
import asyncio

from voice import VoiceHandler




class VoiceCog(commands.Cog, name="Voice"):
    """Voice-related commands for the bot."""
    
    def __init__(self, bot, voice_handler: VoiceHandler):
        self.bot = bot
        self.voice = voice_handler
        self.keyword = "manga"
        self.require_keyword = False
    
    @commands.command(name="join", aliases=["j", "connect"])
    async def join(self, ctx):
        """Join your voice channel and start listening."""
        await self.voice.join_channel(ctx)
    
    @commands.command(name="leave", aliases=["l", "dc", "disconnect"])
    async def leave(self, ctx):
        """Leave the voice channel."""
        await self.voice.leave_channel(ctx)
    
    @commands.command(name="stop", aliases=["s"])
    async def stop(self, ctx):
        """Stop listening but stay in channel."""
        if ctx.voice_client:
            self.voice.set_listening(False)
            await ctx.send("🔇 Stopped listening, but I'm still here.")
        else:
            await ctx.send("❌ I'm not in a voice channel!")
    
    @commands.command(name="say", aliases=["speak", "tts"])
    async def say(self, ctx, *, text: str):
        """Make the bot speak text in voice chat."""
        if not ctx.voice_client:
            return await ctx.send("❌ I'm not in a voice channel! Use `!join` first.")
        
        await ctx.message.add_reaction("🔊")
        await self.voice.tts.speak(ctx.voice_client, text)
        await ctx.message.add_reaction("✅")
    
    @commands.command(name="voiceopen")
    async def voiceopen(self, ctx, scope: str = "all"):
        """Enable voice replies (everyone or only you)."""
        scope = scope.lower()
        if scope in ["me", "onlyme", "owner"]:
            self.voice.set_owner_only(ctx.author.id)
            await ctx.send(f"🎙️ Voice replies enabled for **{ctx.author.display_name}** only.")
        else:
            self.voice.set_owner_only(None)
            await ctx.send("🎙️ Voice replies enabled for **everyone**.")
        
        self.voice.set_listening(True)
        
        # Auto-join if user is in voice
        if ctx.author.voice and not ctx.voice_client:
            await self.voice.join_channel(ctx)
    
    @commands.command(name="voiceclose")
    async def voiceclose(self, ctx):
        """Disable voice replies."""
        self.voice.set_listening(False)
        await ctx.send("🔇 Voice replies disabled.")
    
    @commands.command(name="voicekeyword")
    async def voicekeyword(self, ctx, action: str = None, *, word: str = None):
        """Configure voice keyword (on/off or set <word>)."""
        if action is None:
            status = "ON" if self.require_keyword else "OFF"
            await ctx.send(f"🔑 Keyword: **{self.keyword}** (Required: {status})")
            return
        
        action = action.lower()
        if action == "on":
            self.require_keyword = True
            await ctx.send(f"🔑 Keyword required: **ON** (say '{self.keyword}' to trigger)")
        elif action == "off":
            self.require_keyword = False
            await ctx.send("🔑 Keyword required: **OFF**")
        elif action == "set" and word:
            self.keyword = word.lower()
            await ctx.send(f"🔑 Keyword set to: **{self.keyword}**")
        else:
            await ctx.send("Usage: `!voicekeyword on/off` or `!voicekeyword set <word>`")
    
    @commands.command(name="mode")
    async def mode(self, ctx, *, style: str):
        """Change AI persona/style."""
        # This would modify the AI prompt style
        await ctx.send(f"🎭 AI mode changed to: **{style}**")
    
    @commands.command(name="sound")
    async def sound(self, ctx, name: str):
        """Play a sound effect."""
        sounds = {
            "airhorn": "BWAAAAAAH!",
            "bruh": "Bruh.",
            "oof": "Oof!",
            "wow": "Wow!",
            "sad": "Sad violin noises...",
        }
        
        if name.lower() in sounds:
            if ctx.voice_client:
                await self.voice.tts.speak(ctx.voice_client, sounds[name.lower()])
                await ctx.message.add_reaction("🔊")
            else:
                await ctx.send("❌ I'm not in voice!")
        else:
            available = ", ".join(sounds.keys())
            await ctx.send(f"Available sounds: {available}")
    
    @commands.command(name="listen")
    async def listen(self, ctx, state: str = None):
        """Toggle listening mode on/off."""
        if state:
            enabled = state.lower() in ["on", "yes", "true", "1", "enable"]
            self.voice.set_listening(enabled)
        else:
            self.voice.set_listening(not self.voice.listening)
        
        status = "ON ✅" if self.voice.listening else "OFF ❌"
        await ctx.send(f"🎙️ Listening: **{status}**")
    
    @commands.command(name="claim")
    async def claim(self, ctx):
        """Only listen to your voice (owner mode)."""
        self.voice.set_owner_only(ctx.author.id)
        await ctx.send(f"👂 Now listening only to **{ctx.author.display_name}**")
    
    @commands.command(name="reset", aliases=["unclaim"])
    async def reset(self, ctx):
        """Listen to everyone again."""
        self.voice.set_owner_only(None)
        self.voice.clear_allowed_users()
        await ctx.send("👂 Now listening to **everyone**")

    @commands.command(name="vcaccess", aliases=["voiceaccess", "allow2"])
    @commands.has_permissions(manage_guild=True)
    async def vcaccess(self, ctx, user1: discord.Member = None, user2: discord.Member = None):
        """Allow voice input from only 2 users. Usage: !vcaccess @user1 @user2"""
        if user1 is None or user2 is None:
            return await ctx.send("Usage: `!vcaccess @user1 @user2`")

        allowed = {user1.id, user2.id}
        self.voice.set_owner_only(None)
        self.voice.set_allowed_users(allowed)
        await ctx.send(
            f"🔐 VC access limited to: **{user1.display_name}** and **{user2.display_name}**"
        )

    @commands.command(name="vcaccessoff", aliases=["voiceaccessoff", "allowall"])
    @commands.has_permissions(manage_guild=True)
    async def vcaccessoff(self, ctx):
        """Disable VC access restriction and allow everyone."""
        self.voice.clear_allowed_users()
        self.voice.set_owner_only(None)
        await ctx.send("🔓 VC access restriction disabled. Listening to everyone.")

    @commands.command(name="vcaccesslist", aliases=["voiceaccesslist"])
    async def vcaccesslist(self, ctx):
        """Show who is currently allowed in VC access filter."""
        if self.voice.owner_only and self.voice.owner_id:
            user = self.bot.get_user(self.voice.owner_id)
            name = user.display_name if user else str(self.voice.owner_id)
            return await ctx.send(f"👤 Owner-only mode is ON: **{name}**")

        if not self.voice.allowed_users:
            return await ctx.send("🌐 VC access: everyone")

        names = []
        for uid in sorted(self.voice.allowed_users):
            user = self.bot.get_user(uid)
            names.append(user.display_name if user else f"Unknown ({uid})")
        await ctx.send("🔐 VC access allowed users:\n" + "\n".join([f"• {n}" for n in names]))
    
    @commands.command(name="ignore", aliases=["block"])
    async def ignore(self, ctx, member: discord.Member):
        """Ignore a user's voice input."""
        self.voice.block_user(member.id)
        await ctx.send(f"🔇 Ignoring **{member.display_name}**")
    
    @commands.command(name="unignore", aliases=["unblock"])
    async def unignore(self, ctx, member: discord.Member):
        """Stop ignoring a user's voice input."""
        self.voice.unblock_user(member.id)
        await ctx.send(f"🔊 Listening to **{member.display_name}** again")
    
    @commands.command(name="voice", aliases=["mangavoice", "setvoice"])
    async def set_voice(self, ctx, voice_name: str = None):
        """Change Manga's TTS voice. Options: english, english_female, arabic, arabic_male"""
        if not voice_name:
            current = self.voice.manga_voice
            voices = ", ".join(self.voice.MANGA_VOICES)
            await ctx.send(f"🔊 Current voice: **{current}**\nAvailable: `{voices}`")
            return
        
        voice_name = voice_name.lower().replace(" ", "_")
        if self.voice.set_voice(voice_name):
            await ctx.send(f"🔊 Manga's voice changed to: **{voice_name}**")
            # Demo the new voice
            if ctx.voice_client:
                await self.voice.tts.speak(ctx.voice_client, "This is my new voice!", voice_name)
        else:
            voices = ", ".join(self.voice.MANGA_VOICES)
            await ctx.send(f"❌ Unknown voice. Available: `{voices}`")
    
    @commands.command(name="voicestatus", aliases=["vstatus"])
    async def voice_status(self, ctx):
        """Show current voice settings."""
        embed = discord.Embed(
            title="🎙️ Voice Status",
            color=discord.Color.blue()
        )
        
        if ctx.voice_client:
            channel = ctx.voice_client.channel.name
            embed.add_field(name="Connected", value=f"✅ {channel}", inline=True)
        else:
            embed.add_field(name="Connected", value="❌ Not connected", inline=True)
        
        listen_status = "✅ ON" if self.voice.listening else "❌ OFF"
        embed.add_field(name="Listening", value=listen_status, inline=True)
        
        # Show Manga's current voice
        embed.add_field(name="Manga Voice", value=f"🔊 {self.voice.manga_voice}", inline=True)
        
        if self.voice.owner_only:
            owner = self.bot.get_user(self.voice.owner_id)
            owner_name = owner.display_name if owner else "Unknown"
            embed.add_field(name="Owner Mode", value=f"👤 {owner_name}", inline=True)
        else:
            embed.add_field(name="Owner Mode", value="❌ Disabled", inline=True)

        if self.voice.allowed_users:
            embed.add_field(name="VC Access Filter", value=f"🔐 {len(self.voice.allowed_users)} users", inline=True)
        else:
            embed.add_field(name="VC Access Filter", value="🌐 Everyone", inline=True)
        
        keyword_status = "ON" if self.require_keyword else "OFF"
        embed.add_field(name="Keyword", value=f"{self.keyword} ({keyword_status})", inline=True)
        
        if self.voice.blocked_users:
            embed.add_field(name="Blocked", value=str(len(self.voice.blocked_users)), inline=True)
        
        # Add voice commands help
        embed.add_field(
            name="📢 Voice Commands",
            value="Say **'Manga'** + command:\n`manga mute <user>`\n`manga unmute <user>`\n`manga kick <user>`\n`manga timeout <user>`\n`manga change voice`",
            inline=False
        )
        
        # Show auto-kick list if any
        if self.voice.auto_kick_users:
            embed.add_field(name="🚫 Auto-Kick", value=str(len(self.voice.auto_kick_users)) + " users", inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="vckick", aliases=["voicekick"])
    @commands.has_permissions(move_members=True)
    async def vckick(self, ctx, member: discord.Member = None):
        """Auto-kick a user whenever they join voice. Usage: !vckick @user"""
        if member is None:
            # Show list if no member specified
            if not self.voice.auto_kick_users:
                await ctx.send("🚫 No one is being auto-kicked. Use `!vckick @user` to add someone.")
                return
            
            lines = ["**🚫 Auto-Kick List:**"]
            for user_id in self.voice.auto_kick_users:
                user = self.bot.get_user(user_id)
                name = user.display_name if user else f"Unknown ({user_id})"
                lines.append(f"• {name}")
            await ctx.send("\n".join(lines))
            return
        
        # Add user to auto-kick
        self.voice.add_auto_kick(member.id)
        await ctx.send(f"🚫 **{member.display_name}** will be auto-kicked from voice!")
        
        # Kick them now if in voice
        if member.voice:
            try:
                await member.move_to(None)
                await ctx.send(f"👢 Kicked from voice!")
            except:
                pass
    
    @commands.command(name="stopvckick", aliases=["svk"])
    @commands.has_permissions(move_members=True)
    async def stopvckick(self, ctx, member: discord.Member):
        """Stop auto-kicking a user. Usage: !stopvckick @user"""
        if member.id not in self.voice.auto_kick_users:
            await ctx.send(f"❌ **{member.display_name}** is not being auto-kicked.")
            return
        
        self.voice.remove_auto_kick(member.id)
        await ctx.send(f"✅ **{member.display_name}** can join voice now.")
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Auto-kick users when they join voice channels."""
        await self.voice.handle_voice_state_update(member, before, after)
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Called when bot joins a new server - setup voice."""
        await self._ensure_voice_channel(guild)
        


    async def _ensure_voice_channel(self, guild):
        """Ensure 'Manga_bot' channel exists and join it."""
        try:
            channel_name = "Manga_bot"
            channel = discord.utils.get(guild.voice_channels, name=channel_name)
            
            # Create if missing
            if not channel:
                try:
                    channel = await guild.create_voice_channel(channel_name)
                    print(f"✅ Created '{channel_name}' channel in {guild.name}")
                except discord.Forbidden:
                    print(f"❌ Missing permission to create channel in {guild.name}")
                    return
                except Exception as e:
                    print(f"❌ Failed to create channel in {guild.name}: {e}")
                    return

            # Join if not connected
            if not guild.voice_client:
                try:
                    # Use the voice handler's join logic but we need a context
                    # So we'll trigger it manually via handler
                    # We can't easily mock ctx here for handler.join_channel due to dependency on ctx.send
                    # So we'll manually do what handler.join_channel does but simplified for auto-join
                    
                    from discord.ext import voice_recv
                    from voice.sink import VoiceSink
                    
                    vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
                    
                    # Setup sink and task manually
                    self.voice.sinks[guild.id] = VoiceSink(self.voice)
                    vc.listen(self.voice.sinks[guild.id])
                    
                    # Need a text channel for output. Search for 'manga-logs' or 'general' or first avail
                    text_channel = discord.utils.get(guild.text_channels, name="manga-logs")
                    if not text_channel:
                         text_channel = guild.text_channels[0]
                    
                    # Start processing
                    import asyncio
                    task = asyncio.create_task(
                        self.voice._process_audio_loop(guild.id, text_channel, vc)
                    )
                    self.voice.tasks[guild.id] = task
                    
                    # Start keep-alive
                    self.voice._start_keep_alive(guild.id, vc)
                    
                    # Reset manual disconnect flag
                    self.voice.manual_disconnect_guilds.discard(guild.id)
                    
                    print(f"✅ Auto-joined '{channel_name}' in {guild.name}")
                    
                except Exception as e:
                    print(f"❌ Failed to auto-join in {guild.name}: {e}")
            
            # If connected but in wrong channel, move
            elif guild.voice_client and guild.voice_client.channel and guild.voice_client.channel.id != channel.id:
                try:
                     await guild.voice_client.move_to(channel)
                     print(f"➡️ Moved to '{channel_name}' in {guild.name}")
                except Exception as e:
                     print(f"❌ Failed to move to channel in {guild.name}: {e}")

        except Exception as e:
            print(f"❌ Error ensuring voice channel in {guild.name}: {e}")
    @commands.Cog.listener()
    async def on_ready(self):
    
        # Startup voice check
        print("🎙️ Startup: Checking voice channels for all guilds...", flush=True)
        for guild in self.bot.guilds:
            try:
                await self._ensure_voice_channel(guild)
            except Exception as e:
                print(f"❌ Startup voice error for {guild.name}: {e}", flush=True)

    # Loop removed in favor of event-driven architecture
    # See voice/handler.py handle_voice_state_update and _after_play_callback







async def setup(bot):
    pass
