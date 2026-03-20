# ......................................................................
# Minecraft Discord Monitor Bot for NAIT
# Content_Farmer
# ver 1.3.1

#

#ideas:
#   idk 
# 

# ......................................................................

# ............. IMPORTS .........................
import sys
import subprocess
import importlib
import os
import socket
import time
import asyncio
import threading
import json
import struct
from datetime import datetime
import tkinter as tk
from tkinter import messagebox
import discord
from discord.ext import commands
from mcstatus import JavaServer, BedrockServer

#.............. Auto Install Dependencies .................................
REQUIRED_PACKAGES = {
    "discord": "discord.py",
    "mcstatus": "mcstatus"
}

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

for mod, pkg in REQUIRED_PACKAGES.items():
    try:
        importlib.import_module(mod)
    except ImportError:
        install(pkg)


# ............ Server configs ................................................................
ID_FILE = "device_ids.json"


def get_runtime_dir():
    """Return a stable directory for runtime files in both script and bundled modes."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "TOKEN": "replace_with_discord_bot_token",
    "CHANNEL_ID": 0,
    "PING_USER_ID": 0,
    "WHITELIST_PING_ROLE_ID": 0,
    "WHITELIST_APPROVAL_CHANNEL_ID": 0,
    "WHITELIST_USER_ANSWER_CHANNEL" : 0,
    "MC_ADDRESS": "127.0.0.1",
    "MC_PORT": 25565,
    "RCON_HOST": "127.0.0.1",
    "RCON_PORT": 25575,
    "RCON_PASSWORD": "replace_with_rcon_password",
    "SERVER_MAC": "AA:BB:CC:DD:EE:FF",
}

REQUIRED_CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())


def _write_default_config(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)


def load_runtime_config():
    config_path = os.path.join(get_runtime_dir(), ID_FILE)

    if not os.path.exists(config_path):
        _write_default_config(config_path)
        raise RuntimeError(
            f"Created {ID_FILE}. Fill in your device values (token/password/IDs) and restart the bot."
        )

    with open(config_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    missing = [key for key in REQUIRED_CONFIG_KEYS if key not in loaded]
    if missing:
        raise RuntimeError(
            f"Missing keys in {ID_FILE}: {', '.join(missing)}"
        )

    return loaded


runtime_config = load_runtime_config()

TOKEN = runtime_config["TOKEN"]
CHANNEL_ID = int(runtime_config["CHANNEL_ID"])
PING_USER_ID = int(runtime_config["PING_USER_ID"])
WHITELIST_PING_ROLE_ID = int(runtime_config["WHITELIST_PING_ROLE_ID"])
WHITELIST_APPROVAL_CHANNEL_ID = int(runtime_config["WHITELIST_APPROVAL_CHANNEL_ID"])
WHITELIST_USER_ANSWER_CHANNEL = int(runtime_config["WHITELIST_USER_END_CHANNEL"])

MC_ADDRESS = runtime_config["MC_ADDRESS"]
MC_PORT = int(runtime_config["MC_PORT"])

RCON_HOST = runtime_config["RCON_HOST"]
RCON_PORT = int(runtime_config["RCON_PORT"])
RCON_PASSWORD = runtime_config["RCON_PASSWORD"]

CHECK_INTERVAL = 60
MAX_BACKOFF = 300

STATE_FILE = "server_state.json"

bot = None
monitor_task = None
running = False
backoff_delay = 5

#........... Server State ......................................................
default_state = {"online": None, "last_up": None, "last_down": None} # checks when the server was last up and done based off the state file

# Writes that file to your computer
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except:
        state = default_state.copy()
else:
    state = default_state.copy()

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ........... Networking ....................................................................
def tcp_check(host, port, timeout=5):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except:
        return False

def _format_player_names(sample):
    if not sample:
        return []

    names = []
    for player in sample:
        name = getattr(player, "name", None)
        if name:
            names.append(name)
    return names

def detect_server_status():
    if not tcp_check(MC_ADDRESS, MC_PORT):

     return {
            "online": False,
            "mode": "OFFLINE",
            "players": None,
            "player_names": [],
            "ping": None,
        }

    try:
        s = JavaServer(MC_ADDRESS, MC_PORT).status()
        return {
            "online": True,
            "mode": "JAVA",
            "players": f"{s.players.online}/{s.players.max}",
            "ping": int(s.latency),
            "player_names": _format_player_names(getattr(s.players, "sample", [])),
        }
    except:
        pass

    try:
        b = BedrockServer.lookup(f"{MC_ADDRESS}:{MC_PORT}").status()
        return {
            "online": True,
            "mode": "BEDROCK",
            "players": f"{b.players_online}/{b.players_max}",
            "player_names": [],
            "ping": int(b.latency)
        }
    except:
        pass
        

    return {
        "online": True,
        "mode": "LIMITED",
        "players": None,
        "player_names": [],
        "ping": None,
    }
   

# .......... SERVER RESTART (NOT ACTIVE) ...................................................
SERVER_MAC = runtime_config["SERVER_MAC"]
RESTART_ATTEMPTS = 6
RESTART_CHECK_DELAY = 10
START_BAT_NAME = "start.bat"
START_BAT_PROCESS = None


def close_start_bat_process():
    global START_BAT_PROCESS
    if START_BAT_PROCESS is None:
        return True, "No tracked start.bat process to close"

    if START_BAT_PROCESS.poll() is not None:
        START_BAT_PROCESS = None
        return True, "Tracked start.bat process already stopped"

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(START_BAT_PROCESS.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            START_BAT_PROCESS.terminate()
            START_BAT_PROCESS.wait(timeout=10)
    except Exception as e:
        return False, f"Failed to close tracked start.bat process: {e}"
    finally:
        START_BAT_PROCESS = None

    return True, "Closed tracked start.bat process"

def locate_start_bat():
    """Find start.bat in common/surface-level locations."""
    candidates = []

    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, START_BAT_NAME))
    candidates.append(os.path.join(os.getcwd(), START_BAT_NAME))

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    # Surface-level search: current folder + immediate child folders.
    try:
        with os.scandir(os.getcwd()) as entries:
            for entry in entries:
                if entry.is_file() and entry.name.lower() == START_BAT_NAME:
                    return entry.path
                if entry.is_dir():
                    nested = os.path.join(entry.path, START_BAT_NAME)
                    if os.path.isfile(nested):
                        return nested
    except Exception:
        pass

    return None

def run_start_bat():
    global START_BAT_PROCESS
    start_bat_path = locate_start_bat()
    if not start_bat_path:
        return False, (
            f"Could not find {START_BAT_NAME}. Checked script folder/current folder, "
            "then ran a surface-level search in immediate subfolders."
        )

    try:
        if os.name == "nt":
            START_BAT_PROCESS = subprocess.Popen(["cmd", "/c", start_bat_path], cwd=os.path.dirname(start_bat_path))
        else:
            START_BAT_PROCESS = subprocess.Popen([start_bat_path], cwd=os.path.dirname(start_bat_path))
        return True, f"Executed {start_bat_path}"
    except Exception as e:
        return False, f"Failed to execute {start_bat_path}: {e}"

def attempt_server_restart():
    try:
        mac = SERVER_MAC.replace(":", "").replace("-", "")
        data = bytes.fromhex("FF"*6 + mac*16)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(data, ("<broadcast>", 9))
        sock.close()
        log("Wake-on-LAN packet sent.")
    except Exception as e:
        log(f"Restart failed: {e}")

def restart_with_retries():
    closed, close_msg = close_start_bat_process()
    if not closed:
        return False, None, 0, close_msg

    log(close_msg)

    started, start_msg = run_start_bat()
    if not started:
        return False, None, 0, start_msg

    log(start_msg)
    attempt_server_restart()

    for attempt in range(1, RESTART_ATTEMPTS + 1):
        time.sleep(RESTART_CHECK_DELAY)
        result = detect_server_status()

        if result["online"]:
            return True, result, attempt, start_msg

    return False, None, RESTART_ATTEMPTS, start_msg


def describe_status(result):
    details = []

    if result.get("players"):
        details.append(f"Players: {result['players']}")

    if result.get("player_names"):
        details.append(f"Names: {', '.join(result['player_names'])}")

    if result.get("ping") is not None:
        details.append(f"Ping: {result['ping']}ms")

    return " | ".join(details) if details else "No extra details"


# .......... RCON whitelist helper ...................................................................
RCON_AUTH = 3
RCON_COMMAND = 2

def _rcon_packet(req_id, packet_type, body):
    payload = body.encode("utf-8") + b"\x00\x00"
    size = 4 + 4 + len(payload)
    return struct.pack("<iii", size, req_id, packet_type) + payload

def _rcon_recv(sock):
    size_data = sock.recv(4)
    if len(size_data) < 4:
        raise RuntimeError("RCON did not return a full packet header")

    size = struct.unpack("<i", size_data)[0]
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RuntimeError("RCON connection closed unexpectedly")
        data += chunk

    req_id, packet_type = struct.unpack("<ii", data[:8])
    body = data[8:-2].decode("utf-8", errors="replace")
    return req_id, packet_type, body

def run_rcon_command(command):
    with socket.create_connection((RCON_HOST, RCON_PORT), timeout=8) as sock:
        sock.sendall(_rcon_packet(1, RCON_AUTH, RCON_PASSWORD))
        auth_id, _, _ = _rcon_recv(sock)
        if auth_id == -1:
            raise RuntimeError("RCON authentication failed. Check RCON_PASSWORD.")

        sock.sendall(_rcon_packet(2, RCON_COMMAND, command))
        _, _, response = _rcon_recv(sock)
        return response.strip()

def whitelist_player(username):
    run_rcon_command("whitelist on")
    output = run_rcon_command(f"whitelist add {username}")
    run_rcon_command("whitelist reload")
    return output or f"Whitelist command sent for '{username}'."

def white_list_approved(self, username, requester_id, approve):
    return (f"{WHITELIST_USER_ANSWER_CHANNEL}")


def has_whitelist_approval_role(user):
    return any(getattr(role, "id", None) == WHITELIST_PING_ROLE_ID for role in getattr(user, "roles", []))


def whitelist_ping_mention():
    return f"<@&{WHITELIST_PING_ROLE_ID}>"
class WhitelistApprovalView(discord.ui.View):
    def __init__(self, username, requester_id):
        super().__init__(timeout=None)
        self.username = username
        self.requester_id = requester_id



    @discord.ui.button(label="Approve whitelist", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_whitelist(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not has_whitelist_approval_role(interaction.user):
            await interaction.response.send_message("❌ You are not authorized to approve whitelist requests.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            result = await asyncio.to_thread(whitelist_player, self.username)
            await interaction.followup.send(
                f"✅ `{self.username}` has been whitelisted by <@{interaction.user.id}>.\nRCON: {result}"
            )
            await interaction.message.edit(view=None)
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to whitelist `{self.username}`. Reason: {e}",
                ephemeral=True
            )

# .......... Message Discord ...................................................................
intents = discord.Intents.default()

def create_bot():
    global bot
    bot = commands.Bot(command_prefix="!", intents=intents, reconnect=True)

    @bot.tree.command(name="status")
    async def status(interaction):
        await interaction.response.defer()
        result = await asyncio.to_thread(detect_server_status)

        if not result["online"]:
            await interaction.followup.send("🔴 Server OFFLINE")
            return

        msg = f"🟢 Online ({result['mode']})"
        if result["players"]:
            msg += f"\nPlayers: {result['players']}"
        if result["player_names"]:
            msg += f"\nNames: {', '.join(result['player_names'])}"
        if result["ping"]:
            msg += f"\nPing: {result['ping']}ms"

        await interaction.followup.send(msg)

    @bot.tree.command(name="restart")
    async def restart(interaction):
        if interaction.user.id != PING_USER_ID:
            await interaction.response.send_message("❌ Not authorized", ephemeral=True)
            return

        await interaction.response.send_message("Attempting restart, this can take up to 60 seconds...")
        success, result, attempts, start_msg = await asyncio.to_thread(restart_with_retries)

        if attempts == 0:
            await interaction.followup.send(f"<@{PING_USER_ID}> ❌ Restart aborted. {start_msg}")
            return

        if success:
            await interaction.followup.send(
                f"<@{PING_USER_ID}> ✅ Restart successful after {attempts} checks. {describe_status(result)} | {start_msg}"
            )
            return

        await interaction.followup.send(
            f"<@{PING_USER_ID}> ❌ Restart failed after {attempts} checks. Server is still offline. | {start_msg}")

    @bot.tree.command(name="whitelistme", description="Request whitelist approval for your Minecraft username.")
    async def whitelistme(interaction: discord.Interaction, username: str):
        cleaned = username.strip()
        if not cleaned or len(cleaned) > 16:
            await interaction.response.send_message(
                "❌ Invalid username. Minecraft usernames must be 1-16 characters.",
                ephemeral=True,
            )
            return

        if not cleaned.replace("_", "").isalnum():
            await interaction.response.send_message(
                "❌ Username can only contain letters, numbers, and underscores.",
                ephemeral=True,
            )
            return

        approval_channel = bot.get_channel(WHITELIST_APPROVAL_CHANNEL_ID)
        if approval_channel is None:
            await interaction.response.send_message(
                "❌ Could not find the whitelist approval channel. Check WHITELIST_APPROVAL_CHANNEL_ID.",
                ephemeral=True,
            )
            return

        approval_view = WhitelistApprovalView(cleaned, interaction.user.id)
        await approval_channel.send(
            f"{whitelist_ping_mention()} Whitelist request from <@{interaction.user.id}>\n"
            f"Minecraft username: `{cleaned}`",
            view=approval_view,
        )

        await interaction.response.send_message(
            f"✅ Request sent! Staff will review your whitelist request for `{cleaned}`.",
            ephemeral=True,
        )
    
        await interaction.message.send_message(
            f"✅ Request accepted for '{cleaned}'. ",
            ephemeral=True,
        )
        await interaction.message.edit(view=None)
            
    
        
        
    @bot.event
    async def on_ready():
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
        log("Bot connected.")
        start_monitor()

    return bot

# .................... Backoff reset and increase ...............................
def reset_backoff():
    global backoff_delay
    backoff_delay = 1

def increase_backoff():
    global backoff_delay
    backoff_delay = min(backoff_delay * 2, MAX_BACKOFF)

# ........ Minecraft monitoring + ping discord ....................................................................................................
async def monitor():
    global running
    channel = bot.get_channel(CHANNEL_ID)

    while running:
        if channel is None:
            await asyncio.sleep(5)
            channel = bot.get_channel(CHANNEL_ID)
            continue

        try:
            result = await asyncio.to_thread(detect_server_status)

            if state["online"] != result["online"]:
                state["online"] = result["online"]
                now = datetime.utcnow().isoformat()

                if not result["online"]:
                    state["last_down"] = now
                    await channel.send(f"<@{PING_USER_ID}> 🔴 Server OFFLINE")
                else:
                    state["last_up"] = now
                    await channel.send(f"<@{PING_USER_ID}> 🟢 Server ONLINE ({result['mode']})\n{describe_status(result)}")
                    state["last_down"] = None

                save_state()
                reset_backoff()

        except Exception as e:
            log(f"Monitor error: {e}")
            increase_backoff()
            await asyncio.sleep(backoff_delay)
            continue

        await asyncio.sleep(CHECK_INTERVAL)

def start_monitor():
    global monitor_task
    monitor_task = bot.loop.create_task(monitor())

# ............ STOP BOT ............................................
def stop_bot():
    global running
    running = False

    async def shutdown():
        await bot.close()

    if bot:
        asyncio.run_coroutine_threadsafe(shutdown(), bot.loop) # this fixes the err from pooping up

    log("Bot stopped.")

# .......... Run Bot Thread .................................
def run_bot():
    global backoff_delay

    while running:
        try:
            create_bot()
            bot.run(TOKEN)
        except Exception as e:
            log(f"Bot crashed: {e}")
            increase_backoff()
            log(f"Retrying in {backoff_delay}s...")
            import time
            time.sleep(backoff_delay)

# ........ Connection Test ...................................
def connection_test():
    def run():
        log("=== Connection Test ===")

        if tcp_check("discord.com", 443):
            log("🟢 Discord reachable")
        else:
            log("❌ Cannot reach Discord")
            return

        if tcp_check(MC_ADDRESS, MC_PORT):
            log("🟢 Minecraft port reachable")
        else:
            log("❌ Minecraft port unreachable")
            return

        result = detect_server_status()

        if result["mode"] == "JAVA":
             log(f"🟢 Java OK | {describe_status(result)}")
        elif result["mode"] == "BEDROCK":
            log(f"🟢 Bedrock OK ({result['players']}) | {describe_status(result)}")
        else:
            log("⚠ Limited status (proxy/protected)")

        log("=== Test Complete ===")

    threading.Thread(target=run, daemon=True).start()

# ................. GUI .............................................................................
def log(msg):
    log_box.config(state="normal")
    log_box.insert(tk.END, msg + "\n")
    log_box.config(state="disabled")
    log_box.see(tk.END)

def start_bot():
    global running
    if running:
        return
    running = True
    threading.Thread(target=run_bot, daemon=True).start()
    log("Starting bot...")

def restart_from_gui():
    log("Attempting server restart (max 60 seconds)...")
    success, result, attempts, start_msg = restart_with_retries()

    if attempts == 0:
        log(f"❌ Restart aborted. {start_msg}")
        return

    if success:
        log(f"✅ Restart successful after {attempts} checks. {describe_status(result)} | {start_msg}")
    else:
        log(f"❌ Restart failed after {attempts} checks. Server is still offline. | {start_msg}")


def refresh_live_status():
    result = detect_server_status()
    if result["online"]:
        details = describe_status(result)
        gui_status_var.set(f"🟢 {result['mode']} | {details}")
    else:
        gui_status_var.set("🔴 OFFLINE")

    root.after(10000, refresh_live_status)

root = tk.Tk()
root.title("Minecraft Server Monitor Bot")
root.geometry("620x420")
root.configure(bg="#1e1e1e")

tk.Label(root, text="Minecraft Monitor",
         fg="white", bg="#1e1e1e",
         font=("Segoe UI", 14, "bold")).pack(pady=10)

gui_status_var = tk.StringVar(value="Status: Waiting for first check...")
tk.Label(root, textvariable=gui_status_var,
         fg="#8bd5ff", bg="#1e1e1e",
         font=("Segoe UI", 10, "bold"), wraplength=590, justify="left").pack(padx=10, pady=5)

btn_frame = tk.Frame(root, bg="#1e1e1e")
btn_frame.pack(pady=5)

# Start Button
tk.Button(btn_frame, text="Start", width=12,
          bg="#2d7d46", fg="white",
          command=start_bot).pack(side="left", padx=5)

# Stop button
tk.Button(btn_frame, text="Stop", width=12,
          bg="#7d2d2d", fg="white",
          command=stop_bot).pack(side="left", padx=5)

# Connection test Button
tk.Button(btn_frame, text="Connection Test", width=16,
          bg="#2d4a7d", fg="white",
          command=connection_test).pack(side="left", padx=5)

# Server reset Button
tk.Button(btn_frame, text="Restart Server", width=16,
          bg="#7d6a2d", fg="white",
          command=lambda: threading.Thread(target=restart_from_gui, daemon=True).start()
          ).pack(side="left", padx=5)
# Text 
log_box = tk.Text(root, height=14, bg="#111",
                  fg="#00ff9c",
                  state="disabled",
                  font=("Consolas", 9))
log_box.pack(fill="both", expand=True, padx=10, pady=10)


refresh_live_status()

root.mainloop()
