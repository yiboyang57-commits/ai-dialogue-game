# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：单文件 GUI 程序（无控制台、自包含，避免 .app 内部软链被 iCloud 破坏）。
# 用法：pip install pyinstaller && pyinstaller game.spec
#   输出：dist/AI对话模拟器（单文件可执行；macOS 再经 build_macos.sh 包成 .app）

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('cacert.pem', '.')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AI对话模拟器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # GUI 程序，不弹终端
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
