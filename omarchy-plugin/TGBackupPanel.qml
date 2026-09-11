import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "tgbackup"
  ipcTarget: "tgbackup"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null

  readonly property var statusData: hostWidget ? hostWidget.statusData : null
  readonly property bool backupRunning: hostWidget ? hostWidget.backupRunning : false
  readonly property bool hasError: hostWidget ? hostWidget.hasError : false

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { if (root.hostWidget) root.hostWidget.refresh(); return "ok" }
    function status(): string { return root.statusData ? JSON.stringify(root.statusData) : "no data" }
    function backup(): string { if (root.hostWidget) root.hostWidget.startQuickBackup(false); return "started" }
  }

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color accent: Color.accent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          // 1. Panel Hero Header
          PanelHero {
            id: hero
            width: parent.width
            title: "TGBackup"
            meta: root.backupRunning ? "BACKING UP..." : (root.statusData ? "CLOUD VAULT READY" : "INITIALIZING")
            detail: (root.statusData && root.statusData.timer && root.statusData.timer.active) ?
              (root.statusData.timer.next_left ? root.statusData.timer.next_left.toUpperCase() : "SCHEDULED") :
              "MANUAL"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                text: root.backupRunning ? "󰁯" : (root.hasError ? "󰅚" : "󰒃")
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                color: root.backupRunning ? root.accent : (root.hasError ? root.urgent : root.foreground)
                anchors.centerIn: parent
              }
            }
            trailingControl: Component {
              PanelActionButton {
                iconText: "󰑐"
                tooltipText: "Refresh status"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: if (root.hostWidget) root.hostWidget.refresh()
              }
            }
          }

          // 2. In-Progress Backup Banner
          CursorSurface {
            visible: root.backupRunning
            width: parent.width
            implicitHeight: backupBannerRow.implicitHeight + Style.space(16)
            foreground: root.foreground
            accent: root.accent
            current: true

            RowLayout {
              id: backupBannerRow
              anchors.fill: parent
              anchors.margins: Style.space(12)
              spacing: Style.space(10)

              Text {
                text: "󰑮"
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                color: root.accent
                Layout.alignment: Qt.AlignVCenter
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(2)
                Layout.alignment: Qt.AlignVCenter

                Text {
                  text: "Backup in Progress"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  text: "Compressing, encrypting, and uploading chunks..."
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          // 3. Vault Metrics Section
          PanelSeparator {
            foreground: root.foreground
          }

          PanelSectionHeader {
            text: "VAULT METRICS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          CursorSurface {
            width: parent.width
            bordered: true
            foreground: root.foreground
            implicitHeight: metricsCol.implicitHeight + Style.space(20)

            Column {
              id: metricsCol
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(12)
              spacing: Style.space(8)

              InfoPair {
                label: "Latest Snapshot"
                value: {
                  if (!root.statusData || !root.statusData.latest_snapshot) return "None"
                  return root.statusData.latest_snapshot.timestamp.replace("T", " ").substring(0, 16)
                }
              }

              InfoPair {
                label: "Encrypted Size"
                value: {
                  if (!root.statusData || !root.statusData.latest_snapshot) return "0 B"
                  return root.statusData.latest_snapshot.compressed_str
                }
              }

              InfoPair {
                label: "Total Snapshots"
                value: String(root.statusData ? (root.statusData.total_snapshots || 0) : 0)
              }

              InfoPair {
                label: "Bot Cluster"
                value: {
                  if (!root.statusData || !root.statusData.bots) return "Not connected"
                  return root.statusData.bots.online + " of " + root.statusData.bots.total + " online"
                }
              }

              InfoPair {
                label: "Local HDD Mirror"
                value: (root.statusData && root.statusData.local_backup_dir) ? root.statusData.local_backup_dir : "Disabled"
              }

              InfoPair {
                label: "Staging Dir"
                value: (root.statusData && root.statusData.staging_dir) ? root.statusData.staging_dir : "Default (~/.cache)"
              }
            }
          }

          // 4. Automated Schedule Section
          PanelSeparator {
            foreground: root.foreground
          }

          PanelSectionHeader {
            text: "AUTOMATED SCHEDULE"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          CursorSurface {
            width: parent.width
            bordered: true
            foreground: root.foreground
            implicitHeight: schedRow.implicitHeight + Style.space(16)

            RowLayout {
              id: schedRow
              anchors.fill: parent
              anchors.margins: Style.space(12)
              spacing: Style.space(10)

              Text {
                text: "󰔛"
                font.family: root.fontFamily
                font.pixelSize: Style.font.heading
                color: (root.statusData && root.statusData.timer && root.statusData.timer.active) ? root.accent : root.dim
                Layout.alignment: Qt.AlignVCenter
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(2)
                Layout.alignment: Qt.AlignVCenter

                Text {
                  text: "Systemd Timer"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  text: {
                    if (!root.statusData || !root.statusData.timer || !root.statusData.timer.active) return "Automated backup disabled"
                    var left = root.statusData.timer.next_left ? ("Next run: " + root.statusData.timer.next_left) : "Active"
                    var date = root.statusData.timer.next_run ? (" • " + root.statusData.timer.next_run.replace(/CEST|CET|UTC/g, "").trim()) : ""
                    return left + date
                  }
                  color: (root.statusData && root.statusData.timer && root.statusData.timer.active) ? root.accent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              ToggleSwitch {
                id: scheduleSwitch
                Layout.alignment: Qt.AlignVCenter
                checked: root.statusData && root.statusData.timer && root.statusData.timer.active
                busy: timerToggleProc.running
                foreground: root.foreground
                onToggled: {
                  var isAct = root.statusData && root.statusData.timer && root.statusData.timer.active
                  timerToggleProc.command = ["tgbackup", "schedule", isAct ? "disable" : "enable"]
                  timerToggleProc.running = true
                }

                PanelToolTip {
                  visible: scheduleSwitch.containsMouse
                  text: scheduleSwitch.checked ? "Disable background timer" : "Enable background timer"
                  fontFamily: root.fontFamily
                }
              }
            }
          }

          // 5. Actions Section
          PanelSeparator {
            foreground: root.foreground
          }

          PanelSectionHeader {
            text: "ACTIONS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Column {
            width: parent.width
            spacing: Style.space(8)

            Row {
              width: parent.width
              spacing: Style.space(8)

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰁪"
                text: "Backup"
                tooltipText: "Run incremental backup for all profiles"
                enabled: !root.backupRunning
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.startQuickBackup(false)
                    root.close()
                  }
                }
              }

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰚥"
                text: "Full Backup"
                tooltipText: "Force complete full backup reset"
                enabled: !root.backupRunning
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.startQuickBackup(true)
                    root.close()
                  }
                }
              }
            }

            Row {
              width: parent.width
              spacing: Style.space(8)

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰄲"
                text: "Cloud Scrub"
                tooltipText: "Verify remote chunk integrity on Telegram"
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.openFloatingTerminal("tgbackup check")
                    root.close()
                  }
                }
              }

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰁩"
                text: "Restore"
                tooltipText: "Browse snapshots and restore files"
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.openFloatingTerminal("bash -c 'tgbackup list; echo; read -p \"Snapshot ID to restore: \" sid; [ -n \"$sid\" ] && tgbackup restore \"$sid\"'")
                    root.close()
                  }
                }
              }
            }

            Row {
              width: parent.width
              spacing: Style.space(8)

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰒓"
                text: "Setup Wizard"
                tooltipText: "Launch interactive configuration wizard"
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.openFloatingTerminal("tgbackup init")
                    root.close()
                  }
                }
              }

              Button {
                width: (parent.width - Style.space(8)) / 2
                bordered: true
                iconText: "󰈔"
                text: "Edit Config"
                tooltipText: "Open config.json in default editor"
                fontFamily: root.fontFamily
                foreground: root.foreground
                onClicked: {
                  if (root.hostWidget) {
                    root.hostWidget.openConfigEditor()
                    root.close()
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    Text {
      textFormat: Text.PlainText
      text: label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
    }

    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth - parent.children[2].implicitWidth - parent.spacing * 2)
      height: 1
    }

    Text {
      textFormat: Text.PlainText
      text: value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      elide: Text.ElideRight
    }
  }

  Process {
    id: timerToggleProc
    command: ["tgbackup", "schedule", "enable"]
    onExited: function() {
      if (root.hostWidget) root.hostWidget.refresh()
    }
  }
}
