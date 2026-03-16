"""
Hugging Face Spaces Entry Point
Handles DNS patching, Web Server (for keep-alive), Persistence, and Bot startup.
"""
import os
import socket
import asyncio
import signal
import aiohttp
import shutil
import json
import time
import urllib.request
import random
from pathlib import Path
from datetime import datetime
from aiohttp import web
from dotenv import load_dotenv

# Keep-alive interval (seconds) - configurable for platform limits
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "60"))
DISCORD_RETRY_DELAY = max(5, int(os.getenv("DISCORD_RETRY_DELAY", "20")))
DISCORD_MAX_RETRIES = max(
    0, int(os.getenv("DISCORD_MAX_RETRIES", "0")))  # 0 = infinite
DISCORD_RETRY_MAX_DELAY = max(DISCORD_RETRY_DELAY, int(
    os.getenv("DISCORD_RETRY_MAX_DELAY", "300")))
DISCORD_RETRY_BACKOFF = max(1.0, float(
    os.getenv("DISCORD_RETRY_BACKOFF", "1.7")))
DISCORD_RETRY_JITTER = max(0.0, float(
    os.getenv("DISCORD_RETRY_JITTER", "0.15")))


def _env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


FORCE_STATIC_DNS = _env_enabled("FORCE_STATIC_DNS", "0")
DNS_DOH_FALLBACK = _env_enabled("DNS_DOH_FALLBACK", "1")
DNS_DOH_CACHE_TTL = max(30, int(os.getenv("DNS_DOH_CACHE_TTL", "300")))
_DNS_DOH_CACHE = {}
_DNS_RR_STATE = {}
_DOH_ENDPOINTS = (
    ("https://1.1.1.1/dns-query?name={name}&type=A",
     {"accept": "application/dns-json"}),
    ("https://dns.google/resolve?name={name}&type=A",
     {"accept": "application/dns-json"}),
)

# --- 1. DNS Patching (Aggressive) ---


def setup_network_access():
    """
    Hugging Face Spaces often has broken DNS. 
    We attempt to fix this by writing directly to /etc/hosts.
    """
    if not FORCE_STATIC_DNS:
        print("ℹ️ Static DNS patch disabled. Using system DNS resolution.", flush=True)
        return

    print("DEBUG: Setting up network access (fixing /etc/hosts)...", flush=True)

    # Known working IPs
    mappings = [
        ("162.159.135.232", "discord.com"),
        ("162.159.135.232", "gateway.discord.gg"),
        ("162.159.135.232", "voice.discord.media"),
        ("149.154.167.220", "api.telegram.org"),
        ("104.18.2.161", "api.groq.com"),
        ("172.217.21.10", "generativelanguage.googleapis.com"),
        ("172.217.21.10", "google.generativeai.com"),
        ("8.8.8.8", "dns.google"),
        # Rotterdam voice servers (often used)
        ("35.214.219.205", "rotterdam.discord.gg"),
        ("162.159.129.233", "voice.discord.media"),
        ("162.159.130.233", "voice.discord.media"),
        ("162.159.133.233", "voice.discord.media"),
        ("162.159.134.233", "voice.discord.media")
    ]

    try:
        with open("/etc/hosts", "a") as f:
            f.write("\n# Added by Bot for DNS Fix\n")
            for ip, host in mappings:
                f.write(f"{ip} {host}\n")
        print("✅ Successfully patched /etc/hosts", flush=True)
    except PermissionError:
        print("⚠️ Could not write to /etc/hosts (Permission Denied). DNS might fail.", flush=True)
    except Exception as e:
        print(f"⚠️ Failed to patch /etc/hosts: {e}", flush=True)


# Run setup immediately
setup_network_access()

# Fallback: Python Socket Patch (in case /etc/hosts fails)
_orig_getaddrinfo = socket.getaddrinfo
_orig_gethostbyname = socket.gethostbyname

DNS_MAP = {
    "discord.com": ["162.159.135.232", "162.159.138.232"],
    "gateway.discord.gg": ["162.159.135.232", "162.159.138.232"],
    "voice.discord.media": ["162.159.135.232", "162.159.134.233"],
    "api.telegram.org": "149.154.167.220",
    "api.groq.com": "104.18.2.161",
    "generativelanguage.googleapis.com": "172.217.21.10",
    "dns.google": "8.8.8.8",
}


def _normalize_host(host) -> str:
    """Normalize host keys so DNS fallback works reliably."""
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", "ignore")
        except Exception:
            host = str(host)
    host = str(host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.rstrip(".")


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except Exception:
        return False


def _dns_targets(host: str):
    target = DNS_MAP.get(_normalize_host(host))
    if target is None:
        return []
    if isinstance(target, (list, tuple)):
        return [str(x) for x in target if x]
    return [str(target)]


def _resolve_via_doh(host: str):
    host_key = _normalize_host(host)
    if not DNS_DOH_FALLBACK or not host_key or _is_ipv4(host_key):
        return []

    now = time.time()
    cached = _DNS_DOH_CACHE.get(host_key)
    if cached and now - cached["ts"] < DNS_DOH_CACHE_TTL:
        return list(cached["ips"])

    ips = []
    for url_template, headers in _DOH_ENDPOINTS:
        url = url_template.format(name=host_key)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
            answers = payload.get("Answer") or []
            for answer in answers:
                if int(answer.get("type", 0)) != 1:
                    continue
                ip = str(answer.get("data", "")).strip()
                if _is_ipv4(ip):
                    ips.append(ip)
            if ips:
                break
        except Exception:
            continue

    deduped = []
    seen = set()
    for ip in ips:
        if ip in seen:
            continue
        seen.add(ip)
        deduped.append(ip)

    _DNS_DOH_CACHE[host_key] = {"ts": now, "ips": deduped}
    if deduped:
        print(f"🌐 DoH resolved {host_key}: {', '.join(deduped[:5])}")
    return deduped


def _combined_dns_targets(host: str):
    host_key = _normalize_host(host)
    dynamic_targets = _resolve_via_doh(host_key)
    static_targets = _dns_targets(host_key)

    combined = []
    seen = set()
    for ip in dynamic_targets + static_targets:
        if not ip or ip in seen:
            continue
        seen.add(ip)
        combined.append(ip)

    if not combined:
        return combined

    idx = _DNS_RR_STATE.get(host_key, 0) % len(combined)
    _DNS_RR_STATE[host_key] = idx + 1
    return combined[idx:] + combined[:idx]


def _resolve_via_dns_map(host, port, family=0, type=0, proto=0, flags=0):
    targets = _combined_dns_targets(host)
    if not targets:
        raise socket.gaierror(f"No DNS_MAP target for host: {host}")

    for ip in targets:
        try:
            print(f"🔧 DNS-V3 (fallback): Resolved {host} -> {ip}")
            return _orig_getaddrinfo(ip, port, family, type, proto, flags)
        except socket.gaierror:
            continue

    # Last-resort synthetic IPv4 answer if resolver still rejects literal IP lookups.
    return [(2, 1, 6, "", (targets[0], port))]


def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if not isinstance(host, str):
        host_key = _normalize_host(host)
    else:
        host_key = _normalize_host(host)

    try:
        if FORCE_STATIC_DNS and host_key in DNS_MAP:
            return _resolve_via_dns_map(host_key, port, family, type, proto, flags)
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if host_key in DNS_MAP:
            print(
                f"⚠️ DNS lookup failed for {host_key}; using static fallback map.")
            return _resolve_via_dns_map(host_key, port, family, type, proto, flags)
        raise


def patched_gethostbyname(hostname):
    hostname_key = _normalize_host(hostname)
    if FORCE_STATIC_DNS and hostname_key in DNS_MAP:
        targets = _dns_targets(hostname_key)
        if targets:
            return targets[0]
    try:
        return _orig_gethostbyname(hostname)
    except socket.gaierror:
        if hostname_key in DNS_MAP:
            targets = _dns_targets(hostname_key)
            if targets:
                print(
                    f"⚠️ DNS gethostbyname failed for {hostname_key}; using static fallback {targets[0]}.")
                return targets[0]
        raise


# Always install patched resolvers:
# - FORCE_STATIC_DNS=1: always route mapped hosts through static IPs.
# - FORCE_STATIC_DNS=0: use system DNS first, static map only on DNS failure.
socket.getaddrinfo = patched_getaddrinfo
socket.gethostbyname = patched_gethostbyname

# --- 2. Import Bot ---
# Import AFTER patching to ensure libraries use patched socket


def _get_bot_class():
    # Import lazily so Discord networking uses patched resolvers.
    from bot import MangaBot
    return MangaBot

# --- 3. Persistence Setup ---


def _has_db_files(db_root: Path) -> bool:
    return any(db_root.rglob("*.db"))


def _is_huggingface_space() -> bool:
    return bool(os.getenv("SPACE_ID") or os.getenv("SPACE_HOST") or os.getenv("HF_SPACE_ID"))


def _resolve_firebase_credentials_path() -> str:
    for env_name in ("FIREBASE_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS"):
        configured = os.getenv(env_name, "").strip()
        if not configured:
            continue
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return str(candidate)

    matches = sorted(Path(os.getcwd()).glob("*firebase-adminsdk*.json"))
    if matches:
        return str(matches[0])
    return ""


def _auth_uses_firebase() -> bool:
    forced_backend = os.getenv("AUTH_STORAGE_BACKEND", "").strip().lower()
    if forced_backend in {"firebase", "firestore", "firebase-firestore"}:
        return True
    if forced_backend in {"sqlite", "local"}:
        return False
    return bool(_resolve_firebase_credentials_path())


def _latest_backup_snapshot(backup_root: Path):
    if not backup_root.exists():
        return None
    snapshots = sorted(
        [p for p in backup_root.iterdir() if p.is_dir()
         and p.name.startswith("snapshot_")],
        key=lambda p: p.name,
        reverse=True,
    )
    return snapshots[0] if snapshots else None


def _restore_auth_from_latest_backup(db_root: Path, backup_root: Path):
    """If DB root is empty/missing, restore it from the latest backup snapshot."""
    db_root.mkdir(parents=True, exist_ok=True)
    if _has_db_files(db_root):
        return

    latest = _latest_backup_snapshot(backup_root)
    if not latest:
        return

    shutil.copytree(latest, db_root, dirs_exist_ok=True)
    print(f"♻️ Restored auth DB from backup snapshot: {latest}")


def _create_auth_backup_snapshot(db_root: Path, backup_root: Path, keep: int):
    """Create a snapshot backup of the auth DB directory and prune old snapshots."""
    if not db_root.exists() or not _has_db_files(db_root):
        return None

    backup_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    tmp_snapshot = backup_root / f".tmp_snapshot_{ts}"
    final_snapshot = backup_root / f"snapshot_{ts}"

    shutil.copytree(db_root, tmp_snapshot, dirs_exist_ok=True)
    tmp_snapshot.rename(final_snapshot)

    snapshots = sorted(
        [p for p in backup_root.iterdir() if p.is_dir()
         and p.name.startswith("snapshot_")],
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


def setup_persistence():
    """Configure auth DB root + backup root."""
    firebase_auth = _auth_uses_firebase()
    if firebase_auth:
        os.environ.setdefault("AUTH_STORAGE_BACKEND", "firebase-firestore")
        cred_path = _resolve_firebase_credentials_path()
        if cred_path:
            print(
                f"☁️ Firebase auth backend detected (credentials: {cred_path})")
        else:
            print("☁️ Firebase auth backend detected via AUTH_STORAGE_BACKEND")
        print("☁️ Local auth DB backup loop is disabled for Firebase backend.")

    PERSISTENT_DIR = "/data"
    LOCAL_DB_DIR = os.path.join(os.getcwd(), "auth_db")
    LEGACY_JSON = "auth_data.json"
    hf_mode = _is_huggingface_space()
    has_persistent_dir = os.path.exists(
        PERSISTENT_DIR) and os.access(PERSISTENT_DIR, os.W_OK)
    allow_non_persistent_hf_db = os.getenv(
        "ALLOW_NON_PERSISTENT_DB", "0").lower() in {"1", "true", "yes", "on"}

    configured_db_root = os.getenv("AUTH_DB_ROOT")
    if hf_mode and has_persistent_dir and not allow_non_persistent_hf_db:
        db_root = os.path.join(PERSISTENT_DIR, "auth_db")
        os.makedirs(db_root, exist_ok=True)
        os.environ["AUTH_DB_ROOT"] = db_root
        print(
            f"🤗 HF mode detected: forcing persistent auth DB root to {db_root}")
    elif configured_db_root:
        db_root = configured_db_root
        os.makedirs(db_root, exist_ok=True)
        print(f"🗄️ Auth DB root (configured): {db_root}")
    elif has_persistent_dir:
        print(f"💾 Persistent storage found at {PERSISTENT_DIR}")
        db_root = os.path.join(PERSISTENT_DIR, "auth_db")
        os.makedirs(db_root, exist_ok=True)
        os.environ["AUTH_DB_ROOT"] = db_root
        print(f"🗄️ Auth DB root set to {db_root}")

        # Backward compatibility: make persisted legacy JSON visible for one-time migration.
        persistent_legacy = os.path.join(PERSISTENT_DIR, LEGACY_JSON)
        local_legacy = os.path.join(os.getcwd(), LEGACY_JSON)
        if os.path.exists(persistent_legacy) and not os.path.exists(local_legacy):
            shutil.copy2(persistent_legacy, local_legacy)
            print(f"📦 Copied legacy {LEGACY_JSON} for migration")
    else:
        print("⚠️ No persistent storage found. Data will be lost on restart.")
        db_root = LOCAL_DB_DIR
        os.makedirs(db_root, exist_ok=True)
        os.environ.setdefault("AUTH_DB_ROOT", db_root)
        print(f"🗄️ Using local auth DB root: {os.environ['AUTH_DB_ROOT']}")

    configured_backup_root = os.getenv("AUTH_DB_BACKUP_DIR")
    if hf_mode and has_persistent_dir and not allow_non_persistent_hf_db:
        backup_root = os.path.join(PERSISTENT_DIR, "auth_db_backup")
    elif configured_backup_root:
        backup_root = configured_backup_root
    elif has_persistent_dir:
        backup_root = os.path.join(PERSISTENT_DIR, "auth_db_backup")
    else:
        backup_root = os.path.join(os.getcwd(), "auth_db_backup")
    os.makedirs(backup_root, exist_ok=True)
    os.environ["AUTH_DB_BACKUP_DIR"] = backup_root
    print(f"💾 Auth DB backup root: {backup_root}")
    if hf_mode and not has_persistent_dir:
        print("⚠️ HF mode detected but /data is not writable. Enable Persistent Storage in Space settings.")

    _restore_auth_from_latest_backup(Path(db_root), Path(backup_root))


async def auth_backup_task():
    """Periodic backup of auth DB root to external backup directory."""
    if _auth_uses_firebase():
        print("☁️ Auth DB backup task disabled (Firebase backend in use).")
        return

    interval = int(os.getenv("AUTH_DB_BACKUP_INTERVAL", "21600"))
    keep = int(os.getenv("AUTH_DB_BACKUP_KEEP", "48"))
    if interval <= 0:
        print("💾 Auth DB backup task disabled (AUTH_DB_BACKUP_INTERVAL <= 0)")
        return

    db_root = Path(
        os.getenv("AUTH_DB_ROOT", os.path.join(os.getcwd(), "auth_db")))
    backup_root = Path(os.getenv("AUTH_DB_BACKUP_DIR",
                       os.path.join(os.getcwd(), "auth_db_backup")))
    print(f"💾 Auth DB backup task started: every {interval}s -> {backup_root}")

    try:
        while True:
            snapshot = await asyncio.to_thread(
                _create_auth_backup_snapshot, db_root, backup_root, max(
                    1, keep)
            )
            if snapshot:
                print(f"💾 Auth DB backup snapshot created: {snapshot.name}")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        print("💾 Auth DB backup task cancelled")
        raise


# --- 4. Main Application ---
async def web_server():
    """Simple web server to satisfy HF health checks."""
    app = web.Application()
    app.add_routes(
        [web.get('/', lambda r: web.Response(text="Manga Bot Active 🤖"))])
    app.add_routes([web.get('/health', lambda r: web.Response(text="OK"))])
    return app


async def keep_alive_task(port: int):
    """
    Self-ping task to prevent Hugging Face Spaces from shutting down due to inactivity.
    HF monitors HTTP activity - this task ensures continuous activity.
    """
    # Get the Space URL from environment or use localhost
    space_host = os.getenv("SPACE_HOST")  # HF sets this automatically
    if space_host:
        url = f"https://{space_host}/health"
    else:
        url = f"http://localhost:{port}/health"

    print(f"💓 Keep-alive task started. Pinging: {url}")

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    await asyncio.sleep(KEEP_ALIVE_INTERVAL)
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            print(
                                f"💓 Keep-alive ping successful (status: {resp.status})")
                        else:
                            print(
                                f"⚠️ Keep-alive ping returned status: {resp.status}")
                except asyncio.CancelledError:
                    print("ℹ️ Keep-alive task stopped")
                    raise
                except Exception as e:
                    print(
                        f"⚠️ Keep-alive ping failed: {e} - retrying in {KEEP_ALIVE_INTERVAL}s")
    except asyncio.CancelledError:
        # Preserve cancellation so callers can await task shutdown cleanly.
        raise


def _is_non_retryable_discord_error(message: str) -> bool:
    lower = (message or "").lower()
    fatal_markers = (
        "improper token",
        "401 unauthorized",
        "privileged intents",
        "disallowed intents",
    )
    if "cannot connect to host" in lower or "operation not permitted" in lower:
        return False
    return any(marker in lower for marker in fatal_markers)


def _compute_discord_retry_delay(attempt: int) -> int:
    """Compute reconnect delay with exponential backoff and capped jitter."""
    if attempt <= 1:
        base = float(DISCORD_RETRY_DELAY)
    else:
        base = float(DISCORD_RETRY_DELAY) * \
            (DISCORD_RETRY_BACKOFF ** (attempt - 1))
    base = min(base, float(DISCORD_RETRY_MAX_DELAY))

    if DISCORD_RETRY_JITTER <= 0:
        return max(1, int(round(base)))

    window = base * DISCORD_RETRY_JITTER
    low = max(1.0, base - window)
    high = max(low, base + window)
    return int(round(random.uniform(low, high)))


async def main():
    load_dotenv()
    setup_persistence()

    # Start Web Server
    port = int(os.getenv("PORT", 7860))
    app = await web_server()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Web server running on port {port}")

    # Start Keep-Alive Task (prevents HF Spaces inactivity shutdown)
    keep_alive = asyncio.create_task(keep_alive_task(port))
    print(
        f"💓 Keep-alive mechanism activated (pings every {KEEP_ALIVE_INTERVAL}s)")
    backup_task = asyncio.create_task(auth_backup_task())

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ FATAL: DISCORD_TOKEN not found.")
        print("ℹ️  Please add DISCORD_TOKEN to your Space secrets.")
        # Keep server alive so HF health checks pass and logs stay visible
        try:
            while True:
                await asyncio.sleep(60)
        finally:
            backup_task.cancel()
            keep_alive.cancel()
            await asyncio.gather(backup_task, keep_alive, return_exceptions=True)
            await runner.cleanup()
        return

    # Start bot with retry loop so transient network failures don't kill the container.
    attempt = 0
    try:
        while True:
            attempt += 1
            MangaBot = _get_bot_class()
            bot = MangaBot()  # Initializes AI + voice services for each connection attempt
            print(f"🚀 Starting Discord Bot... (attempt {attempt})")
            try:
                await bot.start(token)
                print("ℹ️ Discord bot stopped cleanly.")
                break
            except Exception as e:
                err_msg = str(e)
                print(f"❌ Bot crashed: {err_msg}")

                if _is_non_retryable_discord_error(err_msg):
                    print("❌ Non-retryable Discord error. Exiting bot loop.")
                    break

                if DISCORD_MAX_RETRIES > 0 and attempt >= DISCORD_MAX_RETRIES:
                    print(
                        f"❌ Reached DISCORD_MAX_RETRIES={DISCORD_MAX_RETRIES}. Exiting bot loop.")
                    break

                if "operation not permitted" in err_msg.lower() and FORCE_STATIC_DNS:
                    print(
                        "⚠️ Connection blocked while FORCE_STATIC_DNS is enabled. Try FORCE_STATIC_DNS=0.")

                retry_delay = _compute_discord_retry_delay(attempt)
                print(f"🔁 Retrying Discord connection in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
            finally:
                try:
                    if not bot.is_closed():
                        await bot.close()
                except Exception as e:
                    print(f"⚠️ Bot cleanup failed: {e}")
    finally:
        backup_task.cancel()
        keep_alive.cancel()
        await asyncio.gather(backup_task, keep_alive, return_exceptions=True)
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
