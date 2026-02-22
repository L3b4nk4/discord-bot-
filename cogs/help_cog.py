"""
Help Cog - Advanced Interactive Help System
Provides a GUI-based help command with deep navigation (Categories -> Commands).
"""
import discord
from discord.ext import commands
from discord import ui

# --- UI Components ---


def _help_access_flags(bot, ctx):
    """Return (is_bot_owner, is_bot_admin, is_server_owner) for help visibility."""
    auth_cog = bot.get_cog("Auth") or bot.get_cog("AuthCog")
    user_id = ctx.author.id
    is_bot_owner = bool(auth_cog and auth_cog.is_owner(user_id))
    is_bot_admin = bool(auth_cog and auth_cog.is_admin(user_id))
    is_server_owner = bool(ctx.guild and ctx.guild.owner_id == user_id)
    return is_bot_owner, is_bot_admin, is_server_owner


def _cog_all_commands(cog):
    """Collect all visible commands from a cog, including subcommands."""
    seen = set()
    commands_out = []
    for cmd in cog.walk_commands():
        if cmd.hidden:
            continue
        qname = cmd.qualified_name
        if qname in seen:
            continue
        seen.add(qname)
        commands_out.append(cmd)
    return commands_out

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
                label=cmd.qualified_name[:100],
                description=desc, 
                value=cmd.qualified_name
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


class CommandPageView(HelpView):
    """Paginated command browser so large categories show all commands."""
    PAGE_SIZE = 25

    def __init__(self, bot, ctx, category_name, category_emoji, commands_list, timeout=180):
        super().__init__(ctx, timeout=timeout)
        self.bot = bot
        self.category_name = category_name
        self.category_emoji = category_emoji
        self.commands_list = sorted(commands_list, key=lambda c: c.name)
        self.page = 0
        self._rebuild()

    @property
    def total_pages(self):
        if not self.commands_list:
            return 1
        return (len(self.commands_list) - 1) // self.PAGE_SIZE + 1

    def _current_page_commands(self):
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        return self.commands_list[start:end]

    def build_embed(self):
        current = self._current_page_commands()
        embed = discord.Embed(
            title=f"{self.category_emoji} {self.category_name} Commands",
            description=f"Found **{len(self.commands_list)}** commands.\nPage **{self.page + 1}/{self.total_pages}**.",
            color=discord.Color.green(),
        )
        cmd_list_str = "\n".join([f"`{cmd.qualified_name}`" for cmd in current]) if current else "No commands."
        embed.add_field(name="Available Commands", value=cmd_list_str, inline=False)
        embed.set_footer(text="Select a command below for details.")
        return embed

    def _rebuild(self):
        self.clear_items()

        current = self._current_page_commands()
        if current:
            self.add_item(CommandSelect(self.bot, self.ctx, current))

        if self.total_pages > 1:
            prev_btn = ui.Button(
                label="⬅️ Prev",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page == 0,
            )
            next_btn = ui.Button(
                label="Next ➡️",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=self.page >= self.total_pages - 1,
            )

            async def prev_callback(interaction: discord.Interaction):
                self.page = max(0, self.page - 1)
                self._rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            async def next_callback(interaction: discord.Interaction):
                self.page = min(self.total_pages - 1, self.page + 1)
                self._rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            prev_btn.callback = prev_callback
            next_btn.callback = next_callback
            self.add_item(prev_btn)
            self.add_item(next_btn)

        self.add_item(BackButton(self.bot, self.ctx))

class CategorySelect(ui.Select):
    """Main dropdown to select a category."""
    def __init__(self, bot, ctx):
        self.bot = bot
        self.ctx = ctx
        
        is_owner, is_admin, is_server_owner = _help_access_flags(bot, ctx)
        
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
                if is_owner or is_admin or is_server_owner:
                    final_options.append(opt)
            elif opt.value in public_categories:
                final_options.append(opt)
                
        super().__init__(placeholder="Select a category...", min_values=1, max_values=1, options=final_options)



    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        
        if val == "home":
            await show_main_menu(interaction, self.bot, self.ctx, edit=True)
            return

        category_cog_map = {
            # Admin panel should show all privileged commands, not only AdminCog.
            "admin": ["Admin", "Auth", "Utility"],
            "auth": ["Auth"],
            "utility": ["Utility"],
            "fun": ["Fun"],
            "agent": ["Agent"],
            "voice": ["Voice"],
            "troll": ["Troll"],
        }
        category_title_map = {
            "admin": "Admin Panel",
            "auth": "Auth",
            "utility": "Utility",
            "fun": "Fun",
            "agent": "Agent",
            "voice": "Voice",
            "troll": "Troll",
        }

        selected_cog_names = category_cog_map.get(val, [])
        selected_cogs = [self.bot.get_cog(name) for name in selected_cog_names if self.bot.get_cog(name)]
        category_title = category_title_map.get(val, val.title())
        is_owner, is_admin, is_server_owner = _help_access_flags(self.bot, self.ctx)
        can_view_restricted = is_owner or is_admin or is_server_owner
        
        if not selected_cogs:
            await interaction.response.edit_message(embed=discord.Embed(description="❌ Category not found.", color=discord.Color.red()))
            return

        is_restricted_category = val in {"admin", "auth", "utility"}
        available_commands = []
        seen = set()

        if is_restricted_category and can_view_restricted:
            # Owner/Admin/Server Owner can browse full restricted command list.
            for cog in selected_cogs:
                for cmd in _cog_all_commands(cog):
                    qname = cmd.qualified_name
                    if qname in seen:
                        continue
                    seen.add(qname)
                    available_commands.append(cmd)
        else:
            # Normal users only see commands they can actually run.
            for cog in selected_cogs:
                for cmd in _cog_all_commands(cog):
                    qname = cmd.qualified_name
                    if qname in seen:
                        continue
                    try:
                        if await cmd.can_run(self.ctx):
                            seen.add(qname)
                            available_commands.append(cmd)
                    except Exception:
                        pass
        
        if not available_commands:
            await interaction.response.edit_message(embed=discord.Embed(description="🚫 No commands available for you here.", color=discord.Color.red()))
            return

        view = CommandPageView(
            self.bot,
            self.ctx,
            category_title,
            str(self.options[self._get_index(val)].emoji),
            available_commands,
            timeout=180,
        )
        await interaction.response.edit_message(embed=view.build_embed(), view=view)

    def _get_index(self, val):
        for i, opt in enumerate(self.options):
            if opt.value == val: return i
        return 0

# --- Helper Functions ---

# --- Helper Functions ---

async def show_main_menu(interaction_or_ctx, bot, ctx, edit=False, ephemeral=False):
    """Show the main category menu with personalized Master List."""
    
    is_owner, is_admin, is_server_owner = _help_access_flags(bot, ctx)
    
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
        if is_restricted and not (is_owner or is_admin or is_server_owner):
            continue
            
        cmd_list = []
        for cog_name in cog_names:
            cog = bot.get_cog(cog_name.capitalize()) or bot.get_cog(cog_name)
            if cog:
                for cmd in _cog_all_commands(cog):
                    try:
                        cmd_list.append(f"`!{cmd.qualified_name}`")
                    except Exception:
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
        title=f"Command: !{cmd.qualified_name}",
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
            
    usage_str = f"`{ctx.prefix}{cmd.qualified_name} {' '.join(params)}`"
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
        is_owner, is_admin, is_server_owner = _help_access_flags(self.bot, ctx)
        can_bypass_help_permissions = is_owner or is_admin or is_server_owner
        
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
            
            if not can_bypass_help_permissions:
                # Regular users only see command docs for commands they can use.
                try:
                    if not await cmd.can_run(ctx):
                        return await target_send(f"⛔ You don't have permission to view/use `{command_name}`.", **kwargs)
                except Exception:
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
