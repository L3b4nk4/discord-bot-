"""
Utility Commands Cog - Useful utility commands.
"""
import discord
from discord.ext import commands
import time
import asyncio
import re
import aiohttp

from services import AIService


class UtilityCog(commands.Cog, name="Utility"):
    """Utility and information commands."""
    
    def __init__(self, bot, ai_service: AIService):
        self.bot = bot
        self.ai = ai_service
        self.start_time = time.time()

    async def cog_check(self, ctx):
        """Restrict all commands in this cog to Owner/Admin only."""
        # Fix: Cog name is "Auth", not "AuthCog"
        auth = self.bot.get_cog("Auth") or self.bot.get_cog("AuthCog")
        if not auth:
            # If Auth cog is missing, fail safe but log it
            print("⚠️ AuthCog not found during check!")
            return False
        # Allow if Owner or Admin
        if auth.is_owner(ctx.author.id) or auth.is_admin(ctx.author.id):
            return True
            
        await ctx.send("❌ This command is restricted to Bot Admins only.", delete_after=5)
        return False
    
    # --- AI Utilities ---
    
    @commands.command(name="gpt", aliases=["ai", "ask", "chat", "q"])
    async def ai_chat(self, ctx, *, text: str):
        """Ask the AI a question."""
        if not self.ai.enabled:
            return await ctx.send("❌ AI is not available.")
        
        async with ctx.typing():
            history = []
            if hasattr(self.bot, "build_ai_reply_history"):
                try:
                    history = await self.bot.build_ai_reply_history(ctx.message)
                except Exception:
                    history = []
            if not history and hasattr(self.bot, "get_cached_ai_history"):
                try:
                    history = self.bot.get_cached_ai_history(
                        ctx.channel.id, ctx.author.id)
                except Exception:
                    history = []

            response = await self.ai.chat_response(
                ctx.author.display_name,
                text,
                history=history,
            )
            if hasattr(self.bot, "remember_ai_exchange"):
                try:
                    self.bot.remember_ai_exchange(
                        ctx.message,
                        text,
                        response,
                        base_history=history,
                    )
                except Exception:
                    pass
            await ctx.reply(response)
    
    @commands.command(name="translate")
    async def translate(self, ctx, lang: str, *, text: str):
        """Translate text to another language."""
        if self.ai.enabled:
            result = await self.ai.generate(f"Translate this to {lang}: '{text}'")
            await ctx.send(f"🌐 **{lang}:** {result}")
        else:
            await ctx.send("❌ AI is not available for translation.")
    
    @commands.command(name="define")
    async def define(self, ctx, *, word: str):
        """Define a word."""
        if self.ai.enabled:
            result = await self.ai.generate(f"Define the word '{word}' concisely.")
            await ctx.send(f"📖 **{word}:** {result}")
        else:
            await ctx.send("❌ AI is not available.")
    
    @commands.command(name="urban")
    async def urban(self, ctx, *, word: str):
        """Get slang/urban definition."""
        if self.ai.enabled:
            result = await self.ai.generate(f"Give a funny Urban Dictionary style definition for '{word}'.")
            await ctx.send(f"🏙️ **{word}:** {result}")
        else:
            await ctx.send("❌ AI is not available.")
    
    # --- Calculators ---
    
    @commands.command(name="math", aliases=["calc"])
    async def math(self, ctx, *, expression: str):
        """Calculator."""
        allowed = set("0123456789+-*/().% ")
        
        if not set(expression).issubset(allowed):
            return await ctx.send("❌ Invalid characters in expression.")
        
        try:
            result = eval(expression, {"__builtins__": None})
            await ctx.send(f"🧮 **Result:** {result}")
        except:
            await ctx.send("❌ Invalid expression.")
    
    # --- Reminders ---
    
    @commands.command(name="remindme", aliases=["remind"])
    async def remindme(self, ctx, time_str: str, *, reminder: str):
        """Set a reminder (e.g., 10m, 1h, 30s)."""
        # Parse duration
        match = re.match(r"(\d+)([smhd])", time_str.lower())
        if not match:
            return await ctx.send("❌ Invalid format. Use: `10s`, `5m`, `1h`, `1d`")
        
        value, unit = int(match.group(1)), match.group(2)
        
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        seconds = value * multipliers[unit]
        
        if seconds > 86400:
            return await ctx.send("❌ Maximum reminder time is 24 hours.")
        
        await ctx.send(f"⏰ I'll remind you in {time_str}: '{reminder}'")
        await asyncio.sleep(seconds)
        await ctx.send(f"⏰ **REMINDER** {ctx.author.mention}: {reminder}")
    
    # --- Polls ---
    
    @commands.command(name="poll")
    async def poll(self, ctx, *, question: str):
        """Create a simple poll."""
        embed = discord.Embed(
            title="📊 Poll",
            description=question,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Asked by {ctx.author.display_name}")
        
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    
    # --- User Info ---
    
    @commands.command(name="whois", aliases=["userinfo", "user"])
    async def whois(self, ctx, member: discord.Member = None):
        """Get user information."""
        member = member or ctx.author
        
        roles = [r.name for r in member.roles if r.name != "@everyone"]
        
        embed = discord.Embed(color=member.color)
        embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        embed.add_field(name="🆔 ID", value=member.id, inline=True)
        embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="📅 Created", value=member.created_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="🎭 Roles", value=", ".join(roles[:5]) if roles else "None", inline=False)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="avatar", aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        """Get user's avatar."""
        member = member or ctx.author
        
        embed = discord.Embed(
            title=f"🖼️ {member.display_name}'s Avatar",
            color=discord.Color.purple()
        )
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.command(name="serverinfo", aliases=["server"])
    async def serverinfo(self, ctx):
        """Get server information."""
        guild = ctx.guild
        
        embed = discord.Embed(title=f"ℹ️ {guild.name}", color=discord.Color.gold())
        embed.add_field(name="👑 Owner", value=guild.owner.mention, inline=True)
        embed.add_field(name="👥 Members", value=guild.member_count, inline=True)
        embed.add_field(name="💬 Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="📅 Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        await ctx.send(embed=embed)
    
    # --- Text Manipulation ---
    
    @commands.command(name="shorten")
    async def shorten(self, ctx, *, url: str):
        """Shorten a URL."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://tinyurl.com/api-create.php?url={url}") as resp:
                    short = await resp.text()
                    await ctx.send(f"🔗 **Short:** {short}")
        except:
            await ctx.send("❌ Failed to shorten URL.")
    
    @commands.command(name="emojify")
    async def emojify(self, ctx, *, text: str):
        """Convert text to emoji letters."""
        mapping = {
            'a': '🇦', 'b': '🇧', 'c': '🇨', 'd': '🇩', 'e': '🇪',
            'f': '🇫', 'g': '🇬', 'h': '🇭', 'i': '🇮', 'j': '🇯',
            'k': '🇰', 'l': '🇱', 'm': '🇲', 'n': '🇳', 'o': '🇴',
            'p': '🇵', 'q': '🇶', 'r': '🇷', 's': '🇸', 't': '🇹',
            'u': '🇺', 'v': '🇻', 'w': '🇼', 'x': '🇽', 'y': '🇾', 'z': '🇿',
            '0': '0️⃣', '1': '1️⃣', '2': '2️⃣', '3': '3️⃣', '4': '4️⃣',
            '5': '5️⃣', '6': '6️⃣', '7': '7️⃣', '8': '8️⃣', '9': '9️⃣',
            '!': '❗', '?': '❓', ' ': '  '
        }
        
        result = "".join(mapping.get(c.lower(), c) + " " for c in text)
        await ctx.send(result[:2000])
    
    @commands.command(name="flip")
    async def flip(self, ctx, *, text: str):
        """Flip text upside down."""
        normal = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        flipped = "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz∀qƆpƎℲפHIſʞ˥WNOԀQᴚS┴∩ΛMX⅄Z0ƖᄅƐㄣϛ9ㄥ86"
        
        table = str.maketrans(normal, flipped)
        await ctx.send(text.translate(table)[::-1])
    
    @commands.command(name="morse")
    async def morse(self, ctx, *, text: str):
        """Convert text to Morse code."""
        morse_dict = {
            'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
            'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
            'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
            'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
            'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
            'Z': '--..', '0': '-----', '1': '.----', '2': '..---',
            '3': '...--', '4': '....-', '5': '.....', '6': '-....',
            '7': '--...', '8': '---..', '9': '----.', ' ': '/'
        }
        
        result = " ".join(morse_dict.get(c.upper(), c) for c in text)
        await ctx.send(f"📡 **Morse:** `{result}`")
    
    # --- Bot Info ---
    
    @commands.command(name="ping")
    async def ping(self, ctx):
        """Check bot latency."""
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"🏓 Pong! **{latency}ms**")
    
    @commands.command(name="uptime")
    async def uptime(self, ctx):
        """Show bot uptime."""
        elapsed = int(time.time() - self.start_time)
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        await ctx.send(f"⏱️ Uptime: **{hours}h {minutes}m {seconds}s**")
    
    # Access control commands (`!onlyme`, `!openall`) live in AuthCog.


async def setup(bot):
    pass
