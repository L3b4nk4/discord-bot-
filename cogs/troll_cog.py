"""
Troll Commands Cog - Fun trolling commands.
"""
import discord
from discord.ext import commands
import asyncio
import random


class TrollCog(commands.Cog, name="Troll"):
    """Trolling and prank commands."""
    
    def __init__(self, bot, voice_handler=None):
        self.bot = bot
        self.voice = voice_handler
        
    @property
    def limits(self):
        """Helper to get limits from AdminCog."""
        admin = self.bot.get_cog("Admin")
        if admin:
            return admin.LIMITS
        return {
            "spam_max": 6,
            "spamping_max": 200,
            "troll_moves": 4,
            "scramble_max": 15,
        }
    
    @commands.command(name="jumpscare")
    async def jumpscare(self, ctx, member: discord.Member = None):
        """Play jumpscare audio."""
        if not ctx.voice_client:
            return await ctx.send("❌ I'm not in voice!")
        
        target = member or ctx.author
        await ctx.send(f"👻 **BOO {target.display_name}!**")
        
        if self.voice:
            await self.voice.tts.speak(ctx.voice_client, "BOO! JUMPSCARE!")
    
    @commands.command(name="troll")
    @commands.has_permissions(move_members=True)
    async def troll(self, ctx, member: discord.Member):
        """Move user between voice channels rapidly."""
        if not member.voice:
            return await ctx.send("❌ User is not in a voice channel!")
        
        current = member.voice.channel
        other = next((c for c in ctx.guild.voice_channels if c.id != current.id), None)
        
        if not other:
            return await ctx.send("❌ Need at least 2 voice channels!")
        
        await ctx.send(f"😈 Trolling **{member.display_name}**...")
        
        for _ in range(self.limits["troll_moves"]):
            try:
                await member.move_to(other)
                await asyncio.sleep(0.5)
                await member.move_to(current)
                await asyncio.sleep(0.5)
            except:
                break
    
    @commands.command(name="scramble")
    @commands.has_permissions(move_members=True)
    async def scramble(self, ctx):
        """Shuffle users across voice channels."""
        if not ctx.author.voice:
            return await ctx.send("❌ You're not in a voice channel!")
        
        channel = ctx.author.voice.channel
        members = [m for m in channel.members if not m.bot]
        channels = ctx.guild.voice_channels
        
        if len(members) > self.limits["scramble_max"]:
            members = random.sample(members, self.limits["scramble_max"])
            await ctx.send(f"⚠️ Scrambling {self.limits['scramble_max']} users (limit)...")
        else:
            await ctx.send(f"🌪️ Scrambling {len(members)} users...")
        
        for member in members:
            target = random.choice(channels)
            try:
                await member.move_to(target)
            except:
                pass
    
    @commands.command(name="hack")
    async def hack(self, ctx, member: discord.Member):
        """Fake hacking sequence."""
        msg = await ctx.send(f"💻 Hacking **{member.display_name}**...")
        
        steps = [
            "⏳ Fetching IP address...",
            "🔓 Bypassing firewall...",
            "📂 Downloading `sus_folder.zip`...",
            "🕵️ Stealing Discord token...",
            "✅ **HACKED!** Details sent to the dark web."
        ]
        
        for step in steps:
            await asyncio.sleep(1.5)
            await msg.edit(content=step)
    
    @commands.command(name="fakeban")
    async def fakeban(self, ctx, member: discord.Member):
        """Send a fake ban message."""
        embed = discord.Embed(
            title="🚨 USER BANNED 🚨",
            description=f"**{member.display_name}** has been banned from the server!",
            color=discord.Color.red()
        )
        embed.set_image(url="https://media.giphy.com/media/fe4dDMD2cAU5RfEaCU/giphy.gif")
        embed.set_footer(text="Reason: Being too cool (Just kidding)")
        await ctx.send(embed=embed)
    
    @commands.command(name="mimic")
    @commands.has_permissions(manage_nicknames=True)
    async def mimic(self, ctx, target: discord.Member):
        """Copy another user's nickname."""
        try:
            await ctx.guild.me.edit(nick=target.display_name)
            await ctx.send(f"🥸 I am now **{target.display_name}**!")
        except Exception as e:
            await ctx.send(f"❌ Failed: {e}")
    
    @commands.command(name="ghostping")
    async def ghostping(self, ctx, member: discord.Member):
        """Ghost ping a user."""
        msg = await ctx.send(member.mention)
        await msg.delete()
        await ctx.message.delete()
        await ctx.send(f"👻 Ghost pinged **{member.display_name}**!", delete_after=3)
    
    @commands.command(name="spamping")
    @commands.has_permissions(manage_messages=True)
    async def spamping(self, ctx, member: discord.Member, amount: int = 5):
        """Spam ping a user."""
        amount = min(amount, self.limits["spamping_max"])
        await ctx.message.delete()
        
        for _ in range(amount):
            await ctx.send(member.mention, delete_after=1.0)
            await asyncio.sleep(0.5)
    
    @commands.command(name="spam")
    @commands.has_permissions(manage_messages=True)
    async def spam(self, ctx, amount: int, *, text: str):
        """Spam a message."""
        amount = min(amount, self.limits["spam_max"])
        
        for _ in range(amount):
            await ctx.send(text)
            await asyncio.sleep(0.8)
    
    @commands.command(name="nuke")
    @commands.has_permissions(manage_channels=True)
    async def nuke(self, ctx):
        """Clone channel and delete original."""
        channel = ctx.channel
        pos = channel.position
        
        await channel.delete()
        new_channel = await channel.clone()
        await new_channel.edit(position=pos)
        await new_channel.send("💥 **Channel Nuked!**")
    
    @commands.command(name="mock")
    async def mock(self, ctx, *, text: str):
        """Mock text SpongeBob style."""
        mocked = "".join(
            c.upper() if i % 2 else c.lower() 
            for i, c in enumerate(text)
        )
        await ctx.send(f"🤪 {mocked}")
    
    @commands.command(name="slap")
    async def slap(self, ctx, member: discord.Member):
        """Slap a user."""
        gifs = [
            "https://media.giphy.com/media/Gf3AUz3eBNbTW/giphy.gif",
            "https://media.giphy.com/media/xUO4t2gkWsf8s/giphy.gif",
            "https://media.giphy.com/media/3XlEk2RxPS1m8/giphy.gif",
        ]
        
        embed = discord.Embed(
            description=f"**{ctx.author.name}** slaps **{member.name}**! 👋",
            color=discord.Color.red()
        )
        embed.set_image(url=random.choice(gifs))
        await ctx.send(content=member.mention, embed=embed)
    



async def setup(bot):
    pass
