"""
===============================================================================
Project      : TGBackup
File         : tgbackup/systemd.py
Description  : Integration and management of Systemd User units (Service & Timer).
Purpose      : Enables users to schedule periodic automated backups via native
               systemd user timers (~/.config/systemd/user/), providing routines
               to setup, enable, disable, and inspect timer status.
===============================================================================
"""

import os
import sys
import shutil
import subprocess
from typing import Tuple

# Non-privileged user systemd directory
USER_SYSTEMD_DIR = os.path.expanduser("~/.config/systemd/user")

# Oneshot service unit template
SERVICE_TEMPLATE = """[Unit]
Description=TGBackup Automated Telegram Cloud Backup
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_path} backup --all
Nice=19
IOSchedulingClass=idle
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

# Scheduled timer unit template
TIMER_TEMPLATE = """[Unit]
Description=TGBackup Scheduled Backup Timer
After=network-online.target

[Timer]
OnCalendar={schedule}
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
"""


def get_executable_path() -> str:
    """
    ---------------------------------------------------------------------------
    Function: get_executable_path
    Description:
        Detects the absolute path of the 'tgbackup' binary on the host system,
        resolving from PATH or deriving from the active Python interpreter.
    
    Input parameters:
        None
    
    Return value:
        @return (str) : Absolute path to the tgbackup executable.
    ---------------------------------------------------------------------------
    """
    which_path = shutil.which("tgbackup")
    if which_path:
        return which_path
    # Fallback derived from active Python environment
    return sys.executable.replace("python", "tgbackup")


def setup_systemd_units(schedule: str = "daily") -> Tuple[str, str]:
    """
    ---------------------------------------------------------------------------
    Function: setup_systemd_units
    Description:
        Generates and writes tgbackup.service and tgbackup.timer unit files
        into the user's ~/.config/systemd/user/ directory, followed by
        invoking 'systemctl --user daemon-reload'.
    
    Input parameters:
        @param schedule (str)            : Systemd OnCalendar expression (e.g. 'daily', 'hourly').
    
    Return value:
        @return (Tuple[str, str])        : (service_path, timer_path).
    ---------------------------------------------------------------------------
    """
    os.makedirs(USER_SYSTEMD_DIR, exist_ok=True)
    exec_path = get_executable_path()
    
    service_content = SERVICE_TEMPLATE.format(exec_path=exec_path)
    timer_content = TIMER_TEMPLATE.format(schedule=schedule)

    service_file = os.path.join(USER_SYSTEMD_DIR, "tgbackup.service")
    timer_file = os.path.join(USER_SYSTEMD_DIR, "tgbackup.timer")

    with open(service_file, "w", encoding="utf-8") as f:
        f.write(service_content)

    with open(timer_file, "w", encoding="utf-8") as f:
        f.write(timer_content)

    # Reload systemd user daemon
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    except Exception:
        pass

    return service_file, timer_file


def enable_schedule(schedule: str = "daily") -> str:
    """
    ---------------------------------------------------------------------------
    Function: enable_schedule
    Description:
        Configures and enables the systemd user timer for automated backups,
        activating it immediately ('enable --now').
    
    Input parameters:
        @param schedule (str) : Schedule expression for the timer (e.g. 'daily').
    
    Return value:
        @return (str)         : Status message confirming enablement.
    
    Exceptions raised:
        @raises RuntimeError  : If systemctl command fails.
    ---------------------------------------------------------------------------
    """
    setup_systemd_units(schedule=schedule)
    cmd = ["systemctl", "--user", "enable", "--now", "tgbackup.timer"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Error enabling systemd timer: {res.stderr}")
    return "Systemd timer successfully enabled and started."


def disable_schedule() -> str:
    """
    ---------------------------------------------------------------------------
    Function: disable_schedule
    Description:
        Disables and stops the systemd backup timer ('disable --now').
    
    Input parameters:
        None
    
    Return value:
        @return (str)         : Confirmation message.
    
    Exceptions raised:
        @raises RuntimeError  : If systemctl command fails.
    ---------------------------------------------------------------------------
    """
    cmd = ["systemctl", "--user", "disable", "--now", "tgbackup.timer"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Error disabling systemd timer: {res.stderr}")
    return "Systemd timer disabled."


def get_schedule_status() -> str:
    """
    ---------------------------------------------------------------------------
    Function: get_schedule_status
    Description:
        Queries operational status by running 'systemctl --user status tgbackup.timer'.
    
    Input parameters:
        None
    
    Return value:
        @return (str) : Status output string from systemctl.
    ---------------------------------------------------------------------------
    """
    cmd = ["systemctl", "--user", "status", "tgbackup.timer"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout or res.stderr


def get_timer_info() -> Dict[str, Any]:
    """
    ---------------------------------------------------------------------------
    Function: get_timer_info
    Description:
        Queries systemd for structured timer attributes: active state, enabled state,
        next scheduled execution timestamp, and remaining time duration.
    
    Input parameters:
        None
    
    Return value:
        @return (Dict[str, Any]) : Structured timer state with next_run and next_left.
    ---------------------------------------------------------------------------
    """
    cmd = [
        "systemctl", "--user", "show", "tgbackup.timer",
        "--property=ActiveState,SubState,UnitFileState,NextElapseUSecRealtime,TimersCalendar"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    props: Dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    active = props.get("ActiveState") == "active"
    enabled = props.get("UnitFileState") == "enabled"
    next_elapse = props.get("NextElapseUSecRealtime", "")
    if next_elapse in ("", "0", "n/a"):
        next_elapse = None

    next_left = None
    if active:
        lt_cmd = ["systemctl", "--user", "list-timers", "--plain", "--no-legend", "tgbackup.timer"]
        lt_res = subprocess.run(lt_cmd, capture_output=True, text=True)
        parts = lt_res.stdout.strip().split()
        for p in parts:
            if any(p.endswith(sfx) for sfx in ("s", "min", "h", "d", "y")):
                next_left = f"in {p}"
                break

    return {
        "active": active,
        "enabled": enabled,
        "next_run": next_elapse,
        "next_left": next_left,
        "raw": get_schedule_status().strip()
    }
