"""
===============================================================================
Project      : TGBackup
File         : tgbackup/notify.py
Description  : Native Linux desktop notification module.
Purpose      : Sends desktop notifications to Wayland/X11 notification servers via
               the standard 'notify-send' utility or D-Bus, allowing users to monitor
               backup start, completion, and critical errors in the background.
               Completely configurable (enable/disable) in config.json.
===============================================================================
"""

import os
import shutil
import subprocess
import logging
from typing import Optional

logger = logging.getLogger("tgbackup.notify")


def is_desktop_session_available() -> bool:
    """
    ---------------------------------------------------------------------------
    Function: is_desktop_session_available
    Description:
        Checks whether a graphical session (Wayland or X11) is active and
        whether the standard notify-send utility is available in PATH.
    Input parameters:
        None
    Return value:
        @return (bool) : True if desktop notifications can be dispatched.
    ---------------------------------------------------------------------------
    """
    has_display = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    has_bin = bool(shutil.which("notify-send"))
    return has_display and has_bin


def send_desktop_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon: str = "security-high",
    enabled: bool = True
) -> bool:
    """
    ---------------------------------------------------------------------------
    Function: send_desktop_notification
    Description:
        Invokes 'notify-send' to display a visual desktop notification in the
        system notification daemon (e.g., Quickshell, Dunst, Mako, SwayNC).
    Input parameters:
        @param title (str)   : Notification title.
        @param message (str) : Notification body content.
        @param urgency (str) : Urgency level: 'low', 'normal', or 'critical'.
        @param icon (str)    : System icon name or file path for the icon.
        @param enabled (bool): If False, notification dispatch is suppressed.
    Return value:
        @return (bool)       : True if the notification was dispatched successfully.
    ---------------------------------------------------------------------------
    """
    if not enabled:
        return False

    if not is_desktop_session_available():
        logger.debug("Desktop session or notify-send not available. Notification skipped.")
        return False

    try:
        cmd = [
            "notify-send",
            "-a", "TGBackup",
            "-u", urgency,
            "-i", icon,
            title,
            message
        ]
        subprocess.run(cmd, capture_output=True, check=False)
        return True
    except Exception as e:
        logger.debug(f"Error while executing notify-send: {e}")
        return False
