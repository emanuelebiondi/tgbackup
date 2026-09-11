"""
===============================================================================
Project      : TGBackup
File         : tgbackup/lock.py
Description  : Inter-process mutual exclusion locking using POSIX file locks.
Purpose      : Prevents concurrent execution of backup, restore, or reindexing
               operations, avoiding race conditions on the local database and
               conflicts in incremental manifest chaining.
===============================================================================
"""

import os
import fcntl
import logging
from typing import Optional

logger = logging.getLogger("tgbackup.lock")


class ProcessLockedError(Exception):
    """Raised when another instance of TGBackup is already executing."""
    pass


class ProcessLock:
    """
    ---------------------------------------------------------------------------
    Class: ProcessLock
    Description:
        Manages exclusive file-based locking using fcntl.flock to guarantee
        that only one TGBackup worker process runs at any given time.
    ---------------------------------------------------------------------------
    """

    def __init__(self, lock_path: Optional[str] = None):
        if lock_path:
            self.lock_path = lock_path
        else:
            uid = os.getuid() if hasattr(os, "getuid") else 1000
            run_user_dir = f"/run/user/{uid}"
            if os.path.exists(run_user_dir) and os.access(run_user_dir, os.W_OK):
                self.lock_path = os.path.join(run_user_dir, "tgbackup.lock")
            else:
                cache_dir = os.path.expanduser("~/.cache/tgbackup")
                os.makedirs(cache_dir, exist_ok=True)
                self.lock_path = os.path.join(cache_dir, "tgbackup.lock")

        self.file_descriptor = None

    def acquire(self):
        """
        -----------------------------------------------------------------------
        Method: acquire
        Description:
            Attempts to acquire an exclusive non-blocking file lock.
        Input parameters:
            None
        Return value:
            None
        Exceptions raised:
            ProcessLockedError: If lock is already held by another process.
        -----------------------------------------------------------------------
        """
        os.makedirs(os.path.dirname(self.lock_path), exist_ok=True)
        self.file_descriptor = open(self.lock_path, "w")
        try:
            fcntl.flock(self.file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file_descriptor.write(f"{os.getpid()}\n")
            self.file_descriptor.flush()
        except (BlockingIOError, OSError):
            self.file_descriptor.close()
            self.file_descriptor = None
            raise ProcessLockedError(
                f"Another TGBackup process is already running (locked via {self.lock_path})."
            )

    def release(self):
        """
        -----------------------------------------------------------------------
        Method: release
        Description:
            Releases the file lock and closes the descriptor.
        Input parameters:
            None
        Return value:
            None
        Exceptions raised:
            None
        -----------------------------------------------------------------------
        """
        if self.file_descriptor:
            try:
                fcntl.flock(self.file_descriptor, fcntl.LOCK_UN)
                self.file_descriptor.close()
            except Exception as e:
                logger.debug(f"Error releasing lock: {e}")
            finally:
                self.file_descriptor = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
