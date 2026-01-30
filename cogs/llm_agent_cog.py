"""
LLM Agent Cog - Discord commands for interacting with the LLM agent.
"""
import discord
from discord.ext import commands
from services.llm_agent_service import LLMAgentService


class LLMAgentCog(commands.Cog, name="LLM Agent"):
    """Commands for interacting with the LLM AI agent."""
    
    def __init__(self, bot, llm_service: LLMAgentService):
        self.bot = bot
        self.llm = llm_service
        # Store conversation IDs per user for continuity
        self.user_conversations = {}
    
    @commands.command(name="agent", aliases=["llm", "ask"])
    async def agent_prompt(self, ctx, *, message: str):
        """
        Send a prompt to the LLM agent.
        Usage: !agent <your question or task>
        """
        async with ctx.typing():
            response = await self.llm.prompt(message)
            
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
    
    @commands.command(name="agentchat", aliases=["llmchat", "ac"])
    async def agent_chat(self, ctx, *, message: str):
        """
        Chat with the LLM agent (maintains conversation).
        Usage: !agentchat <your message>
        """
        user_id = str(ctx.author.id)
        conv_id = self.user_conversations.get(user_id)
        
        async with ctx.typing():
            response = await self.llm.chat(message, conv_id)
            
            # Store conversation ID for continuity
            if user_id not in self.user_conversations:
                self.user_conversations[user_id] = user_id
            
            if len(response) > 1900:
                chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await ctx.reply(f"```\n{chunk}\n```")
                    else:
                        await ctx.send(f"```\n{chunk}\n```")
            else:
                await ctx.reply(response)
    
    @commands.command(name="agentclear", aliases=["llmclear", "clearchat"])
    async def clear_conversation(self, ctx):
        """
        Clear your conversation history with the agent.
        Usage: !agentclear
        """
        user_id = str(ctx.author.id)
        if user_id in self.user_conversations:
            del self.user_conversations[user_id]
            await ctx.reply("🗑️ Conversation cleared!")
        else:
            await ctx.reply("No active conversation to clear.")
    
    @commands.command(name="models", aliases=["listmodels"])
    async def list_models(self, ctx):
        """
        List available LLM models.
        Usage: !models
        """
        async with ctx.typing():
            models = await self.llm.list_models()
            
            embed = discord.Embed(
                title="🤖 Available LLM Models",
                description=f"```\n{models[:4000]}\n```" if models else "No models found",
                color=discord.Color.blue()
            )
            await ctx.reply(embed=embed)
    
    @commands.command(name="agenttask", aliases=["task", "do"])
    async def agent_task(self, ctx, *, task: str):
        """
        Give the agent a task to complete.
        Usage: !agenttask <describe your task>
        """
        async with ctx.typing():
            response = await self.llm.agent_task(task)
            
            embed = discord.Embed(
                title="🎯 Agent Task Result",
                description=response[:4000] if len(response) > 4000 else response,
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Requested by {ctx.author.display_name}")
            await ctx.reply(embed=embed)
    
    @commands.command(name="agenthelp", aliases=["llmhelp"])
    async def agent_help(self, ctx):
        """
        Show LLM Agent commands.
        Usage: !agenthelp
        """
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


async def setup(bot):
    """Setup function for loading the cog."""
    pass  # Cog is loaded manually in app.py
