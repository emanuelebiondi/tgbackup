"""
===============================================================================
Project      : TGBackup
File         : tgbackup/fs_snapshot.py
Description  : Filesystem detector and atomic snapshot manager (Btrfs / ZFS).
Purpose      : Detects if backup source paths reside on filesystems with native
               snapshot capabilities (such as Btrfs subvolumes or ZFS datasets).
               If supported and permissions allow, creates a temporary read-only
               snapshot from which data is consistently read, automatically
               destroying it after backup completion to ensure point-in-time consistency.
===============================================================================
"""

import os
import shutil
import subprocess
import logging
from typing import List, Optional, Tuple, Dict
from contextlib import contextmanager

logger = logging.getLogger("tgbackup.fs_snapshot")


def get_mount_info(path: str) -> Optional[Dict[str, str]]:
    """
    ---------------------------------------------------------------------------
    Function: get_mount_info
    Description:
        Resolves filesystem type (fstype), mount point (target), and source
        device for a given local path using the standard Linux 'findmnt' tool.
    
    Input parameters:
        @param path (str) : Path to the file or directory to inspect.
    
    Return value:
        @return (Optional[Dict[str, str]]) : Dictionary with keys 'fstype',
                                             'target', 'source', or None.
    ---------------------------------------------------------------------------
    """
    abs_path = os.path.abspath(path)
    findmnt_bin = shutil.which("findmnt")
    if not findmnt_bin:
        return None

    try:
        res = subprocess.run(
            [findmnt_bin, "-T", abs_path, "-o", "FSTYPE,TARGET,SOURCE", "-J"],
            capture_output=True,
            text=True,
            check=True
        )
        import json
        data = json.loads(res.stdout)
        filesystems = data.get("filesystems", [])
        if filesystems:
            return filesystems[0]
    except Exception as e:
        logger.debug(f"Could not determine mount info for {abs_path}: {e}")
    return None


@contextmanager
def atomic_snapshot_context(paths: List[str]):
    """
    ---------------------------------------------------------------------------
    Function: atomic_snapshot_context
    Description:
        Context manager that inspects backup source paths:
        - If residing on a Btrfs subvolume and user permissions allow,
          creates a temporary read-only snapshot (.tgbackup_snap_*) and remaps
          source paths to the consistent snapshot.
        - Otherwise (non-Btrfs, ext4, xfs, or insufficient privileges), gracefully
          falls back to reading the original directory paths directly.
        - Upon block exit (finally), ensures the temporary snapshot is deleted.
    
    Input parameters:
        @param paths (List[str]) : List of paths to include in the backup.
    
    Return value:
        @yield (List[str])       : List of paths (native or snapshot-remapped)
                                   from which the Archiver will read files.
    ---------------------------------------------------------------------------
    """
    created_snapshots: List[str] = []
    effective_paths: List[str] = []

    try:
        for p in paths:
            abs_p = os.path.abspath(p)
            mount_info = get_mount_info(abs_p)

            if mount_info and mount_info.get("fstype") == "btrfs" and os.path.isdir(abs_p):
                # Attempt to create read-only Btrfs snapshot
                snap_dir = os.path.join(
                    mount_info.get("target", "/"),
                    f".tgbackup_snap_{os.getpid()}_{len(created_snapshots)}"
                )
                btrfs_bin = shutil.which("btrfs")
                if btrfs_bin:
                    cmd = [btrfs_bin, "subvolume", "snapshot", "-r", abs_p, snap_dir]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    if res.returncode == 0:
                        logger.info(f"[Btrfs] Atomic snapshot created successfully: {snap_dir}")
                        created_snapshots.append(snap_dir)
                        effective_paths.append(snap_dir)
                        continue
                    else:
                        logger.debug(f"Btrfs snapshot unavailable on {abs_p} ({res.stderr.strip()}). Falling back to direct read.")

            # Fallback to standard direct filesystem read
            effective_paths.append(abs_p)

        yield effective_paths

    finally:
        # Automatic cleanup of temporary Btrfs snapshots
        btrfs_bin = shutil.which("btrfs")
        for s in created_snapshots:
            if btrfs_bin and os.path.exists(s):
                try:
                    subprocess.run([btrfs_bin, "subvolume", "delete", s], capture_output=True, check=True)
                    logger.info(f"[Btrfs] Temporary snapshot removed: {s}")
                except Exception as e:
                    logger.warning(f"Could not remove temporary snapshot {s}: {e}")
