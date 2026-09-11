import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "tgbackup"

  readonly property int refreshSec: Math.max(5, Math.min(120, setting("refreshIntervalSec", 15)))
  
  property var statusData: null
  property bool backupRunning: runBackupProc.running
  property bool hasError: statusData ? (statusData.bots && statusData.bots.online === 0) : false
  property string tooltipMsg: "TGBackup Cloud: Initializing..."

  // Nerd Font Icon: 󰁯 (nf-md-cloud_sync) or 󰋼 (nf-md-cloud_check)
  readonly property string iconGlyph: backupRunning ? "󰁯" : (hasError ? "󰅚" : "󰋼")

  // Dynamic status color
  readonly property color statusColor: {
    if (backupRunning) return Color.accent
    if (hasError) return Color.urgent
    if (statusData && statusData.timer && statusData.timer.active) return Color.accent
    return (bar ? bar.barForeground : Color.foreground)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!statusProc.running) {
      statusProc.running = true
    }
  }

  // Shape contract for Omarchy bar popup routing (Bar.findPanelWidget)
  readonly property bool opened: panel ? panel.opened : false
  function open() { if (panel) panel.open() }
  function close() { if (panel) panel.close() }
  readonly property bool popoutSwitchClosing: panel ? panel.popoutSwitchClosing === true : false
  function closeForPopoutSwitch() {
    if (panel) panel.closeForPopoutSwitch()
  }

  function togglePanel() {
    if (opened) close()
    else open()
  }

  function startQuickBackup(forceFull) {
    if (runBackupProc.running) return
    runBackupProc.command = forceFull ? 
      ["tgbackup", "backup", "--all", "--full"] : 
      ["tgbackup", "backup", "--all"]
    runBackupProc.running = true
  }

  function openFloatingTerminal(cmd) {
    if (root.bar) {
      root.bar.run("omarchy-launch-floating-terminal-with-presentation " + cmd)
    } else {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", cmd])
    }
  }

  function openConfigEditor() {
    if (root.bar) {
      root.bar.run("omarchy-launch-editor ~/.config/tgbackup/config.json")
    } else {
      Quickshell.execDetached(["omarchy-launch-editor", "~/.config/tgbackup/config.json"])
    }
  }

  // Continuous background status polling via CLI
  Process {
    id: statusProc
    command: ["tgbackup", "status", "--json"]
    stdout: StdioCollector {
      id: statusOut
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.hasError = true
        root.tooltipMsg = "TGBackup: Error or unconfigured ('tgbackup init')"
        return
      }
      try {
        var raw = String(statusOut.text || "").trim()
        var data = JSON.parse(raw)
        root.statusData = data
        root.hasError = false
        
        var snapInfo = data.latest_snapshot ? 
          ("Latest: " + data.latest_snapshot.timestamp.substring(0, 16) + " (" + data.latest_snapshot.compressed_str + ")") : 
          "No snapshots found"
        var timerInfo = data.timer && data.timer.active ? "Timer: Active" : "Timer: Inactive"
        root.tooltipMsg = "TGBackup Cloud\n" + snapInfo + "\n" + timerInfo + "\nLeft Click: Panel | Right Click: Quick Backup"
      } catch (e) {
        root.hasError = true
      }
    }
  }

  // Background quick backup process
  Process {
    id: runBackupProc
    command: ["tgbackup", "backup", "--all"]
    onExited: function(exitCode) {
      root.refresh()
    }
  }

  Timer {
    interval: root.refreshSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.iconGlyph
    foreground: root.statusColor
    tooltipText: root.tooltipMsg
    onPressed: function(mouseButton) {
      if (mouseButton === Qt.RightButton) {
        root.startQuickBackup(false)
      } else {
        root.togglePanel()
      }
    }
  }

  TGBackupPanel {
    id: panel
    bar: root.bar
    anchorItem: button
    hostWidget: root
  }
}
