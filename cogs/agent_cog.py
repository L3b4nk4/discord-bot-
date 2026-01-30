"""
Agent Cog - LLM Agent Commands
Provides access to the llm CLI agent features.
"""
import discord
from discord.ext import commands

class AgentCog(commands.Cog, name="Agent"):
    """Advanced AI Agent commands using LLM CLI."""
    
    def __init__(self, bot, agent_service):
        self.bot = bot
        self.agent_service = agent_service
    
    @commands.group(name="agent", aliases=["llm", "a"])
    async def agent_group(self, ctx):
        """Ask the AI Agent."""
        if ctx.invoked_subcommand is None:
            # If used as !agent <prompt>
            content = ctx.message.content
            # Strip command trigger
            args = content.split(" ", 1)
            if len(args) > 1:
                prompt = args[1]
                async with ctx.typing():
                    response = await self.agent_service.prompt(prompt)
                    # Split if too long
                    if len(response) > 2000:
                        for chunk in [response[i:i+2000] for i in range(0, len(response), 2000)]:
                            await ctx.send(chunk)
                    else:
                        await ctx.send(response)
            else:
                await ctx.send_help(ctx.command)

    @agent_group.command(name="chat", aliases=["c"])
    async def agent_chat(self, ctx, *, message: str):
        """Chat with the agent (maintains context)."""
        async with ctx.typing():
            # Use channel ID as conversation ID for context
            conv_id = f"discord-{ctx.channel.id}"
            response = await self.agent_service.chat(message, conversation_id=conv_id)
            
            if len(response) > 2000:
                for chunk in [response[i:i+2000] for i in range(0, len(response), 2000)]:
                    await ctx.send(chunk)
            else:
                await ctx.send(response)

    @agent_group.command(name="task", aliases=["do", "t"])
    async def agent_task(self, ctx, *, task: str):
        """Execute a complex task."""
        async with ctx.typing():
            response = await self.agent_service.agent_task(task)
            
            embed = discord.Embed(
                title="🤖 Agent Task Result",
                description=response[:4096],
                color=discord.Color.green()
            )
            if len(response) > 4096:
                embed.set_footer(text="Response truncated due to length.")
            
            await ctx.send(embed=embed)

    @agent_group.command(name="models")
    async def agent_models(self, ctx):
        """List available LLM models."""
        async with ctx.typing():
            models = await self.agent_service.list_models()
            await ctx.send(f"📋 **Available Models**:\n```{models}```")

async def setup(bot):
    # This is handled in bot.py manually for dependency injection
    pass
