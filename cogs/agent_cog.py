"""
Agent Cog - LLM Agent Commands
Provides access to the llm CLI agent features.
"""
import discord
from discord.ext import commands
import json
import re
from typing import Any, Dict, List, Optional

class AgentCog(commands.Cog, name="Agent"):
    """Advanced AI Agent commands using LLM CLI."""

    MAX_COMMAND_CATALOG = 180
    NATURAL_TRIGGERS = (
        "hey manga",
        "hey gemini",
        "hey gmini",
    )
    ACTION_HINTS = (
        "kick",
        "ban",
        "unban",
        "mute",
        "unmute",
        "timeout",
        "clear",
        "voice channel",
        "create channel",
        "make channel",
        "create role",
        "make role",
        "create roll",
        "make roll",
        "category",
        "catogery",
        "catagory",
    )
    
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

    @agent_group.command(name="capabilities", aliases=["caps", "commands"])
    async def agent_capabilities(self, ctx):
        """List what the AI can do in this server."""
        catalog = self._build_command_catalog()
        action_lines = [
            "**🎯 AI Natural Actions**",
            "`hey manga create role <name>`",
            "`hey manga create voice channel <name>`",
            "`hey manga create category <name>`",
            "`hey manga kick @user`",
            "",
            f"**📚 Known Bot Commands ({len(catalog)} shown)**",
        ]
        action_lines.extend(f"`{line}`" for line in catalog[:self.MAX_COMMAND_CATALOG])

        text = "\n".join(action_lines)
        if len(text) <= 1990:
            await ctx.send(text)
            return

        # Split long output.
        chunks = [text[i:i + 1990] for i in range(0, len(text), 1990)]
        for chunk in chunks:
            await ctx.send(chunk)

    async def handle_natural_request(self, message: discord.Message) -> bool:
        """
        Handle free-form requests like:
        - hey manga make a voice channel that role Test can access
        - hey gemini make a role Moderator
        - hey kick @user
        """
        if not message.guild or message.author.bot:
            return False
        if not self.bot.user or not self.bot.user.mentioned_in(message):
            # Mention-only behavior for natural AI actions.
            return False

        prompt = self._extract_natural_prompt(message.content)
        if not prompt:
            return False

        ai = getattr(self.bot, "ai_service", None)
        if not ai or not ai.enabled:
            await message.reply(
                "❌ AI is not available. Set `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, or `GROQ_API_KEY`.",
                mention_author=False,
            )
            return True

        async with message.channel.typing():
            plan = await self._plan_action(message, prompt)
            response = await self._execute_action_plan(message, prompt, plan)

        await message.reply(response[:2000], mention_author=False)
        return True

    def _extract_natural_prompt(self, content: str) -> Optional[str]:
        # Mention-only mode is enforced by caller; strip raw mention tokens first.
        text = re.sub(r"<@!?\d+>", "", content or "").strip()
        lower = text.lower()
        if not text:
            return None

        for trigger in self.NATURAL_TRIGGERS:
            if lower.startswith(trigger):
                tail = text[len(trigger):].strip(" ,:.-")
                return tail or None

        # Allow quick action style without naming Manga (e.g. "hey kick @user").
        if lower.startswith("hey "):
            tail = text[4:].strip()
            tail_lower = tail.lower()
            if any(hint in tail_lower for hint in self.ACTION_HINTS):
                return tail or None

        # Mention + direct action text (e.g. "@Manga create voice channel test").
        if any(hint in lower for hint in self.ACTION_HINTS):
            return text

        return None

    async def _plan_action(self, message: discord.Message, prompt: str) -> Dict[str, Any]:
        fallback_plan = self._fallback_action_plan(message, prompt)
        ai_plan = await self._plan_action_with_ai(message, prompt)
        actionable = {"create_voice_channel", "create_role", "create_category", "kick_member"}

        # Prefer explicit server actions. If AI only returns chat/none,
        # use deterministic fallback action parsing.
        if ai_plan and ai_plan.get("action") in actionable:
            ai_plan = dict(ai_plan)
            for key in ("channel_name", "category_name", "role_name", "member_query", "reason"):
                if not ai_plan.get(key):
                    ai_plan[key] = fallback_plan.get(key, "")
            if not ai_plan.get("role_names"):
                ai_plan["role_names"] = fallback_plan.get("role_names", [])
            return ai_plan

        if fallback_plan.get("action") in actionable:
            return fallback_plan

        if ai_plan:
            return ai_plan
        return fallback_plan

    def _build_command_catalog(self) -> List[str]:
        entries: List[str] = []
        seen = set()
        for cmd in sorted(self.bot.walk_commands(), key=lambda c: c.qualified_name):
            if cmd.hidden:
                continue
            qname = cmd.qualified_name.strip()
            if not qname or qname in seen:
                continue
            seen.add(qname)

            brief = ""
            if cmd.help:
                brief = str(cmd.help).strip().splitlines()[0]
            elif cmd.brief:
                brief = str(cmd.brief).strip()

            if brief:
                entries.append(f"!{qname} - {brief}")
            else:
                entries.append(f"!{qname}")

            if len(entries) >= self.MAX_COMMAND_CATALOG:
                break
        return entries

    async def _plan_action_with_ai(self, message: discord.Message, prompt: str) -> Optional[Dict[str, Any]]:
        ai = getattr(self.bot, "ai_service", None)
        if not ai or not ai.enabled:
            return None

        role_names = [r.name for r in message.guild.roles if r.name != "@everyone"][:60]
        member_names = [m.display_name for m in message.guild.members if not m.bot][:80]
        channel_names = [c.name for c in message.guild.channels][:80]
        mention_names = [m.display_name for m in message.mentions]
        category_names = [c.name for c in message.guild.categories][:60]
        command_catalog = self._build_command_catalog()
        command_block = "\n".join(command_catalog)

        planner_prompt = (
            "You are Manga, a Discord bot action planner.\n"
            "Turn the user request into ONE JSON object.\n"
            "Allowed actions: create_voice_channel, create_role, create_category, kick_member, chat, none.\n"
            "Output schema:\n"
            "{\n"
            '  "action": "create_voice_channel|create_role|create_category|kick_member|chat|none",\n'
            '  "channel_name": "string",\n'
            '  "category_name": "string",\n'
            '  "role_names": ["string"],\n'
            '  "role_name": "string",\n'
            '  "member_query": "string",\n'
            '  "reason": "string",\n'
            '  "reply": "string"\n'
            "}\n"
            "Rules:\n"
            "- If user says role/roll, interpret as create_role.\n"
            "- If user asks for voice channel with role access, use create_voice_channel and fill role_names.\n"
            "- If user asks to make/add/create category, use create_category.\n"
            "- If user asks to kick someone, use kick_member.\n"
            "- If no server action is requested, use chat.\n"
            "- Return ONLY JSON, no markdown.\n\n"
            f"Guild: {message.guild.name}\n"
            f"Known roles: {role_names}\n"
            f"Known channels: {channel_names}\n"
            f"Known categories: {category_names}\n"
            f"Known members: {member_names}\n"
            f"Mentioned members: {mention_names}\n"
            f"Known commands:\n{command_block}\n"
            f"User request: {prompt}\n"
        )

        raw = await ai.generate(planner_prompt)
        parsed = self._extract_json_object(raw)
        if not parsed:
            return None

        return self._normalize_plan(parsed)

    @staticmethod
    def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None

        text = raw.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None

    def _normalize_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        action = str(plan.get("action", "chat")).strip().lower()
        allowed_actions = {"create_voice_channel", "create_role", "create_category", "kick_member", "chat", "none"}
        if action not in allowed_actions:
            action = "chat"

        raw_roles = plan.get("role_names", [])
        if not isinstance(raw_roles, list):
            raw_roles = []
        role_names = [str(v).strip() for v in raw_roles if str(v).strip()]

        normalized = {
            "action": action,
            "channel_name": str(plan.get("channel_name", "")).strip(),
            "category_name": str(plan.get("category_name", "")).strip(),
            "role_names": role_names,
            "role_name": str(plan.get("role_name", "")).strip(),
            "member_query": str(plan.get("member_query", "")).strip(),
            "reason": str(plan.get("reason", "")).strip(),
            "reply": str(plan.get("reply", "")).strip(),
        }
        return normalized

    def _fallback_action_plan(self, message: discord.Message, prompt: str) -> Dict[str, Any]:
        low = prompt.lower()
        quoted = self._extract_quoted_values(prompt)

        if re.search(r"\b(make|create|add)\b.*\b(category|catogery|catagory)\b", low):
            category_name = quoted[0] if quoted else self._extract_category_name(prompt)
            role_names = self._match_role_names_in_text(message.guild, low)
            return {
                "action": "create_category",
                "channel_name": "",
                "category_name": category_name or "new-category",
                "role_names": role_names,
                "role_name": "",
                "member_query": "",
                "reason": "",
                "reply": "",
            }

        if "voice" in low and "channel" in low:
            channel_name = quoted[0] if quoted else self._extract_channel_name(prompt)
            role_names = self._match_role_names_in_text(message.guild, low)
            return {
                "action": "create_voice_channel",
                "channel_name": channel_name or "voice-room",
                "category_name": "",
                "role_names": role_names,
                "role_name": "",
                "member_query": "",
                "reason": "",
                "reply": "",
            }

        if re.search(r"\b(make|create)\b.*\b(role|roll)\b", low):
            role_name = quoted[0] if quoted else self._extract_role_name(prompt)
            return {
                "action": "create_role",
                "channel_name": "",
                "category_name": "",
                "role_names": [],
                "role_name": role_name or "",
                "member_query": "",
                "reason": "",
                "reply": "",
            }

        if "kick" in low:
            member_query = ""
            if message.mentions:
                member_query = str(message.mentions[0].id)
            else:
                match = re.search(r"\bkick\b\s+(.+)$", prompt, re.IGNORECASE)
                if match:
                    member_query = match.group(1).strip()
            return {
                "action": "kick_member",
                "channel_name": "",
                "category_name": "",
                "role_names": [],
                "role_name": "",
                "member_query": member_query,
                "reason": "",
                "reply": "",
            }

        return {
            "action": "chat",
            "channel_name": "",
            "category_name": "",
            "role_names": [],
            "role_name": "",
            "member_query": "",
            "reason": "",
            "reply": "",
        }

    @staticmethod
    def _extract_quoted_values(text: str) -> List[str]:
        results: List[str] = []
        for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', text):
            value = match.group(1) or match.group(2)
            value = value.strip()
            if value:
                results.append(value)
        return results

    @staticmethod
    def _extract_channel_name(prompt: str) -> str:
        match = re.search(r"voice\s+channel(?:\s+named|\s+called)?\s+([A-Za-z0-9 _-]{2,100})", prompt, re.IGNORECASE)
        if not match:
            return ""
        value = match.group(1).strip(" .,:;-")
        value = re.split(r"\b(with|for|that|where)\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,:;-")
        return value

    @staticmethod
    def _extract_category_name(prompt: str) -> str:
        match = re.search(
            r"(?:category|catogery|catagory)(?:\s+named|\s+called)?\s+([A-Za-z0-9 _-]{2,100})",
            prompt,
            re.IGNORECASE,
        )
        if not match:
            return ""
        value = match.group(1).strip(" .,:;-")
        value = re.split(r"\b(with|for|that|where)\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,:;-")
        return value

    @staticmethod
    def _extract_role_name(prompt: str) -> str:
        match = re.search(r"(?:role|roll)(?:\s+named|\s+called)?\s+([A-Za-z0-9 _-]{2,100})", prompt, re.IGNORECASE)
        if not match:
            return ""
        value = match.group(1).strip(" .,:;-")
        value = re.split(r"\b(with|for|that|where)\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip(" .,:;-")
        return value

    @staticmethod
    def _clean_role_name(name: str) -> str:
        value = (name or "").strip().strip("`'\"")
        value = re.sub(r"\s+", " ", value)
        return value[:100]

    @staticmethod
    def _clean_category_name(name: str) -> str:
        value = (name or "").strip().strip("`'\"")
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"[#:@]", "", value).strip()
        if not value:
            value = "New Category"
        return value[:100]

    @staticmethod
    def _clean_channel_name(name: str) -> str:
        value = (name or "").strip().lower().strip("`'\"")
        value = value.replace(" ", "-")
        value = re.sub(r"[^a-z0-9\-_]", "", value)
        value = re.sub(r"-{2,}", "-", value).strip("-")
        if not value:
            value = "voice-room"
        return value[:100]

    def _match_role_names_in_text(self, guild: discord.Guild, text_lower: str) -> List[str]:
        names = [r.name for r in guild.roles if r.name != "@everyone"]
        names.sort(key=len, reverse=True)
        matches: List[str] = []
        for name in names:
            if name.lower() in text_lower:
                matches.append(name)
        return matches[:10]

    @staticmethod
    def _find_role_case_insensitive(guild: discord.Guild, role_name: str) -> Optional[discord.Role]:
        query = role_name.strip().lower()
        if not query:
            return None
        for role in guild.roles:
            if role.name.lower() == query:
                return role
        return None

    def _resolve_roles(self, guild: discord.Guild, role_names: List[str]) -> (List[discord.Role], List[str]):
        resolved: List[discord.Role] = []
        missing: List[str] = []
        seen_ids = set()
        for raw in role_names:
            role = self._find_role_case_insensitive(guild, raw)
            if not role:
                missing.append(raw)
                continue
            if role.id in seen_ids:
                continue
            seen_ids.add(role.id)
            resolved.append(role)
        return resolved, missing

    def _resolve_member(self, message: discord.Message, member_query: str) -> Optional[discord.Member]:
        guild = message.guild
        if not guild:
            return None

        if message.mentions:
            return message.mentions[0]

        query = (member_query or "").strip()
        if not query:
            return None

        mention = re.fullmatch(r"<@!?(\d+)>", query)
        if mention:
            return guild.get_member(int(mention.group(1)))

        if query.isdigit():
            return guild.get_member(int(query))

        q = query.lower()
        for member in guild.members:
            if member.display_name.lower() == q or member.name.lower() == q:
                return member

        for member in guild.members:
            tag = f"{member.name}#{member.discriminator}".lower()
            if tag == q:
                return member

        for member in guild.members:
            if q in member.display_name.lower() or q in member.name.lower():
                return member
        return None

    def _is_auth_admin(self, user_id: int) -> bool:
        auth = self.bot.get_cog("Auth")
        if not auth:
            return False
        try:
            return bool(auth.is_owner(user_id) or auth.is_admin(user_id))
        except Exception:
            return False

    def _author_can(self, message: discord.Message, permission_name: str) -> bool:
        if message.guild and message.author.id == message.guild.owner_id:
            return True
        if getattr(message.author.guild_permissions, permission_name, False):
            return True
        return self._is_auth_admin(message.author.id)

    def _bot_can(self, guild: discord.Guild, permission_name: str) -> bool:
        me = guild.me or guild.get_member(self.bot.user.id)
        if not me:
            return False
        return bool(getattr(me.guild_permissions, permission_name, False))

    async def _execute_action_plan(self, message: discord.Message, prompt: str, plan: Dict[str, Any]) -> str:
        action = (plan.get("action") or "chat").strip().lower()

        if action == "create_role":
            return await self._action_create_role(message, plan)
        if action == "create_category":
            return await self._action_create_category(message, plan)
        if action == "create_voice_channel":
            return await self._action_create_voice_channel(message, prompt, plan)
        if action == "kick_member":
            return await self._action_kick_member(message, plan)

        ai = getattr(self.bot, "ai_service", None)
        if ai and ai.enabled:
            command_catalog = self._build_command_catalog()
            catalog_text = "\n".join(command_catalog)
            enriched_prompt = (
                "You are Manga, a Discord bot.\n"
                "Answer based on your real capabilities below.\n"
                "If user asks for an unsupported action, say exactly what command/action they should use.\n"
                f"Known commands:\n{catalog_text}\n\n"
                f"User ({message.author.display_name}) says: {prompt}"
            )
            return await ai.generate(enriched_prompt)
        return "❌ I couldn't process that request."

    async def _action_create_role(self, message: discord.Message, plan: Dict[str, Any]) -> str:
        guild = message.guild
        if not guild:
            return "❌ This action works only in a server."

        if not self._author_can(message, "manage_roles"):
            return "❌ You need Manage Roles (or bot admin access) to do that."
        if not self._bot_can(guild, "manage_roles"):
            return "❌ I need Manage Roles permission."

        role_name = self._clean_role_name(plan.get("role_name", ""))
        if not role_name:
            return "❌ Tell me the role name. Example: `hey manga create role Test`."

        existing = self._find_role_case_insensitive(guild, role_name)
        if existing:
            return f"ℹ️ Role `{existing.name}` already exists."

        try:
            role = await guild.create_role(
                name=role_name,
                reason=f"AI request by {message.author} ({message.author.id})",
            )
            return f"✅ Role created: {role.mention}"
        except Exception as e:
            return f"❌ Failed to create role: {e}"

    async def _action_create_category(self, message: discord.Message, plan: Dict[str, Any]) -> str:
        guild = message.guild
        if not guild:
            return "❌ This action works only in a server."

        if not self._author_can(message, "manage_channels"):
            return "❌ You need Manage Channels (or bot admin access) to do that."
        if not self._bot_can(guild, "manage_channels"):
            return "❌ I need Manage Channels permission."

        category_name = self._clean_category_name(plan.get("category_name", ""))
        requested_roles = plan.get("role_names", [])
        resolved_roles, missing_roles = self._resolve_roles(guild, requested_roles)

        existing = discord.utils.get(guild.categories, name=category_name)
        if existing:
            return f"ℹ️ Category `{existing.name}` already exists."

        overwrites = None
        if requested_roles:
            if not resolved_roles:
                return f"❌ I couldn't find these roles: {', '.join(requested_roles)}"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            for role in resolved_roles:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True)

        try:
            create_kwargs = {
                "reason": f"AI request by {message.author} ({message.author.id})",
            }
            if overwrites is not None:
                create_kwargs["overwrites"] = overwrites
            category = await guild.create_category(category_name, **create_kwargs)
        except Exception as e:
            return f"❌ Failed to create category: {e}"

        if resolved_roles:
            role_mentions = ", ".join(r.mention for r in resolved_roles)
            if missing_roles:
                return (
                    f"✅ Created category **{category.name}** with access for {role_mentions}.\n"
                    f"⚠️ Missing roles: {', '.join(missing_roles)}"
                )
            return f"✅ Created category **{category.name}** with access for {role_mentions}."

        return f"✅ Created category **{category.name}**."

    async def _action_create_voice_channel(self, message: discord.Message, prompt: str, plan: Dict[str, Any]) -> str:
        guild = message.guild
        if not guild:
            return "❌ This action works only in a server."

        if not self._author_can(message, "manage_channels"):
            return "❌ You need Manage Channels (or bot admin access) to do that."
        if not self._bot_can(guild, "manage_channels"):
            return "❌ I need Manage Channels permission."

        channel_name = self._clean_channel_name(plan.get("channel_name", ""))
        category_name = self._clean_category_name(plan.get("category_name", "")) if plan.get("category_name") else ""
        parent_category = None
        if category_name:
            parent_category = discord.utils.get(guild.categories, name=category_name)
            if not parent_category:
                return f"❌ Category `{category_name}` was not found."
        requested_roles = plan.get("role_names", []) or self._match_role_names_in_text(guild, prompt.lower())
        resolved_roles, missing_roles = self._resolve_roles(guild, requested_roles)

        overwrites = None
        if requested_roles:
            if not resolved_roles:
                return f"❌ I couldn't find these roles: {', '.join(requested_roles)}"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False,
                    connect=False,
                )
            }
            for role in resolved_roles:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    connect=True,
                    speak=True,
                )

        try:
            create_kwargs = {
                "reason": f"AI request by {message.author} ({message.author.id})",
            }
            if parent_category is not None:
                create_kwargs["category"] = parent_category
            if overwrites is not None:
                create_kwargs["overwrites"] = overwrites
            channel = await guild.create_voice_channel(channel_name, **create_kwargs)
        except Exception as e:
            return f"❌ Failed to create voice channel: {e}"

        if resolved_roles:
            role_mentions = ", ".join(r.mention for r in resolved_roles)
            if missing_roles:
                return (
                    f"✅ Created voice channel {channel.mention} with access for {role_mentions}.\n"
                    f"⚠️ Missing roles: {', '.join(missing_roles)}"
                )
            return f"✅ Created voice channel {channel.mention} with access for {role_mentions}."

        return f"✅ Created voice channel {channel.mention}."

    async def _action_kick_member(self, message: discord.Message, plan: Dict[str, Any]) -> str:
        guild = message.guild
        if not guild:
            return "❌ This action works only in a server."

        if not self._author_can(message, "kick_members"):
            return "❌ You need Kick Members (or bot admin access) to do that."
        if not self._bot_can(guild, "kick_members"):
            return "❌ I need Kick Members permission."

        member = self._resolve_member(message, plan.get("member_query", ""))
        if not member:
            return "❌ Mention the user you want to kick. Example: `hey kick @user`."
        if member.id == message.author.id:
            return "❌ I won't kick you."
        if member.id == self.bot.user.id:
            return "❌ I can't kick myself."
        if member.id == guild.owner_id:
            return "❌ I can't kick the server owner."

        me = guild.me or guild.get_member(self.bot.user.id)
        if me and member.top_role >= me.top_role:
            return "❌ I can't kick that user because their role is above or equal to mine."

        reason = plan.get("reason", "").strip() or f"Requested by {message.author} ({message.author.id})"
        try:
            await member.kick(reason=reason)
            return f"✅ Kicked {member.mention}."
        except Exception as e:
            return f"❌ Failed to kick {member.mention}: {e}"

async def setup(bot):
    # This is handled in bot.py manually for dependency injection
    pass
