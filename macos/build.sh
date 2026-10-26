#!/usr/bin/env bash
# 构建并安装 Server Vault 菜单栏应用到 ~/Applications(需要 Xcode 命令行工具)
# 用法:./macos/build.sh        可选环境变量 SV_PORT(默认 8780)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${SV_PORT:-8780}"
BUILD="$ROOT/macos/build"
APP="$HOME/Applications/Server Vault.app"
mkdir -p "$BUILD" "$HOME/Applications"

# 1. Python 环境(与 run.sh 相同)
cd "$ROOT"
if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then uv venv -q .venv; else python3 -m venv .venv; fi
fi
if ! .venv/bin/python -c "import fastapi, paramiko, cryptography, uvicorn" 2>/dev/null; then
  if command -v uv >/dev/null 2>&1; then uv pip install -q -p .venv/bin/python -r requirements.txt
  else .venv/bin/pip install -q -r requirements.txt; fi
fi

# 2. 编译
echo "编译中…"
swiftc -O -o "$BUILD/ServerVault" "$ROOT/macos/main.swift" -framework AppKit -framework WebKit -framework ServiceManagement

# 3. 图标
if [ ! -f "$BUILD/AppIcon.icns" ]; then
  swift "$ROOT/macos/icon.swift" "$BUILD/icon.png"
  ICONSET="$BUILD/AppIcon.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$BUILD/icon.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$BUILD/AppIcon.icns"
fi

# 4. 组装 .app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD/ServerVault" "$APP/Contents/MacOS/ServerVault"
cp "$BUILD/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Server Vault</string>
  <key>CFBundleDisplayName</key><string>Server Vault</string>
  <key>CFBundleIdentifier</key><string>com.sikefix.servervault</string>
  <key>CFBundleExecutable</key><string>ServerVault</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$ROOT/app/config.py" | head -1)</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoads</key><true/><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>ServerVaultHome</key><string>$ROOT</string>
  <key>ServerVaultPort</key><string>$PORT</string>
</dict></plist>
PLIST
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "已安装:$APP"
echo "启动:open \"$APP\"   (顶部菜单栏会出现盾牌锁图标)"
