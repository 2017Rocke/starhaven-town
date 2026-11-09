#!/bin/zsh
set -eu
APP="${HOME}/Applications/星栖小镇.app"
if [[ ! -d "$APP" ]]; then
  echo "未找到桌面版：$APP"
  echo "请先打开 dist/ 里的 macOS 安装包完成安装。"
  exit 1
fi
open "$APP"
