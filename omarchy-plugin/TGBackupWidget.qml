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

  implicitWidth: root.backupRunning ? (button.slotSize + percentText.implicitWidth + Style.space(6)) : button.slotSize
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
          root.backupStatusMsg = obj.message || "Operazione in corso..."
        }
      } catch (e) {}
    }
  }

  function startQuickBackup(forceFull) {
    if (runBackupProc.running) return
    root.backupPercent = 0
    root.backupPhase = "starting"
    root.backupStatusMsg = "Avvio backup in corso..."
    root.backupJustFinished = false
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
    onExited: function(exitCode) {
      root.backupPercent = 100
      root.backupJustFinished = true
      root.backupStatusMsg = (exitCode === 0) ? "Backup completato con successo!" : "Errore durante il backup"
      root.refresh()
      resetFinishedTimer.restart()
    }
  }

  Timer {
    id: resetFinishedTimer
    interval: 5000
    running: false
    repeat: false
    onTriggered: {
      root.backupJustFinished = false
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

  Row {
    anchors.centerIn: parent
    spacing: Style.space(2)

    BarIconButton {
      id: button
      bar: root.bar
      text: root.iconGlyph
      foreground: root.statusColor
      tooltipText: root.backupRunning ? ("TGBackup: " + root.backupStatusMsg + " (" + root.backupPercent + "%)") : root.tooltipMsg
      onPressed: function(mouseButton) {
        if (mouseButton === Qt.RightButton) {
          root.startQuickBackup(false)
        } else {
          root.togglePanel()
        }
      }
    }

    Text {
      id: percentText
      visible: root.backupRunning
      anchors.verticalCenter: parent.verticalCenter
      text: root.backupPercent + "%"
      color: Color.accent
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }

  TGBackupPanel {
    id: panel
    bar: root.bar
    anchorItem: button
    hostWidget: root
  }
}
