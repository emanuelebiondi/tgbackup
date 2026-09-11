# 🛡️ TGBackup - Omarchy Shell Bar Widget & Panel Plugin



Official plugin for **Omarchy Linux Desktop** (Hyprland / Quickshell), integrated into the status bar to monitor Telegram backup status in real-time and trigger quick actions with a single click.

---

## 📦 Plugin Structure

```
omarchy-plugin/
├── manifest.json          # Quickshell metadata, entry point and schema configuration
├── TGBackupWidget.qml     # Dynamic status bar icon with status polling
├── TGBackupPanel.qml      # Interactive popup drawer with live controls
└── README.md              # Documentation
```

---

## 🚀 Installation in Omarchy Desktop

To install and activate the plugin in your Omarchy status bar:

### 1. Create the Symlink
Symlink the plugin folder into `~/.config/omarchy/plugins/`:
```bash
ln -sf "$PWD" ~/.config/omarchy/plugins/tgbackup
# or from your local git clone path:
# ln -sf ~/Documents/tgbackup/omarchy-plugin ~/.config/omarchy/plugins/tgbackup
```

### 2. Add the Widget to the Status Bar
Open `~/.config/omarchy/shell.json` and add `"tgbackup"` into the desired bar section (e.g. `right`):
```json
{
  "bar": {
    "sections": {
      "right": [
        "tgbackup",
        "omarchy.clock"
      ]
    }
  }
}
```
Or via the official Omarchy CLI command:
```bash
omarchy bar move tgbackup --section right
```

The Omarchy bar will automatically hot-reload immediately!

---

## 🎮 Features and Controls

| Action | Description |
| :--- | :--- |
| **Left Click on bar icon** | Opens the interactive popup drawer. |
| **Right Click on bar icon** | Instantly triggers an asynchronous quick backup in background with desktop notification. |
| **🚀 Quick Backup** | Runs `tgbackup backup --all` (high-speed incremental backup). |
| **⚡ Full Backup** | Forces a complete full snapshot regardless of incremental history. |
| **🔍 Cloud Scrub** | Launches remote Telegram chunk integrity verification in a floating terminal. |
| **📥 Restore** | Launches the interactive snapshot selection and restoration wizard. |
| **⏰ Toggle Timer** | Enables or disables the `systemd` user timer for backup automation. |
| **⚙️ Settings** | Launches the guided `tgbackup init` wizard in a floating terminal. |
