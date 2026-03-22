"""
Admin Commands Cog - Moderation and admin commands.
"""
import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta
import os
import re


class AdminCog(commands.Cog, name="Admin"):
    """Moderation and administration commands."""
    
    LIMITS = {
        "spam_max": 6,
        "spamping_max": 200,
        "troll_moves": 4,
        "scramble_max": 15,
    }

    def __init__(self, bot):
        self.bot = bot

    def _auth_cog(self):
        return self.bot.get_cog("Auth") or self.bot.get_cog("AuthCog")

    def _is_bot_admin(self, user_id: int) -> bool:
        auth = self._auth_cog()
        if not auth:
            return False
        try:
            return bool(auth.is_admin(user_id))
        except Exception:
            return False

    async def cog_check(self, ctx):
        """Require bot admin access for all admin prefix commands."""
        if self._is_bot_admin(ctx.author.id):
            return True
        await ctx.send("❌ Only bot admins can use admin commands.")
        return False

    async def cog_app_command_check(self, interaction: discord.Interaction) -> bool:
        """Require bot admin access for all admin slash commands."""
        if self._is_bot_admin(interaction.user.id):
            return True

        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ Only bot admins can use admin commands.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ Only bot admins can use admin commands.",
                ephemeral=True,
            )
        return False
    
    # --- DM ---
    
    @commands.hybrid_command(name="dm")
    @commands.has_permissions(manage_messages=True)
    async def dm(self, ctx, member: discord.Member, *, text: str):
        """Send a DM to a user."""
        try:
            embed = discord.Embed(
                title=f"📩 Message from {ctx.guild.name}",
                description=text,
                color=discord.Color.blue()
            )
            embed.set_footer(text=f"Sent by {ctx.author.name}")
            await member.send(embed=embed)
            
            confirm = discord.Embed(
                title="✅ DM Sent",
                description=f"Message sent to {member.mention}",
                color=discord.Color.green()
            )
            confirm.add_field(name="Content", value=text)
            await ctx.send(embed=confirm)
        except:
            await ctx.send(embed=discord.Embed(
                title="❌ Error", 
                description=f"Could not DM {member.mention} (DMs likely closed).",
                color=discord.Color.red()
            ))
    
    # --- Voice Moderation ---
    
    @commands.hybrid_command(name="kick")
    @commands.has_permissions(move_members=True)
    async def kick_voice(self, ctx, member: discord.Member):
        """Kick a user from voice channel."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.move_to(None)
        embed = discord.Embed(
            title="👢 User Kicked from Voice",
            description=f"{member.mention} has been kicked from the voice channel.",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="move")
    @commands.has_permissions(move_members=True)
    async def move(self, ctx, member: discord.Member, channel: discord.VoiceChannel):
        """Move a user to another voice channel."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.move_to(channel)
        embed = discord.Embed(
            title="🚚 User Moved",
            description=f"{member.mention} moved to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="mute")
    @commands.has_permissions(mute_members=True)
    async def mute(self, ctx, member: discord.Member):
        """Mute a user in voice."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.edit(mute=True)
        embed = discord.Embed(
            title="🔇 User Muted",
            description=f"{member.mention} has been server muted.",
            color=discord.Color.red()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="unmute")
    @commands.has_permissions(mute_members=True)
    async def unmute(self, ctx, member: discord.Member):
        """Unmute a user in voice."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.edit(mute=False)
        embed = discord.Embed(
            title="🔊 User Unmuted",
            description=f"{member.mention} has been unmuted.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="muteall")
    @commands.has_permissions(mute_members=True)
    async def muteall(self, ctx):
        """Mute everyone in your voice channel."""
        if not ctx.author.voice:
            return await ctx.send(embed=discord.Embed(description="❌ You're not in a voice channel.", color=discord.Color.red()))
        
        count = 0
        for member in ctx.author.voice.channel.members:
            if not member.bot:
                try:
                    await member.edit(mute=True)
                    count += 1
                except:
                    pass
        
        await ctx.send(embed=discord.Embed(
            title="🔇 Muted All",
            description=f"Server muted **{count}** users in {ctx.author.voice.channel.mention}",
            color=discord.Color.red()
        ))
    
    @commands.hybrid_command(name="unmuteall")
    @commands.has_permissions(mute_members=True)
    async def unmuteall(self, ctx):
        """Unmute everyone in your voice channel."""
        if not ctx.author.voice:
            return await ctx.send(embed=discord.Embed(description="❌ You're not in a voice channel.", color=discord.Color.red()))
        
        count = 0
        for member in ctx.author.voice.channel.members:
            try:
                await member.edit(mute=False)
                count += 1
            except:
                pass
        
        await ctx.send(embed=discord.Embed(
            title="🔊 Unmuted All",
            description=f"Unmuted **{count}** users in {ctx.author.voice.channel.mention}",
            color=discord.Color.green()
        ))
    
    @commands.hybrid_command(name="deafen")
    @commands.has_permissions(deafen_members=True)
    async def deafen(self, ctx, member: discord.Member):
        """Deafen a user in voice."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.edit(deafen=True)
        embed = discord.Embed(
            title="🙉 User Deafened",
            description=f"{member.mention} has been server deafened.",
            color=discord.Color.red()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="undeafen")
    @commands.has_permissions(deafen_members=True)
    async def undeafen(self, ctx, member: discord.Member):
        """Undeafen a user in voice."""
        if not member.voice:
            return await ctx.send(embed=discord.Embed(description="❌ User is not in a voice channel.", color=discord.Color.red()))
        
        await member.edit(deafen=False)
        embed = discord.Embed(
            title="👂 User Undeafened",
            description=f"{member.mention} has been undeafened.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    # --- Timeout ---
    
    @commands.hybrid_command(name="timeout")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx, member: discord.Member, minutes: int = 5):
        """Timeout a user."""
        await member.timeout(timedelta(minutes=minutes))
        embed = discord.Embed(
            title="⏳ User Timed Out",
            description=f"{member.mention} has been timed out.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Duration", value=f"{minutes} minutes")
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="untimeout")
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self, ctx, member: discord.Member):
        """Remove timeout from a user."""
        await member.timeout(None)
        embed = discord.Embed(
            title="✅ Timeout Removed",
            description=f"{member.mention} is no longer timed out.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    # --- Ban ---
    
    @commands.hybrid_command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason provided"):
        """Ban a user."""
        await member.ban(reason=reason)
        embed = discord.Embed(
            title="🔨 User Banned",
            description=f"{member.mention} has been banned.",
            color=discord.Color.dark_red()
        )
        embed.add_field(name="Reason", value=reason)
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="unban")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, *, user_id_or_name: str):
        """Unban a user by ID or name."""
        banned_users = [entry async for entry in ctx.guild.bans()]
        
        for ban_entry in banned_users:
            user = ban_entry.user
            if str(user.id) == user_id_or_name or user.name.lower() == user_id_or_name.lower():
                await ctx.guild.unban(user)
                embed = discord.Embed(
                    title="🔓 User Unbanned",
                    description=f"{user.mention} has been unbanned.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                return
        
        await ctx.send(embed=discord.Embed(description="❌ User not found in ban list.", color=discord.Color.red()))
    
    # --- Message Management ---
    
    @commands.hybrid_command(name="clear", aliases=["purge"])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int = 5):
        """Clear messages from channel."""
        amount = min(amount, 100)
        deleted = await ctx.channel.purge(limit=amount + 1)
        count = len(deleted) - 1
        
        embed = discord.Embed(
            title="🧹 Messages Cleared",
            description=f"Deleted **{count}** messages.",
            color=discord.Color.blue()
        )
        msg = await ctx.send(embed=embed)
        await msg.delete(delay=3)
    
    # --- Role Management ---
    
    @commands.hybrid_command(name="addrole")
    @commands.has_permissions(manage_roles=True)
    async def addrole(self, ctx, member: discord.Member, role: discord.Role):
        """Add a role to a user."""
        if ctx.author.top_role <= role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=discord.Embed(description="❌ Cannot assign a role higher than yours.", color=discord.Color.red()))
        
        await member.add_roles(role)
        embed = discord.Embed(
            title="✅ Role Added",
            description=f"Added {role.mention} to {member.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="removerole")
    @commands.has_permissions(manage_roles=True)
    async def removerole(self, ctx, member: discord.Member, role: discord.Role):
        """Remove a role from a user."""
        if ctx.author.top_role <= role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=discord.Embed(description="❌ Cannot remove a role higher than yours.", color=discord.Color.red()))
        
        await member.remove_roles(role)
        embed = discord.Embed(
            title="🗑️ Role Removed",
            description=f"Removed {role.mention} from {member.mention}.",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addcategory", aliases=["makecategory", "createcategory", "catadd"])
    @commands.has_permissions(manage_channels=True)
    async def add_category(self, ctx, *, spec: str):
        """
        Create a category.
        Examples:
        - !addcategory general
        - !addcategory Staff Area @Test @Test1
        """
        me = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
        if not me or not me.guild_permissions.manage_channels:
            return await ctx.send("❌ I need `Manage Channels` permission.")

        role_ids = [int(rid) for rid in re.findall(r"<@&(\d+)>", spec)]
        category_name = re.sub(r"<@&\d+>", "", spec).strip().strip("'\"")
        category_name = re.sub(r"\s+", " ", category_name).strip()

        if not category_name:
            return await ctx.send("❌ Usage: `!addcategory <name> [@Role @Role2 ...]`")

        if len(category_name) > 100:
            category_name = category_name[:100]

        existing = discord.utils.get(ctx.guild.categories, name=category_name)
        if existing:
            return await ctx.send(f"ℹ️ Category `{existing.name}` already exists.")

        roles = []
        missing_roles = []
        for role_id in role_ids:
            role = ctx.guild.get_role(role_id)
            if role:
                roles.append(role)
            else:
                missing_roles.append(str(role_id))

        overwrites = None
        if roles:
            overwrites = {
                ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            for role in roles:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True)

        try:
            category = await ctx.guild.create_category(
                category_name,
                overwrites=overwrites,
                reason=f"Requested by {ctx.author} ({ctx.author.id})",
            )
        except Exception as e:
            return await ctx.send(f"❌ Failed to create category: {e}")

        embed = discord.Embed(
            title="✅ Category Created",
            description=f"Created category **{category.name}**.",
            color=discord.Color.green(),
        )

        if roles:
            embed.add_field(
                name="🔒 Access Roles",
                value=", ".join(role.mention for role in roles),
                inline=False,
            )
        else:
            embed.add_field(name="🌐 Visibility", value="Visible to everyone.", inline=False)

        if missing_roles:
            embed.add_field(
                name="⚠️ Missing Role IDs",
                value=", ".join(missing_roles),
                inline=False,
            )

        await ctx.send(embed=embed)


    @commands.hybrid_command(name="setlimit")
    @commands.has_permissions(manage_guild=True)
    async def setlimit(self, ctx, key: str = None, value: int = None):
        """Change command limits."""
        if not key:
            limits_str = "\n".join(f"`{k}`: {v}" for k, v in self.LIMITS.items())
            return await ctx.send(f"**Current Limits:**\n{limits_str}")
        
        key = key.lower()
        if key not in self.LIMITS:
            return await ctx.send(f"❌ Unknown limit. Available: {', '.join(self.LIMITS.keys())}")
        
        if value is None or value < 1:
            return await ctx.send("❌ Value must be a positive integer.")
        
        self.LIMITS[key] = value
        await ctx.send(f"✅ Set `{key}` to **{value}**")

    @commands.hybrid_command(name="sync")
    async def sync(self, ctx):
        """Sync slash commands (Owner only)."""
        auth = self.bot.get_cog("Auth") or self.bot.get_cog("AuthCog")
        if not auth or not auth.is_owner(ctx.author.id):
             return await ctx.send("❌ Access Denied.")
        
        async with ctx.typing():
             global_synced = await self.bot.tree.sync()

             if ctx.guild is not None:
                 self.bot.tree.copy_global_to(guild=ctx.guild)
                 guild_synced = await self.bot.tree.sync(guild=ctx.guild)
                 await ctx.send(
                     f"✅ Synced {len(global_synced)} global slash commands.\n"
                     f"✅ Synced {len(guild_synced)} guild slash commands for **{ctx.guild.name}**.\n"
                     "Guild sync updates immediately. Global sync can take a while to appear."
                 )
             else:
                 await ctx.send(
                     f"✅ Synced {len(global_synced)} global slash commands.\n"
                     "Global sync can take a while to appear in Discord."
                 )

    @commands.hybrid_command(name="debugkeys")
    async def debug_keys(self, ctx):
        """Check if API keys are loaded (Owner only)."""
        auth = self.bot.get_cog("Auth") or self.bot.get_cog("AuthCog")
        if not auth or not auth.is_owner(ctx.author.id):
            return await ctx.send("❌ Access Denied.")
            
        gemini = os.getenv("GEMINI_API_KEY")
        groq = os.getenv("GROQ_API_KEY")
        owner = os.getenv("BOT_OWNER_ID")
        
        status = []
        status.append(f"GEMINI_API_KEY: {'✅ Found' if gemini else '❌ Missing'} (Len: {len(gemini) if gemini else 0})")
        status.append(f"GROQ_API_KEY: {'✅ Found' if groq else '❌ Missing'} (Len: {len(groq) if groq else 0})")
        status.append(f"BOT_OWNER_ID: {'✅ Found' if owner else '❌ Missing'} (Value: {owner})")
        
        await ctx.send("🔑 **API Key Status**\n" + "\n".join(status))

    @commands.hybrid_command(name="voicediag")
    @commands.has_permissions(manage_guild=True)
    async def voicediag(self, ctx):
        """Voice diagnostics for debugging."""
        if not self.bot.voice_handler:
             return await ctx.send("❌ Voice handler not initialized.")
             
        voice = self.bot.voice_handler
        embed = discord.Embed(title="🔧 Voice Diagnostics", color=discord.Color.orange())
        
        embed.add_field(name="Listening", value=str(voice.listening), inline=True)
        embed.add_field(name="Owner Only", value=str(voice.owner_only), inline=True)
        embed.add_field(name="Active Sinks", value=str(len(voice.sinks)), inline=True)
        embed.add_field(name="Active Tasks", value=str(len(voice.tasks)), inline=True)
        embed.add_field(name="Blocked Users", value=str(len(voice.blocked_users)), inline=True)
        
        if ctx.voice_client:
            embed.add_field(name="Voice Client", value="Connected", inline=True)
        else:
            embed.add_field(name="Voice Client", value="Not connected", inline=True)
        
        await ctx.send(embed=embed)


async def setup(bot):
    pass
