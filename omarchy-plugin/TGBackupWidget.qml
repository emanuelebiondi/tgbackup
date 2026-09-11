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
  property int backupPercent: 0
  property string backupPhase: ""
  property string backupStatusMsg: ""
  property bool backupJustFinished: false
  property bool backupFailed: false
  property bool hasError: statusData ? (statusData.bots && statusData.bots.online === 0) : false
  property string tooltipMsg: "TGBackup Cloud: Initializing..."

  // Nerd Font Icon: 󰁯 (nf-md-cloud_sync), 󰅚 (nf-md-close_circle), or 󰋼 (nf-md-cloud_check)
  readonly property string iconGlyph: (hasError || backupFailed) ? "󰅚" : (backupRunning ? "󰁯" : "󰋼")

  // Dynamic status color
  readonly property color statusColor: {
    if (backupFailed || hasError) return Color.urgent
    if (backupRunning) return Color.accent
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

  function handleProgressLine(line) {
    if (!line) return
    var str = String(line).trim()
    var jsonStart = str.indexOf('{"event":')
    if (jsonStart !== -1) {
      try {
        var obj = JSON.parse(str.substring(jsonStart))
        if (obj.event === "progress") {
          root.backupPercent = obj.percent || 0
          root.backupPhase = obj.phase || "working"
          root.backupStatusMsg = obj.message || "Backup in progress..."
          if (obj.phase === "error") {
            root.backupFailed = true
          }
        }
      } catch (e) {}
    }
  }

  function startQuickBackup(forceFull) {
    if (runBackupProc.running) return
    root.backupPercent = 0
    root.backupPhase = "starting"
    root.backupStatusMsg = "Starting backup..."
    root.backupJustFinished = false
    root.backupFailed = false
    resetFinishedTimer.stop()
    runBackupProc.command = forceFull ? 
      ["tgbackup", "backup", "--all", "--full", "--json-progress"] : 
      ["tgbackup", "backup", "--all", "--json-progress"]
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
        var nextStr = (data.timer && data.timer.active) ? 
          ("Next: " + (data.timer.next_left ? data.timer.next_left : (data.timer.next_run || "Scheduled"))) : 
          "Schedule: Disabled"
        root.tooltipMsg = "TGBackup Cloud\n" + snapInfo + "\n" + nextStr + "\nLeft Click: Panel | Right Click: Quick Backup"
      } catch (e) {
        root.hasError = true
      }
    }
  }

  // Background quick backup process with streaming JSON progress
  Process {
    id: runBackupProc
    command: ["tgbackup", "backup", "--all", "--json-progress"]
    stdout: SplitParser {
      onRead: function(line) {
        root.handleProgressLine(line)
      }
    }
    stderr: StdioCollector {
      id: runBackupErr
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.backupPercent = 100
      root.backupJustFinished = (exitCode === 0)
      root.backupFailed = (exitCode !== 0)
      if (exitCode === 0) {
        root.backupStatusMsg = "Backup completed successfully!"
      } else {
        var err = String(runBackupErr.text || "").trim()
        if (err.indexOf("Lock Error") !== -1 || err.indexOf("already running") !== -1) {
          root.backupStatusMsg = "Lock error: Another backup is already in progress"
        } else if (err.length > 0) {
          var lines = err.split("\n")
          var firstErr = lines[0]
          for (var i = 0; i < lines.length; i++) {
            var l = lines[i].trim()
            if (l.length > 0 && l.indexOf("Traceback") === -1) {
              firstErr = l
              break
            }
          }
          root.backupStatusMsg = firstErr.replace(/\[\/?\w+\]/g, "")
        } else if (!root.backupStatusMsg || root.backupStatusMsg.indexOf("Error") === -1) {
          root.backupStatusMsg = "Backup failed (exit code " + exitCode + ")"
        }
      }
      root.refresh()
      resetFinishedTimer.restart()
    }
  }

  Timer {
    id: resetFinishedTimer
    interval: 6000
    running: false
    repeat: false
    onTriggered: {
      root.backupJustFinished = false
      root.backupFailed = false
      root.backupPercent = 0
      root.backupPhase = ""
      root.backupStatusMsg = ""
    }
  }

  Timer {
    interval: root.refreshSec * 1000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.backupRunning ? (root.iconGlyph + "  " + root.backupPercent + "%") : root.iconGlyph
    active: root.backupRunning || root.hasError || root.backupFailed
    activeColor: (root.hasError || root.backupFailed) ? Color.urgent : Color.accent
    foreground: root.statusColor
    fontSize: Style.bar.iconFont
    fixedWidth: root.backupRunning ? -1 : (root.vertical ? -1 : Style.bar.iconSlot)
    horizontalMargin: 8
    tooltipText: root.backupRunning ? ("TGBackup: " + root.backupStatusMsg + " (" + root.backupPercent + "%)") : root.tooltipMsg

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
