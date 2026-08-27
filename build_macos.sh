#!/bin/bash
# 一键打包 macOS 双击即玩的 .app（单文件自包含，无内部软链，抗 iCloud 同步破坏）。
set -e
cd "$(dirname "$0")"

export PYINSTALLER_CONFIG_DIR="$PWD/.pyinstaller-cache"

echo "==> 1/4 打包单文件可执行"
rm -rf build dist
.venv/bin/pyinstaller --noconfirm game.spec

APP="dist/AI对话模拟器.app"
echo "==> 2/4 组装 .app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleDisplayName</key><string>AI对话模拟器</string>
	<key>CFBundleExecutable</key><string>AI对话模拟器</string>
	<key>CFBundleIconFile</key><string>icon.icns</string>
	<key>CFBundleIdentifier</key><string>com.dsh.aidialogsimulator</string>
	<key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
	<key>CFBundleName</key><string>AI对话模拟器</string>
	<key>CFBundlePackageType</key><string>APPL</string>
	<key>CFBundleShortVersionString</key><string>1.0.0</string>
	<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

mv "dist/AI对话模拟器" "$APP/Contents/MacOS/AI对话模拟器"
chmod +x "$APP/Contents/MacOS/AI对话模拟器"
cp icon.icns "$APP/Contents/Resources/icon.icns"
if [ -f config.json ]; then cp config.json "$APP/Contents/MacOS/config.json"; fi

echo "==> 3/4 签名"
xattr -cr "$APP" 2>/dev/null || true
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "==> 4/4 完成"
echo "成品：$PWD/$APP"
