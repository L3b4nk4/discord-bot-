import socket
import os
import asyncio
import discord
from discord.ext import commands
from discord.ext import voice_recv
import speech_recognition as sr
from dotenv import load_dotenv
import time
import sys
import aiohttp
import base64
from aiohttp import web
from groq import Groq
import edge_tts
import logging
import math
import struct
import difflib
import discord.ext.voice_recv.opus as _recv_opus
import discord.opus
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest
from datetime import timedelta, datetime
import re
import signal
import random
import telegram
import json

# Optional: Google GenAI SDK
try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False

# --- DNS MONKEY PATCH (CRITICAL FOR SPACES/DOCKER) ---
_orig_getaddrinfo = socket.getaddrinfo

DNS_MAP = {
    "discord.com": "162.159.135.232",
    "gateway.discord.gg": "162.159.135.232",
    "voice.discord.media": "162.159.135.232",
    "api.telegram.org": "149.154.167.220",
    "api.groq.com": "104.18.2.161",
    "generativelanguage.googleapis.com": "172.217.21.10",
    "dns.google": "8.8.8.8"
}

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if host in DNS_MAP:
            print(f"🔧 DNS Patch: Resolved {host} -> {DNS_MAP[host]}")
            return [(2, 1, 6, '', (DNS_MAP[host], port))]
        raise

socket.getaddrinfo = patched_getaddrinfo
# -----------------------------------------------------

# Global reference for cleanup
TG_APP_INSTANCE = None

# Load Environment Variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
AI_PROVIDER_TIMEOUT = max(5, int(os.getenv("AI_PROVIDER_TIMEOUT", "12")))
AI_TOTAL_TIMEOUT = max(AI_PROVIDER_TIMEOUT, int(os.getenv("AI_TOTAL_TIMEOUT", "20")))

# Configure Groq
groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        print("✅ Groq Client Initialized")
    except Exception as e:
        print(f"⚠️ Failed to initialize Groq: {e}")

gemini_client = None
if GEMINI_API_KEY and GENAI_AVAILABLE:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        print(f"✅ Gemini Configured ({GEMINI_MODEL})")
    except Exception as e:
        print(f"⚠️ Failed to initialize Gemini SDK: {e}")
elif GEMINI_API_KEY and not GENAI_AVAILABLE:
    print("⚠️ GEMINI_API_KEY found but google-genai is not installed.")

if OPENROUTER_API_KEY:
    print(f"✅ OpenRouter Configured ({OPENROUTER_MODEL})")


def _run_gemini_generate(prompt: str) -> str:
    if not gemini_client:
        raise RuntimeError("Gemini client unavailable")
    full_prompt = (
        "You are Manga, a helpful and witty Discord bot. Be concise and clear.\n\n"
        f"User request:\n{prompt}"
    )
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=full_prompt,
    )
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    raise RuntimeError("Gemini returned no text")


def _run_groq_generate(prompt: str) -> str:
    if not groq_client:
        raise RuntimeError("Groq client unavailable")
    completion = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a helpful and witty Discord bot named Manga."},
            {"role": "user", "content": prompt},
        ],
        model="llama-3.3-70b-versatile",
    )
    return completion.choices[0].message.content.strip()


async def _generate_openrouter(prompt: str) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter key unavailable")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/your-project",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": "You are Manga, a helpful and witty Discord bot."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=AI_PROVIDER_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"OpenRouter HTTP {resp.status}: {body[:200]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()

# Configure LLM Agent (Ollama - free, local)
from services.llm_agent_service import LLMAgentService
llm_agent = LLMAgentService()

async def ai_generate(prompt):
    """Helper to generate AI content using Gemini, OpenRouter, then Groq fallback."""
    async def _run_chain() -> str:
        if gemini_client:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(_run_gemini_generate, prompt),
                    timeout=AI_PROVIDER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"⚠️ Gemini generation timed out after {AI_PROVIDER_TIMEOUT}s")
            except Exception as e:
                print(f"⚠️ Gemini generation failed: {e}")

        if OPENROUTER_API_KEY:
            try:
                return await asyncio.wait_for(
                    _generate_openrouter(prompt),
                    timeout=AI_PROVIDER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"⚠️ OpenRouter generation timed out after {AI_PROVIDER_TIMEOUT}s")
            except Exception as e:
                print(f"⚠️ OpenRouter generation failed: {e}")

        if groq_client:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(_run_groq_generate, prompt),
                    timeout=AI_PROVIDER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                print(f"⚠️ Groq generation timed out after {AI_PROVIDER_TIMEOUT}s")
            except Exception as e:
                print(f"⚠️ Groq generation failed: {e}")

        return "I need my AI brain (Gemini, Groq, or OpenRouter) to do that."

    try:
        return await asyncio.wait_for(_run_chain(), timeout=AI_TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"⚠️ ai_generate total timeout reached after {AI_TOTAL_TIMEOUT}s")
        return "⏱️ I'm taking too long right now. Please try again."

async def shutdown_telegram(signal_name):
    """Cleanup Telegram on shutdown."""
    print(f"🛑 Received {signal_name}, shutting down Telegram...", flush=True)
    if TG_APP_INSTANCE:
        try:
            if TG_APP_INSTANCE.updater.running:
                await TG_APP_INSTANCE.updater.stop()
            if TG_APP_INSTANCE.running:
                await TG_APP_INSTANCE.stop()
            await TG_APP_INSTANCE.shutdown()
            print("✅ Telegram shutdown complete.", flush=True)
        except Exception as e:
            print(f"⚠️ Error during Telegram shutdown: {e}", flush=True)

# --- NETWORK SETUP (Fallback) ---
def setup_network_access():
    print("DEBUG: Attempting to patch /etc/hosts...", flush=True)
    mappings = [
        ("162.159.138.232", "discord.com"),
        ("162.159.138.232", "gateway.discord.gg"),
        ("149.154.166.110", "api.telegram.org"),
        ("35.214.219.205", "rotterdam.discord.gg"),
        ("162.159.129.233", "voice.discord.media")
    ]
    try:
        with open("/etc/hosts", "a") as f:
            f.write("\n# Added by Bot for DNS Fix\n")
            for ip, host in mappings:
                f.write(f"{ip} {host}\n")
        print("✅ Successfully patched /etc/hosts", flush=True)
    except Exception as e:
        print(f"⚠️ Could not patch /etc/hosts: {e}", flush=True)

setup_network_access()

# --- MONKEY PATCH OPUS ---
original_decode_packet = _recv_opus.PacketDecoder._decode_packet

def patched_decode_packet(self, packet):
    try:
        return original_decode_packet(self, packet)
    except discord.opus.OpusError:
        return packet, b'\x00' * 3840

_recv_opus.PacketDecoder._decode_packet = patched_decode_packet

discord.utils.setup_logging(level=logging.INFO)
logging.getLogger("discord.ext.voice_recv").setLevel(logging.WARNING)
os.environ["PATH"] += os.pathsep + os.path.abspath(".")

try:
    discord.opus.load_opus('libopus.so.0')
except:
    try: discord.opus.load_opus('/usr/lib/x86_64-linux-gnu/libopus.so.0')
    except: pass

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Globals
OWNER_ID = None
LISTENING_MODE = True
audio_tasks = {}
TELEGRAM_MAPPING = {"L3B4nk4": True}
TELEGRAM_CHAT_ID = None
AUDIO_BUFFER = []
VOICE_REPLY_MODE = "all"
VOICE_KEYWORD_ENABLED = False
VOICE_KEYWORD = "manga"
IGNORED_USERS = set()
AI_PERSONA = "helpful"
START_TIME = datetime.now()
TEXT_ONLY_ME_USER_ID = None

def _strip_bot_mentions(text: str, user_id: int) -> str:
    return re.sub(rf"<@!?{user_id}>", "", text or "").strip()

@bot.event
async def on_message(message):
    global TEXT_ONLY_ME_USER_ID
    if message.author == bot.user: return

    if message.content.startswith("!send "):
        content = message.content[6:].strip()
        sender = message.author.display_name
        print(f"📨 Forwarding '!send' from {sender}: {content}")
        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            try:
                temp_bot = telegram.Bot(TELEGRAM_TOKEN)
                await temp_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"📩 **From Discord ({sender}):**\n{content}")
                await message.add_reaction("✅")
            except Exception as e:
                print(f"Failed to forward to Telegram: {e}")
                await message.add_reaction("❌")
        else:
            await message.channel.send("⚠️ Telegram link not active.")
        return

    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    # Mention-only text AI.
    if bot.user.mentioned_in(message):
        # !onlyme text lock: silently ignore other users.
        if TEXT_ONLY_ME_USER_ID is not None and message.author.id != TEXT_ONLY_ME_USER_ID:
            return

        try:
            clean_text = _strip_bot_mentions(message.content, bot.user.id)
            if not clean_text:
                return
            async with message.channel.typing():
                chat_prompt = f"User '{message.author.display_name}' said: '{clean_text}'. Reply concisely."
                response_text = await ai_generate(chat_prompt)
                await message.reply(response_text)
                if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
                    try:
                        temp_bot = telegram.Bot(TELEGRAM_TOKEN)
                        await temp_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=f"💬 **Discord AI Reply:**\n{response_text}")
                    except:
                        pass
        except Exception as e:
            print(f"AI Chat Error: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        # Mention-only mode: ignore unknown commands.
        return
    else:
        print(f"⚠️ Command Error: {error}")


@bot.check
async def onlyme_command_gate(ctx):
    """If !onlyme text lock is active, restrict command usage too."""
    if TEXT_ONLY_ME_USER_ID is None:
        return True

    if ctx.author.id == TEXT_ONLY_ME_USER_ID:
        return True

    # Allow server owner/admin to unlock with !openall.
    is_admin = bool(ctx.guild and ctx.author.guild_permissions.administrator)
    is_server_owner = bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)
    cmd_name = ctx.command.name if ctx.command else ""
    if cmd_name == "openall" and (is_admin or is_server_owner):
        return True

    await ctx.send("🔒 Bot is locked by `!onlyme`. Commands are allowed only for the lock owner.")
    return False

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot: return
    if after.channel and not before.channel:
        if not member.guild.voice_client:
            try:
                vc = await after.channel.connect(cls=voice_recv.VoiceRecvClient)
                await speak(vc, f"Hello {member.display_name}")
                sink = SpeechSink(bot, after.channel, vc)
                vc.listen(sink)
                if member.guild.id in audio_tasks: audio_tasks[member.guild.id].cancel()
                audio_tasks[member.guild.id] = bot.loop.create_task(process_audio_loop(sink))
                print(f"✅ Auto-joined {after.channel.name}")
            except Exception as e: print(f"Auto-join error: {e}")

# --- TTS ---
async def speak(vc, text):
    """Speak text in voice channel using edge-tts"""
    if not vc or not vc.is_connected():
        print("⚠️ TTS: Not connected to voice")
        return
    
    try:
        # Stop current audio if playing
        if vc.is_playing():
            vc.stop()
        
        # Pick voice based on text language
        voice = "en-US-BrianNeural"  # Default English
        if not all(ord(c) < 128 for c in text.replace(" ", "")):
            voice = "ar-EG-SalmaNeural"  # Arabic for non-ASCII
        
        print(f"🔊 TTS: Speaking '{text[:30]}...' with {voice}")
        
        # Generate TTS audio
        communicate = edge_tts.Communicate(text, voice)
        unique_id = int(time.time() * 1000)
        output_file = f"tts_{unique_id}.mp3"
        
        await communicate.save(output_file)
        
        if not os.path.exists(output_file):
            print("⚠️ TTS: File not created")
            return
        
        # Play audio with FFmpeg
        audio_source = discord.FFmpegPCMAudio(
            output_file,
            options="-loglevel quiet"
        )
        
        def cleanup(error):
            if error:
                print(f"⚠️ TTS playback error: {error}")
            try:
                if os.path.exists(output_file):
                    os.remove(output_file)
            except: pass
        
        vc.play(audio_source, after=cleanup)
        
        # Wait for playback to finish
        while vc.is_playing():
            await asyncio.sleep(0.1)
        
        print("✅ TTS: Done speaking")
        
    except Exception as e:
        print(f"⚠️ TTS Error: {e}")
        # Try to clean up file
        try:
            if 'output_file' in locals() and os.path.exists(output_file):
                os.remove(output_file)
        except: pass

# --- AUDIO SINK ---
class SpeechSink(voice_recv.AudioSink):
    def __init__(self, bot, channel, voice_client):
        super().__init__()
        self.bot = bot
        self.channel = channel
        self.vc_conn = voice_client 
        self.user_data = {}
        self.recognizer = sr.Recognizer()
        self.packet_count = 0
    
    def wants_opus(self): 
        return False
    
    def write(self, user, data):
        if not LISTENING_MODE or not data.pcm: return
        uid = user.id if user else "unknown"
        if uid in IGNORED_USERS: return
        if OWNER_ID and uid != OWNER_ID: return
        
        if uid not in self.user_data: self.user_data[uid] = [bytearray(), time.time()]
        self.user_data[uid][0].extend(data.pcm)
        self.user_data[uid][1] = time.time()
    
    def cleanup(self):
        """Required by AudioSink - called when sink is stopped"""
        self.user_data.clear()

async def process_audio_loop(sink):
    """Process voice commands from users"""
    try:
        while True:
            await asyncio.sleep(0.2) 
            now = time.time()
            for user_id in list(sink.user_data.keys()):
                buf, last = sink.user_data[user_id]
                if len(buf) > 0 and (now - last) > 0.6:
                    seg = bytes(buf)
                    sink.user_data[user_id][0] = bytearray()
                    if len(seg) < 20000: continue 
                    
                    # Check audio volume
                    count = len(seg) // 2
                    shorts = struct.unpack(f"<{count}h", seg)
                    if math.sqrt(sum(s**2 for s in shorts) / count) < 100: continue

                    try:
                        # Convert audio to WAV
                        uid_s = f"{user_id}_{int(time.time()*1000)}"
                        raw, wav = f"t_{uid_s}.raw", f"t_{uid_s}.wav"
                        open(raw, "wb").write(seg)
                        await (await asyncio.create_subprocess_shell(
                            f"ffmpeg -f s16le -ar 48000 -ac 2 -i {raw} -ar 16000 -ac 1 {wav} -y -loglevel quiet"
                        )).communicate()
                        
                        with sr.AudioFile(wav) as src: 
                            audio = sink.recognizer.record(src)
                        
                        # Cleanup temp files
                        if os.path.exists(raw): os.remove(raw)
                        if os.path.exists(wav): os.remove(wav)

                        # Speech to text
                        text = await asyncio.get_event_loop().run_in_executor(
                            None, 
                            lambda: sink.recognizer.recognize_google(audio, language="en-US")
                        )
                        text_lower = text.lower().strip()
                        print(f"🗣️ Heard: {text}")
                        
                        # === WAKE WORD CHECK ===
                        # Bot only responds if "manga" is said first
                        wake_words = ["manga", "mango", "hey manga", "ok manga", "okay manga"]
                        has_wake_word = any(text_lower.startswith(w) for w in wake_words)
                        
                        if not has_wake_word:
                            # Ignore speech without wake word
                            continue
                        
                        # Remove wake word from command
                        for w in wake_words:
                            if text_lower.startswith(w):
                                text_lower = text_lower[len(w):].strip()
                                text = text[len(w):].strip()
                                break
                        
                        print(f"🎤 Command: {text_lower}")
                        
                        members = sink.vc_conn.channel.members
                        guild = sink.vc_conn.guild
                        
                        # === DIRECT VOICE COMMANDS ===
                        
                        # MUTE command: "mute [name]" or "mute everyone"
                        if text_lower.startswith("mute "):
                            target = text_lower[5:].strip()
                            if target in ["everyone", "all", "everybody"]:
                                for m in members:
                                    if not m.bot:
                                        try: await m.edit(mute=True)
                                        except: pass
                                await sink.channel.send("🔇 Muted everyone")
                                await speak(sink.vc_conn, "Muted everyone")
                            else:
                                t_mem = discord.utils.find(lambda m: target in m.display_name.lower(), members)
                                if t_mem:
                                    try:
                                        await t_mem.edit(mute=True)
                                        await sink.channel.send(f"🔇 Muted {t_mem.display_name}")
                                        await speak(sink.vc_conn, f"Muted {t_mem.display_name}")
                                    except Exception as e:
                                        await speak(sink.vc_conn, "I can't mute them")
                                else:
                                    await speak(sink.vc_conn, f"I can't find {target}")
                            continue
                        
                        # UNMUTE command: "unmute [name]" or "unmute everyone"
                        if text_lower.startswith("unmute "):
                            target = text_lower[7:].strip()
                            if target in ["everyone", "all", "everybody"]:
                                for m in members:
                                    try: await m.edit(mute=False)
                                    except: pass
                                await sink.channel.send("🔊 Unmuted everyone")
                                await speak(sink.vc_conn, "Unmuted everyone")
                            else:
                                t_mem = discord.utils.find(lambda m: target in m.display_name.lower(), members)
                                if t_mem:
                                    try:
                                        await t_mem.edit(mute=False)
                                        await sink.channel.send(f"🔊 Unmuted {t_mem.display_name}")
                                        await speak(sink.vc_conn, f"Unmuted {t_mem.display_name}")
                                    except:
                                        await speak(sink.vc_conn, "I can't unmute them")
                                else:
                                    await speak(sink.vc_conn, f"I can't find {target}")
                            continue
                        
                        # KICK command: "kick [name]"
                        if text_lower.startswith("kick "):
                            target = text_lower[5:].strip()
                            t_mem = discord.utils.find(lambda m: target in m.display_name.lower(), members)
                            if t_mem:
                                try:
                                    await t_mem.move_to(None)
                                    await sink.channel.send(f"👢 Kicked {t_mem.display_name}")
                                    await speak(sink.vc_conn, f"Kicked {t_mem.display_name}")
                                except:
                                    await speak(sink.vc_conn, "I can't kick them")
                            else:
                                await speak(sink.vc_conn, f"I can't find {target}")
                            continue
                        
                        # DEAFEN command: "deafen [name]"
                        if text_lower.startswith("deafen "):
                            target = text_lower[7:].strip()
                            t_mem = discord.utils.find(lambda m: target in m.display_name.lower(), members)
                            if t_mem:
                                try:
                                    await t_mem.edit(deafen=True)
                                    await speak(sink.vc_conn, f"Deafened {t_mem.display_name}")
                                except:
                                    await speak(sink.vc_conn, "I can't deafen them")
                            continue
                        
                        # TROLL command: "troll [name]"
                        if text_lower.startswith("troll "):
                            target = text_lower[6:].strip()
                            t_mem = discord.utils.find(lambda m: target in m.display_name.lower(), members)
                            if t_mem:
                                await speak(sink.vc_conn, f"Trolling {t_mem.display_name}")
                                other = next((c for c in guild.voice_channels if c.id != sink.vc_conn.channel.id), None)
                                if other:
                                    for _ in range(4):
                                        await t_mem.move_to(other)
                                        await asyncio.sleep(0.3)
                                        await t_mem.move_to(sink.vc_conn.channel)
                                        await asyncio.sleep(0.3)
                            continue
                        
                        # STOP LISTENING command
                        if any(x in text_lower for x in ["stop listening", "be quiet", "shut up", "stop"]):
                            await speak(sink.vc_conn, "Okay, I'll stop listening")
                            sink.vc_conn.stop_listening()
                            continue
                        
                        # LEAVE command
                        if any(x in text_lower for x in ["leave", "disconnect", "go away", "bye"]):
                            await speak(sink.vc_conn, "Goodbye!")
                            await sink.vc_conn.disconnect()
                            return
                        
                        # === AI RESPONSE FOR OTHER SPEECH ===
                        if groq_client:
                            m_names = ", ".join([m.display_name for m in members if not m.bot])
                            prompt = f"""You are Manga, a helpful voice assistant in a Discord voice chat.
Users in channel: {m_names}
User said: "{text}"

Give a short, friendly response (1-2 sentences max). Be helpful and natural."""
                            
                            reply = await ai_generate(prompt)
                            if reply:
                                print(f"🤖 Reply: {reply[:50]}...")
                                await sink.channel.send(f"🤖 {reply}")
                                await speak(sink.vc_conn, reply)
                        else:
                            # No AI, just echo
                            await speak(sink.vc_conn, f"I heard you say: {text[:30]}")

                    except sr.UnknownValueError:
                        pass  # Couldn't understand audio
                    except Exception as e:
                        print(f"⚠️ Voice processing error: {e}")
    except asyncio.CancelledError: 
        pass

# ============ DISCORD COMMANDS ============
@bot.command(name="join", aliases=["j"])
async def join(ctx):
    if not ctx.author.voice: return await ctx.send("Join VC first")
    try:
        vc = ctx.voice_client or await ctx.author.voice.channel.connect(cls=voice_recv.VoiceRecvClient)
    except Exception as e: return await ctx.send(f"Err: {e}")
    sink = SpeechSink(bot, ctx.channel, vc); vc.listen(sink)
    if ctx.guild.id in audio_tasks: audio_tasks[ctx.guild.id].cancel()
    audio_tasks[ctx.guild.id] = bot.loop.create_task(process_audio_loop(sink))
    await ctx.send("✅ Joined")

@bot.command(name="leave", aliases=["dc"])
async def leave(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_listening(): ctx.voice_client.stop_listening()
        if ctx.guild.id in audio_tasks: audio_tasks[ctx.guild.id].cancel()
        await ctx.voice_client.disconnect()
        await ctx.send("👋")

@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_listening(): ctx.voice_client.stop_listening()
        if ctx.guild.id in audio_tasks: audio_tasks[ctx.guild.id].cancel()
        await ctx.send("🔇 Stopped listening")

@bot.command()
async def say(ctx, *, text):
    if ctx.voice_client: await speak(ctx.voice_client, text); await ctx.message.add_reaction("✅")
    else: await ctx.send("Not in VC")

@bot.command()
async def troll(ctx, m: discord.Member):
    if not m.voice: return await ctx.send("Not in VC")
    curr = m.voice.channel
    other = next((c for c in ctx.guild.voice_channels if c.id != curr.id), None)
    if other:
        await ctx.send(f"😈 Trolling {m.display_name}...")
        for _ in range(5): await m.move_to(other); await asyncio.sleep(0.5); await m.move_to(curr); await asyncio.sleep(0.5)

@bot.command()
async def roast(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"🔥 {m.mention} {await ai_generate(f'Short savage roast for {m.display_name}')}")

@bot.command()
async def insult(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"😤 {m.mention} {await ai_generate(f'Funny insult for {m.display_name}')}")

@bot.command()
async def compliment(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"💖 {m.mention} {await ai_generate(f'Nice compliment for {m.display_name}')}")

@bot.command()
async def joke(ctx): await ctx.send(f"😂 {await ai_generate('Tell a short funny joke')}")

@bot.command()
async def pickup(ctx): await ctx.send(f"😏 {await ai_generate('Give a cheesy pickup line')}")

@bot.command()
async def truth(ctx): await ctx.send(f"🤔 {await ai_generate('Truth question for truth or dare')}")

@bot.command()
async def dare(ctx): await ctx.send(f"😈 {await ai_generate('Dare challenge for truth or dare')}")

@bot.command()
async def rizz(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"😏 {m.display_name}'s Rizz: {random.randint(0, 100)}%")

@bot.command()
async def iq(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"🧠 {m.display_name}'s IQ: {random.randint(50, 200)}")

@bot.command()
async def pp(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"8{'=' * random.randint(1, 12)}D")

@bot.command()
async def howgay(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"🏳️‍🌈 {m.display_name}: {random.randint(0, 100)}% gay")

@bot.command(name="8ball")
async def eightball(ctx, *, q):
    answers = ["Yes", "No", "Maybe", "Definitely", "Ask again", "Doubtful", "Absolutely", "No way"]
    await ctx.send(f"🎱 {random.choice(answers)}")

@bot.command()
async def coinflip(ctx): await ctx.send(f"🪙 {random.choice(['Heads!', 'Tails!'])}")

@bot.command()
async def roll(ctx, n: int = 100): await ctx.send(f"🎲 {random.randint(1, n)}")

@bot.command()
async def slot(ctx):
    s = ["🍒", "🍋", "⭐", "💎"]
    r = [random.choice(s) for _ in range(3)]
    await ctx.send(f"🎰 {' '.join(r)}" + (" WIN!" if len(set(r)) == 1 else ""))

@bot.command()
async def ship(ctx, u1: discord.Member, u2: discord.Member = None):
    u2 = u2 or ctx.author
    await ctx.send(f"💕 {u1.display_name} x {u2.display_name}: {random.randint(0, 100)}%")

@bot.command()
async def ai(ctx, *, t):
    async with ctx.typing(): await ctx.reply(await ai_generate(t))

@bot.command()
async def ping(ctx): await ctx.send(f"🏓 {round(bot.latency * 1000)}ms")

@bot.command()
async def uptime(ctx):
    d = datetime.now() - START_TIME
    await ctx.send(f"⏱️ {d.days}d {d.seconds//3600}h {(d.seconds//60)%60}m")

# --- VOICE EXTRA ---
@bot.command()
async def voiceopen(ctx, mode: str = "all"):
    global VOICE_REPLY_MODE; VOICE_REPLY_MODE = mode
    await ctx.send(f"🔊 Voice replies: {mode}")

@bot.command()
async def voiceclose(ctx):
    global VOICE_REPLY_MODE; VOICE_REPLY_MODE = "off"
    await ctx.send("🔇 Voice replies off")

@bot.command()
async def voicekeyword(ctx, action: str, *, word: str = None):
    global VOICE_KEYWORD_ENABLED, VOICE_KEYWORD
    if action == "on": VOICE_KEYWORD_ENABLED = True; await ctx.send(f"✅ Keyword ON ({VOICE_KEYWORD})")
    elif action == "off": VOICE_KEYWORD_ENABLED = False; await ctx.send("❌ Keyword OFF")
    elif action == "set" and word: VOICE_KEYWORD = word; await ctx.send(f"✅ Keyword: {word}")

@bot.command()
async def mode(ctx, *, style: str):
    global AI_PERSONA; AI_PERSONA = style
    await ctx.send(f"🎭 AI Persona: {style}")

@bot.command()
async def sound(ctx, name: str):
    await ctx.send(f"🔊 Playing: {name}")

@bot.command()
async def claim(ctx):
    global OWNER_ID; OWNER_ID = ctx.author.id
    await ctx.send(f"👤 Listening only to {ctx.author.name}")

@bot.command()
async def ignore(ctx, m: discord.Member):
    IGNORED_USERS.add(m.id); await ctx.send(f"🔇 Ignoring {m.display_name}")

@bot.command()
async def unignore(ctx, m: discord.Member):
    IGNORED_USERS.discard(m.id); await ctx.send(f"👂 Unignoring {m.display_name}")

@bot.command()
async def reset(ctx):
    global OWNER_ID; OWNER_ID = None; IGNORED_USERS.clear()
    await ctx.send("👥 Reset. Listening to everyone.")

@bot.command(name="onlyme")
async def onlyme_text(ctx):
    """Lock text mentions + commands to command author only."""
    global TEXT_ONLY_ME_USER_ID
    if not (ctx.author.guild_permissions.administrator or ctx.author.id == ctx.guild.owner_id):
        return await ctx.send("❌ Only server admins can enable onlyme text mode.")
    TEXT_ONLY_ME_USER_ID = ctx.author.id
    await ctx.send(f"🔒 Text bot mode is now locked to {ctx.author.mention} (mentions + commands).")

@bot.command(name="openall")
async def openall_text(ctx):
    """Unlock text mentions + commands for everyone."""
    global TEXT_ONLY_ME_USER_ID
    if TEXT_ONLY_ME_USER_ID is not None:
        can_unlock = (
            ctx.author.id == TEXT_ONLY_ME_USER_ID
            or ctx.author.guild_permissions.administrator
            or ctx.author.id == ctx.guild.owner_id
        )
        if not can_unlock:
            return await ctx.send("❌ Only the lock owner or an admin can disable onlyme text mode.")
    TEXT_ONLY_ME_USER_ID = None
    await ctx.send("🔓 Text bot mode is now open to everyone.")

# --- TROLL ---
@bot.command()
async def jumpscare(ctx, m: discord.Member = None):
    m = m or ctx.author
    await ctx.send(f"👻 BOO! {m.mention}")
    if ctx.voice_client: await speak(ctx.voice_client, "Boo! Hahahaha!")

@bot.command()
async def scramble(ctx):
    vcs = [c for c in ctx.guild.voice_channels if c.members]
    members = [m for c in vcs for m in c.members if not m.bot]
    random.shuffle(members)
    for i, m in enumerate(members):
        try: await m.move_to(vcs[i % len(vcs)])
        except: pass
    await ctx.send("🌀 Scrambled!")

@bot.command()
async def hack(ctx, m: discord.Member):
    msg = await ctx.send(f"🔓 Hacking {m.name}...")
    await asyncio.sleep(1); await msg.edit(content="📡 Downloading data...")
    await asyncio.sleep(1); await msg.edit(content="✅ Hacked! (IP: 127.0.0.1)")

@bot.command()
async def fakeban(ctx, m: discord.Member):
    e = discord.Embed(title="🚨 USER BANNED", description=f"**{m.name}** has been banned!", color=discord.Color.red())
    await ctx.send(embed=e)

@bot.command()
async def mimic(ctx, m: discord.Member):
    try: await ctx.guild.me.edit(nick=m.display_name); await ctx.send(f"🎭 Mimicking {m.display_name}")
    except: await ctx.send("❌ Permission denied")

@bot.command()
async def ghostping(ctx, m: discord.Member):
    await ctx.message.delete()
    msg = await ctx.send(m.mention)
    await msg.delete()

@bot.command()
@commands.has_permissions(manage_messages=True)
async def spamping(ctx, m: discord.Member, n: int = 3):
    n = min(n, 10)
    for _ in range(n): await ctx.send(m.mention); await asyncio.sleep(0.5)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def spam(ctx, n: int, *, text):
    n = min(n, 10)
    for _ in range(n): await ctx.send(text); await asyncio.sleep(0.5)

@bot.command()
@commands.has_permissions(administrator=True)
async def nuke(ctx):
    pos = ctx.channel.position
    new = await ctx.channel.clone()
    await ctx.channel.delete()
    await new.edit(position=pos)
    await new.send("💥 Nuked!")

@bot.command()
async def mock(ctx, *, text):
    await ctx.send("".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text)))

@bot.command()
async def slap(ctx, m: discord.Member):
    await ctx.send(f"👋 {ctx.author.name} slaps {m.name}!")

# --- MORE FUN ---
@bot.command()
async def meme(ctx): await ctx.send(f"🖼️ {await ai_generate('Meme idea')}")

@bot.command()
async def trivia(ctx): await ctx.send(f"❓ {await ai_generate('Trivia question')}")

@bot.command()
async def love(ctx, u1: discord.Member, u2: discord.Member = None):
    u2 = u2 or ctx.author
    await ctx.send(f"❤️ Love: {random.randint(0, 100)}%")

@bot.command()
async def rate(ctx, *, thing): await ctx.send(f"⭐ I rate '{thing}' a {random.randint(1, 10)}/10")

@bot.command()
async def choice(ctx, a: str, b: str): await ctx.send(f"🎯 I choose: **{random.choice([a, b])}**")

@bot.command()
async def rps(ctx, c: str):
    bot_choice = random.choice(['Rock', 'Paper', 'Scissors'])
    await ctx.send(f"🤖 {bot_choice}")

# --- UTILITY ---
@bot.command()
async def translate(ctx, lang: str, *, text):
    await ctx.send(await ai_generate(f"Translate to {lang}: {text}"))

@bot.command()
async def define(ctx, *, word):
    await ctx.send(await ai_generate(f"Define: {word}"))

@bot.command()
async def urban(ctx, *, word):
    await ctx.send(await ai_generate(f"Urban dict definition: {word}"))

@bot.command(name="math")
async def math_cmd(ctx, *, expr):
    try: await ctx.send(f"🧮 {eval(expr, {'__builtins__': None}, {'math': math})}")
    except: await ctx.send("❌ Error")

@bot.command()
async def poll(ctx, *, question):
    msg = await ctx.send(f"📊 **Poll:** {question}")
    await msg.add_reaction("👍"); await msg.add_reaction("👎")

@bot.command()
async def remindme(ctx, t: int, *, text):
    await ctx.send(f"⏰ Reminder set for {t}s")
    await asyncio.sleep(t)
    await ctx.send(f"⏰ {ctx.author.mention}: {text}")

@bot.command()
async def whois(ctx, m: discord.Member = None):
    m = m or ctx.author
    e = discord.Embed(title=m.display_name, color=m.color)
    e.add_field(name="Joined", value=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "?")
    e.set_thumbnail(url=m.display_avatar.url)
    await ctx.send(embed=e)

@bot.command()
async def avatar(ctx, m: discord.Member = None):
    m = m or ctx.author
    e = discord.Embed(title=f"{m.name}'s Avatar")
    e.set_image(url=m.display_avatar.url)
    await ctx.send(embed=e)

@bot.command()
async def shorten(ctx, url: str):
    await ctx.send(f"🔗 {url}")

@bot.command()
async def emojify(ctx, *, text):
    await ctx.send(" ".join(f":regional_indicator_{c}:" if c.isalpha() else c for c in text.lower()))

@bot.command()
async def flip(ctx, *, text):
    flipped = text.translate(str.maketrans("abcdefghijklmnopqrstuvwxyz", "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz"))
    await ctx.send(flipped[::-1])

@bot.command()
async def morse(ctx, *, text):
    m = {'A':'.-','B':'-...','C':'-.-.','D':'-..','E':'.','F':'..-.','G':'--.','H':'....','I':'..','J':'.---',
         'K':'-.-','L':'.-..','M':'--','N':'-.','O':'---','P':'.--.','Q':'--.-','R':'.-.','S':'...','T':'-',
         'U':'..-','V':'...-','W':'.--','X':'-..-','Y':'-.--','Z':'--..','1':'.----','2':'..---','3':'...--',
         '4':'....-','5':'.....','6':'-....','7':'--...','8':'---..','9':'----.','0':'-----',' ':'/'}
    await ctx.send(' '.join(m.get(c.upper(), c) for c in text))

@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    e = discord.Embed(title=g.name, color=discord.Color.gold())
    e.add_field(name="Members", value=g.member_count)
    if g.icon: e.set_thumbnail(url=g.icon.url)
    await ctx.send(embed=e)

# --- LLM AGENT COMMANDS ---
agent_conversations = {}  # Store per-user conversations

@bot.command(name="agent", aliases=["llm", "ask"])
async def agent_prompt(ctx, *, message: str):
    """Send a prompt to the LLM agent."""
    async with ctx.typing():
        response = await llm_agent.prompt(message)
        
        # Split long responses
        if len(response) > 1900:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await ctx.reply(f"```\n{chunk}\n```")
                else:
                    await ctx.send(f"```\n{chunk}\n```")
        else:
            await ctx.reply(response)

@bot.command(name="agentchat", aliases=["llmchat", "ac"])
async def agent_chat(ctx, *, message: str):
    """Chat with the LLM agent (maintains conversation)."""
    user_id = str(ctx.author.id)
    conv_id = agent_conversations.get(user_id)
    
    async with ctx.typing():
        response = await llm_agent.chat(message, conv_id)
        
        # Store conversation ID for continuity
        if user_id not in agent_conversations:
            agent_conversations[user_id] = user_id
        
        if len(response) > 1900:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await ctx.reply(f"```\n{chunk}\n```")
                else:
                    await ctx.send(f"```\n{chunk}\n```")
        else:
            await ctx.reply(response)

@bot.command(name="agentclear", aliases=["llmclear", "clearchat"])
async def clear_conversation(ctx):
    """Clear your conversation history with the agent."""
    user_id = str(ctx.author.id)
    if user_id in agent_conversations:
        del agent_conversations[user_id]
        await ctx.reply("🗑️ Conversation cleared!")
    else:
        await ctx.reply("No active conversation to clear.")

@bot.command(name="models", aliases=["listmodels"])
async def list_models(ctx):
    """List available LLM models."""
    async with ctx.typing():
        models = await llm_agent.list_models()
        
        embed = discord.Embed(
            title="🤖 Available LLM Models",
            description=f"```\n{models[:4000]}\n```" if models else "No models found",
            color=discord.Color.blue()
        )
        await ctx.reply(embed=embed)

@bot.command(name="agenttask", aliases=["task", "do"])
async def agent_task(ctx, *, task: str):
    """Give the agent a task to complete."""
    async with ctx.typing():
        response = await llm_agent.agent_task(task)
        
        embed = discord.Embed(
            title="🎯 Agent Task Result",
            description=response[:4000] if len(response) > 4000 else response,
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.reply(embed=embed)

@bot.command(name="agenthelp", aliases=["llmhelp"])
async def agent_help(ctx):
    """Show LLM Agent commands."""
    embed = discord.Embed(
        title="🤖 LLM Agent Commands",
        description="Interact with AI directly through the bot!",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="Basic Commands",
        value="""
`!agent <prompt>` - Ask the AI anything
`!agentchat <msg>` - Chat (remembers context)
`!agentclear` - Clear your chat history
`!agenttask <task>` - Give the AI a task
`!models` - List available models
""",
        inline=False
    )
    embed.add_field(
        name="Aliases",
        value="""
`!llm`, `!ask` → `!agent`
`!llmchat`, `!ac` → `!agentchat`
`!task`, `!do` → `!agenttask`
""",
        inline=False
    )
    embed.add_field(
        name="Examples",
        value="""
`!agent Explain quantum computing`
`!agentchat Tell me a joke`
`!agenttask Write a Python hello world script`
""",
        inline=False
    )
    await ctx.reply(embed=embed)

# --- ADMIN ---
@bot.command()
@commands.has_permissions(administrator=True)
async def dm(ctx, m: discord.Member, *, text):
    try: await m.send(text); await ctx.send("✅ Sent")
    except: await ctx.send("❌ Fail")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, m: discord.Member):
    if m.voice: await m.move_to(None); await ctx.send(f"👢 Kicked {m.name} from VC")

@bot.command()
@commands.has_permissions(move_members=True)
async def move(ctx, m: discord.Member, *, channel: discord.VoiceChannel):
    await m.move_to(channel); await ctx.send(f"🚚 Moved to {channel.name}")

@bot.command()
@commands.has_permissions(mute_members=True)
async def mute(ctx, m: discord.Member):
    if m.voice: await m.edit(mute=True); await ctx.send(f"🔇 Muted {m.display_name}")

@bot.command()
@commands.has_permissions(mute_members=True)
async def unmute(ctx, m: discord.Member):
    if m.voice: await m.edit(mute=False); await ctx.send(f"🔊 Unmuted {m.display_name}")

@bot.command()
@commands.has_permissions(mute_members=True)
async def muteall(ctx):
    for m in ctx.author.voice.channel.members:
        if not m.bot: await m.edit(mute=True)
    await ctx.send("🔇 Muted all")

@bot.command()
@commands.has_permissions(mute_members=True)
async def unmuteall(ctx):
    for m in ctx.author.voice.channel.members: await m.edit(mute=False)
    await ctx.send("🔊 Unmuted all")

@bot.command()
@commands.has_permissions(deafen_members=True)
async def deafen(ctx, m: discord.Member):
    if m.voice: await m.edit(deafen=True); await ctx.send("🙉 Deafened")

@bot.command()
@commands.has_permissions(deafen_members=True)
async def undeafen(ctx, m: discord.Member):
    if m.voice: await m.edit(deafen=False); await ctx.send("👂 Undeafened")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, m: discord.Member, mins: int = 5):
    await m.timeout(timedelta(minutes=mins)); await ctx.send(f"⏳ Timeout {mins}m")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx, m: discord.Member):
    await m.timeout(None); await ctx.send("✅ Timeout removed")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, m: discord.Member):
    await m.ban(); await ctx.send(f"🔨 Banned {m.name}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, *, name):
    async for entry in ctx.guild.bans():
        if entry.user.name == name:
            await ctx.guild.unban(entry.user); await ctx.send(f"🔓 Unbanned {name}"); return

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, n: int = 5):
    await ctx.channel.purge(limit=n + 1)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, m: discord.Member, r: discord.Role):
    await m.add_roles(r); await ctx.send(f"✅ Added {r.name}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, m: discord.Member, r: discord.Role):
    await m.remove_roles(r); await ctx.send(f"🗑️ Removed {r.name}")

@bot.command()
@commands.has_permissions(administrator=True)
async def setlimit(ctx, key: str, val: int):
    await ctx.send(f"⚙️ Set {key} to {val}")

@bot.command()
@commands.has_permissions(administrator=True)
async def voicediag(ctx):
    await ctx.send(f"📊 Tasks: {len(audio_tasks)}")

# --- HELP ---
@bot.command(name="help")
async def help_cmd(ctx, cat: str = None):
    if cat:
        c = cat.lower()
        if c in ["voice", "v"]:
            e = discord.Embed(title="🎙️ Voice", color=discord.Color.green())
            e.add_field(name="!join", value="Join & listen", inline=True)
            e.add_field(name="!stop", value="Stop listening", inline=True)
            e.add_field(name="!leave", value="Leave VC", inline=True)
            e.add_field(name="!say", value="Speak text", inline=True)
            e.add_field(name="!voiceopen", value="Enable replies", inline=True)
            e.add_field(name="!voiceclose", value="Disable replies", inline=True)
            e.add_field(name="!claim", value="Listen to you only", inline=True)
            e.add_field(name="!ignore", value="Ignore user", inline=True)
            e.add_field(name="!reset", value="Reset", inline=True)
            return await ctx.send(embed=e)
        elif c in ["troll", "t"]:
            e = discord.Embed(title="👺 Troll", color=discord.Color.red())
            e.add_field(name="!troll", value="Move around", inline=True)
            e.add_field(name="!jumpscare", value="Jumpscare", inline=True)
            e.add_field(name="!scramble", value="Shuffle users", inline=True)
            e.add_field(name="!hack", value="Fake hack", inline=True)
            e.add_field(name="!fakeban", value="Fake ban", inline=True)
            e.add_field(name="!ghostping", value="Ghost ping", inline=True)
            e.add_field(name="!spam", value="Spam text", inline=True)
            e.add_field(name="!nuke", value="Nuke channel", inline=True)
            e.add_field(name="!mock", value="Mock text", inline=True)
            e.add_field(name="!slap", value="Slap user", inline=True)
            return await ctx.send(embed=e)
        elif c in ["fun", "f"]:
            e = discord.Embed(title="🎮 Fun", color=discord.Color.purple())
            e.add_field(name="!rizz", value="Rizz score", inline=True)
            e.add_field(name="!roast", value="Roast", inline=True)
            e.add_field(name="!insult", value="Insult", inline=True)
            e.add_field(name="!compliment", value="Compliment", inline=True)
            e.add_field(name="!joke", value="Joke", inline=True)
            e.add_field(name="!truth", value="Truth", inline=True)
            e.add_field(name="!dare", value="Dare", inline=True)
            e.add_field(name="!ship", value="Ship", inline=True)
            e.add_field(name="!iq", value="IQ", inline=True)
            e.add_field(name="!pp", value="PP", inline=True)
            e.add_field(name="!8ball", value="8ball", inline=True)
            e.add_field(name="!coinflip", value="Flip", inline=True)
            return await ctx.send(embed=e)
        elif c in ["util", "u"]:
            e = discord.Embed(title="🛠️ Utility", color=discord.Color.blue())
            e.add_field(name="!ai", value="Ask AI", inline=True)
            e.add_field(name="!translate", value="Translate", inline=True)
            e.add_field(name="!define", value="Define", inline=True)
            e.add_field(name="!math", value="Calculate", inline=True)
            e.add_field(name="!poll", value="Poll", inline=True)
            e.add_field(name="!whois", value="User info", inline=True)
            e.add_field(name="!avatar", value="Avatar", inline=True)
            e.add_field(name="!serverinfo", value="Server", inline=True)
            e.add_field(name="!ping", value="Ping", inline=True)
            e.add_field(name="!uptime", value="Uptime", inline=True)
            return await ctx.send(embed=e)
        elif c in ["admin", "a"]:
            e = discord.Embed(title="⚙️ Admin", color=discord.Color.orange())
            e.add_field(name="!dm", value="Send DM", inline=True)
            e.add_field(name="!kick", value="Kick VC", inline=True)
            e.add_field(name="!move", value="Move user", inline=True)
            e.add_field(name="!mute", value="Mute", inline=True)
            e.add_field(name="!timeout", value="Timeout", inline=True)
            e.add_field(name="!ban", value="Ban", inline=True)
            e.add_field(name="!clear", value="Clear msgs", inline=True)
            e.add_field(name="!addrole", value="Add role", inline=True)
            return await ctx.send(embed=e)
    
    e = discord.Embed(title="🤖 Manga Bot", description="`!help <category>`", color=discord.Color.blue())
    e.add_field(name="🎙️ Voice", value="`!help voice`", inline=True)
    e.add_field(name="👺 Troll", value="`!help troll`", inline=True)
    e.add_field(name="🎮 Fun", value="`!help fun`", inline=True)
    e.add_field(name="🛠️ Utility", value="`!help util`", inline=True)
    e.add_field(name="⚙️ Admin", value="`!help admin`", inline=True)
    await ctx.send(embed=e)

# ============ TELEGRAM HANDLER (EXPANDED) ============
async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TELEGRAM_CHAT_ID
    
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    user = update.effective_user
    username = user.username or "unknown"
    first_name = user.first_name or "User"
    chat_id = update.effective_chat.id
    
    # Log incoming message
    print(f"📩 TG @{username}: {text}")
    
    # Store chat ID for forwarding
    TELEGRAM_CHAT_ID = chat_id
    
    cmd = text.lower()
    resp = None
    
    # === HELP ===
    if cmd in ["/start", "/help"]:
        resp = """🤖 Manga Bot

🎙️ Voice
/join - Join active VC
/stop - Leave voice
/say <text> - Speak in VC

🤖 AI
/ai <text> - Ask AI
/roast <name> - Roast
/insult <name> - Insult
/compliment <name> - Compliment
/joke - Random joke
/pickup - Pickup line
/truth - Truth question
/dare - Dare challenge

🧠 LLM Agent
/agent <prompt> - Ask the AI agent
/agentchat <msg> - Chat with context
/agenttask <task> - Give AI a task
/models - List available models
/agenthelp - Agent commands

🎮 Fun
/rizz - Rizz score
/iq - IQ score
/pp - PP size
/howgay - Gay %
/8ball <q> - Magic 8-ball
/coinflip - Flip coin
/roll [max] - Roll dice
/slot - Slot machine
/ship <a> <b> - Ship score

📊 Status
/ping - Latency
/uptime - Uptime
/status - Bot status

Or just chat with me!"""

    # === VOICE ===
    elif cmd == "/join":
        g = bot.guilds[0] if bot.guilds else None
        if g:
            vc_target = next((c for c in g.voice_channels if c.members), None)
            if vc_target:
                try:
                    if g.voice_client:
                        await g.voice_client.disconnect()
                    v = await vc_target.connect(cls=voice_recv.VoiceRecvClient)
                    sink = SpeechSink(bot, vc_target, v)
                    v.listen(sink)
                    if g.id in audio_tasks:
                        audio_tasks[g.id].cancel()
                    audio_tasks[g.id] = bot.loop.create_task(process_audio_loop(sink))
                    resp = f"✅ Joined {vc_target.name}"
                except Exception as e:
                    resp = f"❌ Error: {str(e)[:50]}"
            else:
                resp = "❌ No active voice channel"
        else:
            resp = "❌ No Discord server"
    
    elif cmd in ["/stop", "/leave"]:
        for v in bot.voice_clients:
            await v.disconnect()
        resp = "👋 Left voice"
    
    elif cmd.startswith("/say "):
        txt = text[5:]
        if bot.voice_clients:
            await speak(bot.voice_clients[0], txt)
            resp = f"🔊 Said: {txt[:30]}"
        else:
            resp = "❌ Not in voice"

    # === AI COMMANDS ===
    elif cmd.startswith("/agent ") or cmd.startswith("/llm ") or cmd.startswith("/ask "):
        prompt = text.split(" ", 1)[1] if " " in text else ""
        resp = await llm_agent.prompt(prompt)
    
    elif cmd.startswith("/agentchat ") or cmd.startswith("/ac "):
        prompt = text.split(" ", 1)[1] if " " in text else ""
        resp = await llm_agent.chat(prompt, str(chat_id))
    
    elif cmd == "/models" or cmd == "/listmodels":
        resp = await llm_agent.list_models()
    
    elif cmd.startswith("/agenttask ") or cmd.startswith("/task ") or cmd.startswith("/do "):
        task = text.split(" ", 1)[1] if " " in text else ""
        resp = await llm_agent.agent_task(task)
    
    elif cmd == "/agenthelp" or cmd == "/llmhelp":
        resp = """🤖 LLM Agent Commands:

/agent <prompt> - Ask the AI anything
/agentchat <msg> - Chat (remembers context)  
/agenttask <task> - Give the AI a task
/models - List available models

Aliases: /llm, /ask, /ac, /task, /do"""
    
    elif cmd.startswith("/ai "):
        resp = await ai_generate(text[4:])
    
    elif cmd.startswith("/roast "):
        resp = f"🔥 {await ai_generate(f'Short roast for {text[7:]}')}"
    
    elif cmd.startswith("/insult "):
        resp = f"😤 {await ai_generate(f'Funny insult for {text[8:]}')}"
    
    elif cmd.startswith("/compliment "):
        resp = f"💖 {await ai_generate(f'Nice compliment for {text[12:]}')}"
    
    elif cmd == "/joke":
        resp = f"😂 {await ai_generate('Tell a short funny joke')}"
    
    elif cmd == "/pickup":
        resp = f"😏 {await ai_generate('Give a cheesy pickup line')}"
    
    elif cmd == "/truth":
        resp = f"🤔 {await ai_generate('Truth question for truth or dare')}"
    
    elif cmd == "/dare":
        resp = f"😈 {await ai_generate('Dare challenge for truth or dare')}"
    
    elif cmd == "/trivia":
        resp = f"❓ {await ai_generate('Trivia question with answer')}"
    
    elif cmd == "/meme":
        resp = f"🖼️ {await ai_generate('Funny meme idea')}"

    # === FUN COMMANDS ===
    elif cmd == "/rizz":
        resp = f"😏 {first_name}'s Rizz: {random.randint(0, 100)}%"
    
    elif cmd == "/iq":
        resp = f"🧠 {first_name}'s IQ: {random.randint(50, 200)}"
    
    elif cmd == "/pp":
        resp = f"8{'=' * random.randint(1, 12)}D"
    
    elif cmd == "/howgay":
        resp = f"🏳️‍🌈 {first_name}: {random.randint(0, 100)}% gay"
    
    elif cmd.startswith("/8ball "):
        answers = ["Yes", "No", "Maybe", "Definitely", "Ask again", "Doubtful", "Absolutely", "No way"]
        resp = f"🎱 {random.choice(answers)}"
    
    elif cmd in ["/coinflip", "/flip"]:
        resp = f"🪙 {random.choice(['Heads!', 'Tails!'])}"
    
    elif cmd.startswith("/roll"):
        parts = cmd.split()
        mx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 100
        resp = f"🎲 {random.randint(1, mx)}"
    
    elif cmd == "/slot":
        s = ["🍒", "🍋", "⭐", "💎"]
        r = [random.choice(s) for _ in range(3)]
        resp = f"🎰 {' '.join(r)}" + (" WIN!" if len(set(r)) == 1 else "")
    
    elif cmd.startswith("/ship "):
        parts = text.split()[1:]
        if len(parts) >= 2:
            resp = f"💕 {parts[0]} x {parts[1]}: {random.randint(0, 100)}%"
        else:
            resp = "Usage: /ship name1 name2"
    
    elif cmd.startswith("/rate "):
        thing = text[6:]
        resp = f"⭐ I rate '{thing}' a {random.randint(1, 10)}/10"

    # === STATUS ===
    elif cmd == "/ping":
        resp = f"🏓 {round(bot.latency * 1000)}ms"
    
    elif cmd == "/uptime":
        d = datetime.now() - START_TIME
        resp = f"⏱️ {d.days}d {d.seconds//3600}h {(d.seconds//60)%60}m"
    
    elif cmd == "/status":
        resp = f"📊 Servers: {len(bot.guilds)} | Voice: {len(bot.voice_clients)} | AI: {'✅' if groq_client else '❌'}"

    # === DEFAULT: CHAT WITH AI ===
    if resp is None:
        print(f"💬 AI chat from {first_name}: {text[:30]}...")
        resp = await ai_generate(f"You are a helpful assistant. User '{first_name}' says: {text}")
    
    # Send response
    try:
        await context.bot.send_message(chat_id=chat_id, text=resp)
        print(f"📤 TG sent: {resp[:50]}...")
    except Exception as e:
        print(f"⚠️ TG send error: {e}")

async def telegram_voice_reporter():
    while True:
        try:
            await asyncio.sleep(10)
            if not AUDIO_BUFFER or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: continue
            files = AUDIO_BUFFER[:]; AUDIO_BUFFER.clear()
            if not files: continue
            
            from telegram import Bot
            t_bot = Bot(TELEGRAM_TOKEN)
            for f in files:
                if os.path.exists(f):
                    with open(f, 'rb') as af: await t_bot.send_voice(chat_id=TELEGRAM_CHAT_ID, voice=af)
                    os.remove(f)
        except: pass

async def run_bot_background():
    global TG_APP_INSTANCE
    if TELEGRAM_TOKEN:
        try:
            req = HTTPXRequest(connection_pool_size=8)
            app = ApplicationBuilder().token(TELEGRAM_TOKEN).request(req).build()
            TG_APP_INSTANCE = app
            app.add_handler(MessageHandler(filters.TEXT, handle_telegram_message))
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            print("✅ Telegram OK")
        except Exception as e:
            print(f"⚠️ Telegram Error: {e}")
    
    if DISCORD_TOKEN:
        try:
            await bot.start(DISCORD_TOKEN)
        except Exception as e:
            print(f"⚠️ Discord Error: {e}")

async def main():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown_telegram(s.name)))
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    app = web.Application()
    app.add_routes([web.get('/', lambda r: web.Response(text="Manga Bot Active"))])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", 7860))).start()
    print(f"🌐 Web server on port {os.getenv('PORT', 7860)}")
    
    asyncio.create_task(run_bot_background())
    asyncio.create_task(telegram_voice_reporter())
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
