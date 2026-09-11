# Maintainer: TGBackup Contributors <contributors@tgbackup.org>
pkgname=tgbackup
pkgver=1.0.0
pkgrel=1
pkgdesc="Native standalone Arch/Linux backup CLI and daemon to Telegram Supergroup Cluster with AES-256-GCM and zstd"
arch=('any')
url="https://github.com/tgbackup/tgbackup"
license=('MIT')
depends=(
    'python'
    'python-cryptography'
    'python-zstandard'
    'python-rich'
    'python-aiosqlite'
    'python-telegram-bot'
)
optdepends=(
    'libnotify: Native desktop notifications (notify-send)'
    'quickshell: Omarchy Shell bar widget and panel support'
)
makedepends=('python-build' 'python-installer' 'python-wheel' 'python-setuptools')
source=("$pkgname-$pkgver.tar.gz::https://github.com/tgbackup/$pkgname/archive/refs/tags/v$pkgver.tar.gz")
# For local builds:
# source=()

build() {
    cd "$srcdir/$pkgname-$pkgver" 2>/dev/null || cd "$startdir"
    python -m build --wheel --no-isolation
}

package() {
    cd "$srcdir/$pkgname-$pkgver" 2>/dev/null || cd "$startdir"
    python -m installer --destdir="$pkgdir" dist/*.whl

    # Install systemd user units
    install -Dm644 systemd/tgbackup.service "$pkgdir/usr/lib/systemd/user/tgbackup.service"
    install -Dm644 systemd/tgbackup.timer "$pkgdir/usr/lib/systemd/user/tgbackup.timer"

    # Install Omarchy Shell Plugin
    install -d "$pkgdir/usr/share/tgbackup/omarchy-plugin"
    install -Dm644 omarchy-plugin/manifest.json "$pkgdir/usr/share/tgbackup/omarchy-plugin/manifest.json"
    install -Dm644 omarchy-plugin/TGBackupWidget.qml "$pkgdir/usr/share/tgbackup/omarchy-plugin/TGBackupWidget.qml"
    install -Dm644 omarchy-plugin/TGBackupPanel.qml "$pkgdir/usr/share/tgbackup/omarchy-plugin/TGBackupPanel.qml"
    install -Dm644 omarchy-plugin/README.md "$pkgdir/usr/share/tgbackup/omarchy-plugin/README.md"
}
