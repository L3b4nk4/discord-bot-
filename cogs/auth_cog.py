"""
Auth Commands Cog - Authentication and authorization commands.
Handles user verification, access levels, and permission management.
"""
import discord
from discord.ext import commands
from discord import ui
from datetime import datetime
import asyncio
import json
import os
import queue
import re
import shutil
import sqlite3
import threading
from pathlib import Path

try:
    import firebase_admin
    from firebase_admin import credentials as firebase_credentials
    from firebase_admin import firestore as firebase_firestore
except Exception:
    firebase_admin = None
    firebase_credentials = None
    firebase_firestore = None


def _default_auth_data() -> dict:
    """Default in-memory auth model."""
    return {
        "admins": [],              # Bot admins (global)
        "moderators": [],          # Bot moderators (global)
        "verified_users": {},      # Guild -> list[user_id]
        "blacklisted": {},         # Guild -> list[user_id]
        "whitelisted": {},         # Guild -> list[user_id]
        "reaction_roles": {},      # Guild -> {message_id, channel_id, options:[{role_id, emoji}]}
        "command_overrides": {},   # Guild -> CommandName -> {disabled, allowed_roles, allowed_users}
        "autokick": {}             # Guild -> {enabled, min_age_days}
    }


def _normalize_int_list(values) -> list:
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        try:
            out.append(int(value))
        except Exception:
            continue
    return sorted(set(out))


class AuthSQLiteStore:
    """SQLite-backed storage split per guild to avoid global write contention."""
    backend_name = "sqlite"
    uses_local_files = True

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.guild_dir = self.root_dir / "guilds"
        self.global_db = self.root_dir / "global.db"
        self._lock = threading.Lock()

        self.guild_dir.mkdir(parents=True, exist_ok=True)
        self._init_global_db()

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_global_db(self):
        with self._lock, self._connect(self.global_db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS moderators (user_id INTEGER PRIMARY KEY)")
            conn.commit()

    def _guild_db_path(self, guild_key: str) -> Path:
        return self.guild_dir / f"{guild_key}.db"

    def _init_guild_db(self, guild_key: str):
        db_path = self._guild_db_path(guild_key)
        with self._lock, self._connect(db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS verified_users (user_id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS whitelisted_users (user_id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE IF NOT EXISTS blacklisted_users (user_id INTEGER PRIMARY KEY)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verify_message (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    message_id INTEGER,
                    channel_id INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS verify_role_options (
                    emoji TEXT PRIMARY KEY,
                    role_id INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reaction_role (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    message_id INTEGER,
                    channel_id INTEGER,
                    verify_role_id INTEGER,
                    emoji TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_overrides (
                    command_name TEXT PRIMARY KEY,
                    disabled INTEGER NOT NULL DEFAULT 0,
                    allowed_roles TEXT NOT NULL DEFAULT '[]',
                    allowed_users TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autokick_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    min_age_days INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    def load(self) -> dict:
        data = _default_auth_data()

        with self._lock, self._connect(self.global_db) as conn:
            data["admins"] = [row["user_id"] for row in conn.execute("SELECT user_id FROM admins ORDER BY user_id")]
            data["moderators"] = [row["user_id"] for row in conn.execute("SELECT user_id FROM moderators ORDER BY user_id")]

        for db_file in sorted(self.guild_dir.glob("*.db")):
            guild_key = db_file.stem
            self._load_guild(guild_key, data)

        return data

    def _load_guild(self, guild_key: str, data: dict):
        self._init_guild_db(guild_key)
        db_path = self._guild_db_path(guild_key)

        with self._lock, self._connect(db_path) as conn:
            verified = [row["user_id"] for row in conn.execute("SELECT user_id FROM verified_users ORDER BY user_id")]
            if verified:
                data["verified_users"][guild_key] = verified

            whitelisted = [row["user_id"] for row in conn.execute("SELECT user_id FROM whitelisted_users ORDER BY user_id")]
            if whitelisted:
                data["whitelisted"][guild_key] = whitelisted

            blacklisted = [row["user_id"] for row in conn.execute("SELECT user_id FROM blacklisted_users ORDER BY user_id")]
            if blacklisted:
                data["blacklisted"][guild_key] = blacklisted

            verify_message = conn.execute("SELECT message_id, channel_id FROM verify_message WHERE id = 1").fetchone()
            verify_opts = conn.execute("SELECT emoji, role_id FROM verify_role_options ORDER BY rowid").fetchall()

            if verify_message and verify_opts:
                options = []
                for opt in verify_opts:
                    try:
                        options.append({
                            "emoji": str(opt["emoji"]),
                            "role_id": int(opt["role_id"]),
                        })
                    except Exception:
                        continue

                if options:
                    data["reaction_roles"][guild_key] = {
                        "message_id": verify_message["message_id"],
                        "channel_id": verify_message["channel_id"],
                        "options": options,
                    }
            else:
                # Legacy fallback from previous single-role schema.
                rr = conn.execute("SELECT message_id, channel_id, verify_role_id, emoji FROM reaction_role WHERE id = 1").fetchone()
                if rr and rr["verify_role_id"]:
                    data["reaction_roles"][guild_key] = {
                        "message_id": rr["message_id"],
                        "channel_id": rr["channel_id"],
                        "options": [
                            {
                                "emoji": rr["emoji"] or "✅",
                                "role_id": int(rr["verify_role_id"]),
                            }
                        ],
                    }

            overrides = {}
            for row in conn.execute(
                "SELECT command_name, disabled, allowed_roles, allowed_users FROM command_overrides ORDER BY command_name"
            ):
                try:
                    allowed_roles = [int(v) for v in json.loads(row["allowed_roles"] or "[]")]
                except Exception:
                    allowed_roles = []
                try:
                    allowed_users = [int(v) for v in json.loads(row["allowed_users"] or "[]")]
                except Exception:
                    allowed_users = []

                overrides[row["command_name"]] = {
                    "disabled": bool(row["disabled"]),
                    "allowed_roles": allowed_roles,
                    "allowed_users": allowed_users,
                }

            if overrides:
                data["command_overrides"][guild_key] = overrides

            autokick = conn.execute("SELECT enabled, min_age_days FROM autokick_config WHERE id = 1").fetchone()
            if autokick:
                data["autokick"][guild_key] = {
                    "enabled": bool(autokick["enabled"]),
                    "min_age_days": int(autokick["min_age_days"] or 0),
                }

    def save_global(self, data: dict):
        admins = sorted({int(v) for v in data.get("admins", [])})
        moderators = sorted({int(v) for v in data.get("moderators", [])})

        with self._lock, self._connect(self.global_db) as conn:
            conn.execute("DELETE FROM admins")
            conn.executemany("INSERT INTO admins(user_id) VALUES (?)", [(uid,) for uid in admins])

            conn.execute("DELETE FROM moderators")
            conn.executemany("INSERT INTO moderators(user_id) VALUES (?)", [(uid,) for uid in moderators])
            conn.commit()

    def save_guild(self, guild_key: str, payload: dict):
        self._init_guild_db(guild_key)
        db_path = self._guild_db_path(guild_key)

        verified = sorted({int(v) for v in payload.get("verified_users", [])})
        whitelisted = sorted({int(v) for v in payload.get("whitelisted", [])})
        blacklisted = sorted({int(v) for v in payload.get("blacklisted", [])})
        reaction_roles = payload.get("reaction_roles") or {}
        overrides = payload.get("command_overrides", {})
        autokick = payload.get("autokick") or {}

        with self._lock, self._connect(db_path) as conn:
            conn.execute("DELETE FROM verified_users")
            conn.executemany("INSERT INTO verified_users(user_id) VALUES (?)", [(uid,) for uid in verified])

            conn.execute("DELETE FROM whitelisted_users")
            conn.executemany("INSERT INTO whitelisted_users(user_id) VALUES (?)", [(uid,) for uid in whitelisted])

            conn.execute("DELETE FROM blacklisted_users")
            conn.executemany("INSERT INTO blacklisted_users(user_id) VALUES (?)", [(uid,) for uid in blacklisted])

            message_id = reaction_roles.get("message_id")
            channel_id = reaction_roles.get("channel_id")
            raw_options = reaction_roles.get("options", [])
            options = []
            for item in raw_options:
                if not isinstance(item, dict):
                    continue
                try:
                    options.append(
                        {
                            "emoji": str(item.get("emoji", "")).strip(),
                            "role_id": int(item.get("role_id")),
                        }
                    )
                except Exception:
                    continue
            options = [opt for opt in options if opt["emoji"]]

            conn.execute("DELETE FROM verify_message")
            conn.execute("DELETE FROM verify_role_options")
            if message_id and channel_id and options:
                try:
                    conn.execute(
                        "INSERT INTO verify_message(id, message_id, channel_id) VALUES (1, ?, ?)",
                        (int(message_id), int(channel_id)),
                    )
                    conn.executemany(
                        "INSERT INTO verify_role_options(emoji, role_id) VALUES (?, ?)",
                        [(opt["emoji"], opt["role_id"]) for opt in options],
                    )
                except Exception:
                    pass

            # Keep legacy table synced to first option for backward compatibility.
            conn.execute("DELETE FROM reaction_role")
            if message_id and channel_id and options:
                first = options[0]
                try:
                    conn.execute(
                        """
                        INSERT INTO reaction_role(id, message_id, channel_id, verify_role_id, emoji)
                        VALUES (1, ?, ?, ?, ?)
                        """,
                        (
                            int(message_id),
                            int(channel_id),
                            int(first["role_id"]),
                            first["emoji"],
                        ),
                    )
                except Exception:
                    pass

            conn.execute("DELETE FROM command_overrides")
            for command_name, override_data in overrides.items():
                if not isinstance(override_data, dict):
                    continue
                conn.execute(
                    """
                    INSERT INTO command_overrides(command_name, disabled, allowed_roles, allowed_users)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        command_name,
                        1 if override_data.get("disabled") else 0,
                        json.dumps([int(v) for v in override_data.get("allowed_roles", [])]),
                        json.dumps([int(v) for v in override_data.get("allowed_users", [])]),
                    ),
                )

            conn.execute("DELETE FROM autokick_config")
            if autokick:
                conn.execute(
                    "INSERT INTO autokick_config(id, enabled, min_age_days) VALUES (1, ?, ?)",
                    (
                        1 if autokick.get("enabled") else 0,
                        int(autokick.get("min_age_days", 0)),
                    ),
                )

            conn.commit()

    def delete_guild(self, guild_key: str):
        """Delete per-guild DB when bot leaves a server."""
        db_path = self._guild_db_path(guild_key)
        with self._lock:
            try:
                if db_path.exists():
                    db_path.unlink()
            except Exception:
                pass

    def ensure_guild_db(self, guild_key: str):
        """Ensure a guild DB file exists."""
        self._init_guild_db(guild_key)

    def list_guild_keys(self) -> set:
        """List guild keys currently present on disk."""
        with self._lock:
            return {p.stem for p in self.guild_dir.glob("*.db") if p.is_file()}

    def guild_db_path(self, guild_key: str) -> Path:
        """Get absolute DB path for a guild key."""
        return self._guild_db_path(guild_key)

    def storage_label(self, guild_key: str) -> str:
        return str(self._guild_db_path(guild_key))

    def storage_exists(self, guild_key: str) -> bool:
        return self._guild_db_path(guild_key).exists()


class AuthFirebaseStore:
    """Firestore-backed storage split by guild document."""
    backend_name = "firebase-firestore"
    uses_local_files = False

    @staticmethod
    def _pick_env(*names: str) -> str:
        for name in names:
            value = os.getenv(name)
            if value:
                return value
        return ""

    @classmethod
    def _load_certificate_payload(cls, credentials_path: Path) -> dict:
        with credentials_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid Firebase credential JSON: {credentials_path}")

        private_key_id = cls._pick_env("FIREBASE_PRIVATE_KEY_ID", "GOOGLE_PRIVATE_KEY_ID").strip()
        if private_key_id:
            payload["private_key_id"] = private_key_id

        private_key = cls._pick_env("FIREBASE_PRIVATE_KEY", "GOOGLE_PRIVATE_KEY")
        if private_key:
            payload["private_key"] = private_key.replace("\\n", "\n")

        return payload

    def __init__(self, credentials_path: str, collection_name: str = "discord_auth", project_id: str = ""):
        if not firebase_admin or not firebase_credentials or not firebase_firestore:
            raise RuntimeError("firebase-admin is not installed. Run `pip install firebase-admin`.")

        cred_path = Path(credentials_path).expanduser()
        if not cred_path.exists():
            raise FileNotFoundError(f"Firebase credentials file not found: {cred_path}")

        app_name = f"auth-store-{cred_path.stem}"
        try:
            self._app = firebase_admin.get_app(app_name)
        except ValueError:
            cert_payload = self._load_certificate_payload(cred_path)
            cert = firebase_credentials.Certificate(cert_payload)
            options = {"projectId": project_id} if project_id else None
            self._app = firebase_admin.initialize_app(cert, options=options, name=app_name)

        self.client = firebase_firestore.client(app=self._app)
        self.collection_name = collection_name
        self.root_dir = Path(f"firestore/{collection_name}")
        self._base_doc = self.client.collection(collection_name).document("auth")
        self._global_doc = self._base_doc.collection("meta").document("global")
        self._guilds = self._base_doc.collection("guilds")

    @classmethod
    def _normalize_reaction_roles(cls, reaction_roles):
        if not isinstance(reaction_roles, dict):
            return {}
        try:
            message_id = int(reaction_roles.get("message_id"))
            channel_id = int(reaction_roles.get("channel_id"))
        except Exception:
            return {}

        options = []
        for option in reaction_roles.get("options", []):
            if not isinstance(option, dict):
                continue
            emoji = str(option.get("emoji", "")).strip()
            if not emoji:
                continue
            try:
                role_id = int(option.get("role_id"))
            except Exception:
                continue
            options.append({"emoji": emoji, "role_id": role_id})

        if not options:
            return {}

        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "options": options,
        }

    @classmethod
    def _normalize_overrides(cls, overrides):
        if not isinstance(overrides, dict):
            return {}
        normalized = {}
        for command_name, cfg in overrides.items():
            if not isinstance(command_name, str) or not isinstance(cfg, dict):
                continue
            normalized[command_name] = {
                "disabled": bool(cfg.get("disabled", False)),
                "allowed_roles": _normalize_int_list(cfg.get("allowed_roles", [])),
                "allowed_users": _normalize_int_list(cfg.get("allowed_users", [])),
            }
        return normalized

    @staticmethod
    def _normalize_autokick(autokick):
        if not isinstance(autokick, dict):
            return {}
        return {
            "enabled": bool(autokick.get("enabled", False)),
            "min_age_days": int(autokick.get("min_age_days", 0)),
        }

    @classmethod
    def _sanitize_guild_payload(cls, payload: dict) -> dict:
        return {
            "verified_users": _normalize_int_list(payload.get("verified_users", [])),
            "blacklisted": _normalize_int_list(payload.get("blacklisted", [])),
            "whitelisted": _normalize_int_list(payload.get("whitelisted", [])),
            "reaction_roles": cls._normalize_reaction_roles(payload.get("reaction_roles", {})),
            "command_overrides": cls._normalize_overrides(payload.get("command_overrides", {})),
            "autokick": cls._normalize_autokick(payload.get("autokick", {})),
        }

    def load(self) -> dict:
        data = _default_auth_data()

        global_payload = self._global_doc.get()
        if global_payload.exists:
            raw_global = global_payload.to_dict() or {}
            data["admins"] = _normalize_int_list(raw_global.get("admins", []))
            data["moderators"] = _normalize_int_list(raw_global.get("moderators", []))

        for guild_doc in self._guilds.stream():
            guild_key = str(guild_doc.id)
            payload = self._sanitize_guild_payload(guild_doc.to_dict() or {})

            if payload["verified_users"]:
                data["verified_users"][guild_key] = payload["verified_users"]
            if payload["blacklisted"]:
                data["blacklisted"][guild_key] = payload["blacklisted"]
            if payload["whitelisted"]:
                data["whitelisted"][guild_key] = payload["whitelisted"]
            if payload["reaction_roles"]:
                data["reaction_roles"][guild_key] = payload["reaction_roles"]
            if payload["command_overrides"]:
                data["command_overrides"][guild_key] = payload["command_overrides"]
            if payload["autokick"]:
                data["autokick"][guild_key] = payload["autokick"]

        return data

    def save_global(self, data: dict):
        payload = {
            "admins": _normalize_int_list(data.get("admins", [])),
            "moderators": _normalize_int_list(data.get("moderators", [])),
        }
        self._global_doc.set(payload)

    def save_guild(self, guild_key: str, payload: dict):
        clean_payload = self._sanitize_guild_payload(payload)
        self._guilds.document(str(guild_key)).set(clean_payload)

    def delete_guild(self, guild_key: str):
        self._guilds.document(str(guild_key)).delete()

    def ensure_guild_db(self, guild_key: str):
        guild_ref = self._guilds.document(str(guild_key))
        if not guild_ref.get().exists:
            guild_ref.set(
                {
                    "verified_users": [],
                    "blacklisted": [],
                    "whitelisted": [],
                    "reaction_roles": {},
                    "command_overrides": {},
                    "autokick": {},
                },
                merge=True,
            )

    def list_guild_keys(self) -> set:
        return {doc.id for doc in self._guilds.stream()}

    def _guild_doc_label(self, guild_key: str) -> str:
        return f"{self.collection_name}/auth/guilds/{guild_key}"

    def guild_db_path(self, guild_key: str) -> Path:
        return Path(self._guild_doc_label(guild_key))

    def storage_label(self, guild_key: str) -> str:
        return self._guild_doc_label(guild_key)

    def storage_exists(self, guild_key: str) -> bool:
        return self._guilds.document(str(guild_key)).get().exists


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


class BlacklistPickerSelect(ui.Select):
    """Dropdown for picking a blacklisted user to remove."""

    def __init__(self, picker_view, user_ids):
        self.picker_view = picker_view
        options = []
        for user_id in user_ids:
            name = picker_view.display_name_for(user_id)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    description=f"ID: {user_id}",
                    value=str(user_id),
                )
            )

        super().__init__(
            placeholder=f"Choose user to unblacklist ({picker_view.page + 1}/{picker_view.total_pages})",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_id = int(self.values[0])
        removed_name = self.picker_view.display_name_for(selected_id)

        guild_key = str(self.picker_view.ctx.guild.id)
        guild_blacklist = self.picker_view.auth_cog.auth_data.get("blacklisted", {}).get(guild_key, [])
        if selected_id in guild_blacklist:
            guild_blacklist.remove(selected_id)
            self.picker_view.auth_cog._save_auth_data(self.picker_view.ctx.guild.id)

        self.picker_view.refresh_ids()
        if not self.picker_view.blacklisted_ids:
            done_embed = discord.Embed(
                title="✅ User Removed",
                description=f"**{removed_name}** was removed from blacklist.\nNo blacklisted users left in this server.",
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=done_embed, view=None)
            return

        if self.picker_view.page >= self.picker_view.total_pages:
            self.picker_view.page = self.picker_view.total_pages - 1

        self.picker_view.rebuild()
        await interaction.response.edit_message(
            embed=self.picker_view.build_embed(status=f"✅ Removed **{removed_name}** from blacklist."),
            view=self.picker_view,
        )


class BlacklistPickerView(ui.View):
    """Paged view to remove blacklisted users by selecting their name."""
    PAGE_SIZE = 25

    def __init__(self, auth_cog, ctx, timeout=180):
        super().__init__(timeout=timeout)
        self.auth_cog = auth_cog
        self.ctx = ctx
        self.page = 0
        self.blacklisted_ids = []
        self.refresh_ids()
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This menu is only for the command author.", ephemeral=True)
            return False
        return True

    @property
    def total_pages(self):
        if not self.blacklisted_ids:
            return 1
        return (len(self.blacklisted_ids) - 1) // self.PAGE_SIZE + 1

    def refresh_ids(self):
        guild_key = str(self.ctx.guild.id)
        raw_ids = self.auth_cog.auth_data.get("blacklisted", {}).get(guild_key, [])
        cleaned = []
        for value in raw_ids:
            try:
                cleaned.append(int(value))
            except Exception:
                continue
        self.blacklisted_ids = sorted(set(cleaned))

    def display_name_for(self, user_id: int) -> str:
        member = self.ctx.guild.get_member(user_id)
        if member:
            return member.display_name

        user = self.auth_cog.bot.get_user(user_id)
        if user:
            return user.name

        return f"Unknown ({user_id})"

    def current_page_ids(self):
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        return self.blacklisted_ids[start:end]

    def build_embed(self, status: str = None) -> discord.Embed:
        embed = discord.Embed(
            title="🚫 Blacklist Manager",
            description=status or "Choose a user below to remove them from this server blacklist.",
            color=discord.Color.orange(),
        )
        lines = []
        for user_id in self.current_page_ids():
            name = self.display_name_for(user_id)
            lines.append(f"`{user_id}` - {name[:28]}")

        embed.add_field(
            name=f"Users (Page {self.page + 1}/{self.total_pages})",
            value="\n".join(lines) if lines else "No users on this page.",
            inline=False,
        )
        embed.set_footer(text=f"Total blacklisted (this server): {len(self.blacklisted_ids)}")
        return embed

    def rebuild(self):
        self.clear_items()
        page_ids = self.current_page_ids()
        if page_ids:
            self.add_item(BlacklistPickerSelect(self, page_ids))

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
                self.rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            async def next_callback(interaction: discord.Interaction):
                self.page = min(self.total_pages - 1, self.page + 1)
                self.rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)

            prev_btn.callback = prev_callback
            next_btn.callback = next_callback
            self.add_item(prev_btn)
            self.add_item(next_btn)


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
        overrides = self.auth_cog.get_command_override(self.ctx.guild.id, self.cmd_name)
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
        overrides = self.auth_cog.get_command_override(self.ctx.guild.id, self.cmd_name)
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

        override = self.auth_cog.ensure_command_override(self.ctx.guild.id, self.cmd_name)
        override["disabled"] = not override.get("disabled", False)
        self.auth_cog._save_auth_data(self.ctx.guild.id)
        await self.update_view(interaction)

    async def reset_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return

        guild_key = str(self.ctx.guild.id)
        overrides = self.auth_cog.auth_data.get("command_overrides", {}).get(guild_key, {})
        if self.cmd_name in overrides:
            del overrides[self.cmd_name]
            self.auth_cog._save_auth_data(self.ctx.guild.id)
        await self.update_view(interaction)

    async def add_role_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        role_id = int(interaction.data["values"][0])

        override = self.auth_cog.ensure_command_override(self.ctx.guild.id, self.cmd_name)
        override.setdefault("allowed_roles", [])

        if role_id not in override["allowed_roles"]:
            override["allowed_roles"].append(role_id)
            self.auth_cog._save_auth_data(self.ctx.guild.id)
            
        await self.update_view(interaction)

    async def remove_role_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        role_id = int(interaction.data["values"][0])

        override = self.auth_cog.get_command_override(self.ctx.guild.id, self.cmd_name)
        try:
            override.get("allowed_roles", []).remove(role_id)
            self.auth_cog._save_auth_data(self.ctx.guild.id)
        except Exception:
            pass
        
        await self.update_view(interaction)


class AuthCog(commands.Cog, name="Auth"):
    """Authentication and authorization commands."""
    
    def __init__(self, bot):
        self.bot = bot
        self.legacy_data_file = "auth_data.json"
        self.store = self._build_store()

        # Async-safe save queue: avoid blocking the event loop with storage writes.
        self._save_queue = queue.Queue()
        self._queued_save_ops = set()
        self._deleted_guilds = set()
        self._startup_bootstrap_done = False
        self._save_queue_lock = threading.Lock()
        self._save_worker_stop = threading.Event()
        self._save_worker = threading.Thread(target=self._save_worker_loop, daemon=True)
        self._save_worker.start()
        
        # Load or initialize auth data
        self.auth_data = self._load_auth_data()
        
        # Bot owner (set dynamically or from env)
        self.owner_id = int(os.getenv("BOT_OWNER_ID", "1208492606774968331"))
        
        # Only me mode - when set, only this user ID can use commands
        self.only_me_user_id = None
        
        # Register persistent views on startup
        self._register_views()

    @staticmethod
    def _resolve_firebase_credentials_path() -> str:
        configured = os.getenv("FIREBASE_CREDENTIALS", "").strip()
        if configured:
            return configured

        google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if google_creds:
            return google_creds

        for pattern in ("*firebase-adminsdk*.json",):
            matches = sorted(Path(os.getcwd()).glob(pattern))
            if matches:
                return str(matches[0])
        return ""

    @staticmethod
    def _sqlite_root_from_env() -> str:
        db_root = os.getenv("AUTH_DB_ROOT")
        if db_root:
            return db_root

        persistent_dir = "/data"
        if os.path.exists(persistent_dir) and os.access(persistent_dir, os.W_OK):
            return os.path.join(persistent_dir, "auth_db")
        return "auth_db"

    def _build_store(self):
        firebase_path = self._resolve_firebase_credentials_path()
        if firebase_path:
            project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
            collection_name = os.getenv("FIREBASE_AUTH_COLLECTION", "discord_auth").strip() or "discord_auth"
            try:
                store = AuthFirebaseStore(
                    firebase_path,
                    collection_name=collection_name,
                    project_id=project_id,
                )
                print(f"☁️ Auth storage backend: Firebase Firestore ({collection_name})")
                return store
            except Exception as e:
                print(f"⚠️ Firebase auth storage unavailable: {e}. Falling back to SQLite.")

        db_root = self._sqlite_root_from_env()
        store = AuthSQLiteStore(db_root)
        print(f"💾 Auth storage backend: SQLite ({db_root})")
        return store

    def cog_unload(self):
        """Flush and stop background save worker on unload."""
        try:
            self._save_queue.join()
        except Exception:
            pass

        self._save_worker_stop.set()
        self._save_queue.put(None)
        if self._save_worker.is_alive():
            self._save_worker.join(timeout=2)

    def _enqueue_save(self, operation):
        """Queue a save operation with coalescing."""
        kind, guild_key = operation
        if kind == "guild" and guild_key in self._deleted_guilds:
            if guild_key in self._all_guild_keys():
                self._deleted_guilds.discard(guild_key)
            else:
                return

        with self._save_queue_lock:
            if operation in self._queued_save_ops:
                return
            self._queued_save_ops.add(operation)
        self._save_queue.put(operation)

    def _save_worker_loop(self):
        """Serialize save operations off the event loop."""
        while not self._save_worker_stop.is_set():
            operation = self._save_queue.get()
            if operation is None:
                self._save_queue.task_done()
                break

            kind, guild_key = operation
            try:
                if kind == "global":
                    self.store.save_global(self.auth_data)
                elif kind == "guild" and guild_key:
                    if guild_key not in self._deleted_guilds:
                        self.store.save_guild(guild_key, self._guild_payload(guild_key))
            except Exception as e:
                print(f"⚠️ Failed to save auth data ({kind}:{guild_key}): {e}")
            finally:
                with self._save_queue_lock:
                    self._queued_save_ops.discard(operation)
                self._save_queue.task_done()
    
    def _all_guild_keys(self):
        keys = set()
        for key in (
            "verified_users",
            "blacklisted",
            "whitelisted",
            "reaction_roles",
            "command_overrides",
            "autokick",
        ):
            keys.update(self.auth_data.get(key, {}).keys())
        return keys

    def _active_guild_keys(self):
        return {str(guild.id) for guild in self.bot.guilds}

    def _purge_guild_from_memory(self, guild_key: str):
        for key in ("verified_users", "blacklisted", "whitelisted", "reaction_roles", "command_overrides", "autokick"):
            if guild_key in self.auth_data.get(key, {}):
                del self.auth_data[key][guild_key]

    def _bootstrap_databases(self):
        """
        Startup bootstrap:
        1) Ensure a storage record exists for every connected guild.
        2) Remove orphan guild records (guilds the bot is no longer in).
        """
        active_keys = self._active_guild_keys()

        for guild_key in active_keys:
            self.store.ensure_guild_db(guild_key)

        disk_keys = self.store.list_guild_keys()
        orphan_keys = disk_keys - active_keys
        if orphan_keys:
            for guild_key in orphan_keys:
                self._deleted_guilds.add(guild_key)
                self._purge_guild_from_memory(guild_key)
                with self._save_queue_lock:
                    self._queued_save_ops.discard(("guild", guild_key))
                self.store.delete_guild(guild_key)
            print(f"🧹 Removed orphan auth records: {len(orphan_keys)}")

        # Persist current state (global + known guild payloads).
        self._save_auth_data()

    async def _flush_pending_saves(self, timeout: float = 3.0):
        """Wait briefly for queued DB writes to reach disk."""
        try:
            await asyncio.wait_for(asyncio.to_thread(self._save_queue.join), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    @staticmethod
    def _has_db_files(db_root: Path) -> bool:
        return any(db_root.rglob("*.db"))

    @staticmethod
    def _create_backup_snapshot_sync(db_root: Path, backup_root: Path, keep: int):
        if not db_root.exists() or not AuthCog._has_db_files(db_root):
            return None

        backup_root.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        tmp_snapshot = backup_root / f".tmp_snapshot_{ts}"
        final_snapshot = backup_root / f"snapshot_{ts}"

        shutil.copytree(db_root, tmp_snapshot, dirs_exist_ok=True)
        tmp_snapshot.rename(final_snapshot)

        snapshots = sorted(
            [p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("snapshot_")],
            key=lambda p: p.name,
            reverse=True,
        )
        for old in snapshots[max(1, keep):]:
            shutil.rmtree(old, ignore_errors=True)

        # Keep a readable "latest" mirror for quick host-side inspection.
        latest_dir = backup_root / "latest"
        shutil.rmtree(latest_dir, ignore_errors=True)
        shutil.copytree(db_root, latest_dir, dirs_exist_ok=True)
        return final_snapshot

    async def _backup_auth_db_now(self, reason: str):
        """Create an immediate DB snapshot when critical auth config changes."""
        if not getattr(self.store, "uses_local_files", True):
            return None

        backup_dir = os.getenv("AUTH_DB_BACKUP_DIR")
        if not backup_dir:
            return None

        db_root = Path(os.getenv("AUTH_DB_ROOT", "auth_db"))
        backup_root = Path(backup_dir)
        keep = max(1, int(os.getenv("AUTH_DB_BACKUP_KEEP", "48")))

        try:
            snapshot = await asyncio.to_thread(
                self._create_backup_snapshot_sync,
                db_root,
                backup_root,
                keep,
            )
            if snapshot:
                print(f"💾 Auth DB backup snapshot created ({reason}): {snapshot.name}")
            return snapshot
        except Exception as e:
            print(f"⚠️ Failed to create auth DB backup snapshot ({reason}): {e}")
            return None

    def _guild_payload(self, guild_key: str) -> dict:
        return {
            "verified_users": self.auth_data.get("verified_users", {}).get(guild_key, []),
            "blacklisted": self.auth_data.get("blacklisted", {}).get(guild_key, []),
            "whitelisted": self.auth_data.get("whitelisted", {}).get(guild_key, []),
            "reaction_roles": self.auth_data.get("reaction_roles", {}).get(guild_key, {}),
            "command_overrides": self.auth_data.get("command_overrides", {}).get(guild_key, {}),
            "autokick": self.auth_data.get("autokick", {}).get(guild_key, {}),
        }

    def _is_data_empty(self, data: dict) -> bool:
        if data.get("admins") or data.get("moderators"):
            return False
        return not any(data.get(key) for key in ("verified_users", "blacklisted", "whitelisted", "reaction_roles", "command_overrides", "autokick"))

    def _normalize_guild_list_map(self, values) -> dict:
        out = {}
        if not isinstance(values, dict):
            return out
        for guild_key, user_ids in values.items():
            key = str(guild_key)
            normalized = _normalize_int_list(user_ids)
            if normalized:
                out[key] = normalized
        return out

    def _normalize_reaction_roles(self, values) -> dict:
        out = {}
        if not isinstance(values, dict):
            return out
        for guild_key, cfg in values.items():
            if not isinstance(cfg, dict):
                continue
            options = []
            if isinstance(cfg.get("options"), list):
                for option in cfg["options"]:
                    if not isinstance(option, dict):
                        continue
                    try:
                        options.append(
                            {
                                "role_id": int(option["role_id"]),
                                "emoji": str(option.get("emoji", "✅")).strip(),
                            }
                        )
                    except Exception:
                        continue
            elif "verify_role_id" in cfg:
                # Legacy single-role format
                try:
                    options.append(
                        {
                            "role_id": int(cfg["verify_role_id"]),
                            "emoji": str(cfg.get("emoji", "✅")).strip(),
                        }
                    )
                except Exception:
                    pass

            if not options:
                continue

            try:
                out[str(guild_key)] = {
                    "message_id": int(cfg["message_id"]),
                    "channel_id": int(cfg["channel_id"]),
                    "options": options,
                }
            except Exception:
                continue
        return out

    @staticmethod
    def _is_override_record(value) -> bool:
        return isinstance(value, dict) and any(k in value for k in ("disabled", "allowed_roles", "allowed_users"))

    def _normalize_override(self, value: dict) -> dict:
        if not isinstance(value, dict):
            return {}
        return {
            "disabled": bool(value.get("disabled", False)),
            "allowed_roles": _normalize_int_list(value.get("allowed_roles", [])),
            "allowed_users": _normalize_int_list(value.get("allowed_users", [])),
        }

    def _normalize_command_overrides(self, values, known_guilds) -> dict:
        out = {}
        if not isinstance(values, dict):
            return out

        # Legacy shape: command_name -> override (global)
        if values and all(self._is_override_record(v) for v in values.values()):
            normalized_global = {
                cmd_name: self._normalize_override(cfg)
                for cmd_name, cfg in values.items()
                if isinstance(cmd_name, str)
            }
            if normalized_global:
                for guild_key in known_guilds:
                    out[guild_key] = json.loads(json.dumps(normalized_global))
                if not known_guilds:
                    out["_global"] = normalized_global
            return out

        # New shape: guild -> command -> override
        for guild_key, command_map in values.items():
            if not isinstance(command_map, dict):
                continue
            normalized_map = {}
            for cmd_name, cfg in command_map.items():
                if not isinstance(cmd_name, str):
                    continue
                normalized_map[cmd_name] = self._normalize_override(cfg)
            if normalized_map:
                out[str(guild_key)] = normalized_map
        return out

    def _migrate_legacy_json(self, raw: dict) -> dict:
        migrated = _default_auth_data()

        migrated["admins"] = _normalize_int_list(raw.get("admins", []))
        migrated["moderators"] = _normalize_int_list(raw.get("moderators", []))
        migrated["verified_users"] = self._normalize_guild_list_map(raw.get("verified_users", {}))
        migrated["whitelisted"] = self._normalize_guild_list_map(raw.get("whitelisted", {}))
        migrated["reaction_roles"] = self._normalize_reaction_roles(raw.get("reaction_roles", {}))

        autokick = {}
        if isinstance(raw.get("autokick"), dict):
            for guild_key, cfg in raw["autokick"].items():
                if not isinstance(cfg, dict):
                    continue
                autokick[str(guild_key)] = {
                    "enabled": bool(cfg.get("enabled", False)),
                    "min_age_days": int(cfg.get("min_age_days", 0)),
                }
        migrated["autokick"] = autokick

        known_guilds = set()
        for key in ("verified_users", "whitelisted", "reaction_roles", "autokick"):
            known_guilds.update(migrated.get(key, {}).keys())

        raw_blacklisted = raw.get("blacklisted", {})
        if isinstance(raw_blacklisted, list):
            normalized = _normalize_int_list(raw_blacklisted)
            if normalized:
                if known_guilds:
                    for guild_key in known_guilds:
                        migrated["blacklisted"][guild_key] = list(normalized)
                else:
                    migrated["blacklisted"]["_global"] = normalized
        elif isinstance(raw_blacklisted, dict):
            migrated["blacklisted"] = self._normalize_guild_list_map(raw_blacklisted)

        migrated["command_overrides"] = self._normalize_command_overrides(raw.get("command_overrides", {}), known_guilds)
        return migrated

    def _register_views(self):
        """Register persistent views for button interactions."""
        # Multi-verify now uses reactions and DB-backed mappings; no persistent UI is required.
        return
    
    def _load_auth_data(self) -> dict:
        """Load auth data from configured store. Migrate legacy JSON once if needed."""
        data = self.store.load()

        # Legacy migration path from auth_data.json
        if self._is_data_empty(data) and os.path.exists(self.legacy_data_file):
            try:
                with open(self.legacy_data_file, "r", encoding="utf-8") as f:
                    legacy = json.load(f)
                data = self._migrate_legacy_json(legacy)
                self.auth_data = data
                self._save_auth_data()
                migrated_path = f"{self.legacy_data_file}.migrated"
                if not os.path.exists(migrated_path):
                    os.rename(self.legacy_data_file, migrated_path)
                print(f"✅ Migrated legacy auth data to storage backend ({migrated_path})")
            except Exception as e:
                print(f"⚠️ Legacy auth migration failed: {e}")

        # Ensure missing keys are always present
        defaults = _default_auth_data()
        for key, default_value in defaults.items():
            if key not in data:
                data[key] = default_value
        return data
    
    def _save_auth_data(self, guild_id=None):
        """Persist auth data to active storage backend."""
        try:
            if guild_id is None:
                self._enqueue_save(("global", None))
                for guild_key in self._all_guild_keys():
                    self._enqueue_save(("guild", guild_key))
            else:
                guild_key = str(guild_id)
                self._enqueue_save(("guild", guild_key))
        except Exception as e:
            print(f"⚠️ Failed to save auth data: {e}")
    
    def get_command_override(self, guild_id: int, command_name: str) -> dict:
        guild_key = str(guild_id)
        guild_overrides = self.auth_data.get("command_overrides", {}).get(guild_key, {})
        if command_name in guild_overrides:
            return guild_overrides[command_name]

        # Migration fallback for old global key
        return self.auth_data.get("command_overrides", {}).get("_global", {}).get(command_name, {})

    def ensure_command_override(self, guild_id: int, command_name: str) -> dict:
        guild_key = str(guild_id)
        self.auth_data.setdefault("command_overrides", {})
        self.auth_data["command_overrides"].setdefault(guild_key, {})
        self.auth_data["command_overrides"][guild_key].setdefault(
            command_name,
            {"disabled": False, "allowed_roles": [], "allowed_users": []},
        )
        return self.auth_data["command_overrides"][guild_key][command_name]

    @staticmethod
    def _role_id_from_mention(token: str):
        match = re.fullmatch(r"<@&(\d+)>", token.strip())
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    @staticmethod
    def _normalize_emoji_token(token: str) -> str:
        token = token.strip()
        if not token:
            return ""
        # Custom emoji format: <:name:id> or <a:name:id>
        if re.fullmatch(r"<a?:\w+:\d+>", token):
            return token
        # Basic unicode emoji / symbol token
        return token

    def _parse_multi_verify_pairs(self, ctx, args):
        """Parse tokens like: ✅ @Role1 🔥 @Role2."""
        tokens = list(args)
        if not tokens:
            return [], "❌ Usage: `!setupverify ✅ @Role1 🌐 @Role2`"

        pairs = []
        pending_emoji = None
        bot_member = ctx.guild.me or ctx.guild.get_member(self.bot.user.id)
        if not bot_member:
            return [], "❌ Could not resolve my member permissions in this server."

        for token in tokens:
            role_id = self._role_id_from_mention(token)
            if role_id is not None:
                role = ctx.guild.get_role(role_id)
                if not role:
                    return [], f"❌ Role not found for token: `{token}`"

                if role >= bot_member.top_role:
                    return [], f"❌ I can't assign {role.mention}. My role must be higher."

                if pending_emoji:
                    emoji = pending_emoji
                    pending_emoji = None
                else:
                    emoji = "✅" if not pairs else None

                if not emoji:
                    return [], f"❌ Missing emoji for role {role.mention}. Use pairs like `🔥 {role.mention}`."

                pairs.append({"emoji": emoji, "role": role})
                continue

            # Allow role name token right after an emoji (example: ✅ Verified)
            if pending_emoji:
                role_by_name = discord.utils.get(ctx.guild.roles, name=token.strip())
                if role_by_name and not role_by_name.managed and role_by_name.name != "@everyone":
                    if role_by_name >= bot_member.top_role:
                        return [], f"❌ I can't assign {role_by_name.mention}. My role must be higher."
                    pairs.append({"emoji": pending_emoji, "role": role_by_name})
                    pending_emoji = None
                    continue

            normalized = self._normalize_emoji_token(token)
            if not normalized:
                return [], f"❌ Invalid emoji token: `{token}`"
            pending_emoji = normalized

        if pending_emoji:
            return [], f"❌ Emoji `{pending_emoji}` has no role after it."

        if not pairs:
            return [], "❌ No valid role mappings found. Example: `!setupverify ✅ @Verified 🌐 @Web`"

        # Ensure unique emoji and unique role ids
        seen_emoji = set()
        seen_roles = set()
        deduped = []
        for pair in pairs:
            emoji = pair["emoji"]
            role_id = pair["role"].id
            if emoji in seen_emoji:
                return [], f"❌ Duplicate emoji `{emoji}`. Each emoji can map to only one role."
            if role_id in seen_roles:
                continue
            seen_emoji.add(emoji)
            seen_roles.add(role_id)
            deduped.append(pair)

        return deduped, None

    def is_owner(self, user_id: int) -> bool:
        """Check if user is bot owner."""
        return user_id == self.owner_id
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is bot admin."""
        return user_id in self.auth_data["admins"] or self.is_owner(user_id)
    
    def is_moderator(self, user_id: int) -> bool:
        """Check if user is moderator."""
        return user_id in self.auth_data["moderators"] or self.is_admin(user_id)
    
    def is_blacklisted(self, user_id: int, guild_id=None) -> bool:
        """Check if user is blacklisted in a guild."""
        blacklisted = self.auth_data.get("blacklisted", {})

        if guild_id is not None:
            guild_users = blacklisted.get(str(guild_id), [])
            if user_id in guild_users:
                return True

        # Migration fallback for legacy global values
        if user_id in blacklisted.get("_global", []):
            return True

        if guild_id is None:
            return any(user_id in users for users in blacklisted.values())

        return False
    
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
        if not ctx.guild:
            return True

        overrides = self.get_command_override(ctx.guild.id, cmd_name)
        
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
        if self.is_blacklisted(user_id, ctx.guild.id):
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
        
        guild_key = str(ctx.guild.id)
        self.auth_data.setdefault("blacklisted", {})
        self.auth_data["blacklisted"].setdefault(guild_key, [])

        if member.id not in self.auth_data["blacklisted"][guild_key]:
            self.auth_data["blacklisted"][guild_key].append(member.id)
            self._save_auth_data(ctx.guild.id)
            embed = discord.Embed(
                title="🚫 User Blacklisted",
                description=f"{member.mention} has been blacklisted in this server.",
                color=discord.Color.dark_red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Effect", value="This user cannot use bot commands in this server.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"ℹ️ **{member.display_name}** is already blacklisted.")
    
    @commands.command(name="unblacklist")
    async def unblacklist_user(self, ctx, member: discord.Member = None):
        """Remove a user from blacklist (pick by name or pass a member)."""
        if not self.is_admin(ctx.author.id):
            embed = discord.Embed(title="❌ Access Denied", description="Only bot admins can manage the blacklist.", color=discord.Color.red())
            return await ctx.send(embed=embed)

        guild_key = str(ctx.guild.id)
        guild_blacklist = self.auth_data.get("blacklisted", {}).get(guild_key, [])

        if member is None:
            if not guild_blacklist:
                return await ctx.send("ℹ️ No blacklisted users in this server.")

            view = BlacklistPickerView(self, ctx, timeout=180)
            await ctx.send(embed=view.build_embed(), view=view)
            return

        if member.id in guild_blacklist:
            guild_blacklist.remove(member.id)
            self._save_auth_data(ctx.guild.id)
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

        guild_key = str(ctx.guild.id)
        guild_blacklist = self.auth_data.get("blacklisted", {}).get(guild_key, [])
        global_legacy = self.auth_data.get("blacklisted", {}).get("_global", [])
        users = sorted(set(guild_blacklist + global_legacy))

        if not users:
            return await ctx.send("ℹ️ No blacklisted users.")
        
        bl_list = []
        for user_id in users:
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
            self._save_auth_data(ctx.guild.id)
            
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
            self._save_auth_data(ctx.guild.id)
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
                await ctx.send(f"✅ Go to {channel.mention} and react on the verification message to claim your role.")
                return
        
        await ctx.send("❌ Self-verification is not set up. Ask a moderator to set it up with `!setupverify`.")
    
    # --- GUI Verification Setup ---
    
    @commands.command(name="setupverify")
    @commands.has_permissions(manage_guild=True)
    async def setup_verify(self, ctx, *args):
        """Setup multi verification roles. Example: !setupverify ✅ @Rev 🌐 @Web."""
        if not args:
            embed = discord.Embed(
                title="🔐 Setup Multi Verification",
                description=(
                    "Use emoji + role pairs.\n\n"
                    "**Examples**\n"
                    "`!setupverify ✅ @Verified`\n"
                    "`!setupverify 🧠 @Rev 🌐 @Web 🔥 @PWN`"
                ),
                color=discord.Color.blue(),
            )
            embed.add_field(
                name="📋 Available Roles",
                value="\n".join([f"• {r.mention}" for r in ctx.guild.roles[1:15] if not r.managed]) or "No roles found",
                inline=False,
            )
            embed.set_footer(text="The bot role must be higher than every role you assign.")
            return await ctx.send(embed=embed)

        parsed_pairs, err = self._parse_multi_verify_pairs(ctx, args)
        if err:
            return await ctx.send(err)

        guild_key = str(ctx.guild.id)
        old_cfg = self.auth_data.get("reaction_roles", {}).get(guild_key)
        if old_cfg:
            try:
                old_channel = ctx.guild.get_channel(old_cfg.get("channel_id"))
                if old_channel:
                    old_msg = await old_channel.fetch_message(old_cfg.get("message_id"))
                    await old_msg.delete()
            except Exception:
                pass

        lines = [f"{pair['emoji']} {pair['role'].mention}" for pair in parsed_pairs]

        verify_embed = discord.Embed(
            title="🧩 Category Role Selection",
            description=(
                f"Claim your roles in **{ctx.guild.name}** by reacting below.\n\n"
                + "\n".join(lines)
                + "\n\nRemove your reaction to remove the role."
            ),
            color=discord.Color.green(),
        )
        verify_embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        verify_embed.set_footer(text="You can change your roles anytime.")

        verify_msg = await ctx.send(embed=verify_embed)

        valid_options = []
        invalid = []
        for pair in parsed_pairs:
            emoji = pair["emoji"]
            role = pair["role"]
            try:
                await verify_msg.add_reaction(emoji)
                valid_options.append({"emoji": emoji, "role_id": role.id})
            except Exception:
                invalid.append(f"{emoji} -> {role.name}")

        if not valid_options:
            try:
                await verify_msg.delete()
            except Exception:
                pass
            return await ctx.send("❌ Failed to add reactions. Check emoji validity and bot permissions.")

        self.auth_data.setdefault("reaction_roles", {})
        self.auth_data["reaction_roles"][guild_key] = {
            "message_id": verify_msg.id,
            "channel_id": ctx.channel.id,
            "options": valid_options,
        }
        # Ensure the guild storage exists immediately.
        self.store.ensure_guild_db(guild_key)
        self._save_auth_data(ctx.guild.id)
        await self._flush_pending_saves(timeout=3.0)

        storage_label = self.store.storage_label(guild_key)
        try:
            storage_exists = self.store.storage_exists(guild_key)
        except Exception:
            storage_exists = False

        backend_name = getattr(self.store, "backend_name", "unknown")
        snapshot = await self._backup_auth_db_now("setupverify")

        confirm_embed = discord.Embed(
            title="✅ Verification Setup Complete!",
            description=f"Saved **{len(valid_options)}** emoji-role mappings in this server database.",
            color=discord.Color.green(),
        )
        if invalid:
            confirm_embed.add_field(
                name="⚠️ Skipped invalid mappings",
                value="\n".join(invalid[:10]),
                inline=False,
            )
        confirm_embed.add_field(
            name="💾 Storage",
            value=(
                f"Backend: **{backend_name}**\n"
                f"Location: `{storage_label}`\n"
                f"Status: {'Ready' if storage_exists else 'Pending write'}"
            ),
            inline=False,
        )
        if snapshot:
            confirm_embed.add_field(
                name="🗂️ Backup Snapshot",
                value=f"`{snapshot}`\nExists: `{snapshot.exists()}`",
                inline=False,
            )
        await ctx.send(embed=confirm_embed, delete_after=15)
    
    @commands.command(name="verifyinfo")
    async def verify_info(self, ctx):
        """Show verification system info."""
        guild_key = str(ctx.guild.id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        
        if not reaction_data:
            return await ctx.send("❌ Verification system is not set up. Use `!setupverify ✅ @Role` to set it up.")

        channel = ctx.guild.get_channel(reaction_data.get("channel_id"))
        options = reaction_data.get("options", [])
        option_lines = []
        for option in options:
            role = ctx.guild.get_role(option.get("role_id"))
            role_text = role.mention if role else f"`{option.get('role_id')}`"
            option_lines.append(f"{option.get('emoji', '✅')} {role_text}")
        
        embed = discord.Embed(
            title="🔐 Verification System Info",
            color=discord.Color.blue()
        )
        embed.add_field(name="📺 Channel", value=channel.mention if channel else "Not found", inline=True)
        embed.add_field(name="🆔 Message ID", value=str(reaction_data.get("message_id", "Unknown")), inline=True)
        embed.add_field(name="🧩 Role Mappings", value="\n".join(option_lines) if option_lines else "No mappings", inline=False)
        
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
            self._save_auth_data(ctx.guild.id)
            await ctx.send("✅ Verification system removed.")
        else:
            await ctx.send("ℹ️ No verification system is set up.")
    
    # --- Reaction Role Event Listener ---
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle reaction-based verification."""
        if payload.member and payload.member.bot:
            return
        
        guild_key = str(payload.guild_id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        
        if not reaction_data:
            return
        
        # Check if this is the verification message
        if payload.message_id != reaction_data.get("message_id"):
            return
        
        emoji_value = str(payload.emoji)
        role_id = None
        for option in reaction_data.get("options", []):
            if str(option.get("emoji")) == emoji_value:
                role_id = option.get("role_id")
                break
        if not role_id:
            return
        
        # Give the role
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = payload.member or guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        role_added = False
        if role not in member.roles:
            try:
                await member.add_roles(role)
                role_added = True
                # Send DM confirmation
                try:
                    await member.send(f"✅ You received **{role.name}** in **{guild.name}**.")
                except Exception:
                    pass
            except Exception as e:
                print(f"Failed to add verification role: {e}")

        # Persist verified user state in storage (SQLite/Firebase).
        if role_added or role in member.roles:
            self.auth_data.setdefault("verified_users", {})
            self.auth_data["verified_users"].setdefault(guild_key, [])
            if member.id not in self.auth_data["verified_users"][guild_key]:
                self.auth_data["verified_users"][guild_key].append(member.id)
                self._save_auth_data(payload.guild_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        """Remove mapped role when user removes reaction."""
        guild_key = str(payload.guild_id)
        reaction_data = self.auth_data.get("reaction_roles", {}).get(guild_key)
        if not reaction_data:
            return

        if payload.message_id != reaction_data.get("message_id"):
            return

        emoji_value = str(payload.emoji)
        role_id = None
        for option in reaction_data.get("options", []):
            if str(option.get("emoji")) == emoji_value:
                role_id = option.get("role_id")
                break
        if not role_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        role_removed = False
        if role in member.roles:
            try:
                await member.remove_roles(role)
                role_removed = True
            except Exception:
                pass

        if role_removed:
            verify_role_ids = set()
            for option in reaction_data.get("options", []):
                try:
                    verify_role_ids.add(int(option.get("role_id")))
                except Exception:
                    continue

            still_verified = any(r.id in verify_role_ids for r in member.roles)
            if not still_verified:
                guild_verified = self.auth_data.get("verified_users", {}).get(guild_key, [])
                if member.id in guild_verified:
                    guild_verified.remove(member.id)
                    self._save_auth_data(payload.guild_id)
    
    # --- Auth Admin Panel ---
    
    @commands.command(name="authpanel")
    @commands.has_permissions(administrator=True)
    async def auth_panel(self, ctx):
        """Show the authentication admin panel."""
        guild_key = str(ctx.guild.id)
        
        # Stats
        verified_count = len(self.auth_data.get("verified_users", {}).get(guild_key, []))
        whitelisted_count = len(self.auth_data.get("whitelisted", {}).get(guild_key, []))
        blacklisted_count = len(self.auth_data.get("blacklisted", {}).get(guild_key, []))
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
        verify_count = len(reaction_data.get("options", [])) if reaction_data else 0
        embed.add_field(
            name="🔐 Verification",
            value=f"Status: {verify_status}\nMappings: {verify_count}",
            inline=True
        )
        
        # Commands section
        embed.add_field(
            name="⚙️ Quick Commands",
            value="```\n"
                  "!setupverify ✅ @Role - Setup verification\n"
                  "!verifyinfo - View verify info\n"
                  "!removeverify - Remove system\n"
                  "!backupstatus - Show backup health\n"
                  "!backupnow - Create backup now\n"
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

    @commands.command(name="backupstatus")
    @commands.has_permissions(administrator=True)
    async def backup_status(self, ctx):
        """Show database and backup health (useful on Hugging Face)."""
        guild_key = str(ctx.guild.id)
        backend_name = getattr(self.store, "backend_name", "unknown")
        storage_label = self.store.storage_label(guild_key)
        try:
            storage_exists = self.store.storage_exists(guild_key)
        except Exception:
            storage_exists = False

        if not getattr(self.store, "uses_local_files", True):
            embed = discord.Embed(
                title="💾 Storage Status",
                color=discord.Color.blue(),
                timestamp=datetime.now(),
            )
            embed.add_field(name="Backend", value=f"**{backend_name}**", inline=False)
            embed.add_field(name="This Server Storage", value=f"`{storage_label}`\nExists: `{storage_exists}`", inline=False)
            collection_name = getattr(self.store, "collection_name", "")
            if collection_name:
                embed.add_field(
                    name="Collection",
                    value=f"`{collection_name}`",
                    inline=False,
                )
            embed.add_field(
                name="Backups",
                value="Local snapshot backup is disabled on Firebase backend.",
                inline=False,
            )
            return await ctx.send(embed=embed)

        db_root = Path(os.getenv("AUTH_DB_ROOT", str(self.store.root_dir)))
        backup_root_env = os.getenv("AUTH_DB_BACKUP_DIR", "")
        backup_root = Path(backup_root_env) if backup_root_env else None
        guild_db = self.store.guild_db_path(guild_key)

        snapshots = []
        latest_mirror = None
        if backup_root and backup_root.exists():
            snapshots = sorted(
                [p for p in backup_root.iterdir() if p.is_dir() and p.name.startswith("snapshot_")],
                key=lambda p: p.name,
                reverse=True,
            )
            latest_mirror = backup_root / "latest"

        embed = discord.Embed(
            title="💾 Backup Status",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )
        embed.add_field(
            name="Backend",
            value=f"**{backend_name}**",
            inline=False,
        )
        embed.add_field(
            name="DB Root",
            value=f"`{db_root}`\nExists: `{db_root.exists()}`",
            inline=False,
        )
        embed.add_field(
            name="This Server DB",
            value=f"`{guild_db}`\nExists: `{guild_db.exists()}`",
            inline=False,
        )
        if backup_root:
            embed.add_field(
                name="Backup Root",
                value=f"`{backup_root}`\nExists: `{backup_root.exists()}`",
                inline=False,
            )
            embed.add_field(
                name="Snapshots",
                value=f"Count: **{len(snapshots)}**\nLatest: `{snapshots[0].name if snapshots else 'None'}`",
                inline=False,
            )
            if latest_mirror is not None:
                embed.add_field(
                    name="Latest Mirror",
                    value=f"`{latest_mirror}`\nExists: `{latest_mirror.exists()}`",
                    inline=False,
                )
        else:
            embed.add_field(
                name="Backup Root",
                value="Not configured (`AUTH_DB_BACKUP_DIR` missing).",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="backupnow")
    @commands.has_permissions(administrator=True)
    async def backup_now(self, ctx):
        """Create backup snapshot immediately."""
        if not getattr(self.store, "uses_local_files", True):
            return await ctx.send("ℹ️ Local backup snapshots are only available on SQLite backend.")

        await self._flush_pending_saves(timeout=5.0)
        snapshot = await self._backup_auth_db_now("manual-command")
        if snapshot:
            await ctx.send(f"✅ Backup created: `{snapshot}`")
        else:
            await ctx.send("⚠️ Backup not created. Run `!backupstatus`.")
    
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
            self._save_auth_data(ctx.guild.id)
            
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
            self._save_auth_data(ctx.guild.id)
            
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
        embed.add_field(name="🚫 Blacklisted (this server)", value=str(len(self.auth_data.get("blacklisted", {}).get(guild_key, []))), inline=True)
        
        await ctx.send(embed=embed)
    
    # --- Only Me Mode ---
    
    @commands.command(name="onlyme")
    async def only_me_mode(self, ctx):
        """Lock text AI interactions to the command author only."""
        is_server_owner = bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)
        if not (self.is_admin(ctx.author.id) or is_server_owner):
            return await ctx.send("❌ Only bot admins or the server owner can enable `!onlyme`.")

        self.only_me_user_id = ctx.author.id
        await ctx.send(f"🔒 Text AI mode locked to {ctx.author.mention}.")

    @commands.command(name="openall", aliases=["exit"])
    async def open_all_mode(self, ctx):
        """Unlock text AI interactions for everyone (alias: !exit)."""
        is_server_owner = bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)
        can_unlock = (
            self.only_me_user_id is None
            or ctx.author.id == self.only_me_user_id
            or self.is_admin(ctx.author.id)
            or is_server_owner
        )
        if not can_unlock:
            return await ctx.send("❌ Only the current lock owner, bot admin, or server owner can run `!openall`.")

        self.only_me_user_id = None
        await ctx.send("🔓 Text AI mode unlocked for everyone.")

    

    
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
            overrides = self.auth_data.get("command_overrides", {}).get(str(ctx.guild.id), {})
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
        cmd_name = ctx.command.name if ctx.command else ""

        # 1. Check blacklist
        guild_id = ctx.guild.id if ctx.guild else None
        if self.is_blacklisted(ctx.author.id, guild_id):
            await ctx.send("❌ You are blacklisted from using this bot.")
            return False
        
        # 2. Check only_me mode
        if self.only_me_user_id is not None:
            is_lock_owner = ctx.author.id == self.only_me_user_id
            is_bot_admin = self.is_admin(ctx.author.id)
            is_server_owner = bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)

            # Always allow unlocking from the unlock command.
            if cmd_name == "openall" and (is_lock_owner or is_bot_admin or is_server_owner):
                return True

            if not is_lock_owner:
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
            guild_key = str(ctx.guild.id)
            self.auth_data.setdefault("blacklisted", {})
            self.auth_data["blacklisted"].setdefault(guild_key, [])

            # Case 1: !autokick @User -> Blacklist & Kick
            if member:
                # Add to blacklist
                if member.id not in self.auth_data["blacklisted"][guild_key]:
                    self.auth_data["blacklisted"][guild_key].append(member.id)
                    self._save_auth_data(ctx.guild.id)
                
                # Kick
                try:
                    await member.send("🚫 You have been auto-kicked and blacklisted.")
                    await member.kick(reason="Manual Auto-Kick by Admin")
                    await ctx.send(f"✅ **{member.display_name}** has been blacklisted and kicked.")
                except Exception as e:
                    await ctx.send(f"⚠️ Blacklisted **{member.display_name}**, but failed to kick: {e}")
                return

            # Case 2: !autokick (no arg) -> Show Status
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
        self._save_auth_data(ctx.guild.id)
        await ctx.send(f"✅ Auto Kick **ENABLED**. New accounts younger than **{days} days** will be kicked.")

    @autokick.command(name="off")
    async def autokick_off(self, ctx):
        """Disable auto-kick."""
        guild_key = str(ctx.guild.id)
        if "autokick" in self.auth_data and guild_key in self.auth_data["autokick"]:
            self.auth_data["autokick"][guild_key]["enabled"] = False
            self._save_auth_data(ctx.guild.id)
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
        guild_key = str(ctx.guild.id)
        self.auth_data.setdefault("blacklisted", {})
        self.auth_data["blacklisted"].setdefault(guild_key, [])

        if member.id in self.auth_data["blacklisted"][guild_key]:
            self.auth_data["blacklisted"][guild_key].remove(member.id)
            self._save_auth_data(ctx.guild.id)
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
        if self.is_blacklisted(member.id, member.guild.id):
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

    @commands.Cog.listener()
    async def on_ready(self):
        """
        On startup/reconnect:
        - Ensure storage entries exist for connected guilds.
        - Load from existing records if present.
        - Create missing records if needed.
        """
        if self._startup_bootstrap_done:
            return
        self._startup_bootstrap_done = True
        self._bootstrap_databases()

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Create storage record immediately for newly joined guild."""
        guild_key = str(guild.id)
        self._deleted_guilds.discard(guild_key)
        self.store.ensure_guild_db(guild_key)
        self._save_auth_data(guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Clean up per-server auth database when bot leaves a guild."""
        guild_key = str(guild.id)
        self._deleted_guilds.add(guild_key)

        for key in ("verified_users", "blacklisted", "whitelisted", "reaction_roles", "command_overrides", "autokick"):
            if guild_key in self.auth_data.get(key, {}):
                del self.auth_data[key][guild_key]

        with self._save_queue_lock:
            self._queued_save_ops.discard(("guild", guild_key))

        self.store.delete_guild(guild_key)
        print(f"🧹 Removed auth data for guild {guild.id}")

# Global bot check - add this listener to check ALL commands, not just this cog


# Global bot check - add this listener to check ALL commands, not just this cog
def setup_global_check(bot, auth_cog):
    """Setup a global command check for the entire bot."""
    @bot.check
    async def global_auth_check(ctx):
        cmd_name = ctx.command.name if ctx.command else ""

        # 1. Check only_me mode
        if auth_cog.only_me_user_id is not None:
            is_lock_owner = ctx.author.id == auth_cog.only_me_user_id
            is_bot_admin = auth_cog.is_admin(ctx.author.id)
            is_server_owner = bool(ctx.guild and ctx.author.id == ctx.guild.owner_id)

            if cmd_name == "openall" and (is_lock_owner or is_bot_admin or is_server_owner):
                return True

            if not is_lock_owner:
                return False
        
        # 2. Check blacklist
        guild_id = ctx.guild.id if ctx.guild else None
        if auth_cog.is_blacklisted(ctx.author.id, guild_id):
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
