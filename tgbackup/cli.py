"""
===============================================================================
Project      : TGBackup
File         : tgbackup/cli.py
Description  : Advanced CLI with parallel multi-bot uploads, incremental backups,
               cloud scrub integrity verification, and Telegram Disaster Recovery.
Purpose      : Provides user interface and command orchestration:
               - init: interactive TUI setup wizard
               - backup: atomic filesystem snapshots (Btrfs), incremental/full, parallel upload
               - list: snapshot catalog inspection
               - restore: layered multi-part overlay restoration
               - check: cloud scrub / remote message persistence verification
               - reindex: disaster recovery reconstructing vault.db from pinned cloud catalog
               - schedule: systemd user timer automation
               - status: cluster diagnostic and health reporting
===============================================================================
"""

import os
import sys
import time
import json
import shutil
import asyncio
import tempfile
import getpass
import argparse
from datetime import datetime
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.panel import Panel

from .config import load_config, save_config, get_default_config_path, interactive_init
from .archiver import Archiver
from .cluster import BotCluster, format_size, format_duration
from .crypto import encrypt_bytes, decrypt_bytes
from .db import VaultDB, DEFAULT_DB_PATH
from .retention import select_snapshots_to_prune
from .systemd import enable_schedule, disable_schedule, get_schedule_status, get_timer_info
from .fs_snapshot import atomic_snapshot_context
from .notify import send_desktop_notification
from .lock import ProcessLock, ProcessLockedError

console = Console()


def get_passphrase(cfg: dict) -> str:
    """
    ---------------------------------------------------------------------------
    Function: get_passphrase
    Description:
        Resolves encryption passphrase from config, TGBACKUP_PASSPHRASE env var,
        or interactive getpass prompt.
    Input parameters:
        @param cfg (dict) : Configuration dictionary.
    Return value:
        @return (str)     : Secret passphrase.
    ---------------------------------------------------------------------------
    """
    if cfg.get("passphrase"):
        return cfg["passphrase"]
    env_pass = os.environ.get("TGBACKUP_PASSPHRASE")
    if env_pass:
        return env_pass
    return getpass.getpass("Enter TGBackup encryption passphrase: ")


async def run_backup_profile(
    profile_name: str,
    cfg: dict,
    db: VaultDB,
    cluster: BotCluster,
    force_full: bool = False,
    local_dir: Optional[str] = None,
    mock: bool = False,
    json_progress: bool = False
):
    def emit_progress(phase: str, percent: int, message: str, **kwargs):
        if not json_progress:
            return
        payload = {
            "event": "progress",
            "phase": phase,
            "percent": percent,
            "message": message,
            **kwargs
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
    """
    ---------------------------------------------------------------------------
    Function: run_backup_profile
    Description:
        Executes full or incremental backup with atomic Btrfs snapshotting,
        concurrent multi-bot chunk upload, and encrypted catalog pinning for Disaster Recovery.
    Input parameters:
        @param profile_name (str)       : Profile name.
        @param cfg (dict)               : Global configuration dictionary.
        @param db (VaultDB)             : Local Vault database instance.
        @param cluster (BotCluster)     : Telegram bot cluster instance.
        @param force_full (bool)        : If True, forces a Full backup ignoring incremental history.
        @param local_dir (Optional[str]): Optional secondary local backup destination on external drive.
        @param mock (bool)              : If True, mocks Telegram API calls.
    Return value:
        @return None
    ---------------------------------------------------------------------------
    """
    prof_cfg = cfg.get("profiles", {}).get(profile_name)
    if not prof_cfg:
        console.print(f"[red]Error: Profile '{profile_name}' not found in configuration.[/red]")
        return

    paths = prof_cfg.get("paths", [])
    if not paths:
        console.print(f"[yellow]No paths specified for profile '{profile_name}'. Skipping.[/yellow]")
        return

    excludes = prof_cfg.get("excludes", [])
    topic_name = prof_cfg.get("topic_name", f"{profile_name.capitalize()}_Backup")
    passphrase = get_passphrase(cfg)
    if not passphrase:
        console.print("[red]Passphrase not provided. Operation aborted.[/red]")
        return

    chunk_size = prof_cfg.get("chunk_size_mb", cfg.get("chunk_size_mb", 19))
    comp_level = prof_cfg.get("compression_level", cfg.get("compression_level", 3))
    staging_dir = cfg.get("staging_dir")
    archiver = Archiver(chunk_size_mb=chunk_size, compression_level=comp_level, staging_dir=staging_dir)

    # 1. Retrieve preceding snapshot for incremental backup
    base_manifest = None
    if not force_full:
        prev_snap = await db.get_latest_snapshot(profile_name)
        if prev_snap and prev_snap.get("manifest_json"):
            try:
                base_manifest = json.loads(prev_snap["manifest_json"])
            except Exception:
                pass

    backup_type_label = "INCREMENTAL" if base_manifest else "FULL"

    console.print(Panel(
        f"[bold]Starting Profile Backup:[/bold] [cyan]{profile_name}[/cyan] ([yellow]{backup_type_label}[/yellow])\n"
        f"[bold]Paths:[/bold] {', '.join(paths)}\n"
        f"[bold]Chunk Size:[/bold] {chunk_size}MB | [bold]Compression:[/bold] zstd (Lvl {comp_level}) | [bold]Encryption:[/bold] AES-256-GCM",
        title="TGBackup",
        border_style="blue"
    ))

    start_time = time.time()

    # 2. Telegram Forum Topics
    backup_topic_id = prof_cfg.get("topic_id")
    if cfg.get("use_topics", True) and not backup_topic_id:
        with console.status(f"[cyan]Verifying Telegram topic '{topic_name}'...[/cyan]"):
            backup_topic_id = await cluster.get_or_create_topic(topic_name)
            if backup_topic_id:
                prof_cfg["topic_id"] = backup_topic_id
                save_config(cfg)

    notif_topic_id = cfg.get("notif_topic_id")
    if cfg.get("use_topics", True) and not notif_topic_id:
        notif_name = cfg.get("notif_topic_name", "Notifications")
        with console.status(f"[cyan]Verifying notifications topic '{notif_name}'...[/cyan]"):
            notif_topic_id = await cluster.get_or_create_topic(notif_name)
            if notif_topic_id:
                cfg["notif_topic_id"] = notif_topic_id
                save_config(cfg)

    # Start notification
    notif_text = (
        f"<b>Backup Started ({backup_type_label}):</b> <code>{profile_name}</code>\n"
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Paths: <code>{', '.join(paths)}</code>"
    )
    notif_msg_id = await cluster.send_notification(notif_text, thread_id=notif_topic_id)
    desktop_enabled = cfg.get("desktop_notifications", True)
    send_desktop_notification(
        title=f"TGBackup - {profile_name}",
        message=f"Starting {backup_type_label} backup...",
        urgency="low",
        icon="security-high",
        enabled=desktop_enabled
    )

    emit_progress("scan", 5, f"Scanning files for profile '{profile_name}'...")

    # 3. Atomic filesystem snapshot (Btrfs / direct read fallback)
    with atomic_snapshot_context(paths) as effective_paths:
        with tempfile.TemporaryDirectory(dir=staging_dir, prefix=f"tgb_{profile_name}_") as tmpdir:
            emit_progress("compress", 20, "Generating encrypted archive (zstd + AES-256-GCM)...")
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console
            ) as progress:
                task = progress.add_task("[cyan]Scanning files and generating encrypted archive...", total=None)
                snap_id, manifest, part_files = archiver.create_snapshot(
                    paths=effective_paths,
                    profile_name=profile_name,
                    passphrase=passphrase,
                    output_dir=tmpdir,
                    excludes=excludes,
                    base_manifest=base_manifest
                )
                progress.update(task, completed=100, description="[green]Snapshot generated successfully!")

            uncompressed = manifest.get("uncompressed_bytes", 0)
            compressed = manifest.get("compressed_bytes", 0)
            ratio = (1 - (compressed / uncompressed)) * 100 if uncompressed > 0 else 0
            total_parts = len(part_files)
            changed_files = manifest.get("changed_files", 0)
            reused_files = manifest.get("reused_files", 0)

            console.print(f"Files: [bold]{manifest.get('total_files')}[/bold] "
                          f"(Modified/New: [green]{changed_files}[/green], Unchanged: [dim]{reused_files}[/dim]) | "
                          f"Source size: [bold]{format_size(uncompressed)}[/bold] -> "
                          f"Encrypted: [bold]{format_size(compressed)}[/bold] | "
                          f"Parts to upload: [bold]{total_parts}[/bold]")

            if total_parts == 0:
                emit_progress("done", 100, "No modified files detected. Backup unchanged.")

            # Save snapshot record in DB
            await db.save_snapshot(manifest)

            # Secondary local storage sync (e.g. external HDD)
            manifest_path = os.path.join(tmpdir, f"snap_{profile_name}_{snap_id}.manifest.json")
            target_local_dir = local_dir or cfg.get("local_backup_dir")
            if target_local_dir:
                emit_progress("mirror", 45, f"Syncing {total_parts} chunks to local HDD mirror...")
                local_profile_dir = os.path.join(os.path.abspath(target_local_dir), profile_name)
                os.makedirs(local_profile_dir, exist_ok=True)
                for pf in part_files:
                    shutil.copy2(pf, os.path.join(local_profile_dir, os.path.basename(pf)))
                if os.path.exists(manifest_path):
                    shutil.copy2(manifest_path, os.path.join(local_profile_dir, os.path.basename(manifest_path)))
                console.print(f"  [dim]Local disk mirror synced ({len(part_files)} chunks): {local_profile_dir}[/dim]")

            # 4. Multi-Bot Concurrent Upload
            if total_parts > 0:
                emit_progress("upload", 50, f"Starting parallel upload ({total_parts} chunks, {len(cluster.bots)} bots)...")
                with Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeElapsedColumn(),
                    console=console
                ) as progress:
                    upload_task = progress.add_task(
                        f"[magenta]Concurrent Upload ({len(cluster.bots)} Bots in parallel)...",
                        total=total_parts
                    )

                    uploaded_parts = [0]
                    def on_part_done(fname, mid, bot_i):
                        progress.advance(upload_task)
                        uploaded_parts[0] += 1
                        pct = 50 + int(40 * (uploaded_parts[0] / total_parts))
                        emit_progress(
                            "upload",
                            pct,
                            f"Telegram upload: {uploaded_parts[0]}/{total_parts} chunks (Bot #{bot_i + 1})...",
                            current=uploaded_parts[0],
                            total=total_parts
                        )

                    upload_results = await cluster.upload_parts_parallel(
                        part_files=part_files,
                        profile_name=profile_name,
                        thread_id=backup_topic_id,
                        on_progress=on_part_done
                    )

                    # Save uploaded chunk records to DB
                    parts_meta = {p["filename"]: p for p in manifest["parts"]}
                    for res in upload_results:
                        fname = res["part_name"]
                        pmeta = parts_meta.get(fname, {})
                        await db.record_uploaded_part(
                            part_name=fname,
                            snapshot_id=snap_id,
                            message_id=res["message_id"],
                            topic_id=backup_topic_id or 0,
                            size=pmeta.get("size", os.path.getsize(res["filepath"])),
                            sha256=pmeta.get("sha256", ""),
                            bot_idx=res["bot_idx"]
                        )

            # Upload JSON manifest into profile topic
            if os.path.exists(manifest_path):
                await cluster.upload_part(manifest_path, profile_name, thread_id=backup_topic_id)

            # 5. Disaster Recovery: export, encrypt, and pin master Vault catalog on Telegram
            emit_progress("catalog", 95, "Pinning encrypted recovery catalog on Telegram...")
            catalog_data = await db.export_catalog()
            raw_catalog_bytes = json.dumps(catalog_data).encode("utf-8")
            enc_catalog_bytes = encrypt_bytes(raw_catalog_bytes, passphrase)
            cat_path = os.path.join(tmpdir, "vault_master_catalog.json.enc")
            with open(cat_path, "wb") as cf:
                cf.write(enc_catalog_bytes)
            await cluster.upload_and_pin_catalog(cat_path, thread_id=notif_topic_id)

    duration = time.time() - start_time
    emit_progress("done", 100, f"Backup completed successfully in {format_duration(duration)}!")
    console.print(f"[green]Backup completed successfully in {format_duration(duration)}![/green]")

    # Completion notification
    success_notif = (
        f"<b>Backup Completed Successfully! ({backup_type_label})</b>\n"
        f"Profile: <code>{profile_name}</code>\n"
        f"Snapshot: <code>{snap_id}</code>\n"
        f"New Data: <b>{format_size(uncompressed)}</b> (Ciphertext: <b>{format_size(compressed)}</b>)\n"
        f"Parts Uploaded: <b>{total_parts}</b> (Multi-Bot Parallel)\n"
        f"Duration: <b>{format_duration(duration)}</b>"
    )
    await cluster.edit_notification(notif_msg_id, success_notif, thread_id=notif_topic_id)
    send_desktop_notification(
        title="TGBackup Completed",
        message=f"Profile '{profile_name}' ({backup_type_label}): {format_size(uncompressed)} in {format_duration(duration)}.",
        urgency="normal",
        icon="emblem-default",
        enabled=desktop_enabled
    )

    # 6. GFS Retention with dependency protection
    retention_cfg = cfg.get("retention", {})
    all_snaps = await db.get_snapshots(profile=profile_name)
    to_prune = select_snapshots_to_prune(
        all_snaps,
        keep_daily=retention_cfg.get("keep_daily", 7),
        keep_weekly=retention_cfg.get("keep_weekly", 4),
        keep_monthly=retention_cfg.get("keep_monthly", 12)
    )

    if to_prune:
        console.print(f"\n[yellow]Retention Pruning: {len(to_prune)} obsolete snapshots to remove...[/yellow]")
        for old_snap in to_prune:
            old_id = old_snap["id"]
            old_parts = await db.get_snapshot_parts(old_id)
            for p in old_parts:
                mid = p.get("message_id")
                if mid:
                    await cluster.delete_message(mid)
            await db.delete_snapshot(old_id)
            console.print(f"  [dim]Deleted snapshot {old_id} ({len(old_parts)} chunks removed)[/dim]")


async def do_backup(args):
    """
    ---------------------------------------------------------------------------
    Function: do_backup
    Description:
        Coordinates immediate backup execution for a specific profile or all
        configured profiles based on CLI arguments.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    with ProcessLock():
        cfg = load_config(args.config)
        mock = getattr(args, "mock", False)
        force_full = getattr(args, "full", False)
        local_dir = getattr(args, "local_dir", None)
        json_progress = getattr(args, "json_progress", False)
        cluster = BotCluster(
            tokens=cfg["bot_tokens"],
            channel_id=cfg["channel_id"],
            use_topics=cfg.get("use_topics", True),
            api_endpoint=cfg.get("api_endpoint"),
            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 20),
            mock=mock
        )
        async with VaultDB() as db:
            if args.all or not args.profile:
                profiles = list(cfg.get("profiles", {}).keys())
                if not profiles:
                    console.print("[yellow]No profiles found in configuration.[/yellow]")
                    return
                for prof in profiles:
                    await run_backup_profile(prof, cfg, db, cluster, force_full=force_full, local_dir=local_dir, mock=mock, json_progress=json_progress)
            else:
                await run_backup_profile(args.profile, cfg, db, cluster, force_full=force_full, local_dir=local_dir, mock=mock, json_progress=json_progress)


async def do_list(args):
    """
    ---------------------------------------------------------------------------
    Function: do_list
    Description:
        Lists all snapshots recorded in the local database Vault, formatted
        in a rich table with type, size, date, file count, and parts.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    async with VaultDB() as db:
        snaps = await db.get_snapshots(profile=args.profile)
        if not snaps:
            console.print("[yellow]No snapshots recorded in the local database.[/yellow]")
            return

        table = Table(title=f"TGBackup Snapshots ({len(snaps)} found)")
        table.add_column("Snapshot ID", style="cyan", no_wrap=True)
        table.add_column("Profile", style="bold")
        table.add_column("Type", justify="center")
        table.add_column("Date / Time")
        table.add_column("Files", justify="right")
        table.add_column("Encrypted Size", justify="right")
        table.add_column("Parts", justify="center")

        for s in snaps:
            comp = format_size(s.get("compressed_bytes") or 0)
            dt_str = s.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(dt_str)
                dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            
            stype = "Full"
            if s.get("manifest_json"):
                try:
                    m = json.loads(s["manifest_json"])
                    if m.get("type") == "incremental":
                        stype = f"[yellow]Incr ({m.get('changed_files', 0)})[/yellow]"
                    else:
                        stype = "[green]Full[/green]"
                except Exception:
                    pass

            table.add_row(
                s["id"],
                s["profile"],
                stype,
                dt_str,
                str(s.get("total_files", 0)),
                comp,
                str(s.get("total_parts", 0))
            )

        console.print(table)


async def do_restore(args):
    """
    ---------------------------------------------------------------------------
    Function: do_restore
    Description:
        Restores a snapshot by downloading chunks from Telegram, decrypting,
        verifying SHA-256 integrity hashes, and extracting files to destination.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    with ProcessLock():
        cfg = load_config(args.config)
        mock = getattr(args, "mock", False)
        snap_id = args.snapshot_id
        staging_dir = cfg.get("staging_dir")
        if staging_dir:
            os.makedirs(staging_dir, exist_ok=True)

        async with VaultDB() as db:
            if not snap_id:
                snapshots = await db.get_snapshots()
                if not snapshots:
                    console.print("\n[yellow]No snapshots recorded in the vault database.[/yellow]")
                    console.print("[dim]You have not performed any backups yet. Please create a backup first:[/dim]")
                    console.print("  [cyan]tgbackup backup --all[/cyan]\n")
                    return

                table = Table(title=f"Select Snapshot to Restore ({len(snapshots)} available)")
                table.add_column("#", style="bold cyan", justify="right")
                table.add_column("Snapshot ID", style="bold")
                table.add_column("Profile", style="cyan")
                table.add_column("Type", style="magenta")
                table.add_column("Date / Time", style="green")
                table.add_column("Files", justify="right")
                table.add_column("Encrypted Size", justify="right")

                for idx, s in enumerate(snapshots, start=1):
                    type_str = "Full" if s.get("type") == "full" else f"Incr ({s.get('parent_id') or ''})"
                    table.add_row(
                        str(idx),
                        s["id"],
                        s["profile"],
                        type_str,
                        s["timestamp"].replace("T", " ")[:19],
                        str(s["file_count"]),
                        format_bytes(s["compressed_size"])
                    )
                console.print(table)

                while True:
                    prompt_text = f"\n[bold]Select snapshot number [1-{len(snapshots)}] or enter ID[/bold] [dim](default: 1 - latest)[/dim]: "
                    selection = console.input(prompt_text).strip()
                    if not selection:
                        snap_id = snapshots[0]["id"]
                        break
                    if selection.isdigit():
                        num = int(selection)
                        if 1 <= num <= len(snapshots):
                            snap_id = snapshots[num - 1]["id"]
                            break
                    matched = [s for s in snapshots if s["id"] == selection]
                    if matched:
                        snap_id = matched[0]["id"]
                        break
                    console.print("[red]Invalid selection. Please choose a valid number from the table or enter a Snapshot ID.[/red]")

            snap = await db.get_snapshot(snap_id)
            if not snap:
                console.print(f"[red]Error: Snapshot '{snap_id}' not found in local catalog.[/red]")
                return

            manifest = json.loads(snap["manifest_json"])
            orig_paths = manifest.get("paths", [])
            parts = await db.get_all_parts_for_manifest(manifest)
            if not parts and manifest.get("total_parts", 0) > 0:
                console.print(f"[red]Error: No chunks recorded for snapshot '{snap_id}'.[/red]")
                return

            # 1. Destination Selection (Original location vs Custom path)
            is_in_place = False
            dest_dir = getattr(args, "destination", None)
            if getattr(args, "in_place", False):
                is_in_place = True
                dest_dir = None
            elif dest_dir:
                is_in_place = False
                dest_dir = os.path.abspath(os.path.expanduser(dest_dir))
            else:
                orig_display = ", ".join(orig_paths) if orig_paths else "Recorded snapshot paths"
                console.print(f"\n[bold]Snapshot original path(s):[/bold] [cyan]{orig_display}[/cyan]")
                console.print("[bold]Choose restore destination:[/bold]")
                console.print(f"  [1] Original location ({orig_display}) [dim][in-place overwrite][/dim]")
                console.print("  [2] Custom directory [dim](extract into a specified folder)[/dim]")

                dest_choice = console.input("\n[bold]Select destination option [1-2][/bold] [dim](default: 2)[/dim]: ").strip()
                if dest_choice == "1":
                    confirm = console.input("[bold yellow]Warning: In-place restore will overwrite existing files in their original locations. Proceed? [y/N]: [/bold yellow]").strip().lower()
                    if confirm in ("y", "yes"):
                        is_in_place = True
                        dest_dir = None
                    else:
                        console.print("[yellow]In-place restore cancelled. Falling back to custom directory.[/yellow]")
                        dest_choice = "2"

                if dest_choice != "1" or not is_in_place:
                    is_in_place = False
                    default_dest = os.path.expanduser(f"~/Restore/{snap_id}")
                    dest_input = console.input(f"[bold]Target destination directory[/bold] [dim](default: {default_dest})[/dim]: ").strip()
                    dest_dir = os.path.abspath(os.path.expanduser(dest_input)) if dest_input else default_dest

            # 2. Source Selection (Local Mirror vs Telegram Cloud)
            local_backup_dir = getattr(args, "local_dir", None) or cfg.get("local_backup_dir")
            local_profile_dir = os.path.join(os.path.abspath(local_backup_dir), snap["profile"]) if local_backup_dir else None

            local_available = False
            missing_local_parts = []
            if local_profile_dir and os.path.exists(local_profile_dir):
                for p in parts:
                    lp = os.path.join(local_profile_dir, p["part_name"])
                    if not os.path.exists(lp):
                        missing_local_parts.append(p["part_name"])
                if not missing_local_parts and parts:
                    local_available = True

            cli_source = getattr(args, "source", None)
            if cli_source == "local":
                if not local_available:
                    console.print(f"[red]Error: Local mirror at '{local_profile_dir}' is missing {len(missing_local_parts)} chunks.[/red]")
                    return
                use_local = True
            elif cli_source in ("telegram", "cloud"):
                use_local = False
            else:
                if local_available:
                    console.print(f"\n[bold]Local HDD mirror found:[/bold] [cyan]{local_profile_dir}[/cyan] [dim]({len(parts)} chunks available)[/dim]")
                    console.print("[bold]Choose restore source:[/bold]")
                    console.print("  [1] Local mirror [dim](fast, offline, instant extraction)[/dim]")
                    console.print("  [2] Telegram Cloud [dim](download via multi-bot cluster)[/dim]")
                    src_choice = console.input("\n[bold]Select source [1-2][/bold] [dim](default: 1)[/dim]: ").strip()
                    use_local = (src_choice != "2")
                else:
                    if local_profile_dir and os.path.exists(local_profile_dir):
                        console.print(f"\n[dim]Local mirror at '{local_profile_dir}' has {len(missing_local_parts)} chunks missing. Restoring from Telegram Cloud.[/dim]")
                    use_local = False

            passphrase = get_passphrase(cfg)
            if not passphrase:
                console.print("[red]Passphrase required for restore.[/red]")
                return

            source_label = f"Local Mirror ({local_profile_dir})" if use_local else f"Telegram Cloud ({len(parts)} chunks)"
            dest_label = f"Original location (in-place: {', '.join(orig_paths)})" if is_in_place else f"Custom directory ({dest_dir})"

            console.print(Panel(
                f"[bold cyan]Snapshot Restore:[/bold cyan] [bold]{snap_id}[/bold]\n"
                f"[bold]Profile:[/bold] {snap['profile']} | [bold]Chunks:[/bold] {len(parts)}\n"
                f"[bold]Source:[/bold] {source_label}\n"
                f"[bold]Destination:[/bold] {dest_label}",
                title="TGBackup Restore",
                border_style="green"
            ))

            archiver = Archiver(
                chunk_size_mb=cfg.get("chunk_size_mb", 19),
                staging_dir=staging_dir
            )

            if use_local:
                local_part_paths = [os.path.join(local_profile_dir, p["part_name"]) for p in parts]
                with console.status("[bold green]Verifying SHA-256 hashes, decrypting, and extracting files from local mirror...[/bold green]"):
                    archiver.extract_snapshot(
                        manifest=manifest,
                        part_files=local_part_paths,
                        passphrase=passphrase,
                        destination_dir=dest_dir,
                        in_place=is_in_place
                    )
            else:
                cluster = BotCluster(
                    tokens=cfg["bot_tokens"],
                    channel_id=cfg["channel_id"],
                    use_topics=cfg.get("use_topics", True),
                    api_endpoint=cfg.get("api_endpoint"),
                    rate_limit_per_minute=cfg.get("rate_limit_per_minute", 20),
                    mock=mock
                )
                with tempfile.TemporaryDirectory(prefix=f"tgbrestore_{snap_id}_", dir=staging_dir) as tmpdir:
                    downloaded_paths = []
                    if parts:
                        with Progress(
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                            TimeElapsedColumn(),
                            console=console
                        ) as progress:
                            dl_task = progress.add_task(f"[cyan]Downloading {len(parts)} chunks from Telegram...", total=len(parts))
                            for p in parts:
                                pname = p["part_name"]
                                mid = p["message_id"]
                                local_part_path = os.path.join(tmpdir, pname)
                                await cluster.download_file_by_message_id(mid, local_part_path)
                                downloaded_paths.append(local_part_path)
                                progress.advance(dl_task)

                    with console.status("[bold green]Verifying SHA-256 hashes, decrypting, and extracting files...[/bold green]"):
                        archiver.extract_snapshot(
                            manifest=manifest,
                            part_files=downloaded_paths,
                            passphrase=passphrase,
                            destination_dir=dest_dir,
                            in_place=is_in_place
                        )

            console.print(f"\n[bold green]Restore completed successfully![/bold green]")
            if is_in_place:
                console.print(f"Files restored directly to original location: [bold]{', '.join(orig_paths)}[/bold]")
            else:
                console.print(f"Restored files located in: [bold]{dest_dir}[/bold]")


async def do_check(args):
    """
    ---------------------------------------------------------------------------
    Function: do_check
    Description:
        Cloud scrub integrity check. Queries Telegram for each message_id
        recorded in local Vault to ensure no chunks have been deleted or missing.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    cfg = load_config(args.config)
    mock = getattr(args, "mock", False)
    cluster = BotCluster(
        tokens=cfg["bot_tokens"],
        channel_id=cfg["channel_id"],
        use_topics=cfg.get("use_topics", True),
        api_endpoint=cfg.get("api_endpoint"),
        rate_limit_per_minute=cfg.get("rate_limit_per_minute", 20),
        mock=mock
    )

    console.print(Panel.fit("[bold cyan]TGBackup - Cloud Scrub & Integrity Verification[/bold cyan]"))

    async with VaultDB() as db:
        parts = await db.get_all_parts()
        if not parts:
            console.print("[yellow]No chunks present in local database to verify.[/yellow]")
            return

        table = Table(title=f"Cloud Integrity Check ({len(parts)} total chunks)")
        table.add_column("Chunk File", style="cyan")
        table.add_column("Snapshot ID")
        table.add_column("Message ID", justify="center")
        table.add_column("Cloud Status", justify="center")

        verified_count = 0
        missing_count = 0

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console
        ) as progress:
            check_task = progress.add_task("[cyan]Scanning remote chunks on Telegram...", total=len(parts))

            for p in parts:
                mid = p["message_id"]
                exists = await cluster.check_message_exists(mid)
                if exists:
                    verified_count += 1
                    status_badge = "[green]PRESENT[/green]"
                else:
                    missing_count += 1
                    status_badge = "[red]MISSING[/red]"

                table.add_row(p["part_name"], p["snapshot_id"], str(mid), status_badge)
                progress.advance(check_task)

        console.print(table)
        if missing_count == 0:
            console.print(f"\n[bold green]TOTAL INTEGRITY CONFIRMED: {verified_count}/{len(parts)} chunks verified on the cloud.[/bold green]")
        else:
            console.print(f"\n[bold red]WARNING: {missing_count} chunks appear deleted or missing from Telegram![/bold red]")


async def do_reindex(args):
    """
    ---------------------------------------------------------------------------
    Function: do_reindex
    Description:
        Disaster Recovery from Telegram: completely rebuilds vault.db from the
        encrypted master catalog previously pinned in the channel.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    with ProcessLock():
        cfg = load_config(args.config)
        mock = getattr(args, "mock", False)
        staging_dir = cfg.get("staging_dir")
        if staging_dir:
            os.makedirs(staging_dir, exist_ok=True)

        cluster = BotCluster(
            tokens=cfg["bot_tokens"],
            channel_id=cfg["channel_id"],
            use_topics=cfg.get("use_topics", True),
            api_endpoint=cfg.get("api_endpoint"),
            rate_limit_per_minute=cfg.get("rate_limit_per_minute", 20),
            mock=mock
        )

        mid = getattr(args, "message_id", None)
        if not mid:
            console.print("[cyan]No --message-id specified. Querying Telegram for pinned catalog...[/cyan]")
            mid = await cluster.get_pinned_catalog_message_id(thread_id=cfg.get("notification_topic_id"))
            if not mid:
                console.print("[red]Could not auto-detect pinned catalog message ID. Specify it manually with --message-id <id>[/red]")
                return
            console.print(f"[green]Auto-detected pinned catalog at message ID: {mid}[/green]")

        passphrase = get_passphrase(cfg)

        console.print(Panel(
            f"[bold cyan]Disaster Recovery from Telegram[/bold cyan]\n"
            f"Retrieving encrypted catalog from Telegram message #{mid}...",
            title="TGBackup Reindex",
            border_style="yellow"
        ))

        with tempfile.TemporaryDirectory(dir=staging_dir) as tmpdir:
            dest_enc = os.path.join(tmpdir, "vault_catalog.enc")
            await cluster.download_file_by_message_id(int(mid), dest_enc)

            with open(dest_enc, "rb") as f:
                enc_data = f.read()

            raw_data = decrypt_bytes(enc_data, passphrase)
            catalog_dict = json.loads(raw_data.decode("utf-8"))

            async with VaultDB() as db:
                await db.import_catalog(catalog_dict)
                snaps = await db.get_snapshots()
                parts = await db.get_all_parts()

            console.print(f"[bold green]Vault catalog restored successfully![/bold green]")
            console.print(f"Reindexed snapshots: [bold]{len(snaps)}[/bold] | Registered chunks: [bold]{len(parts)}[/bold]")


async def do_status(args):
    """
    ---------------------------------------------------------------------------
    Function: do_status
    Description:
        System diagnostics and health check: queries bot connectivity,
        vault metrics, and systemd backup timer status. Outputs human-readable
        or JSON format.
    Input parameters:
        args (argparse.Namespace): Parsed command line arguments.
    Return value:
        None
    Exceptions raised:
        None
    ---------------------------------------------------------------------------
    """
    cfg = load_config(args.config)
    mock = getattr(args, "mock", False)
    cluster = BotCluster(
        tokens=cfg["bot_tokens"],
        channel_id=cfg["channel_id"],
        use_topics=cfg.get("use_topics", True),
        mock=mock
    )

    timer_info = get_timer_info()
    as_json = getattr(args, "json", False)
    if as_json:
        bots_info = await cluster.verify_bots()
        async with VaultDB() as db:
            snaps = await db.get_snapshots()
            latest = snaps[0] if snaps else None
            parts = await db.get_all_parts()

        data = {
            "staging_dir": cfg.get("staging_dir"),
            "local_backup_dir": cfg.get("local_backup_dir"),
            "chunk_size_mb": cfg.get("chunk_size_mb", 19),
            "status": "ok",
            "supergroup_id": cfg.get("channel_id"),
            "profiles": list(cfg.get("profiles", {}).keys()),
            "total_snapshots": len(snaps),
            "total_parts": len(parts),
            "latest_snapshot": {
                "id": latest["id"],
                "profile": latest["profile"],
                "timestamp": latest["timestamp"],
                "uncompressed_bytes": latest["uncompressed_bytes"],
                "compressed_bytes": latest["compressed_bytes"],
                "compressed_str": format_size(latest["compressed_bytes"] or 0)
            } if latest else None,
            "timer": timer_info,
            "bots": {
                "total": len(bots_info),
                "online": len([b for b in bots_info if b.get("valid")])
            }
        }
        print(json.dumps(data, indent=2))
        return

    console.print(Panel.fit("[bold cyan]TGBackup - System Diagnostics & Status[/bold cyan]"))
    console.print(f"• [bold]Configuration:[/bold] {args.config or get_default_config_path()}")
    console.print(f"• [bold]Supergroup ID:[/bold] {cfg.get('channel_id')}")
    console.print(f"• [bold]Registered profiles:[/bold] {len(cfg.get('profiles', {}))}")
    console.print(f"• [bold]Chunk Size:[/bold] {cfg.get('chunk_size_mb', 19)} MB")
    if cfg.get("staging_dir"):
        console.print(f"• [bold]Staging Directory:[/bold] {cfg.get('staging_dir')}")
    if cfg.get("local_backup_dir"):
        console.print(f"• [bold]Local HDD Mirror:[/bold] {cfg.get('local_backup_dir')}")

    with console.status("[cyan]Verifying Telegram bots...[/cyan]"):
        bots_info = await cluster.verify_bots()

    table = Table(title="Telegram Bot Cluster")
    table.add_column("Bot ID", justify="center")
    table.add_column("Username / Details")
    table.add_column("Status", justify="center")

    for b in bots_info:
        if b["valid"]:
            name = b.get("first_name") or ""
            table.add_row(str(b["index"]), f"@{b.get('username')} ({name})", "[green]ONLINE[/green]")
        else:
            table.add_row(str(b["index"]), b.get("error", "Error"), "[red]ERROR[/red]")
    console.print(table)

    async with VaultDB() as db:
        snaps = await db.get_snapshots()
        console.print(f"\n• [bold]Snapshots stored in Vault:[/bold] {len(snaps)}")

    if timer_info.get("active") and timer_info.get("next_run"):
        left_str = f" ({timer_info['next_left']})" if timer_info.get("next_left") else ""
        console.print(f"• [bold]Next Scheduled Backup:[/bold] [green]{timer_info['next_run']}{left_str}[/green]")
    elif timer_info.get("enabled"):
        console.print(f"• [bold]Systemd Timer:[/bold] [yellow]Enabled (Waiting)[/yellow]")
    else:
        console.print(f"• [bold]Systemd Timer:[/bold] [dim]Disabled[/dim]")

    console.print("\n• [bold]Systemd Timer Raw Status:[/bold]")
    console.print(f"[dim]{timer_info.get('raw', '').strip()}[/dim]")


def main():
    """
    ---------------------------------------------------------------------------
    Function: main
    Description:
        CLI entry point for TGBackup. Parses arguments, handles subcommands,
        and routes execution to asynchronous handlers.
    Input parameters:
        None
    Return value:
        None
    Exceptions raised:
        SystemExit: On exit or command failure.
    ---------------------------------------------------------------------------
    """
    base_parser = argparse.ArgumentParser(add_help=False)
    base_parser.add_argument("--config", "-c", help="Alternative path to config.json", default=None)
    base_parser.add_argument("--mock", action="store_true", help="Run in MOCK mode without calling Telegram APIs")

    parser = argparse.ArgumentParser(
        prog="tgbackup",
        description="TGBackup - Native Linux Cloud Backup to Telegram with AES-256-GCM & zstd",
        parents=[base_parser]
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # init
    subparsers.add_parser("init", help="Interactive setup wizard", parents=[base_parser])

    # backup
    parser_backup = subparsers.add_parser("backup", help="Run an immediate backup (incremental or full)", parents=[base_parser])
    parser_backup.add_argument("profile", nargs="?", help="Name of profile to back up")
    parser_backup.add_argument("--all", action="store_true", help="Run all profiles")
    parser_backup.add_argument("--full", action="store_true", help="Force a Full Backup, ignoring incremental state")
    parser_backup.add_argument("--local-dir", default=None, help="Secondary local backup directory mirror (e.g. external HDD)")
    parser_backup.add_argument("--json-progress", action="store_true", help="Output machine-readable progress events to stdout")

    # list
    parser_list = subparsers.add_parser("list", help="List available snapshots", parents=[base_parser])
    parser_list.add_argument("profile", nargs="?", help="Filter by profile name")

    # restore
    parser_restore = subparsers.add_parser("restore", help="Restore a snapshot", parents=[base_parser])
    parser_restore.add_argument("snapshot_id", nargs="?", default=None, help="ID of snapshot to restore (optional, interactive selection if omitted)")
    parser_restore.add_argument("destination", nargs="?", default=None, help="Target extraction directory (optional, prompts if omitted)")
    parser_restore.add_argument("--source", choices=["local", "telegram", "cloud"], default=None, help="Restore source: 'local' (mirror) or 'telegram' (cloud)")
    parser_restore.add_argument("--in-place", "--original", action="store_true", default=False, help="Restore files directly to their original location recorded in snapshot")
    parser_restore.add_argument("--local-dir", default=None, help="Path to local backup mirror directory (overrides config)")

    # check (cloud scrub)
    subparsers.add_parser("check", help="Verify integrity of remote chunks on Telegram", parents=[base_parser])

    # reindex (disaster recovery)
    parser_reindex = subparsers.add_parser("reindex", help="Rebuild vault.db from pinned encrypted catalog on Telegram", parents=[base_parser])
    parser_reindex.add_argument("--message-id", "-m", required=False, default=None, help="Telegram message ID of pinned catalog (auto-detected if omitted)")

    # schedule
    parser_sched = subparsers.add_parser("schedule", help="Manage automated background schedule with systemd", parents=[base_parser])
    parser_sched.add_argument("action", choices=["enable", "disable", "status"], help="Timer action")
    parser_sched.add_argument("--cron", default="daily", help="OnCalendar expression (default: daily)")

    # status
    parser_status = subparsers.add_parser("status", help="Check health of bot cluster and database", parents=[base_parser])
    parser_status.add_argument("--json", action="store_true", help="Output status in machine-readable JSON format")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "init":
            asyncio.run(interactive_init(args.config))
        elif args.command == "backup":
            asyncio.run(do_backup(args))
        elif args.command == "list":
            asyncio.run(do_list(args))
        elif args.command == "restore":
            asyncio.run(do_restore(args))
        elif args.command == "check":
            asyncio.run(do_check(args))
        elif args.command == "reindex":
            asyncio.run(do_reindex(args))
        elif args.command == "status":
            asyncio.run(do_status(args))
        elif args.command == "schedule":
            if args.action == "enable":
                cfg = None
                try:
                    cfg = load_config(args.config)
                except Exception:
                    pass
                cron_expr = args.cron
                if cron_expr == "daily" and cfg and cfg.get("schedule"):
                    cron_expr = cfg["schedule"]
                elif cron_expr != "daily" and cfg:
                    cfg["schedule"] = cron_expr
                    save_config(cfg, args.config)
                res = enable_schedule(cron_expr)
                console.print(f"[green]{res}[/green]")
            elif args.action == "disable":
                res = disable_schedule()
                console.print(f"[yellow]{res}[/yellow]")
            elif args.action == "status":
                console.print(get_schedule_status())
    except ProcessLockedError as e:
        if "--json-progress" in sys.argv:
            print(json.dumps({"event": "progress", "phase": "error", "percent": 100, "message": "Lock Error: Another backup process is already running"}), flush=True)
        console.print(f"[red]Lock Error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        if "--json-progress" in sys.argv:
            print(json.dumps({"event": "progress", "phase": "error", "percent": 100, "message": "Operation interrupted by user."}), flush=True)
        console.print("\n[yellow]Operation interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as e:
        if "--json-progress" in sys.argv:
            print(json.dumps({"event": "progress", "phase": "error", "percent": 100, "message": f"Error: {e}"}), flush=True)
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
