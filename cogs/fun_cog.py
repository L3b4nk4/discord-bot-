"""
Fun Commands Cog - Entertainment and AI-powered fun commands.
"""
import discord
from discord.ext import commands
import random

from services import AIService


class FunCog(commands.Cog, name="Fun"):
    """Fun and entertainment commands."""

    def __init__(self, bot, ai_service: AIService):
        self.bot = bot
        self.ai = ai_service

    def _random_percent(self) -> int:
        return random.randint(0, 100)

    def _random_price_from_percent(self, percent: int) -> str:
        if percent < 10:
            return "Priceless"

        base_price = random.randint(500, 50000)
        scaled_price = max(1, int(base_price * (101 - percent) / 100))
        return f"${scaled_price:,}"

    # --- Rating Commands ---

    @commands.command(name="rizz")
    async def rizz(self, ctx, member: discord.Member = None):
        """Get rizz rating."""
        member = member or ctx.author
        score = random.randint(0, 100)

        if score > 90:
            msg = "Rizz God! 🥶"
        elif score > 70:
            msg = "Pretty Rizzy! 😎"
        elif score > 40:
            msg = "Mid Rizz 😐"
        elif score > 20:
            msg = "Low Rizz... 😬"
        else:
            msg = "No Rizz 💀"

        await ctx.send(f"😏 **{member.display_name}**'s Rizz: **{score}%**\n{msg}")

    @commands.command(name="iq")
    async def iq(self, ctx, member: discord.Member = None):
        """Random IQ rating."""
        member = member or ctx.author
        iq = random.randint(1, 200)

        if iq > 140:
            msg = "Genius! 🧠"
        elif iq > 100:
            msg = "Smart! 📚"
        elif iq > 70:
            msg = "Average 😐"
        else:
            msg = "Smooth brain 🥔"

        await ctx.send(f"🧠 **{member.display_name}**'s IQ: **{iq}**\n{msg}")

    @commands.command(name="pp")
    async def pp(self, ctx, member: discord.Member = None):
        """Random PP size."""
        member = member or ctx.author
        size = random.randint(0, 30)
        visual = "=" * size
        await ctx.send(f"🍆 **{member.display_name}**'s PP:\n`8{visual}D`")

    @commands.command(name="howgay")
    async def howgay(self, ctx, member: discord.Member = None):
        """Gay percentage calculator."""
        member = member or ctx.author
        percent = self._random_percent()

        embed = discord.Embed(
            title="🏳️‍🌈 Gay Calculator",
            description=f"**{member.display_name}** is **{percent}%** gay",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @commands.command(name="hown", aliases=["howN"])
    async def hown(self, ctx, member: discord.Member = None):
        """Random HowN meter with a value that drops as the percent rises."""
        member = member or ctx.author
        percent = self._random_percent()
        price = self._random_price_from_percent(percent)

        embed = discord.Embed(
            title="👨🏿 N-word  Calculator",
            description=f"**{member.display_name}** rolled **{percent}%** N-word",
            color=discord.Color.random()
        )
        embed.add_field(name="Price", value=price, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="rate")
    async def rate(self, ctx, *, thing: str):
        """Rate something 0-10."""
        rating = random.randint(0, 10)
        emoji = "🔥" if rating > 8 else "💩" if rating < 3 else "🤔"
        await ctx.send(f"{emoji} I rate **{thing}** a **{rating}/10**")

    # --- Relationship Commands ---

    @commands.command(name="ship")
    async def ship(self, ctx, user1: discord.Member, user2: discord.Member = None):
        """Ship compatibility score."""
        user2 = user2 or ctx.author
        score = random.randint(0, 100)
        bar = "█" * (score // 10) + "░" * (10 - score // 10)

        emoji = "💔" if score < 30 else "💖" if score > 70 else "❤️"
        await ctx.send(f"{emoji} **{user1.display_name}** x **{user2.display_name}**\n**{score}%** [{bar}]")

    @commands.command(name="love")
    async def love(self, ctx, user1: discord.Member, user2: discord.Member = None):
        """Love calculator."""
        user2 = user2 or ctx.author
        percent = random.randint(0, 100)
        emoji = "💔" if percent < 30 else "💖" if percent > 70 else "❤️"

        embed = discord.Embed(
            title="💘 Love Calculator",
            description=f"**{user1.display_name}** + **{user2.display_name}** = **{percent}%** {emoji}",
            color=discord.Color.pink()
        )
        await ctx.send(embed=embed)

    # --- AI Commands ---

    @commands.command(name="pickup")
    async def pickup(self, ctx):
        """Get a pickup line."""
        if self.ai.enabled:
            line = await self.ai.generate("Give me a cheesy or funny pickup line. Just the line, nothing else.")
            await ctx.send(f"😉 {line}")
        else:
            lines = [
                "Are you a magician? Because whenever I look at you, everyone else disappears.",
                "Do you have a map? I keep getting lost in your eyes.",
                "Are you a parking ticket? Because you've got 'fine' written all over you.",
            ]
            await ctx.send(f"😉 {random.choice(lines)}")

    @commands.command(name="roast")
    async def roast(self, ctx, member: discord.Member = None):
        """Roast a user."""
        member = member or ctx.author

        if self.ai.enabled:
            roast = await self.ai.generate(
                f"Give a short, funny, savage roast for '{member.display_name}'. Be creative and edgy but not offensive."
            )
            await ctx.send(f"🔥 {member.mention} {roast}")
        else:
            await ctx.send(f"🔥 {member.mention}, you're like a cloud. When you disappear, it's a beautiful day.")

    @commands.command(name="insult")
    async def insult(self, ctx, member: discord.Member = None):
        """Funny insult."""
        member = member or ctx.author

        if self.ai.enabled:
            insult = await self.ai.generate(
                f"Give a creative, specific funny insult for '{member.display_name}'. Keep it light-hearted."
            )
            await ctx.send(f"😈 {member.mention} {insult}")
        else:
            await ctx.send(f"😈 {member.mention}, I'd agree with you but then we'd both be wrong.")

    @commands.command(name="compliment")
    async def compliment(self, ctx, member: discord.Member = None):
        """Compliment a user."""
        member = member or ctx.author

        if self.ai.enabled:
            comp = await self.ai.generate(
                f"Give a short, sweet, genuine compliment for '{member.display_name}'."
            )
            await ctx.send(f"💖 {member.mention} {comp}")
        else:
            await ctx.send(f"💖 {member.mention}, you're absolutely amazing!")

    @commands.command(name="joke")
    async def joke(self, ctx):
        """Tell a random joke."""
        if self.ai.enabled:
            joke = await self.ai.generate("Tell me a short, funny joke.")
            await ctx.send(f"😂 {joke}")
        else:
            jokes = [
                "Why don't scientists trust atoms? Because they make up everything!",
                "I told my wife she was drawing her eyebrows too high. She looked surprised.",
                "Why did the scarecrow win an award? He was outstanding in his field!",
            ]
            await ctx.send(f"😂 {random.choice(jokes)}")

    @commands.command(name="truth")
    async def truth(self, ctx):
        """Get a truth question."""
        if self.ai.enabled:
            question = await self.ai.generate("Give me a spicy Truth or Dare 'Truth' question.")
            await ctx.send(f"🤫 **TRUTH:** {question}")
        else:
            questions = [
                "What's your biggest fear?",
                "What's the most embarrassing thing you've done?",
                "Who's your secret crush?",
            ]
            await ctx.send(f"🤫 **TRUTH:** {random.choice(questions)}")

    @commands.command(name="dare")
    async def dare(self, ctx):
        """Get a dare."""
        if self.ai.enabled:
            dare = await self.ai.generate("Give me a funny/embarrassing dare for Discord.")
            await ctx.send(f"😈 **DARE:** {dare}")
        else:
            dares = [
                "Change your nickname to 'Stinky' for 10 minutes.",
                "Send a screenshot of your last DM.",
                "Speak in an accent for the next 5 minutes.",
            ]
            await ctx.send(f"😈 **DARE:** {random.choice(dares)}")

    @commands.command(name="meme")
    async def meme(self, ctx):
        """Get a meme idea."""
        if self.ai.enabled:
            meme = await self.ai.generate("Describe a funny meme concept or write a short text-meme.")
            await ctx.send(f"🖼️ {meme}")
        else:
            await ctx.send("🖼️ When you finally fix that bug but create 10 more...")

    @commands.command(name="trivia")
    async def trivia(self, ctx):
        """Get a trivia question."""
        if self.ai.enabled:
            trivia = await self.ai.generate("Generate a multiple-choice trivia question with the answer hidden at the end.")
            await ctx.send(f"❓ **TRIVIA:**\n{trivia}")
        else:
            await ctx.send("❓ What is the capital of France? ||Paris||")

    # --- Games ---

    @commands.command(name="8ball")
    async def eightball(self, ctx, *, question: str):
        """Magic 8-ball."""
        responses = [
            "It is certain.", "Without a doubt.", "Yes, definitely.",
            "Most likely.", "Outlook good.", "Signs point to yes.",
            "Reply hazy, try again.", "Ask again later.", "Cannot predict now.",
            "Don't count on it.", "My reply is no.", "Outlook not so good.",
            "Very doubtful.", "My sources say no."
        ]
        await ctx.send(f"🎱 **Question:** {question}\n**Answer:** {random.choice(responses)}")

    @commands.command(name="choice", aliases=["choose"])
    async def choice(self, ctx, *options):
        """Random choice between options."""
        if len(options) < 2:
            return await ctx.send("Give me options! `!choice pizza burger`")
        await ctx.send(f"🤔 I choose... **{random.choice(options)}**!")

    @commands.command(name="coinflip", aliases=["coin"])
    async def coinflip(self, ctx):
        """Flip a coin."""
        result = random.choice(["Heads", "Tails"])
        await ctx.send(f"🪙 **{result}!**")

    @commands.command(name="roll", aliases=["dice"])
    async def roll(self, ctx, maximum: int = 100):
        """Roll a number."""
        result = random.randint(1, maximum)
        await ctx.send(f"🎲 You rolled: **{result}**")

    @commands.command(name="rps")
    async def rps(self, ctx, choice: str):
        """Rock Paper Scissors."""
        options = ["rock", "paper", "scissors"]
        choice = choice.lower()

        if choice not in options:
            return await ctx.send("Usage: `!rps rock/paper/scissors`")

        bot_choice = random.choice(options)

        if choice == bot_choice:
            result = "It's a tie! 🤝"
        elif (choice == "rock" and bot_choice == "scissors") or \
             (choice == "paper" and bot_choice == "rock") or \
             (choice == "scissors" and bot_choice == "paper"):
            result = "You win! 🎉"
        else:
            result = "I win! 😈"

        await ctx.send(f"✊✋✌️ You: **{choice}** vs Me: **{bot_choice}**\n{result}")

    @commands.command(name="slot", aliases=["slots"])
    async def slot(self, ctx):
        """Slot machine."""
        emojis = ["🍎", "🍊", "🍇", "🍒", "💎", "7️⃣"]
        results = [random.choice(emojis) for _ in range(3)]

        await ctx.send(f"🎰 | {' | '.join(results)} | 🎰")

        if results[0] == results[1] == results[2]:
            await ctx.send("🎉 **JACKPOT!** You win!")
        elif results[0] == results[1] or results[1] == results[2]:
            await ctx.send("😲 Two matches! So close!")
        else:
            await ctx.send("😢 No match. Try again!")


async def setup(bot):
    pass
