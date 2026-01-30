"""
Auth Commands Cog - Authentication and authorization commands.
Handles user verification, access levels, and permission management.
"""
import discord
from discord.ext import commands
from discord import ui
from datetime import datetime
import json
import os


# --- Verification Button View ---
class VerifyButton(ui.View):
    """A persistent view with a verify button."""
    
    def __init__(self, role_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.role_id = role_id
    
    @ui.button(label="✅ Verify Me", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle verify button click."""
        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ Verification role not found.", ephemeral=True)
            return
        
        if role in interaction.user.roles:
            await interaction.response.send_message("ℹ️ You are already verified!", ephemeral=True)
            return
        
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(
                f"✅ You have been verified! Welcome to **{interaction.guild.name}**! 🎉",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to give you that role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)


class CommandControlView(ui.View):
    """View to manage command permissions."""
    def __init__(self, bot, auth_cog, command_name, ctx):
        super().__init__(timeout=180)
        self.bot = bot
        self.auth_cog = auth_cog
        self.cmd_name = command_name
        self.ctx = ctx
        self.setup_buttons()

    def setup_buttons(self):
        # Get current state
        overrides = self.auth_cog.auth_data.get("command_overrides", {}).get(self.cmd_name, {})
        is_disabled = overrides.get("disabled", False)
        
        # 1. Toggle Button
        toggle_btn = ui.Button(
            label="Enable" if is_disabled else "Disable",
            style=discord.ButtonStyle.green if is_disabled else discord.ButtonStyle.red,
            emoji="✅" if is_disabled else "🚫",
            row=0
        )
        toggle_btn.callback = self.toggle_callback
        self.add_item(toggle_btn)
        
        # 2. Reset Button
        reset_btn = ui.Button(label="Reset All", style=discord.ButtonStyle.secondary, row=0, emoji="🔄")
        reset_btn.callback = self.reset_callback
        self.add_item(reset_btn)

        # 3. Add Role Select
        # Get top 25 roles (excluding managed/bot roles if possible)
        roles = [r for r in self.ctx.guild.roles if not r.managed and r.name != "@everyone"][:25]
        role_select = ui.Select(
            placeholder="➕ Restrict to Role (Add)",
            options=[discord.SelectOption(label=r.name, value=str(r.id)) for r in roles],
            min_values=1, max_values=1, row=1
        )
        role_select.callback = self.add_role_callback
        self.add_item(role_select)

        # 4. Remove Role Select (if roles are restricted)
        allowed_roles = overrides.get("allowed_roles", [])
        if allowed_roles:
            current_role_opts = []
            for rid in allowed_roles:
                role = self.ctx.guild.get_role(rid)
                name = role.name if role else f"Unknown ({rid})"
                current_role_opts.append(discord.SelectOption(label=name, value=str(rid)))
            
            if current_role_opts:
                rem_select = ui.Select(
                    placeholder="➖ Remove Restriction",
                    options=current_role_opts,
                    min_values=1, max_values=1, row=2
                )
                rem_select.callback = self.remove_role_callback
                self.add_item(rem_select)

    async def update_view(self, interaction):
        self.clear_items()
        self.setup_buttons()
        embed = self.get_dashboard_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def get_dashboard_embed(self):
        overrides = self.auth_cog.auth_data.get("command_overrides", {}).get(self.cmd_name, {})
        is_disabled = overrides.get("disabled", False)
        allowed_roles = overrides.get("allowed_roles", [])
        
        status = "🔴 Disabled" if is_disabled else "🟢 Enabled"
        
        role_list = "None (Allowed for everyone)"
        if allowed_roles:
            role_mentions = []
            for rid in allowed_roles:
                r = self.ctx.guild.get_role(rid)
                role_mentions.append(r.mention if r else f"`{rid}`")
            role_list = ", ".join(role_mentions)

        embed = discord.Embed(
            title=f"⚙️ Managing Command: {self.cmd_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="🔒 Restricted Roles", value=role_list, inline=False)
        embed.set_footer(text="Use controls below to modify")
        return embed

    async def toggle_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        
        if "command_overrides" not in self.auth_cog.auth_data:
            self.auth_cog.auth_data["command_overrides"] = {}
        if self.cmd_name not in self.auth_cog.auth_data["command_overrides"]:
            self.auth_cog.auth_data["command_overrides"][self.cmd_name] = {}
            
        current = self.auth_cog.auth_data["command_overrides"][self.cmd_name].get("disabled", False)
        self.auth_cog.auth_data["command_overrides"][self.cmd_name]["disabled"] = not current
        self.auth_cog._save_auth_data()
        await self.update_view(interaction)

    async def reset_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        
        if "command_overrides" in self.auth_cog.auth_data and self.cmd_name in self.auth_cog.auth_data["command_overrides"]:
            del self.auth_cog.auth_data["command_overrides"][self.cmd_name]
            self.auth_cog._save_auth_data()
        await self.update_view(interaction)

    async def add_role_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        role_id = int(interaction.data["values"][0])
        
        if "command_overrides" not in self.auth_cog.auth_data:
            self.auth_cog.auth_data["command_overrides"] = {}
        if self.cmd_name not in self.auth_cog.auth_data["command_overrides"]:
            self.auth_cog.auth_data["command_overrides"][self.cmd_name] = {}
        if "allowed_roles" not in self.auth_cog.auth_data["command_overrides"][self.cmd_name]:
            self.auth_cog.auth_data["command_overrides"][self.cmd_name]["allowed_roles"] = []
            
        if role_id not in self.auth_cog.auth_data["command_overrides"][self.cmd_name]["allowed_roles"]:
            self.auth_cog.auth_data["command_overrides"][self.cmd_name]["allowed_roles"].append(role_id)
            self.auth_cog._save_auth_data()
            
        await self.update_view(interaction)

    async def remove_role_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        role_id = int(interaction.data["values"][0])
        
        try:
            self.auth_cog.auth_data["command_overrides"][self.cmd_name]["allowed_roles"].remove(role_id)
            self.auth_cog._save_auth_data()
        except: pass
        
        await self.update_view(interaction)


class AuthCog(commands.Cog, name="Auth"):
    """Authentication and authorization commands."""
    
    def __init__(self, bot):
        self.bot = bot
        self.data_file = "auth_data.json"
        
        # Load or initialize auth data
        self.auth_data = self._load_auth_data()
        
        # Bot owner (set dynamically or from env)
        self.owner_id = int(os.getenv("BOT_OWNER_ID", "1208492606774968331"))
        
        # Only me mode - when set, only this user ID can use commands
        self.only_me_user_id = None
        
        # Register persistent views on startup
        self._register_views()
    
    def _register_views(self):
        """Register persistent views for button interactions."""
        # Re-register verify buttons for all guilds that have them
        for guild_key, data in self.auth_data.get("reaction_roles", {}).items():
            if "verify_role_id" in data:
                view = VerifyButton(data["verify_role_id"])
                self.bot.add_view(view)
    
    def _load_auth_data(self) -> dict:
        """Load auth data from file."""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r") as f:
                    return json.load(f)
            except:
                pass
        return {
            "admins": [],          # Bot admins (can use all commands)
            "moderators": [],      # Moderators (limited admin commands)
            "verified_users": {},  # Guild -> list of verified user IDs
            "blacklisted": [],     # Blacklisted users (can't use bot)
            "whitelisted": {},     # Guild -> list of whitelisted user IDs
            "reaction_roles": {},  # Guild -> {message_id, channel_id, verify_role_id, emoji}
            "reaction_roles": {},  # Guild -> {message_id, channel_id, verify_role_id, emoji}
            "command_overrides": {}, # CommandName -> {disabled, allowed_roles, allowed_users}
            "autokick": {}         # Guild -> {enabled: bool, min_age_days: int}
        }
    
    def _save_auth_data(self):
        """Save auth data to file."""
        try:
            with open(self.data_file, "w") as f:
                json.dump(self.auth_data, f, indent=2)
        except Exception as e:
            print(f"⚠️ Failed to save auth data: {e}")
    
    def is_owner(self, user_id: int) -> bool:
        """Check if user is bot owner."""
        return user_id == self.owner_id
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is bot admin."""
        return user_id in self.auth_data["admins"] or self.is_owner(user_id)
    
    def is_moderator(self, user_id: int) -> bool:
        """Check if user is moderator."""
        return user_id in self.auth_data["moderators"] or self.is_admin(user_id)
    
    def is_blacklisted(self, user_id: int) -> bool:
        """Check if user is blacklisted."""
        return user_id in self.auth_data["blacklisted"]
    
    def is_verified(self, guild_id: int, user_id: int) -> bool:
        """Check if user is verified in a guild."""
        guild_key = str(guild_id)
        return guild_key in self.auth_data["verified_users"] and \
               user_id in self.auth_data["verified_users"][guild_key]

    def check_command_permission(self, ctx) -> bool:
        """
        Check if a command is allowed to run based on dynamic overrides.
        Returns True if allowed, False otherwise.
        """
        if not ctx.command:
            return True
            
        cmd_name = ctx.command.qualified_name
        overrides = self.auth_data.get("command_overrides", {}).get(cmd_name)
        
        if not overrides:
            # --- Default Security Policies ---
            # If no override is set, enforce "Admin Only" for these commands
            admin_commands = {
                "kick", "ban", "unban", "softban",
                "mute", "unmute", "muteall", "unmuteall",
                "deafen", "undeafen", "timeout", "untimeout",
                "move", "kick_voice", "clear", "purge",
                "addrole", "removerole", "addadmin", "removeadmin",
                "addmod", "removemod", "blacklist", "unblacklist",
                "whitelist", "unwhitelist", "setlimit", "voicediag"
            }
            
            if cmd_name in admin_commands:
                # Must be Bot Admin or Owner
                if not self.is_admin(ctx.author.id):
                    return False
            
            return True
            
        # Bot owner bypasses everything
        if self.is_owner(ctx.author.id):
            return True
            
        # Check if disabled globally
        if overrides.get("disabled", False):
            return False
            
        # Check allowed users whitelist
        allowed_users = overrides.get("allowed_users", [])
        if allowed_users and ctx.author.id not in allowed_users:
            return False
            
        # Check allowed roles whitelist
        allowed_roles = overrides.get("allowed_roles", [])
        if allowed_roles:
            has_role = any(role.id in allowed_roles for role in ctx.author.roles)
            if not has_role:
                return False
                
        return True
    
    def is_whitelisted(self, guild_id: int, user_id: int) -> bool:
        """Check if user is whitelisted in a guild."""
        guild_key = str(guild_id)
        return guild_key in self.auth_data["whitelisted"] and \
               user_id in self.auth_data["whitelisted"][guild_key]
    
    # --- Owner Commands ---
    
    @commands.command(name="setowner")
    async def set_owner(self, ctx, member: discord.Member):
        """Set the bot owner (only current owner or server owner can do this)."""
        if self.owner_id != 0 and ctx.author.id != self.owner_id:
            if ctx.author.id != ctx.guild.owner_id:
                return await ctx.send("❌ Only the bot owner can transfer ownership.")
        
        self.owner_id = member.id
        os.environ["BOT_OWNER_ID"] = str(member.id)
        await ctx.send(f"👑 **{member.display_name}** is now the bot owner.")
    
    @commands.command(name="whoami")
    async def whoami(self, ctx):
        """Check your authentication level."""
        user_id = ctx.author.id
        levels = []
        
        if self.is_owner(user_id):
            levels.append("👑 Bot Owner")
        if self.is_admin(user_id):
            levels.append("⚔️ Bot Admin")
        if self.is_moderator(user_id):
            levels.append("🛡️ Bot Moderator")
        if self.is_verified(ctx.guild.id, user_id):
            levels.append("✅ Verified")
        if self.is_whitelisted(ctx.guild.id, user_id):
            levels.append("📋 Whitelisted")
        if self.is_blacklisted(user_id):
            levels.append("🚫 Blacklisted")
        
        if not levels:
            levels.append("👤 Regular User")
        
        embed = discord.Embed(
            title="🔐 Your Auth Status",
            description="\n".join(levels),
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
    
    # --- Admin Management ---
    
    @commands.command(name="addadmin")
    async def add_admin(self, ctx, member: discord.Member):
        """Add a bot admin (owner only)."""
        if not self.is_owner(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only the bot owner can add admins.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id not in self.auth_data["admins"]:
            self.auth_data["admins"].append(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="⚔️ Admin Added",
                description=f"{member.mention} is now a bot admin.",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Permissions", value="Can manage moderators, blacklist users, and access admin commands.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already an admin.")
    
    @commands.command(name="removeadmin")
    async def remove_admin(self, ctx, member: discord.Member):
        """Remove a bot admin (owner only)."""
        if not self.is_owner(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only the bot owner can remove admins.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id in self.auth_data["admins"]:
            self.auth_data["admins"].remove(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="🗑️ Admin Removed",
                description=f"{member.mention} is no longer a bot admin.",
                color=discord.Color.orange()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not an admin.")
    
    @commands.command(name="listadmins")
    async def list_admins(self, ctx):
        """List all bot admins."""
        if not self.auth_data["admins"]:
            return await ctx.send("ℹ️ No bot admins set.")
        
        admin_list = []
        for admin_id in self.auth_data["admins"]:
            user = self.bot.get_user(admin_id)
            if user:
                admin_list.append(f"• {user.mention} (`{admin_id}`)")
            else:
                admin_list.append(f"• Unknown (`{admin_id}`)")
        
        embed = discord.Embed(
            title="⚔️ Bot Admins",
            description="\n".join(admin_list),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)
    
    # --- Moderator Management ---
    
    @commands.command(name="addmod")
    async def add_moderator(self, ctx, member: discord.Member):
        """Add a bot moderator (admin only)."""
        if not self.is_admin(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only bot admins can add moderators.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id not in self.auth_data["moderators"]:
            self.auth_data["moderators"].append(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="🛡️ Moderator Added",
                description=f"{member.mention} is now a bot moderator.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Permissions", value="Can use moderation bot commands.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already a moderator.")
    
    @commands.command(name="removemod")
    async def remove_moderator(self, ctx, member: discord.Member):
        """Remove a bot moderator (admin only)."""
        if not self.is_admin(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only bot admins can remove moderators.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id in self.auth_data["moderators"]:
            self.auth_data["moderators"].remove(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="🗑️ Moderator Removed",
                description=f"{member.mention} is no longer a bot moderator.",
                color=discord.Color.orange()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not a moderator.")
    
    @commands.command(name="listmods")
    async def list_moderators(self, ctx):
        """List all bot moderators."""
        if not self.auth_data["moderators"]:
            return await ctx.send("ℹ️ No bot moderators set.")
        
        mod_list = []
        for mod_id in self.auth_data["moderators"]:
            user = self.bot.get_user(mod_id)
            if user:
                mod_list.append(f"• {user.mention} (`{mod_id}`)")
            else:
                mod_list.append(f"• Unknown (`{mod_id}`)")
        
        embed = discord.Embed(
            title="🛡️ Bot Moderators",
            description="\n".join(mod_list),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    
    # --- Blacklist Management ---
    
    @commands.command(name="blacklist")
    async def blacklist_user(self, ctx, member: discord.Member):
        """Blacklist a user from using the bot (admin only)."""
        if not self.is_admin(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only bot admins can blacklist users.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if self.is_admin(member.id):
            embed = discord.Embed(title="❌ Error", description="Cannot blacklist a bot admin.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id not in self.auth_data["blacklisted"]:
            self.auth_data["blacklisted"].append(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="🚫 User Blacklisted",
                description=f"{member.mention} has been blacklisted from using this bot.",
                color=discord.Color.dark_red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Effect", value="This user cannot use any bot commands.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already blacklisted.")
    
    @commands.command(name="unblacklist")
    async def unblacklist_user(self, ctx, member: discord.Member):
        """Remove a user from the blacklist (admin only)."""
        if not self.is_admin(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only bot admins can manage the blacklist.", color=discord.Color.red())
            return await ctx.send(embed=embed)
        
        if member.id in self.auth_data["blacklisted"]:
            self.auth_data["blacklisted"].remove(member.id)
            self._save_auth_data()
            embed = discord.Embed(
                title="✅ User Unblacklisted",
                description=f"{member.mention} has been removed from the blacklist.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not blacklisted.")
    
    @commands.command(name="listblacklist")
    async def list_blacklist(self, ctx):
        """List all blacklisted users."""
        if not self.is_admin(ctx.author.id):
            return await ctx.send("❌ Only bot admins can view the blacklist.")
        
        if not self.auth_data["blacklisted"]:
            return await ctx.send("ℹ️ No blacklisted users.")
        
        bl_list = []
        for user_id in self.auth_data["blacklisted"]:
            user = self.bot.get_user(user_id)
            if user:
                bl_list.append(f"• {user.mention} (`{user_id}`)")
            else:
                bl_list.append(f"• Unknown (`{user_id}`)")
        
        embed = discord.Embed(
            title="🚫 Blacklisted Users",
            description="\n".join(bl_list),
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    
    # --- Verification System ---
    
    @commands.command(name="verify")
    @commands.has_permissions(manage_roles=True)
    async def verify_user(self, ctx, member: discord.Member):
        """Verify a user in this server."""
        guild_key = str(ctx.guild.id)
        
        if guild_key not in self.auth_data["verified_users"]:
            self.auth_data["verified_users"][guild_key] = []
        
        if member.id not in self.auth_data["verified_users"][guild_key]:
            self.auth_data["verified_users"][guild_key].append(member.id)
            self._save_auth_data()
            
            embed = discord.Embed(
                title="✅ User Verified",
                description=f"{member.mention} has been manually verified.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Verified by", value=ctx.author.mention)
            
            # Try to add a "Verified" role if it exists
            verified_role = discord.utils.get(ctx.guild.roles, name="Verified")
            if verified_role:
                try:
                    await member.add_roles(verified_role)
                    embed.add_field(name="Role Added", value=verified_role.mention)
                except:
                    pass
            
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already verified.")
    
    @commands.command(name="unverify")
    @commands.has_permissions(manage_roles=True)
    async def unverify_user(self, ctx, member: discord.Member):
        """Remove verification from a user."""
        guild_key = str(ctx.guild.id)
        
        if guild_key in self.auth_data["verified_users"] and \
           member.id in self.auth_data["verified_users"][guild_key]:
            self.auth_data["verified_users"][guild_key].remove(member.id)
            self._save_auth_data()
            await ctx.send(f"🗑️ **{member.display_name}** is no longer verified.")
            
            # Try to remove "Verified" role
            verified_role = discord.utils.get(ctx.guild.roles, name="Verified")
            if verified_role:
                try:
                    await member.remove_roles(verified_role)
                except:
                    pass
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not verified.")
    
    @commands.command(name="selfverify")
    async def self_verify(self, ctx):
        """Instructions for self-verification."""
        guild_key = str(ctx.guild.id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        
        if reaction_data and "channel_id" in reaction_data:
            channel = ctx.guild.get_channel(reaction_data["channel_id"])
            if channel:
                await ctx.send(f"✅ Go to {channel.mention} and click the **Verify Me** button to get verified!")
                return
        
        await ctx.send("❌ Self-verification is not set up. Ask a moderator to set it up with `!setupverify`.")
    
    # --- GUI Verification Setup ---
    
    @commands.command(name="setupverify")
    @commands.has_permissions(manage_guild=True)
    async def setup_verify(self, ctx, role: discord.Role = None, emoji: str = "✅"):
        """Setup a verification message with button/reaction."""
        if not role:
            # Show GUI with all roles
            embed = discord.Embed(
                title="🔐 Setup Verification System",
                description="Please mention the role to give to verified users.\n\n**Usage:** `!setupverify @Role`\n\n**Example:** `!setupverify @Verified`",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="📋 Available Roles",
                value="\n".join([f"• {r.mention}" for r in ctx.guild.roles[1:10] if not r.managed]) or "No roles found",
                inline=False
            )
            embed.set_footer(text="The bot role must be higher than the verification role!")
            return await ctx.send(embed=embed)
        
        # Check bot can assign the role
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ I can't assign that role. My role must be higher than the verification role.")
        
        guild_key = str(ctx.guild.id)
        
        # Create the verification embed
        verify_embed = discord.Embed(
            title="🔐 Server Verification",
            description=f"Welcome to **{ctx.guild.name}**!\n\n"
                        f"Click the button below or react with {emoji} to verify yourself and gain access to the server.",
            color=discord.Color.green()
        )
        verify_embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        verify_embed.add_field(
            name="📋 You will receive:",
            value=f"• {role.mention} role",
            inline=False
        )
        verify_embed.set_footer(text="By verifying, you agree to follow the server rules.")
        
        # Create the button view
        view = VerifyButton(role.id)
        
        # Send verification message
        verify_msg = await ctx.send(embed=verify_embed, view=view)
        
        # Add reaction as alternative
        await verify_msg.add_reaction(emoji)
        
        # Save configuration
        if "reaction_roles" not in self.auth_data:
            self.auth_data["reaction_roles"] = {}
        
        self.auth_data["reaction_roles"][guild_key] = {
            "message_id": verify_msg.id,
            "channel_id": ctx.channel.id,
            "verify_role_id": role.id,
            "emoji": emoji
        }
        self._save_auth_data()
        
        # Register the view for persistence
        self.bot.add_view(view)
        
        # Send confirmation
        confirm_embed = discord.Embed(
            title="✅ Verification Setup Complete!",
            description=f"Users can now click the **Verify Me** button or react with {emoji} to get the {role.mention} role.",
            color=discord.Color.green()
        )
        await ctx.send(embed=confirm_embed, delete_after=10)
    
    @commands.command(name="verifyinfo")
    async def verify_info(self, ctx):
        """Show verification system info."""
        guild_key = str(ctx.guild.id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        
        if not reaction_data:
            return await ctx.send("❌ Verification system is not set up. Use `!setupverify @Role` to set it up.")
        
        role = ctx.guild.get_role(reaction_data.get("verify_role_id"))
        channel = ctx.guild.get_channel(reaction_data.get("channel_id"))
        
        embed = discord.Embed(
            title="🔐 Verification System Info",
            color=discord.Color.blue()
        )
        embed.add_field(name="📋 Verification Role", value=role.mention if role else "Not found", inline=True)
        embed.add_field(name="📺 Channel", value=channel.mention if channel else "Not found", inline=True)
        embed.add_field(name="😀 Emoji", value=reaction_data.get("emoji", "✅"), inline=True)
        embed.add_field(name="🆔 Message ID", value=str(reaction_data.get("message_id", "Unknown")), inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="removeverify")
    @commands.has_permissions(manage_guild=True)
    async def remove_verify(self, ctx):
        """Remove the verification system."""
        guild_key = str(ctx.guild.id)
        
        if guild_key in self.auth_data.get("reaction_roles", {}):
            # Try to delete the verification message
            data = self.auth_data["reaction_roles"][guild_key]
            try:
                channel = ctx.guild.get_channel(data["channel_id"])
                if channel:
                    msg = await channel.fetch_message(data["message_id"])
                    await msg.delete()
            except:
                pass
            
            del self.auth_data["reaction_roles"][guild_key]
            self._save_auth_data()
            await ctx.send("✅ Verification system removed.")
        else:
            await ctx.send("ℹ️ No verification system is set up.")
    
    # --- Reaction Role Event Listener ---
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle reaction-based verification."""
        if payload.member.bot:
            return
        
        guild_key = str(payload.guild_id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        
        if not reaction_data:
            return
        
        # Check if this is the verification message
        if payload.message_id != reaction_data.get("message_id"):
            return
        
        # Check if correct emoji
        expected_emoji = reaction_data.get("emoji", "✅")
        if str(payload.emoji) != expected_emoji:
            return
        
        # Give the role
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        role = guild.get_role(reaction_data.get("verify_role_id"))
        if not role:
            return
        
        if role not in payload.member.roles:
            try:
                await payload.member.add_roles(role)
                # Send DM confirmation
                try:
                    await payload.member.send(f"✅ You have been verified in **{guild.name}**! Welcome! 🎉")
                except:
                    pass
            except Exception as e:
                print(f"Failed to add verification role: {e}")
    
    # --- Auth Admin Panel ---
    
    @commands.command(name="authpanel")
    @commands.has_permissions(administrator=True)
    async def auth_panel(self, ctx):
        """Show the authentication admin panel."""
        guild_key = str(ctx.guild.id)
        
        # Stats
        verified_count = len(self.auth_data.get("verified_users", {}).get(guild_key, []))
        whitelisted_count = len(self.auth_data.get("whitelisted", {}).get(guild_key, []))
        blacklisted_count = len(self.auth_data.get("blacklisted", []))
        admin_count = len(self.auth_data.get("admins", []))
        mod_count = len(self.auth_data.get("moderators", []))
        
        # Verification status
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        verify_status = "✅ Active" if reaction_data else "❌ Not Setup"
        
        embed = discord.Embed(
            title="🔐 Auth Admin Panel",
            description="Manage authentication and authorization settings.",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Stats section
        embed.add_field(
            name="📊 Statistics",
            value=f"👑 Admins: **{admin_count}**\n"
                  f"🛡️ Moderators: **{mod_count}**\n"
                  f"✅ Verified: **{verified_count}**\n"
                  f"📋 Whitelisted: **{whitelisted_count}**\n"
                  f"🚫 Blacklisted: **{blacklisted_count}**",
            inline=True
        )
        
        # Verification section
        role_display = f"<@&{reaction_data['verify_role_id']}>" if reaction_data else "N/A"
        embed.add_field(
            name="🔐 Verification",
            value=f"Status: {verify_status}\nRole: {role_display}",
            inline=True
        )
        
        # Commands section
        embed.add_field(
            name="⚙️ Quick Commands",
            value="```\n"
                  "!setupverify @Role - Setup verification\n"
                  "!verifyinfo - View verify info\n"
                  "!removeverify - Remove system\n"
                  "!onlyme - Lock bot to you\n"
                  "!openall - Unlock bot\n"
                  "```",
            inline=False
        )
        
        # User management
        embed.add_field(
            name="👥 User Management",
            value="```\n"
                  "!verify @user - Verify user\n"
                  "!blacklist @user - Block user\n"
                  "!whitelist @user - Whitelist user\n"
                  "!addadmin @user - Add admin\n"
                  "!addmod @user - Add moderator\n"
                  "```",
            inline=False
        )
        
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    
    # --- Whitelist Management ---
    
    @commands.command(name="whitelist")
    @commands.has_permissions(manage_guild=True)
    async def whitelist_user(self, ctx, member: discord.Member):
        """Whitelist a user (bypass certain restrictions)."""
        guild_key = str(ctx.guild.id)
        
        if guild_key not in self.auth_data["whitelisted"]:
            self.auth_data["whitelisted"][guild_key] = []
        
        if member.id not in self.auth_data["whitelisted"][guild_key]:
            self.auth_data["whitelisted"][guild_key].append(member.id)
            self._save_auth_data()
            
            embed = discord.Embed(
                title="📋 User Whitelisted",
                description=f"{member.mention} has been added to the whitelist.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already whitelisted.")
    
    @commands.command(name="unwhitelist")
    @commands.has_permissions(manage_guild=True)
    async def unwhitelist_user(self, ctx, member: discord.Member):
        """Remove a user from the whitelist."""
        guild_key = str(ctx.guild.id)
        
        if guild_key in self.auth_data["whitelisted"] and \
           member.id in self.auth_data["whitelisted"][guild_key]:
            self.auth_data["whitelisted"][guild_key].remove(member.id)
            self._save_auth_data()
            
            embed = discord.Embed(
                title="🗑️ User Removed from Whitelist",
                description=f"{member.mention} has been removed from the whitelist.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not whitelisted.")
    
    # --- Login/Session Commands ---
    
    @commands.command(name="login")
    async def login(self, ctx, password: str = None):
        """Login as bot admin with password."""
        # Delete the command message to hide the password
        try:
            await ctx.message.delete()
        except:
            pass
        
        admin_password = os.getenv("ADMIN_PASSWORD")
        if not admin_password:
            return await ctx.send("❌ Admin login is not configured.", delete_after=5)
        
        if password == admin_password:
            if ctx.author.id not in self.auth_data["admins"]:
                self.auth_data["admins"].append(ctx.author.id)
                self._save_auth_data()
            await ctx.send(f"✅ **{ctx.author.display_name}** logged in as admin.", delete_after=5)
        else:
            await ctx.send("❌ Invalid password.", delete_after=5)
    
    @commands.command(name="logout")
    async def logout(self, ctx):
        """Logout from admin session."""
        if ctx.author.id in self.auth_data["admins"]:
            self.auth_data["admins"].remove(ctx.author.id)
            self._save_auth_data()
            await ctx.send(f"👋 **{ctx.author.display_name}** logged out.")
        else:
            await ctx.send("ℹ️ You're not logged in as admin.")
    
    # --- Permission Check ---
    
    @commands.command(name="checkperm")
    async def check_permission(self, ctx, member: discord.Member = None):
        """Check a user's permissions in the server."""
        member = member or ctx.author
        
        perms = member.guild_permissions
        perm_list = []
        
        if perms.administrator:
            perm_list.append("👑 Administrator")
        if perms.manage_guild:
            perm_list.append("🏛️ Manage Server")
        if perms.manage_channels:
            perm_list.append("📺 Manage Channels")
        if perms.manage_roles:
            perm_list.append("🎭 Manage Roles")
        if perms.manage_messages:
            perm_list.append("💬 Manage Messages")
        if perms.kick_members:
            perm_list.append("👢 Kick Members")
        if perms.ban_members:
            perm_list.append("🔨 Ban Members")
        if perms.mute_members:
            perm_list.append("🔇 Mute Members")
        if perms.deafen_members:
            perm_list.append("🙉 Deafen Members")
        if perms.move_members:
            perm_list.append("🚚 Move Members")
        
        if not perm_list:
            perm_list.append("👤 Basic permissions only")
        
        embed = discord.Embed(
            title=f"🔑 Permissions for {member.display_name}",
            description="\n".join(perm_list),
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.send(embed=embed)
    
    # --- Auth Status ---
    
    @commands.command(name="authstatus")
    async def auth_status(self, ctx):
        """Show authentication system status."""
        if not self.is_admin(ctx.author.id):
            return await ctx.send("❌ Only admins can view auth status.")
        
        guild_key = str(ctx.guild.id)
        
        verified_count = len(self.auth_data["verified_users"].get(guild_key, []))
        whitelisted_count = len(self.auth_data["whitelisted"].get(guild_key, []))
        
        embed = discord.Embed(
            title="🔐 Auth System Status",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(name="👑 Bot Owner", value=f"<@{self.owner_id}>" if self.owner_id else "Not set", inline=True)
        embed.add_field(name="⚔️ Admins", value=str(len(self.auth_data["admins"])), inline=True)
        embed.add_field(name="🛡️ Moderators", value=str(len(self.auth_data["moderators"])), inline=True)
        embed.add_field(name="✅ Verified (this server)", value=str(verified_count), inline=True)
        embed.add_field(name="📋 Whitelisted (this server)", value=str(whitelisted_count), inline=True)
        embed.add_field(name="🚫 Blacklisted (global)", value=str(len(self.auth_data["blacklisted"])), inline=True)
        
        await ctx.send(embed=embed)
    
    # --- Only Me Mode ---
    

    

    
    # --- Dynamic Command Management (GUI) ---
    
    @commands.group(name="cmd")
    async def cmd_management(self, ctx):
        """Manage command permissions (Owner only)."""
        if not self.is_owner(ctx.author.id):
             return await ctx.send("❌ Only the owner can manage commands.")

        if ctx.invoked_subcommand is None:
            # If subcommands (like !cmd disable) are called, they will execute
            # But if arguments are used but NOT a valid subcommand (e.g. !cmd ping), 
            # we treat it as "Show dashboard for 'ping'"
            
            # Helper to check if the argument is actually a command
            msg_content = ctx.message.content.split()
            if len(msg_content) > 1:
                potential_cmd = msg_content[1]
                cmd = self.bot.get_command(potential_cmd)
                if cmd:
                    # Launch GUI for this command
                    view = CommandControlView(self.bot, self, cmd.name, ctx)
                    embed = view.get_dashboard_embed()
                    await ctx.send(embed=embed, view=view)
                    return

            # Show default dashboard (list of overrides)
            overrides = self.auth_data.get("command_overrides", {})
            if not overrides:
                return await ctx.send("ℹ️ No command overrides active. Use `!cmd <command>` to manage one.")
            
            desc = []
            for cmd_name, data in overrides.items():
                status = "🔴 Disabled" if data.get("disabled") else "🟢 Custom Rules"
                desc.append(f"**{cmd_name}**: {status}")
                
            embed = discord.Embed(
                title="⚙️ Override Dashboard",
                description="\n".join(desc),
                color=discord.Color.blue()
            )
            embed.set_footer(text="Type !cmd <command> to edit specific settings")
            await ctx.send(embed=embed)

    @cmd_management.command(name="list")
    async def cmd_list(self, ctx):
         """List all overrides."""
         # Reuses the dashboard logic above, explicit alias
         await self.cmd_management(ctx)
         
    # We remove the old text-based subcommands (disable, enable, restrict, unrestrict)
    # as they are replaced by the GUI, BUT I will keep them as aliases or hidden 
    # if the user still wants text commands? 
    # User asked to "make it gui ed", usually implies replacement.
    # I'll enable the GUI logic to handle everything.

    # --- Global command checks ---
    
    async def cog_check(self, ctx):
        """Global check for all commands in this cog."""
        # 1. Check blacklist
        if self.is_blacklisted(ctx.author.id):
            await ctx.send("❌ You are blacklisted from using this bot.")
            return False
        
        # 2. Check only_me mode
        if self.only_me_user_id is not None:
            if ctx.author.id != self.only_me_user_id and not self.is_owner(ctx.author.id):
                return False

        # 3. Check dynamic overrides
        if not self.check_command_permission(ctx):
            await ctx.send("⛔ You do not have permission to use this command (Overridden).")
            return False
        
        return True

    # --- Auto Kick System ---

    @commands.group(name="autokick")
    @commands.has_permissions(administrator=True)
    async def autokick(self, ctx, member: discord.Member = None):
        """Manage auto-kick settings (or auto-kick a specific user)."""
        if ctx.invoked_subcommand is None:
            # Case 1: !autokick @User -> Blacklist & Kick
            if member:
                # Add to blacklist
                if member.id not in self.auth_data["blacklisted"]:
                    self.auth_data["blacklisted"].append(member.id)
                    self._save_auth_data()
                
                # Kick
                try:
                    await member.send("🚫 You have been auto-kicked and blacklisted.")
                    await member.kick(reason="Manual Auto-Kick by Admin")
                    await ctx.send(f"✅ **{member.display_name}** has been blacklisted and kicked.")
                except Exception as e:
                    await ctx.send(f"⚠️ Blacklisted **{member.display_name}**, but failed to kick: {e}")
                return

            # Case 2: !autokick (no arg) -> Show Status
            guild_key = str(ctx.guild.id)
            config = self.auth_data.get("autokick", {}).get(guild_key, {})
            
            status = "🟢 Enabled" if config.get("enabled") else "🔴 Disabled"
            min_age = config.get("min_age_days", 0)
            
            embed = discord.Embed(title="🛡️ Auto Kick Settings", color=discord.Color.blue())
            embed.add_field(name="Status", value=status, inline=True)
            embed.add_field(name="Min Account Age", value=f"{min_age} days", inline=True)
            embed.set_footer(text="Use !autokick on/off OR !autokick @user")
            await ctx.send(embed=embed)

    @autokick.command(name="on")
    async def autokick_on(self, ctx, days: int = 7):
        """Enable auto-kick for accounts younger than X days."""
        guild_key = str(ctx.guild.id)
        if "autokick" not in self.auth_data:
            self.auth_data["autokick"] = {}
            
        self.auth_data["autokick"][guild_key] = {
            "enabled": True,
            "min_age_days": days
        }
        self._save_auth_data()
        await ctx.send(f"✅ Auto Kick **ENABLED**. New accounts younger than **{days} days** will be kicked.")

    @autokick.command(name="off")
    async def autokick_off(self, ctx):
        """Disable auto-kick."""
        guild_key = str(ctx.guild.id)
        if "autokick" in self.auth_data and guild_key in self.auth_data["autokick"]:
            self.auth_data["autokick"][guild_key]["enabled"] = False
            self._save_auth_data()
        await ctx.send("❌ Auto Kick **DISABLED**.")
    
    @commands.command(name="stopautokick", aliases=["disableautokick"])
    @commands.has_permissions(administrator=True)
    async def stop_autokick_cmd(self, ctx):
        """Shortcut to disable auto-kick."""
        await self.autokick_off(ctx)
        
    @commands.command(name="stopkick")
    @commands.has_permissions(administrator=True)
    async def stop_kick_cmd(self, ctx, member: discord.Member):
        """Allow a specific user to rejoin (remove from blacklist)."""
        # Remove from blacklist logic
        if member.id in self.auth_data["blacklisted"]:
            self.auth_data["blacklisted"].remove(member.id)
            self._save_auth_data()
            await ctx.send(f"✅ **{member.display_name}** has been removed from the blacklist. They can now rejoin.")
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is not in the auto-kick blacklist.")

    @commands.command(name="startautokick", aliases=["enableautokick"])
    @commands.has_permissions(administrator=True)
    async def start_autokick_cmd(self, ctx, days: int = 7):
        """Shortcut to enable auto-kick."""
        await self.autokick_on(ctx, days)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Check joining members for blacklist or age limit."""
        if member.bot: return

        # 1. Check Blacklist
        if self.is_blacklisted(member.id):
            try:
                await member.send("🚫 You are blacklisted from this server's bot system and have been kicked.")
                await member.kick(reason="User is blacklisted.")
                return
            except:
                pass

        # 2. Check Auto Kick (Account Age)
        guild_key = str(member.guild.id)
        config = self.auth_data.get("autokick", {}).get(guild_key, {})
        
        if config.get("enabled"):
            min_days = config.get("min_age_days", 0)
            created_at = member.created_at
            now = datetime.now(created_at.tzinfo)
            age = (now - created_at).days
            
            if age < min_days:
                try:
                    await member.send(f"🛡️ **Auto Kick**: Your account is too new ({age} days). Minimum requirement is {min_days} days.")
                    await member.kick(reason=f"Auto Kick: Account age {age} days < {min_days} days.")
                except:
                    pass

# Global bot check - add this listener to check ALL commands, not just this cog


# Global bot check - add this listener to check ALL commands, not just this cog
def setup_global_check(bot, auth_cog):
    """Setup a global command check for the entire bot."""
    @bot.check
    async def global_auth_check(ctx):
        # 1. Check only_me mode
        if auth_cog.only_me_user_id is not None:
            if ctx.author.id != auth_cog.only_me_user_id and not auth_cog.is_owner(ctx.author.id):
                return False
        
        # 2. Check blacklist
        if auth_cog.is_blacklisted(ctx.author.id):
            return False
            
        # 3. Check dynamic overrides
        if not auth_cog.check_command_permission(ctx):
            return False
        
        return True


async def setup(bot):
    """Setup function for loading cog with bot.load_extension()."""
    cog = AuthCog(bot)
    await bot.add_cog(cog)
    setup_global_check(bot, cog)

