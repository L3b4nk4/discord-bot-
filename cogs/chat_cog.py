"""
Chat Commands Cog - Help and basic chat commands.
"""
import discord
from discord.ext import commands
from services import AIService


class ChatCog(commands.Cog, name="Chat"):
    """Help and basic chat commands."""
    
    def __init__(self, bot, ai_service: AIService):
        self.bot = bot
        self.ai = ai_service
    
    @commands.command(name="help", aliases=["h", "commands"])
    async def help_cmd(self, ctx):
        """Show all available commands."""
        embeds = []
        
        # Main embed
        main = discord.Embed(
            title="🤖 Manga Bot Commands",
            description="Use the sections below to find commands.",
            color=discord.Color.gold()
        )
        embeds.append(main)
        
        # Voice Commands
        voice = discord.Embed(title="🎙️ Voice Commands", color=discord.Color.blue())
        voice.description = """
`!join` - Join voice & start listening
`!stop` - Stop listening but stay
`!leave` - Leave voice channel
`!say <text>` - Speak text in voice
`!voiceopen [all|me]` - Enable voice replies
`!voiceclose` - Disable voice replies
`!voicekeyword on|off` - Require keyword
`!voicekeyword set <word>` - Set keyword
`!mode <style>` - Change AI persona
`!sound <name>` - Play SFX
`!claim` - Listen to only you
`!ignore <user>` - Ignore a user
`!unignore <user>` - Listen to user again
`!reset` - Listen to everyone
"""
        embeds.append(voice)
        
        # Troll Commands
        troll = discord.Embed(title="👺 Troll Commands", color=discord.Color.red())
        troll.description = """
`!jumpscare [user]` - Play jumpscare
`!troll <user>` - Move user between channels
`!scramble` - Shuffle users across channels
`!hack <user>` - Fake hacking sequence
`!fakeban <user>` - Fake ban message
`!mimic <user>` - Copy nickname
`!ghostping <user>` - Ghost ping user
`!spamping <user> [n]` - Spam ping (admin)
`!spam <n> <text>` - Spam text (admin)
`!nuke` - Clone & delete channel (admin)
`!mock <text>` - Mock text
`!slap <user>` - Slap a user
"""
        embeds.append(troll)
        
        # Fun Commands
        fun = discord.Embed(title="🎮 Fun + AI Commands", color=discord.Color.purple())
        fun.description = """
`!rizz [user]` - Rizz rating
`!pickup` - Pickup line
`!insult [user]` - Funny insult
`!roast [user]` - Roast a user
`!compliment [user]` - Compliment
`!truth` - Truth question
`!dare` - Dare challenge
`!joke` - Random joke
`!meme` - Meme idea
`!trivia` - Trivia question
`!ship <u1> [u2]` - Ship score
`!love <u1> [u2]` - Love score
`!iq [user]` - Random IQ
`!pp [user]` - PP length
`!howgay [user]` - Gay calculator
`!rate <thing>` - Rate something
`!choice <a> <b>` - Random choice
`!8ball <question>` - Magic 8-ball
`!rps <choice>` - Rock Paper Scissors
`!slot` - Slot machine
`!coinflip` - Coin flip
`!roll [max]` - Roll a number
"""
        embeds.append(fun)
        
        # Utility Commands
        util = discord.Embed(title="🛠️ Utility Commands", color=discord.Color.green())
        util.description = """
`!ai <text>` - Ask the AI
`!translate <lang> <text>` - Translate
`!define <word>` - Define word
`!urban <word>` - Slang definition
`!math <expr>` - Calculator
`!poll <question>` - Create poll
`!remindme <time> <text>` - Reminder
`!whois [user]` - User info
`!avatar [user]` - Avatar
`!shorten <url>` - Shorten URL
`!emojify <text>` - Emojify text
`!flip <text>` - Flip text
`!morse <text>` - Morse code
`!serverinfo` - Server info
`!uptime` - Bot uptime
`!ping` - Latency
"""
        embeds.append(util)
        
        # Admin Commands
        admin = discord.Embed(title="⚙️ Admin Commands", color=discord.Color.orange())
        admin.description = """
`!dm <user> <text>` - Send DM
`!kick <user>` - Kick from voice
`!move <user> <channel>` - Move user
`!mute/!unmute <user>` - Voice mute
`!muteall/!unmuteall` - Mute all
`!deafen/!undeafen <user>` - Deafen
`!timeout <user> [min]` - Timeout
`!untimeout <user>` - Remove timeout
`!ban/!unban <user>` - Ban user
`!clear [n]` - Clear messages
`!addrole/!removerole` - Manage roles
`!setlimit <key> <val>` - Change limits
`!voicediag` - Voice diagnostics
"""
        embeds.append(admin)
        
        for embed in embeds:
            await ctx.send(embed=embed)


async def setup(bot):
    pass
