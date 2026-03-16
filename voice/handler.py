"""
Voice Handler - Manages voice connections and audio processing.
Supports "manga" trigger word and voice commands.
"""
import discord
from discord.ext import voice_recv
import asyncio
import re
import os
from datetime import timedelta

from .sink import VoiceSink
from services import AIService, TTSService, SpeechRecognitionService


class VoiceHandler:
    """
    Manages voice connections, listening, and responses.
    Coordinates between VoiceSink, SpeechRecognition, AI, and TTS services.

    Supports voice commands:
    - "manga <message>" - Chat with the bot
    - "manga mute <user>" - Mute a user
    - "manga unmute <user>" - Unmute a user  
    - "manga kick <user>" - Kick from voice
    - "manga timeout <user> [minutes]" - Timeout user
    - "manga change voice" - Change TTS voice
    """

    # Default trigger word to activate the bot
    TRIGGER_WORD = "manga"

    # Available voices for Manga (will cycle through these)
    MANGA_VOICES = ['english_female', 'english', 'arabic', 'arabic_male']

    # Target user ID for auto-join and audio playback
    AUTO_JOIN_USER_ID = 1143546576124530729

    # Audio file to play when anyone joins
    AUTO_PLAY_AUDIO = "sle.mp3"

    # Keep-alive interval in seconds (4 minutes to prevent inactivity disconnect)
    KEEP_ALIVE_INTERVAL = 240

    def __init__(self, bot, ai_service: AIService, tts_service: TTSService,
                 speech_service: SpeechRecognitionService):
        """
        Initialize the voice handler.

        Args:
            bot: The Discord bot instance
            ai_service: AI service for generating responses
            tts_service: TTS service for speaking responses
            speech_service: Speech recognition service
        """
        self.bot = bot
        self.ai = ai_service
        self.tts = tts_service
        self.speech = speech_service

        # Listening state
        self.listening = True
        self.owner_only = False
        self.owner_id = None
        self.blocked_users = set()
        self.allowed_users = set()  # if set, only these users are heard

        # Runtime tuning
        self.verbose_logs = os.getenv("VOICE_VERBOSE_LOGS", "0").lower() in {
            "1", "true", "yes", "on"}
        self.max_segment_tasks = max(
            1, int(os.getenv("VOICE_MAX_SEGMENT_TASKS", "4")))

        # Auto-kick list - users to kick when they join voice
        self.auto_kick_users = set()  # user_ids to auto-kick from voice

        # Current voice for Manga TTS (default to female voice)
        self.current_voice_index = 0
        self.manga_voice = self.MANGA_VOICES[0]

        # Active connections per guild
        self.sinks = {}   # guild_id -> VoiceSink
        self.tasks = {}   # guild_id -> asyncio.Task
        self.segment_tasks = {}  # guild_id -> set[asyncio.Task]

        # Keep-alive tasks per guild
        self.keep_alive_tasks = {}  # guild_id -> asyncio.Task
        self.keep_alive_ping_count = {}  # guild_id -> int

        # Track if disconnect was manual (command) or accidental (kick/error)
        # guild_ids where manual disconnect occurred
        self.manual_disconnect_guilds = set()

        # Auto-join state per guild (to avoid enforcement loop interference)
        self._auto_joining = set()
        self.home_channel_name = os.getenv("VOICE_HOME_CHANNEL", "Manga_bot")
        # Keep bot in the active/user channel after welcome audio unless explicitly enabled.
        self.return_home_after_play = os.getenv("VOICE_RETURN_HOME_AFTER_PLAY", "0").lower() in {
            "1", "true", "yes", "on"
        }

        # Voice trigger behavior
        configured_trigger = os.getenv(
            "VOICE_TRIGGER_WORD", self.TRIGGER_WORD).strip().lower()
        self.trigger_word = configured_trigger or self.TRIGGER_WORD
        self.trigger_required = os.getenv("VOICE_TRIGGER_REQUIRED", "1").lower() in {
            "1", "true", "yes", "on"
        }

        # Audio loop pacing (reduce idle CPU burn; configurable via env)
        self.audio_loop_idle_sleep = float(
            os.getenv("VOICE_IDLE_SLEEP", "0.05"))
        self.audio_loop_active_sleep = float(
            os.getenv("VOICE_ACTIVE_SLEEP", "0.005"))

    def _debug(self, message: str):
        if self.verbose_logs:
            print(message)

    def set_trigger_word(self, word: str) -> bool:
        """Update the trigger word used for voice activation."""
        cleaned = (word or "").strip().lower()
        if not cleaned:
            return False
        self.trigger_word = cleaned
        return True

    def set_trigger_required(self, required: bool):
        """Enable or disable trigger-word requirement."""
        self.trigger_required = bool(required)

    def _pick_text_channel(self, guild: discord.Guild):
        """Pick a writable text channel for status/voice messages."""
        preferred = discord.utils.get(guild.text_channels, name="manga-logs")
        if preferred:
            me = guild.me or guild.get_member(self.bot.user.id)
            perms = preferred.permissions_for(me) if me else None
            if perms and perms.send_messages:
                return preferred

        me = guild.me or guild.get_member(self.bot.user.id)
        for channel in guild.text_channels:
            perms = channel.permissions_for(me) if me else None
            if perms and perms.send_messages:
                return channel
        return None

    async def _ensure_voice_pipeline(self, guild: discord.Guild, voice_client):
        """Ensure sink/listener/task are active for the given guild connection."""
        guild_id = guild.id
        sink = self.sinks.get(guild_id)
        if sink is None:
            sink = VoiceSink(self)
            self.sinks[guild_id] = sink

        if isinstance(voice_client, voice_recv.VoiceRecvClient):
            try:
                if not voice_client.is_listening():
                    voice_client.listen(sink)
            except Exception as e:
                print(
                    f"⚠️ Failed to start voice listener for guild {guild_id}: {e}")

        task = self.tasks.get(guild_id)
        if task is None or task.done():
            text_channel = self._pick_text_channel(guild)
            self._cancel_segment_tasks(guild_id)
            self.tasks[guild_id] = asyncio.create_task(
                self._process_audio_loop(guild_id, text_channel, voice_client)
            )

    def _cancel_segment_tasks(self, guild_id: int):
        tasks = self.segment_tasks.pop(guild_id, set())
        for task in list(tasks):
            task.cancel()

    async def join_channel(self, ctx) -> bool:
        """
        Join a voice channel and start listening.

        Args:
            ctx: Command context

        Returns:
            True if successful, False otherwise
        """
        # Check if user is in voice
        if not ctx.author.voice:
            await ctx.send("❌ You need to be in a voice channel!")
            return False

        channel = ctx.author.voice.channel

        try:
            # Connect or move to channel
            if ctx.voice_client:
                await ctx.voice_client.move_to(channel)
                vc = ctx.voice_client
            else:
                vc = await channel.connect(cls=voice_recv.VoiceRecvClient)

            # Create sink for this guild
            sink = VoiceSink(self)
            self.sinks[ctx.guild.id] = sink

            # Start listening
            vc.listen(sink)

            # Cancel existing processing task if any
            if ctx.guild.id in self.tasks:
                self.tasks[ctx.guild.id].cancel()
            self._cancel_segment_tasks(ctx.guild.id)

            # Start new processing task
            task = asyncio.create_task(
                self._process_audio_loop(ctx.guild.id, ctx.channel, vc)
            )
            self.tasks[ctx.guild.id] = task

            # Start keep-alive task to prevent inactivity disconnect
            self._start_keep_alive(ctx.guild.id, vc)

            await ctx.send(f"🎙️ Joined **{channel.name}** and listening!")

            # Greet with Manga's voice
            if self.trigger_required:
                greeting = f"Hello! Say '{self.trigger_word}' followed by your command!"
            else:
                greeting = "Hello! I am listening. You can talk to me directly."
            await self.tts.speak(vc, greeting, self.manga_voice)

            # Reset manual disconnect flag on successful join
            self.manual_disconnect_guilds.discard(ctx.guild.id)

            return True

        except Exception as e:
            print(f"❌ Voice Handler: Failed to join - {e}")
            await ctx.send(f"❌ Failed to join: {e}")
            return False

    async def leave_channel(self, ctx) -> bool:
        """
        Leave the voice channel and cleanup.

        Args:
            ctx: Command context

        Returns:
            True if successful, False otherwise
        """
        if not ctx.voice_client:
            await ctx.send("❌ I'm not in a voice channel!")
            return False

        guild_id = ctx.guild.id

        # Cancel processing task
        if guild_id in self.tasks:
            self.tasks[guild_id].cancel()
            del self.tasks[guild_id]
        self._cancel_segment_tasks(guild_id)

        # Cancel keep-alive task
        self._stop_keep_alive(guild_id)

        # Stop listening
        if isinstance(ctx.voice_client, voice_recv.VoiceRecvClient):
            if ctx.voice_client.is_listening():
                ctx.voice_client.stop_listening()

        # Remove sink
        if guild_id in self.sinks:
            self.sinks[guild_id].cleanup()
            del self.sinks[guild_id]

        # Disconnect
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Left the voice channel!")

        # Set flag so we don't auto-rejoin
        self.manual_disconnect_guilds.add(guild_id)

        return True

    async def _process_audio_loop(self, guild_id: int, text_channel, voice_client):
        """
        Main loop for processing audio from the sink.

        Args:
            guild_id: The guild ID
            text_channel: Text channel for sending messages
            voice_client: Voice client for speaking
        """
        print(f"🎙️ Audio processing loop started for guild {guild_id}")
        self.segment_tasks.setdefault(guild_id, set())

        try:
            while True:
                sink = self.sinks.get(guild_id)
                if not sink:
                    break

                # Get segments ready for processing
                segments = sink.get_ready_segments()
                if not segments:
                    await asyncio.sleep(self.audio_loop_idle_sleep)
                    continue

                for user_id, audio_data in segments:
                    # Bound concurrent work to avoid flooding event loop/CPU.
                    task_set = self.segment_tasks.setdefault(guild_id, set())
                    while len(task_set) >= self.max_segment_tasks:
                        done, _ = await asyncio.wait(
                            task_set,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=0.2,
                        )
                        if done:
                            task_set.difference_update(done)

                    task = asyncio.create_task(
                        self._process_segment(
                            guild_id, user_id, audio_data,
                            text_channel, voice_client
                        )
                    )
                    task_set.add(task)
                    task.add_done_callback(
                        lambda t, gid=guild_id: self.segment_tasks.get(
                            gid, set()).discard(t)
                    )

                # Short sleep to yield when actively processing audio
                await asyncio.sleep(self.audio_loop_active_sleep)

        except asyncio.CancelledError:
            print(f"🛑 Audio loop stopped for guild {guild_id}")
        except Exception as e:
            print(f"❌ Audio loop error: {e}")
        finally:
            self._cancel_segment_tasks(guild_id)

    async def _process_segment(self, guild_id: int, user_id: int,
                               audio_data: bytes, text_channel, voice_client):
        """
        Process a single audio segment from a user.

        Args:
            guild_id: The guild ID
            user_id: The user ID who spoke
            audio_data: Raw PCM audio bytes
            text_channel: Text channel for messages
            voice_client: Voice client for speaking
        """
        try:
            # Check volume level
            rms = VoiceSink.calculate_rms(audio_data)

            if rms < VoiceSink.MIN_RMS:
                self._debug(f"🔇 Audio too quiet (RMS: {int(rms)}), skipping")
                return

            self._debug(
                f"🎤 Processing: {len(audio_data)} bytes, RMS: {int(rms)}")

            # Transcribe audio to text
            text = await self.speech.transcribe(audio_data)

            if not text:
                self._debug("🔇 No speech detected")
                return

            # Get user info
            user = self.bot.get_user(user_id)
            username = user.display_name if user else "User"
            guild = self.bot.get_guild(guild_id)
            member = guild.get_member(user_id) if guild else None

            self._debug(f"🗣️ {username} said: '{text}'")

            # Check for trigger word "manga"
            text_lower = text.lower().strip()

            has_trigger = self._has_trigger(text_lower)
            if self.trigger_required and not has_trigger:
                self._debug(
                    f"🔇 Trigger word '{self.trigger_word}' required, ignoring")
                return

            # Remove trigger when present; otherwise use full text in open mode.
            command_text = self._remove_trigger(
                text_lower) if has_trigger else text_lower

            if not command_text:
                # Just said "manga" with nothing else
                response = "Yes? What do you need?"
                await self._respond(text_channel, voice_client, username, text, response)
                return

            # Try to parse as a voice command
            command_result = await self._parse_and_execute_command(
                command_text, guild, member, voice_client
            )

            if command_result:
                # It was a command, respond with result
                await self._respond(text_channel, voice_client, username, text, command_result)
            else:
                # Not a command, treat as chat
                response = await self.ai.voice_response(username, command_text)
                self._debug(f"🤖 Response: '{response}'")
                await self._respond(text_channel, voice_client, username, text, response)

        except Exception as e:
            print(f"❌ Segment processing error: {e}")

        finally:
            # Mark processing as complete
            sink = self.sinks.get(guild_id)
            if sink:
                sink.finish_processing(user_id)

    def _has_trigger(self, text: str) -> bool:
        """Check if text contains the trigger word."""
        trigger = re.escape(self.trigger_word)
        # Match trigger at start or as standalone word.
        patterns = [
            rf'^{trigger}\b',
            rf'\b{trigger}\b',
            # Common Arabic pronunciation for backward compatibility.
            r'^منجا\b',
        ]
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False

    def _remove_trigger(self, text: str) -> str:
        """Remove trigger word from text and return the rest."""
        trigger = re.escape(self.trigger_word)
        # Remove trigger word and variations from start.
        patterns = [
            rf'^{trigger}[,\s]*',
            r'^منجا[,\s]*',
        ]
        result = text
        for pattern in patterns:
            result = re.sub(pattern, '', result, flags=re.IGNORECASE)
        return result.strip()

    async def _respond(self, text_channel, voice_client, username: str,
                       user_text: str, response: str):
        """Send response to text channel and speak it."""
        # Send to text channel
        if text_channel:
            embed = discord.Embed(color=discord.Color.purple())
            embed.add_field(name=f"🎤 {username}",
                            value=user_text, inline=False)
            embed.add_field(name="🤖 Manga", value=response, inline=False)
            await text_channel.send(embed=embed)

        # Speak the response with Manga's voice
        if voice_client and voice_client.is_connected():
            await self.tts.speak(voice_client, response, self.manga_voice)

    async def _parse_and_execute_command(self, text: str, guild,
                                         invoker: discord.Member,
                                         voice_client) -> str:
        """
        Parse text for voice commands and execute them.

        Returns:
            Result message if command was executed, None if not a command.
        """
        text = text.lower().strip()

        # Command patterns
        commands = {
            'mute': r'^mute\s+(.+)',
            'unmute': r'^unmute\s+(.+)',
            'kick': r'^kick\s+(.+)',
            'timeout': r'^timeout\s+(\S+)(?:\s+(\d+))?',
            'change voice': r'^change\s*voice',
            'change voice': r'^change\s*voice',
            'voice': r'^voice$',
            'leave': r'^(leave|disconnect|dc|exit|bye)',
        }

        # Check for leave command
        if re.match(commands['leave'], text):
            await self.leave_channel(self._create_mock_context(guild, invoker, voice_client))
            return "Goodbye!"

        # Check for change voice command
        if re.match(commands['change voice'], text) or re.match(commands['voice'], text):
            return self._change_voice()

        # Check for mute command
        match = re.match(commands['mute'], text)
        if match:
            target_name = match.group(1).strip()
            return await self._execute_mute(guild, invoker, target_name, mute=True)

        # Check for unmute command
        match = re.match(commands['unmute'], text)
        if match:
            target_name = match.group(1).strip()
            return await self._execute_mute(guild, invoker, target_name, mute=False)

        # Check for kick command
        match = re.match(commands['kick'], text)
        if match:
            target_name = match.group(1).strip()
            return await self._execute_kick(guild, invoker, target_name)

        # Check for timeout command
        match = re.match(commands['timeout'], text)
        if match:
            target_name = match.group(1).strip()
            minutes = int(match.group(2)) if match.group(2) else 5
            return await self._execute_timeout(guild, invoker, target_name, minutes)

        # Not a recognized command
        return None

    def _change_voice(self) -> str:
        """Cycle to next voice."""
        self.current_voice_index = (
            self.current_voice_index + 1) % len(self.MANGA_VOICES)
        self.manga_voice = self.MANGA_VOICES[self.current_voice_index]
        return f"Voice changed to {self.manga_voice.replace('_', ' ')}!"

    def _find_member(self, guild, name: str) -> discord.Member:
        """Find a member by name (fuzzy match)."""
        name = name.lower().strip()

        # Remove common words
        name = re.sub(r'\b(the|user|member)\b', '', name).strip()

        for member in guild.members:
            # Check display name
            if name in member.display_name.lower():
                return member
            # Check username
            if name in member.name.lower():
                return member

        return None

    async def _execute_mute(self, guild, invoker: discord.Member,
                            target_name: str, mute: bool) -> str:
        """Mute or unmute a user in voice."""
        if not invoker:
            return "I couldn't identify who gave the command."

        # Check invoker permissions
        if not invoker.guild_permissions.mute_members:
            return "You don't have permission to mute members."

        # Find target member
        target = self._find_member(guild, target_name)
        if not target:
            return f"I couldn't find anyone named {target_name}."

        if not target.voice:
            return f"{target.display_name} is not in a voice channel."

        try:
            await target.edit(mute=mute)
            action = "muted" if mute else "unmuted"
            return f"{target.display_name} has been {action}!"
        except discord.Forbidden:
            return "I don't have permission to do that."
        except Exception as e:
            return f"Failed: {str(e)}"

    async def _execute_kick(self, guild, invoker: discord.Member,
                            target_name: str) -> str:
        """Kick a user from voice channel."""
        if not invoker:
            return "I couldn't identify who gave the command."

        # Check invoker permissions
        if not invoker.guild_permissions.move_members:
            return "You don't have permission to kick from voice."

        # Find target member
        target = self._find_member(guild, target_name)
        if not target:
            return f"I couldn't find anyone named {target_name}."

        if not target.voice:
            return f"{target.display_name} is not in a voice channel."

        try:
            await target.move_to(None)  # Disconnect from voice
            return f"{target.display_name} has been kicked from voice!"
        except discord.Forbidden:
            return "I don't have permission to do that."
        except Exception as e:
            return f"Failed: {str(e)}"

    async def _execute_timeout(self, guild, invoker: discord.Member,
                               target_name: str, minutes: int) -> str:
        """Timeout a user."""
        if not invoker:
            return "I couldn't identify who gave the command."

        # Check invoker permissions
        if not invoker.guild_permissions.moderate_members:
            return "You don't have permission to timeout members."

        # Find target member
        target = self._find_member(guild, target_name)
        if not target:
            return f"I couldn't find anyone named {target_name}."

        # Limit timeout duration
        minutes = min(minutes, 60)  # Max 1 hour

        try:
            await target.timeout(timedelta(minutes=minutes))
            return f"{target.display_name} has been timed out for {minutes} minutes!"
        except discord.Forbidden:
            return "I don't have permission to do that."
        except Exception as e:
            return f"Failed: {str(e)}"

    # --- Control Methods ---

    def set_listening(self, enabled: bool):
        """Enable or disable listening."""
        self.listening = enabled

    def set_owner_only(self, owner_id: int = None):
        """Set owner-only mode."""
        if owner_id:
            self.owner_only = True
            self.owner_id = owner_id
        else:
            self.owner_only = False
            self.owner_id = None

    def block_user(self, user_id: int):
        """Block a user from being heard."""
        self.blocked_users.add(user_id)

    def unblock_user(self, user_id: int):
        """Unblock a user."""
        self.blocked_users.discard(user_id)

    def set_allowed_users(self, user_ids=None):
        """Set allow-list for voice input. Empty/None means everyone."""
        if user_ids:
            self.allowed_users = set(int(uid) for uid in user_ids)
        else:
            self.allowed_users.clear()

    def clear_allowed_users(self):
        """Clear voice allow-list so everyone is allowed."""
        self.allowed_users.clear()

    def set_voice(self, voice_name: str) -> bool:
        """Set Manga's TTS voice."""
        if voice_name in self.MANGA_VOICES:
            self.manga_voice = voice_name
            self.current_voice_index = self.MANGA_VOICES.index(voice_name)
            return True
        return False

    # --- Auto-Kick Methods ---

    def add_auto_kick(self, user_id: int):
        """Add a user to auto-kick list."""
        self.auto_kick_users.add(user_id)

    def remove_auto_kick(self, user_id: int):
        """Remove a user from auto-kick list."""
        self.auto_kick_users.discard(user_id)

    def is_auto_kick(self, user_id: int) -> bool:
        """Check if user is in auto-kick list."""
        return user_id in self.auto_kick_users

    def get_auto_kick_list(self) -> set:
        """Get the auto-kick user IDs."""
        return self.auto_kick_users.copy()

    async def handle_voice_state_update(self, member, before, after):
        """Handle voice state changes - auto-kick, auto-join/play, and auto-rejoin."""

        # 1. BOT DISCONNECT HANDLING (Auto-Rejoin)
        if member.id == self.bot.user.id:
            # Bot was disconnected (channel is None) and it wasn't manual
            if after.channel is None and member.guild.id not in self.manual_disconnect_guilds:
                print(
                    f"⚠️ Bot disconnected unexpectedly from {before.channel} in {member.guild.name}")
                await self._attempt_rejoin(member.guild)

            # Bot was moved/connected
            elif after.channel is not None:
                # If we're auto-joining to play welcome audio, don't force a move back
                if self.is_auto_joining(member.guild.id):
                    return

                if not self.return_home_after_play:
                    return

                # Check if in "Manga_bot"
                target_channel = discord.utils.get(
                    member.guild.voice_channels, name=self.home_channel_name)
                if target_channel and after.channel.id != target_channel.id:
                    # MITIGATION: Wait a bit to let the moving instance start playing
                    # This helps if there are 2 bot instances fighting
                    await asyncio.sleep(2.0)

                    # If we are playing audio, ignore (we will return after playback via callback)
                    # We check AGAIN after the sleep because playback might have just started
                    if member.guild.voice_client and member.guild.voice_client.is_playing():
                        return

                    print(
                        f"⚠️ Bot detected in wrong channel ({after.channel.name}). Moving back to {target_channel.name}...")
                    try:
                        await member.move_to(target_channel)
                    except Exception as e:
                        print(f"❌ Failed to move bot back: {e}")
            return

        # 2. USER STATE UPDATES
        # Check if user joined a voice channel

        if after.channel is not None and before.channel != after.channel:
            # User joined a channel

            # Auto-kick check
            if member.id in self.auto_kick_users:
                try:
                    await member.move_to(None)  # Kick from voice
                    print(f"🚫 Auto-kicked {member.display_name} from voice")
                    return True
                except Exception as e:
                    print(f"❌ Failed to auto-kick {member.display_name}: {e}")

            # Auto-join and play audio for any non-bot user
            if not member.bot:
                await self._auto_join_and_play(after.channel)

        return False

    async def _auto_join_and_play(self, channel: discord.VoiceChannel):
        """
        Auto-join a voice channel and play the welcome audio.

        Args:
            channel: The voice channel to join
        """
        try:
            guild = channel.guild
            self._auto_joining.add(guild.id)
            guild = channel.guild

            # Check if already connected to this channel
            voice_client = guild.voice_client

            if voice_client:
                if voice_client.channel.id == channel.id:
                    # Already in the same channel, just play audio
                    # Prevent enforcement loop from moving us mid-play
                    # self.manual_disconnect_guilds.add(guild.id) # Removed: Voice loop now respects playback
                    # Ensure connection is ready
                    for _ in range(20):
                        if voice_client.is_connected():
                            break
                        await asyncio.sleep(0.25)
                    await self._ensure_voice_pipeline(guild, voice_client)
                    await self._play_audio_file(voice_client, force=True)
                    return
                else:
                    # Move to the new channel
                    await voice_client.move_to(channel)
            else:
                # Connect to channel
                voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)

            # Wait for connection to be ready
            for _ in range(20):
                if voice_client.is_connected():
                    break
                await asyncio.sleep(0.25)
            if not voice_client.is_connected():
                print("❌ Voice client failed to connect in time.")
                return

            await self._ensure_voice_pipeline(guild, voice_client)

            # Stop keep-alive while we play the welcome audio (avoid interference)
            self._stop_keep_alive(guild.id)

            # Prevent enforcement loop from moving us mid-play
            # self.manual_disconnect_guilds.add(guild.id) # Removed: Voice loop now respects playback

            # Wait 2 seconds before playing welcome audio
            print(f"⏳ Waiting 2.0s to settle in {channel.name}...")
            await asyncio.sleep(2.0)

            # FIGHT BACK LOGIC:
            # Check if we were yanked away by the other bot instance during the sleep
            # If so, move back immediately before playing
            try:
                # Safety check: connection might have been cut
                if not voice_client or not voice_client.is_connected():
                    print("⚠️ Connection lost during wait. Attempting reconnect...")
                    voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient)

                retries = 3
                while retries > 0:
                    # Double check connection
                    if not voice_client.is_connected():
                        print("⚠️ Lost connection during fight back loop.")
                        break

                    if voice_client.channel.id != channel.id:
                        print(
                            f"⚠️ Detected interference! Bot was moved to {voice_client.channel.name}. Fighting back to {channel.name}...")
                        await voice_client.move_to(channel)
                        # Wait a tiny bit for move to complete
                        await asyncio.sleep(0.5)
                        retries -= 1
                    else:
                        break

                # Final check
                if voice_client.is_connected() and voice_client.channel.id != channel.id:
                    print(
                        f"❌ Could not maintain position in {channel.name}. Giving up on audio.")
                    return
            except Exception as e:
                print(f"❌ Error during position enforcement: {e}")
                # Don't return, try to play anyway if possible

            # Play the audio file
            if voice_client and voice_client.is_connected():
                await self._play_audio_file(voice_client, force=True)
            else:
                print("❌ Cannot play audio: Voice client not connected.")

            print(
                f"🔊 Auto-joined {channel.name} and playing audio for target user")

        except Exception as e:
            print(f"❌ Auto-join failed: {e}")
        finally:
            if channel and channel.guild:
                self._auto_joining.discard(channel.guild.id)

    def is_auto_joining(self, guild_id: int) -> bool:
        return guild_id in self._auto_joining

    async def _attempt_rejoin(self, guild):
        """Attempt to rejoin the 'Manga_bot' channel after unexpected disconnect."""
        print(
            f"🔄 Attempting to rejoin '{self.home_channel_name}' in {guild.name}...")
        await asyncio.sleep(3)  # Wait a bit before rejoin

        # Find home channel
        target_channel = discord.utils.get(
            guild.voice_channels, name=self.home_channel_name)

        if target_channel:
            try:
                vc = await target_channel.connect(cls=voice_recv.VoiceRecvClient)

                # Re-setup sink and audio processing
                sink = VoiceSink(self)
                self.sinks[guild.id] = sink
                vc.listen(sink)

                # Restart processing task
                if guild.id in self.tasks:
                    self.tasks[guild.id].cancel()

                # We need a text channel to send messages to. Try to find a suitable one.
                # Ideally we'd store the last used text channel, but for now we'll search.
                text_channel = self._pick_text_channel(guild)

                task = asyncio.create_task(
                    self._process_audio_loop(guild.id, text_channel, vc)
                )
                self.tasks[guild.id] = task

                # Restart keep-alive
                self._start_keep_alive(guild.id, vc)

                print(f"✅ Successfully rejoined {target_channel.name}")
                self.manual_disconnect_guilds.discard(guild.id)

            except Exception as e:
                print(f"❌ Failed to rejoin: {e}")
        else:
            print(
                f"❌ Could not find '{self.home_channel_name}' channel to rejoin.")

    def _create_mock_context(self, guild, invoker, voice_client):
        """Create a mock context object for calling commands internally."""
        from collections import namedtuple
        MockCtx = namedtuple(
            'MockCtx', ['guild', 'author', 'voice_client', 'send', 'channel'])

        async def mock_send(content=None, embed=None):
            # Try to find a channel to send to, or just log it
            print(f"🤖 [MockSend] {content}")

        return MockCtx(guild, invoker, voice_client, mock_send, None)

    async def _play_audio_file(self, voice_client, force: bool = False, file_name: str = None):
        """
        Play the audio file in the voice channel.

        Args:
            voice_client: The voice client to play audio through
            force: If True, stop any current playback to ensure audio plays
        """
        try:
            target_file = (file_name or self.AUTO_PLAY_AUDIO).strip()
            if not target_file:
                target_file = self.AUTO_PLAY_AUDIO
            if not target_file.lower().endswith(".mp3"):
                target_file = f"{target_file}.mp3"

            # Check if voice client is connected
            if not voice_client or not voice_client.is_connected():
                print("❌ Voice client is not connected, cannot play audio")
                return False

            # Check ffmpeg availability (allow override)
            import shutil
            ffmpeg_path = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
            if not ffmpeg_path and os.path.exists("/usr/bin/ffmpeg"):
                ffmpeg_path = "/usr/bin/ffmpeg"
            if not ffmpeg_path:
                print("❌ ffmpeg not found in PATH. Audio playback will fail.")
                # asyncio.create_task(self._wait_and_return(voice_client))
                return False

            # Try multiple possible audio file locations
            possible_paths = [
                # Inside voice folder (same directory as this file)
                os.path.join(os.path.dirname(__file__), target_file),
                # Relative to project root (parent of voice folder)
                os.path.join(os.path.dirname(
                    os.path.dirname(__file__)), target_file),
                # Current working directory
                os.path.join(os.getcwd(), target_file),
                # Voice subfolder from cwd
                os.path.join(os.getcwd(), "voice", target_file),
                # Absolute path in common locations
                f"/app/{target_file}",
                f"/app/voice/{target_file}",
                f"/home/user/app/{target_file}",
                f"/home/user/app/voice/{target_file}",
            ]

            audio_path = None
            for path in possible_paths:
                print(f"🔍 Checking audio path: {path}")
                if os.path.exists(path):
                    audio_path = path
                    print(f"✅ Found audio file at: {path}")
                    break

            if not audio_path:
                print(
                    f"❌ Audio file '{target_file}' not found in any location!")
                print(f"❌ Searched paths: {possible_paths}")
                print(f"❌ Current working directory: {os.getcwd()}")
                print(f"❌ __file__ location: {__file__}")
                # asyncio.create_task(self._wait_and_return(voice_client))
                return False

            # If already playing something, optionally stop to ensure welcome plays
            if voice_client.is_playing():
                if force:
                    voice_client.stop()
                else:
                    while voice_client.is_playing():
                        await asyncio.sleep(0.1)

            # Define callback for when audio finishes
            def after_playback(error):
                if error:
                    print(f"❌ Audio playback error: {error}")
                else:
                    print(f"✅ Finished playing: {target_file}")

                # Trigger return to home channel
                asyncio.run_coroutine_threadsafe(
                    self._after_play_callback(voice_client), self.bot.loop)

            # Create audio source and play
            print(f"🔊 Playing audio from: {audio_path}")
            try:
                audio_source = discord.FFmpegPCMAudio(
                    audio_path,
                    executable=ffmpeg_path,
                    before_options="-nostdin",
                    options="-vn",
                )
                voice_client.play(audio_source, after=after_playback)
            except OSError as e:
                print(f"❌ OSError during play (Socket closed?): {e}")
                asyncio.create_task(self._after_play_callback(voice_client))
                return False

            # Verify playback started
            await asyncio.sleep(0.2)
            if voice_client.is_playing():
                print(f"🔊 Started playing: {target_file}")
                return True
            else:
                print(f"❌ Playback did not start for: {target_file}")
                # If fail, force return immediately
                asyncio.create_task(self._after_play_callback(voice_client))
                return False

        except Exception as e:
            import traceback
            print(f"❌ Failed to play audio: {e}")
            import traceback
            print(f"❌ Failed to play audio: {e}")
            traceback.print_exc()
            asyncio.create_task(self._after_play_callback(voice_client))
            return False

    async def play_sound_file(self, voice_client, file_name: str) -> bool:
        """Public helper for playing a specific mp3 file."""
        return await self._play_audio_file(voice_client, force=True, file_name=file_name)

    async def _after_play_callback(self, voice_client):
        """Callback to return bot to home channel after playback."""
        if not voice_client or not voice_client.guild:
            return

        try:
            # Short delay to ensure audio is fully done
            await asyncio.sleep(0.5)

            guild = voice_client.guild
            if not self.return_home_after_play:
                # Stay where the user triggered playback and keep the connection alive.
                if voice_client.is_connected():
                    self._start_keep_alive(guild.id, voice_client)
                return

            target_channel = discord.utils.get(
                guild.voice_channels, name=self.home_channel_name)

            if target_channel and voice_client.channel != target_channel:
                try:
                    await voice_client.move_to(target_channel)
                    print(
                        f"🔙 Returned to {target_channel.name} (Event-Driven)")
                    # Restart keep-alive in the new channel (resetting it)
                    self._start_keep_alive(guild.id, voice_client)
                except Exception as e:
                    print(f"❌ Failed to return to home channel: {e}")

            # Ensure keep-alive is running
            if voice_client.is_connected():
                self._start_keep_alive(guild.id, voice_client)

        except Exception as e:
            print(f"❌ Error in after_play callback: {e}")

    # --- Keep-Alive Methods (Prevent Hugging Face Inactivity Disconnect) ---

    def _start_keep_alive(self, guild_id: int, voice_client):
        """Start a keep-alive task for a guild's voice connection."""
        # Cancel existing keep-alive if any
        self._stop_keep_alive(guild_id)

        # Create new keep-alive task
        task = asyncio.create_task(
            self._keep_alive_loop(guild_id, voice_client))
        self.keep_alive_tasks[guild_id] = task
        self.keep_alive_ping_count[guild_id] = 0
        print(f"💓 Keep-alive started for guild {guild_id}")

    def _stop_keep_alive(self, guild_id: int):
        """Stop the keep-alive task for a guild."""
        if guild_id in self.keep_alive_tasks:
            self.keep_alive_tasks[guild_id].cancel()
            del self.keep_alive_tasks[guild_id]
            self.keep_alive_ping_count.pop(guild_id, None)
            print(f"💔 Keep-alive stopped for guild {guild_id}")

    async def _keep_alive_loop(self, guild_id: int, voice_client):
        """
        Periodically send activity to keep the voice connection alive.
        This prevents Hugging Face from disconnecting due to inactivity.
        """
        try:
            while True:
                await asyncio.sleep(self.KEEP_ALIVE_INTERVAL)

                # Check if still connected
                if not voice_client or not voice_client.is_connected():
                    print(
                        f"💔 Voice client disconnected, stopping keep-alive for guild {guild_id}")
                    break

                # Send silent audio to keep connection alive
                await self._send_keep_alive_signal(voice_client)
                self.keep_alive_ping_count[guild_id] = self.keep_alive_ping_count.get(
                    guild_id, 0) + 1
                if self.verbose_logs and self.keep_alive_ping_count[guild_id] % 10 == 0:
                    print(
                        f"💓 Keep-alive ping sent for guild {guild_id} ({self.keep_alive_ping_count[guild_id]})")

        except asyncio.CancelledError:
            print(f"💔 Keep-alive cancelled for guild {guild_id}")
        except Exception as e:
            print(f"❌ Keep-alive error for guild {guild_id}: {e}")

    async def _send_keep_alive_signal(self, voice_client):
        """
        Send a keep-alive signal to maintain the connection.
        Uses lightweight connection checks (avoids FFmpeg spawn overhead).
        """
        try:
            if not voice_client or not voice_client.is_connected():
                return

            # Accessing latency yields a low-cost heartbeat touch without audio playback.
            _ = getattr(voice_client, "latency", None)
            await asyncio.sleep(0)
        except Exception as e:
            self._debug(f"⚠️ Keep-alive signal fallback: {e}")
