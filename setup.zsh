#!/bin/zsh
# ================================================================
# setup.zsh — 安装 arXiv Daily Digest 每日定时任务 (macOS / zsh)
# ================================================================
set -euo pipefail

SCRIPT_DIR="${0:A:h}"                           # zsh: 脚本所在目录的绝对路径
PLIST_SRC="$SCRIPT_DIR/com.arxiv.daily-digest.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.arxiv.daily-digest.plist"
LABEL="com.arxiv.daily-digest"

echo "=== arXiv Daily Digest Setup ==="
echo ""

# 1. 找到有 feedparser 的 python3
echo "[1/5] 查找可用的 python3 ..."
PYTHON_BIN=""
for candidate in \
    "$SCRIPT_DIR/.conda/bin/python3" \
    "$(which python3 2>/dev/null)" \
    "/opt/homebrew/bin/python3" \
    "/usr/bin/python3"; do
    if [[ -n "$candidate" && -x "$candidate" ]] && "$candidate" -c "import feedparser" 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "  -> feedparser 未安装，尝试安装到当前 python3 ..."
    PYTHON_BIN="$(which python3 2>/dev/null || echo '/usr/bin/python3')"
    "$PYTHON_BIN" -m pip install --quiet feedparser
fi
echo "  -> 使用: $PYTHON_BIN"

# 2. 修正 plist 里的路径，写入 ~/Library/LaunchAgents
echo "[2/5] 生成 launchd plist ..."
mkdir -p "$HOME/Library/LaunchAgents"
sed \
    -e "s|/Users/Vir-G/Downloads/Projects/QLab-stack|$SCRIPT_DIR|g" \
    -e "s|/usr/bin/python3|$PYTHON_BIN|g" \
    -e "s|/opt/anaconda3/envs/JLtorch/bin/python3|$PYTHON_BIN|g" \
    "$PLIST_SRC" > "$PLIST_DST"
echo "  -> 已写入 $PLIST_DST"

# 3. 卸载旧版本，加载新版本
echo "[3/5] 加载 launchd 任务 ..."
launchctl bootout gui/$(id -u)/$LABEL 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$PLIST_DST"
echo "  -> 已加载（每天 10:00 BJ 时间，仅工作日；唤醒/开机时补执行）"

# 4. 配置防止 Mac 深度休眠（可选）
echo "[4/5] 休眠策略提醒 ..."
echo ""
echo "  ⚠️  MacBook 合盖后默认会休眠，launchd 任务无法执行。"
echo "  如果希望合盖也能准时跑，在终端执行："
echo ""
echo "    sudo pmset -a powernap 1"
echo ""
echo "  这会开启 Power Nap，让 Mac 在休眠期间间歇唤醒以执行后台任务。"
echo "  （仅限插电状态；电池模式下仍需开盖）"

# 5. 邮件配置提醒
echo "[5/5] 邮件配置提醒"
echo ""
echo "========================================"
echo "  还需要手动编辑 Arxiv_filter.py 中的 EMAIL_CONFIG："
echo ""
echo "    sender:      你的 Gmail 地址"
echo "    password:    Gmail 应用专用密码"
echo "    recipient:   接收邮箱"
echo ""
echo "  获取 Gmail 应用专用密码："
echo "    https://myaccount.google.com/apppasswords"
echo "    （需要先开启两步验证）"
echo ""
echo "  测试："
echo "    python3 Arxiv_filter.py --send"
echo "========================================"
echo ""
echo "常用命令："
echo "  查看任务状态:  launchctl list | grep arxiv"
echo "  手动触发一次:  launchctl start $LABEL"
echo "  停止定时任务:  launchctl bootout gui/\$(id -u)/$LABEL"
echo "  查看近期日志:  tail -30 launchd_stdout.log"
echo "  查看错误日志:  tail -30 launchd_stderr.log"
