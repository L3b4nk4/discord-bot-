"""
Hugging Face Spaces Entry Point
Handles DNS patching, Web Server (for keep-alive), Persistence, and Bot startup.
"""
import os
import socket
import asyncio
import signal
import aiohttp
from aiohttp import web
from dotenv import load_dotenv

# Keep-alive interval (1 minute - Aggressive to prevent HF sleep)
KEEP_ALIVE_INTERVAL = 60  # seconds

# --- 1. DNS Patching (Aggressive) ---
def setup_network_access():
    """
    Hugging Face Spaces often has broken DNS. 
    We attempt to fix this by writing directly to /etc/hosts.
    """
    print("DEBUG: Setting up network access (fixing /etc/hosts)...", flush=True)
    
    # Known working IPs
    mappings = [
        ("162.159.138.232", "discord.com"),
        ("162.159.138.232", "gateway.discord.gg"),
        ("149.154.166.110", "api.telegram.org"),
        ("172.217.21.10", "generativelanguage.googleapis.com"),
        ("172.217.21.10", "google.generativeai.com"),
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
    "discord.com": "162.159.138.232",
    "gateway.discord.gg": "162.159.138.232",
    "api.telegram.org": "149.154.166.110",
    "api.groq.com": "104.18.2.161",
    "voice.discord.media": "162.159.134.233"
}

def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        if host in DNS_MAP:
             print(f"🔧 DNS-V3 (getaddrinfo): Resolved {host} -> {DNS_MAP[host]}")
             return _orig_getaddrinfo(DNS_MAP[host], port, family, type, proto, flags)
        return _orig_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        if host in DNS_MAP:
            return [(2, 1, 6, '', (DNS_MAP[host], port))]
        raise

def patched_gethostbyname(hostname):
    if hostname in DNS_MAP:
        return DNS_MAP[hostname]
    return _orig_gethostbyname(hostname)

socket.getaddrinfo = patched_getaddrinfo
socket.gethostbyname = patched_gethostbyname

# --- 2. Import Bot ---
# Import AFTER patching to ensure libraries use patched socket
from bot import MangaBot
from services.telegram_service import TelegramService

# --- 3. Persistence Setup ---
def setup_persistence():
    """Link local data files to persistent storage if available."""
    # HF Spaces with persistent storage mount to /data
    PERSISTENT_DIR = "/data"
    DATA_FILES = ["auth_data.json"]
    
    if os.path.exists(PERSISTENT_DIR) and os.access(PERSISTENT_DIR, os.W_OK):
        print(f"💾 Persistent storage found at {PERSISTENT_DIR}")
        for filename in DATA_FILES:
            persistent_path = os.path.join(PERSISTENT_DIR, filename)
            local_path = os.path.join(os.getcwd(), filename)
            
            # If persistent file doesn't exist but local does, copy local -> persistent (init)
            if not os.path.exists(persistent_path) and os.path.exists(local_path):
                print(f"Moving initial {filename} to persistence...")
                import shutil
                shutil.copy2(local_path, persistent_path)
            
            # If symlink doesn't exist, create it
            if not os.path.islink(local_path):
                if os.path.exists(local_path):
                    os.remove(local_path) # Remove local file to replace with link
                os.symlink(persistent_path, local_path)
                print(f"🔗 Linked {local_path} -> {persistent_path}")
    else:
        print("⚠️ No persistent storage found. Data will be lost on restart.")


# --- 4. Main Application ---
async def web_server():
    """Simple web server to satisfy HF health checks."""
    app = web.Application()
    app.add_routes([web.get('/', lambda r: web.Response(text="Manga Bot Active 🤖"))])
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
    
    while True:
        try:
            await asyncio.sleep(KEEP_ALIVE_INTERVAL)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        print(f"💓 Keep-alive ping successful (status: {resp.status})")
                    else:
                        print(f"⚠️ Keep-alive ping returned status: {resp.status}")
        except asyncio.CancelledError:
            print("💔 Keep-alive task cancelled")
            break
        except Exception as e:
            print(f"⚠️ Keep-alive ping failed: {e} - retrying in {KEEP_ALIVE_INTERVAL}s")

async def main():
    load_dotenv()
    setup_persistence()
    
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ FATAL: DISCORD_TOKEN not found.")
        print("ℹ️  Please add DISCORD_TOKEN to your Space secrets.")
        # Sleep to keep logs visible
        while True:
            await asyncio.sleep(60)
        return

    # Initialize Bot
    # This initializes AIService (Gemini/Groq) internally
    bot = MangaBot() 
    
    # Initialize Telegram Service
    # We pass the bot instance and its AI service
    telegram_service = TelegramService(bot, bot.ai_service)
    await telegram_service.start()

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
    print("💓 Keep-alive mechanism activated (pings every 5 minutes)")

    # Start Bot
    print("🚀 Starting Discord Bot...")
    try:
        await bot.start(token)
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
    finally:
        keep_alive.cancel()
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
