#!/bin/bash
# Windows and macOS share manager.py; this launcher supports Intel and Apple Silicon Python.
cd "$(dirname "$0")" || exit 1
launch() {
    local candidate="$1"
    [ -x "$candidate" ] || return 1
    "$candidate" -c 'import sys; assert sys.version_info >= (3, 8)' >/dev/null 2>&1 || return 1
    "$candidate" "$PWD/launch.pyw" --background
    local result=$?
    if [ "$result" -ne 0 ]; then
        printf '\n管理器未能启动。请检查上方错误，确认使用Python 3.10 或更新版本。\n'
        read -r -p '按回车关闭…' _
    fi
    exit "$result"
}
# Prefer python.org universal2 installations; no third-party Python modules are needed.
for candidate in \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3
 do
    launch "$candidate"
 done
candidate="$(command -v python3 2>/dev/null)"
if [ -n "$candidate" ] && [ "$candidate" != /usr/bin/python3 ]; then
    launch "$candidate"
fi
printf '\n未找到Python 3.10 或更新版本。\n'
printf '请安装 python.org 的 macOS universal2 安装包（同时支持 Apple 芯片和 Intel），然后再次双击本文件。\n'
printf '下载地址：https://www.python.org/downloads/macos/\n'
printf '无需 tkinter 或其他第三方 Python 库。\n'
read -r -p '按回车关闭…' _
exit 1
