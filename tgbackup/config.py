"""
===============================================================================
Project      : TGBackup
File         : tgbackup/config.py
Description  : Local configuration management and interactive TUI setup wizard.
Purpose      : Manages reading and writing ~/.config/tgbackup/config.json with
               restrictive POSIX security permissions (chmod 0600). Provides a
               modern interactive terminal wizard using the Rich library to guide
               users through token, channel, encryption, and profile configuration.
===============================================================================
"""

import os
import json
from typing import Dict, Any, Optional, List
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.table import Table

console = Console()

# Default directories and file paths for configuration
DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/tgbackup")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")

# Default configuration template
DEFAULT_CONFIG: Dict[str, Any] = {
    "channel_id": "",
    "bot_tokens": [],
    "passphrase": "",
    "use_topics": True,
    "notif_topic_name": "Notifications",
    "notif_topic_id": None,
    "chunk_size_mb": 19,
    "compression_level": 3,
    "desktop_notifications": True,
    "api_endpoint": None,
    "staging_dir": None,
    "local_backup_dir": None,
    "rate_limit_per_minute": 20,
    "retention": {
        "keep_daily": 7,
        "keep_weekly": 4,
        "keep_monthly": 12
    },
    "profiles": {
        "default": {
            "paths": [os.path.expanduser("~/Documents")],
            "excludes": [".cache", "node_modules", ".venv", "__pycache__", "*.tmp", ".git"],
            "topic_name": "Default_Backup",
            "topic_id": None
        }
    }
}


def get_default_config_path() -> str:
    """
    ---------------------------------------------------------------------------
    Function: get_default_config_path
    Description:
        Resolves the absolute path to the configuration file, checking
        the TGBACKUP_CONFIG environment variable with fallback to
        ~/.config/tgbackup/config.json.
    
    Input parameters:
        None
    
    Return value:
        @return (str) : Path to config.json.
    ---------------------------------------------------------------------------
    """
    env_path = os.environ.get("TGBACKUP_CONFIG")
    if env_path:
        return env_path
    return DEFAULT_CONFIG_PATH


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    ---------------------------------------------------------------------------
    Function: load_config
    Description:
        Loads and parses the JSON configuration file from disk.
    
    Input parameters:
        @param path (Optional[str]) : Custom path to configuration file.
                                      If None, uses the default path.
    
    Return value:
        @return (Dict[str, Any])    : Parsed configuration dictionary.
    
    Exceptions raised:
        @raises FileNotFoundError   : If the configuration file does not exist.
    ---------------------------------------------------------------------------
    """
    cfg_path = path or get_default_config_path()
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"Configuration not found at '{cfg_path}'. Run 'tgbackup init' to configure the application."
        )
    
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


def save_config(cfg: Dict[str, Any], path: Optional[str] = None):
    """
    ---------------------------------------------------------------------------
    Function: save_config
    Description:
        Serializes configuration dictionary to formatted JSON and enforces
        POSIX permissions 0600 (exclusive user read/write access).
    
    Input parameters:
        @param cfg (Dict[str, Any]) : Configuration data dictionary.
        @param path (Optional[str]) : Target path on disk.
    
    Return value:
        @return None
    ---------------------------------------------------------------------------
    """
    cfg_path = path or get_default_config_path()
    os.makedirs(os.path.dirname(os.path.abspath(cfg_path)), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    
    # Protect tokens and passphrase using chmod 0600
    try:
        os.chmod(cfg_path, 0o600)
    except Exception:
        pass


async def interactive_init(target_path: Optional[str] = None):
    """
    ---------------------------------------------------------------------------
    Function: interactive_init
    Description:
        Launches the interactive TUI/CLI setup wizard for TGBackup:
        1. Prompts for Telegram Supergroup ID.
        2. Acquires and validates bot cluster tokens against Telegram API in real time.
        3. Configures AES-256 encryption passphrase.
        4. Sets up default backup profile and retention policy (GFS).
        5. Saves configuration with secure 0600 permissions.
    
    Input parameters:
        @param target_path (Optional[str]) : Destination path for config.json.
    
    Return value:
        @return None
    ---------------------------------------------------------------------------
    """
    from .cluster import BotCluster

    cfg_path = target_path or get_default_config_path()
    console.print(Panel.fit(
        "[bold cyan]TGBackup - Setup Wizard[/bold cyan]\n"
        "[dim]Native Linux encrypted backup with AES-256-GCM & zstd to Telegram Supergroup Cluster[/dim]",
        border_style="cyan"
    ))

    # Check for existing configuration
    existing_cfg = {}
    if os.path.exists(cfg_path):
        console.print(f"[yellow]Existing configuration found at: [bold]{cfg_path}[/bold][/yellow]")
        if not Confirm.ask("Do you want to overwrite or reconfigure it?", default=True):
            return
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                existing_cfg = json.load(f)
        except Exception:
            pass

    # 1. Telegram Supergroup / Channel ID
    default_chan = str(existing_cfg.get("channel_id", ""))
    console.print("\n[bold]1. Telegram Supergroup Configuration[/bold]")
    console.print("[dim]Enter the Supergroup ID (typically starts with -100...)[/dim]")
    channel_id = Prompt.ask("Supergroup ID", default=default_chan)

    # 2. Bot Cluster Tokens
    console.print("\n[bold]2. Bot Cluster Tokens[/bold]")
    console.print("[dim]Enter one or more bot tokens (comma-separated or one per prompt). Multiple bots increase speed and eliminate rate-limits.[/dim]")
    
    current_tokens = existing_cfg.get("bot_tokens", [])
    if current_tokens:
        console.print(f"[green]Existing configured tokens: {len(current_tokens)}[/green]")
        reuse = Confirm.ask("Do you want to keep the current bots?", default=True)
        if reuse:
            bot_tokens = current_tokens
        else:
            bot_tokens = []
    else:
        bot_tokens = []

    if not bot_tokens:
        while True:
            t_input = Prompt.ask("Enter Bot Token (or press Enter when done)").strip()
            if not t_input:
                if bot_tokens:
                    break
                else:
                    console.print("[red]At least one Bot Token is required![/red]")
                    continue
            for part in t_input.split(","):
                part = part.strip()
                if part and part not in bot_tokens:
                    bot_tokens.append(part)
            if Confirm.ask("Add another bot to the cluster?", default=False) is False:
                break

    # Verify bots against Telegram API
    console.print("\n[cyan]Verifying bots via Telegram API...[/cyan]")
    cluster = BotCluster(tokens=bot_tokens, channel_id=channel_id)
    verified = await cluster.verify_bots()

    table = Table(title="Bot Cluster Status")
    table.add_column("#", style="dim")
    table.add_column("Username / Details")
    table.add_column("Token Status", justify="center")

    all_valid = True
    for item in verified:
        if item["valid"]:
            table.add_row(str(item["index"]), f"@{item['username']} ({item['first_name']})", "[green]VALID[/green]")
        else:
            all_valid = False
            table.add_row(str(item["index"]), item.get("error", "Error"), "[red]INVALID[/red]")
    console.print(table)

    if not all_valid:
        if not Confirm.ask("[yellow]Some tokens are invalid. Do you want to proceed anyway?[/yellow]", default=False):
            return

    # 3. AES-256 Encryption Passphrase
    console.print("\n[bold]3. Security & Encryption (AES-256-GCM)[/bold]")
    console.print("[dim]Snapshots are compressed with zstd and encrypted locally prior to transmission. Telegram only receives ciphertext blobs.[/dim]")
    
    passphrase = Prompt.ask(
        "Encryption Passphrase (leave empty to prompt on each backup run)",
        password=True,
        default=existing_cfg.get("passphrase", "")
    )
    if passphrase:
        confirm_pass = Prompt.ask("Confirm Passphrase", password=True)
        if passphrase != confirm_pass:
            console.print("[red]Passphrases do not match. Setup aborted.[/red]")
            return

    # 4. Primary Backup Profile
    console.print("\n[bold]4. Primary Backup Profile[/bold]")
    prof_name = Prompt.ask("Profile name", default="documents")
    default_dir = os.path.expanduser("~/Documents")
    b_paths_str = Prompt.ask("Directories/Files to include (comma-separated)", default=default_dir)
    b_paths = [p.strip() for p in b_paths_str.split(",") if p.strip()]

    # 5. Chunking & Compression
    console.print("\n[bold]5. Chunking & Compression[/bold]")
    console.print("[dim]Standard Telegram Bot API limits getFile downloads to 20 MB. Default chunk size is 19 MB.[/dim]")
    chunk_size_mb = int(Prompt.ask("Chunk size in Megabytes (MB)", default=str(existing_cfg.get("chunk_size_mb", 19))))
    if chunk_size_mb < 1 or chunk_size_mb > 49:
        console.print("[yellow]Non-optimal chunk size. Defaulting to safe value of 19 MB.[/yellow]")
        chunk_size_mb = 19

    comp_level = int(Prompt.ask("Zstandard compression level (1=fastest, 19=maximum)", default=str(existing_cfg.get("compression_level", 3))))
    if comp_level < 1 or comp_level > 19:
        comp_level = 3

    # 6. Local Storage & Staging
    console.print("\n[bold]6. Storage & Staging Directories[/bold]")
    staging_dir_input = Prompt.ask(
        "Custom staging directory for temporary files (leave empty for ~/.cache/tgbackup/staging)",
        default=existing_cfg.get("staging_dir") or ""
    ).strip()
    staging_dir = staging_dir_input if staging_dir_input else None

    local_backup_input = Prompt.ask(
        "Secondary local backup directory on HDD (e.g. /mnt/backup_hdd, leave empty to disable)",
        default=existing_cfg.get("local_backup_dir") or ""
    ).strip()
    local_backup_dir = local_backup_input if local_backup_input else None

    api_endpoint_input = Prompt.ask(
        "Custom Telegram Bot API Server endpoint (leave empty for official cloud API)",
        default=existing_cfg.get("api_endpoint") or ""
    ).strip()
    api_endpoint = api_endpoint_input if api_endpoint_input else None

    # 7. Native Desktop Notifications
    desktop_notif = Confirm.ask("Enable native Linux desktop notifications (notify-send)?", default=existing_cfg.get("desktop_notifications", True))

    # 8. GFS Retention Policy
    console.print("\n[bold]8. Retention Policy (GFS / Pruning)[/bold]")
    keep_daily = int(Prompt.ask("Daily backups to keep?", default=str(existing_cfg.get("retention", {}).get("keep_daily", 7))))
    keep_weekly = int(Prompt.ask("Weekly backups to keep?", default=str(existing_cfg.get("retention", {}).get("keep_weekly", 4))))
    keep_monthly = int(Prompt.ask("Monthly backups to keep?", default=str(existing_cfg.get("retention", {}).get("keep_monthly", 12))))

    new_cfg: Dict[str, Any] = {
        "channel_id": channel_id,
        "bot_tokens": bot_tokens,
        "passphrase": passphrase,
        "use_topics": True,
        "notif_topic_name": "Notifications",
        "notif_topic_id": existing_cfg.get("notif_topic_id"),
        "chunk_size_mb": chunk_size_mb,
        "compression_level": comp_level,
        "desktop_notifications": desktop_notif,
        "api_endpoint": api_endpoint,
        "staging_dir": staging_dir,
        "local_backup_dir": local_backup_dir,
        "rate_limit_per_minute": existing_cfg.get("rate_limit_per_minute", 20),
        "retention": {
            "keep_daily": keep_daily,
            "keep_weekly": keep_weekly,
            "keep_monthly": keep_monthly
        },
        "profiles": existing_cfg.get("profiles", {})
    }

    new_cfg["profiles"][prof_name] = {
        "paths": b_paths,
        "excludes": [".cache", "node_modules", ".venv", "__pycache__", "*.tmp", ".git"],
        "topic_name": f"{prof_name.capitalize()}_Backup",
        "topic_id": None
    }

    save_config(new_cfg, cfg_path)
    console.print(f"\n[green]Configuration saved successfully to [bold]{cfg_path}[/bold] (mode 0600)[/green]")
