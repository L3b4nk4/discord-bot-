"""
Help Cog - Advanced Interactive Help System
Provides a GUI-based help command with deep navigation (Categories -> Commands).
"""
import discord
from discord.ext import commands
from discord import ui

# --- UI Components ---

class HelpView(ui.View):
    """Custom View that restricts interactions to the command author."""
    def __init__(self, ctx, timeout=180):
        super().__init__(timeout=timeout)
        self.ctx = ctx

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This menu is personalized for the command author. Run `!hh` yourself!", ephemeral=True)
            return False
        return True

class BackButton(ui.Button):
    """Button to return to the main category view."""
    def __init__(self, bot, ctx):
        super().__init__(label="🔙 Back to Categories", style=discord.ButtonStyle.secondary, row=1)
        self.bot = bot
        self.ctx = ctx

    async def callback(self, interaction: discord.Interaction):
        await show_main_menu(interaction, self.bot, self.ctx)

class CommandSelect(ui.Select):
    """Dropdown to select a specific command from a category."""
    def __init__(self, bot, ctx, commands_list):
        self.bot = bot
        self.ctx = ctx
        
        options = []
        for cmd in commands_list:
            # Create a short description for the dropdown
            desc = (cmd.short_doc or "No description")[:100]
            options.append(discord.SelectOption(
                label=cmd.name, 
                description=desc, 
                value=cmd.name
            ))
            
        super().__init__(placeholder="Select a command for details...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        cmd_name = self.values[0]
        cmd = self.bot.get_command(cmd_name)
        
        if not cmd:
            await interaction.response.send_message("❌ Command not found.", ephemeral=True)
            return

        embed = get_command_embed(cmd, self.ctx)
        await interaction.response.edit_message(embed=embed)

class CategorySelect(ui.Select):
    """Main dropdown to select a category."""
    def __init__(self, bot, ctx):
        self.bot = bot
        self.ctx = ctx
        
        # Permission checks
        # Permission checks
        auth_cog = bot.get_cog("Auth") or bot.get_cog("AuthCog")
        user_id = ctx.author.id
        is_owner = auth_cog and auth_cog.is_owner(user_id)
        is_admin = auth_cog and auth_cog.is_admin(user_id)
        
        options = [
            discord.SelectOption(label="🏠 Home", description="General information", emoji="🏠", value="home"),
            discord.SelectOption(label="⚔️ Admin", description="Moderation & Admin commands", emoji="⚔️", value="admin"),
            discord.SelectOption(label="🔐 Auth", description="Authentication & Setup", emoji="🔐", value="auth"),
            discord.SelectOption(label="🧠 Agent", description="LLM Agent commands", emoji="🧠", value="agent"),
            discord.SelectOption(label="🎭 Fun", description="Fun & Games commands", emoji="🎭", value="fun"),
            discord.SelectOption(label="🔊 Voice", description="Voice & TTS commands", emoji="🔊", value="voice"),
            discord.SelectOption(label="😈 Troll", description="Troll commands (for fun)", emoji="😈", value="troll"),
            discord.SelectOption(label="🛠️ Utility", description="Useful tools", emoji="🛠️", value="utility"),
        ]
        
        # Custom Permission Filtering
        # Owner sees everything
        # Admin sees Admin/Auth + Public
        # User sees Public only
        
        public_categories = ["home", "fun", "voice", "agent", "troll"]
        admin_categories = ["admin", "auth", "utility"]
        
        final_options = []
        for opt in options:
            if opt.value in admin_categories:
                if is_owner or is_admin:
                    final_options.append(opt)
            elif opt.value in public_categories:
                final_options.append(opt)
                
        super().__init__(placeholder="Select a category...", min_values=1, max_values=1, options=final_options)



    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        
        if val == "home":
            await show_main_menu(interaction, self.bot, self.ctx, edit=True)
            return

        cog_map = {
            "admin": "Admin", "auth": "Auth", "fun": "Fun", "agent": "Agent",
            "voice": "Voice", "troll": "Troll", "utility": "Utility"
        }
        
        cog_name = cog_map.get(val)
        cog = self.bot.get_cog(cog_name)
        
        if not cog:
            await interaction.response.edit_message(embed=discord.Embed(description="❌ Category not found.", color=discord.Color.red()))
            return

        # Get available commands for this user in this category
        available_commands = []
        for cmd in cog.get_commands():
            if not cmd.hidden:
                try:
                    if await cmd.can_run(self.ctx):
                        available_commands.append(cmd)
                except:
                    pass
        
        if not available_commands:
            await interaction.response.edit_message(embed=discord.Embed(description="🚫 No commands available for you here.", color=discord.Color.red()))
            return

        # Create the command list embed
        embed = discord.Embed(
            title=f"{self.options[self._get_index(val)].emoji} {cog_name} Commands",
            description=f"Found **{len(available_commands)}** commands.\nSelect a command below for details.",
            color=discord.Color.green()
        )
        
        cmd_list_str = "\n".join([f"`{cmd.name}`" for cmd in available_commands])
        embed.add_field(name="Available Commands", value=cmd_list_str)

        # Create new view with CommandSelect
        view = HelpView(self.ctx, timeout=180)
        
        # Handle too many commands for one select menu (max 25)
        # For simplicity, we take the first 25. Pagination could be added later.
        view.add_item(CommandSelect(self.bot, self.ctx, available_commands[:25]))
        view.add_item(BackButton(self.bot, self.ctx))
        
        await interaction.response.edit_message(embed=embed, view=view)

    def _get_index(self, val):
        for i, opt in enumerate(self.options):
            if opt.value == val: return i
        return 0

# --- Helper Functions ---

# --- Helper Functions ---

async def show_main_menu(interaction_or_ctx, bot, ctx, edit=False, ephemeral=False):
    """Show the main category menu with personalized Master List."""
    
    # Permission checks for list generation
    # Permission checks for list generation
    auth_cog = bot.get_cog("Auth") or bot.get_cog("AuthCog")
    user_id = ctx.author.id
    is_owner = auth_cog and auth_cog.is_owner(user_id)
    is_admin = auth_cog and auth_cog.is_admin(user_id)
    
    # Define categories to show
    # Order matches user request: Voice, Troll, Fun, Utility, Admin
    categories = [
        ("Voice", "🎙️", ["voice"]),
        ("Troll", "👺", ["troll"]),
        ("Fun", "🎮", ["fun", "agent"]), # Grouping Agent under Fun/AI
        ("Utility", "🛠️", ["utility"]),
        ("Admin", "⚙️", ["admin", "auth"]),
    ]
    
    embed = discord.Embed(
        title="🤖 Manga Bot Commands",
        description="Use the dropdown for details or reference the list below.",
        color=discord.Color.gold()
    )
    
    for display_name, emoji, cog_names in categories:
        # Check permission for this category block
        is_restricted = any(c.lower() in ["admin", "auth", "utility"] for c in cog_names)
        if is_restricted and not (is_owner or is_admin):
            continue
            
        cmd_list = []
        for cog_name in cog_names:
            cog = bot.get_cog(cog_name.capitalize()) or bot.get_cog(cog_name)
            if cog:
                for cmd in cog.get_commands():
                     if not cmd.hidden:
                        try:
                            # Basic check, avoiding heavy async checks if possible for list
                            # But consistent with detail view, let's just list them
                            cmd_list.append(f"`!{cmd.name}`")
                        except:
                            pass
        
        if cmd_list:
             # Sort and join
             cmd_list.sort()
             embed.add_field(name=f"{emoji} {display_name} Commands", value=", ".join(cmd_list), inline=False)

    embed.set_footer(text="Select a category below for command details and usage.")
    
    view = HelpView(ctx, timeout=180)
    view.add_item(CategorySelect(bot, ctx))
    
    if edit and isinstance(interaction_or_ctx, discord.Interaction):
        await interaction_or_ctx.response.edit_message(embed=embed, view=view)
    else:
        target = interaction_or_ctx
        if isinstance(interaction_or_ctx, discord.Interaction):
             await interaction_or_ctx.response.send_message(embed=embed, view=view, ephemeral=ephemeral)
        else:
             await target.send(embed=embed, view=view)

def get_command_embed(cmd, ctx):
    """Generate a detailed embed for a single command."""
    embed = discord.Embed(
        title=f"Command: !{cmd.name}",
        description=cmd.help or "No description provided.",
        color=discord.Color.gold()
    )
    
    # Aliases
    if cmd.aliases:
        embed.add_field(name="🔀 Aliases", value=", ".join([f"`{a}`" for a in cmd.aliases]), inline=True)
    
    # Usage
    params = []
    for key, val in cmd.clean_params.items():
        if val.default != val.empty:
            params.append(f"[{key}]")
        else:
            params.append(f"<{key}>")
            
    usage_str = f"`{ctx.prefix}{cmd.name} {' '.join(params)}`"
    embed.add_field(name="📝 Usage", value=usage_str, inline=False)
    
    # Permissions info
    perms = []
    for check in cmd.checks:
        if "has_permissions" in str(check):
             perms.append("Requires specific permissions")
        if "is_owner" in str(check):
             perms.append("👑 Owner Only")
             
    if perms:
        embed.add_field(name="🔒 Requirements", value="\n".join(perms), inline=False)
        
    return embed

class HelpCog(commands.Cog, name="Help"):
    """Interactive help command."""
    
    def __init__(self, bot):
        self.bot = bot
        self._original_help_command = bot.help_command
        bot.help_command = None 
    
    def cog_unload(self):
        self.bot.help_command = self._original_help_command
    
    @commands.hybrid_command(name="hh", aliases=[])
    async def hh_command(self, ctx, *, command_name: str = None):
        """Show interactive help menu."""
        
        # Determine if text or slash
        is_text = ctx.interaction is None
        
        if is_text:
            try:
                await ctx.message.delete()
            except:
                pass
            target_send = ctx.author.send
        else:
            target_send = ctx.send
            
        kwargs = {"ephemeral": True} if not is_text else {}

        if command_name:
            # Show specific command logic
            cmd = self.bot.get_command(command_name)
            if not cmd:
                return await target_send(f"❌ Command `{command_name}` not found.", **kwargs)
            
            # Check permissions
            try:
                if not await cmd.can_run(ctx):
                    return await target_send(f"⛔ You don't have permission to view/use `{command_name}`.", **kwargs)
            except:
                return await target_send(f"⛔ You don't have permission to view/use `{command_name}`.", **kwargs)
                
            embed = get_command_embed(cmd, ctx)
            await target_send(embed=embed, **kwargs)
        else:
            # Show main menu
            if is_text:
                await show_main_menu(ctx.author, self.bot, ctx)
            else:
                await show_main_menu(ctx.interaction, self.bot, ctx, ephemeral=True)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
