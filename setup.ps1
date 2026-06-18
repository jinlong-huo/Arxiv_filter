# ================================================================
# setup.ps1 — 安装 arXiv Daily Digest 每日定时任务 (Windows)
# ================================================================
# 用法：右键 → 以管理员身份运行 PowerShell，然后：
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#   .\setup.ps1
# ================================================================

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "arXiv Daily Digest"
$PythonScript = Join-Path $ScriptDir "Arxiv_filter.py"

Write-Host "=== arXiv Daily Digest Setup (Windows) ==="
Write-Host ""

# 1. 找到 python3
Write-Host "[1/4] 查找 python3 ..."
$PythonBin = $null
foreach ($candidate in @(
    (Get-Command python3 -ErrorAction SilentlyContinue).Source,
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
)) {
    if ($candidate -and (Test-Path $candidate)) {
        $result = & $candidate -c "import feedparser; print('ok')" 2>&1
        if ($result -eq "ok") {
            $PythonBin = $candidate
            break
        }
    }
}

if (-not $PythonBin) {
    Write-Host "  -> feedparser 未找到，尝试安装 ..."
    $PythonBin = (Get-Command python3 -ErrorAction SilentlyContinue).Source
    if (-not $PythonBin) { $PythonBin = (Get-Command python -ErrorAction SilentlyContinue).Source }
    if (-not $PythonBin) {
        Write-Host "  [错误] 未找到 Python，请先安装: https://www.python.org/downloads/"
        exit 1
    }
    & $PythonBin -m pip install --quiet feedparser
}
Write-Host "  -> 使用: $PythonBin"

# 2. 删除旧任务（如果存在）
Write-Host "[2/4] 清理旧任务 ..."
schtasks /delete /tn "$TaskName" /f 2>$null
Write-Host "  -> 已清理"

# 3. 创建每日定时任务（每天 10:00 BJ 时间，仅工作日）
Write-Host "[3/4] 创建计划任务（每天 10:00 BJ 时间） ..."
$Action = New-ScheduledTaskAction -Execute $PythonBin `
    -Argument "`"$PythonScript`" --send" `
    -WorkingDirectory $ScriptDir

# 触发器：每天 10:00；错过则在就绪后一小时内补执行
$Trigger = New-ScheduledTaskTrigger -Daily -At 10:00
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew

$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName "$TaskName" `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "arXiv Daily Digest — 每日抓取 arXiv 论文并发送邮件" `
    -Force | Out-Null

Write-Host "  -> 已创建：每天 10:00 BJ 时间 + 错过补执行 + 仅联网时运行"

# 4. 提醒邮件配置
Write-Host "[4/4] 邮件配置提醒"
Write-Host ""
Write-Host "========================================"
Write-Host "  还需要手动编辑 Arxiv_filter.py 中的 EMAIL_CONFIG："
Write-Host "    sender:      你的 Gmail 地址"
Write-Host "    recipient:   接收邮箱"
Write-Host ""
Write-Host "  密码通过以下方式之一设置："
Write-Host "    1. 环境变量: setx ARXIV_DIGEST_EMAIL_PASSWORD ""你的密码"""
Write-Host "    2. 本地文件: 在脚本目录创建 .email_password（一行密码）"
Write-Host ""
Write-Host "  获取 Gmail 应用专用密码："
Write-Host "    https://myaccount.google.com/apppasswords"
Write-Host ""
Write-Host "  测试："
Write-Host "    python3 Arxiv_filter.py --send"
Write-Host "========================================"
Write-Host ""
Write-Host "常用命令（PowerShell）："
Write-Host "  查看任务:      schtasks /query /tn ""$TaskName"" /v"
Write-Host "  手动触发:      schtasks /run /tn ""$TaskName"""
Write-Host "  删除任务:      schtasks /delete /tn ""$TaskName"" /f"
Write-Host "  查看状态:      Get-ScheduledTask -TaskName ""$TaskName"""
