<#
  AIPlays 一键下载 / 更新。在 PowerShell 里粘贴这一行就行：

      irm https://raw.githubusercontent.com/Lemonadestars/AIPlays/main/install.ps1 | iex

  它会：下载最新版 → 放到你选的文件夹（默认 用户文件夹\AIPlays）→ 保留你的 key、角色分配、记忆 → 打开安装菜单。
#>
param([string]$Target = "", [switch]$NoMenu)

$Repo   = "Lemonadestars/AIPlays"     # GitHub 用户名/仓库名
$Branch = "main"

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }

Say ""
Say "=== AIPlays：让 AI 自己玩博德之门 3 ===" Cyan

if (-not $Target) {
    $default = Join-Path $env:USERPROFILE "AIPlays"
    $in = Read-Host "装到哪个文件夹？（直接回车 = $default）"
    $Target = $in.Trim('" ')
    if (-not $Target) { $Target = $default }
}
$updating = Test-Path (Join-Path $Target "setup.ps1")

# 1. 下载
$tmp = Join-Path $env:TEMP ("aiplays_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "aiplays.zip"
Say "正在下载最新版……"
try {
    Invoke-WebRequest "https://codeload.github.com/$Repo/zip/refs/heads/$Branch" -OutFile $zip -UseBasicParsing
} catch {
    Say "下载失败：$($_.Exception.Message)" Red
    Say "检查网络（国内可能需要代理），或者到 https://github.com/$Repo 手动下载 ZIP。" Yellow
    return
}
Expand-Archive $zip -DestinationPath $tmp -Force
$src = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "setup.ps1") } | Select-Object -First 1
if (-not $src) { Say "下载的压缩包里没找到 setup.ps1" Red; return }
$src = $src.FullName

# 2. 复制（保留你自己的文件）
$keep = @("director\keys.env", "director\party.yaml", "director\memories.json", "director\story_log.txt",
          "director\hint.txt", "aiplays-local.json")
New-Item -ItemType Directory -Path $Target -Force | Out-Null
$cfgOld = Join-Path $Target "director\config.yaml"
$shippedOld = Join-Path $Target "director\.config.shipped.yaml"
$userChangedCfg = $false
if ((Test-Path $cfgOld) -and (Test-Path $shippedOld)) {
    $userChangedCfg = (Get-FileHash $cfgOld).Hash -ne (Get-FileHash $shippedOld).Hash
} elseif (Test-Path $cfgOld) { $userChangedCfg = $true }

Get-ChildItem $src -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($src.Length + 1)
    if ($keep -contains $rel -and (Test-Path (Join-Path $Target $rel))) { return }
    $dest = Join-Path $Target $rel
    if ($rel -eq "director\config.yaml" -and $userChangedCfg) { $dest = Join-Path $Target "director\config.new.yaml" }
    New-Item -ItemType Directory -Path (Split-Path $dest) -Force | Out-Null
    Copy-Item $_.FullName $dest -Force
}
Copy-Item (Join-Path $src "director\config.yaml") $shippedOld -Force
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

if ($updating) { Say "已更新到最新版：$Target" Green } else { Say "已下载到：$Target" Green }
if ($userChangedCfg) {
    Say "你改过 config.yaml，所以保留了你的版本；新版的默认配置在 director\config.new.yaml，需要的话自己对照着改。" Yellow
}

# 3. 桌面快捷方式
if (-not $updating) {
    $ans = Read-Host "在桌面放一个 AIPlays 快捷方式吗？(Y/n)"
    if ($ans -ne "n" -and $ans -ne "N") {
        try {
            $ws = New-Object -ComObject WScript.Shell
            $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "AIPlays.lnk"))
            $lnk.TargetPath = Join-Path $Target "AIPlays.bat"
            $lnk.WorkingDirectory = $Target
            $lnk.Save()
            Say "桌面快捷方式已创建" Green
        } catch { Say "快捷方式创建失败（不影响使用）：$($_.Exception.Message)" Yellow }
    }
}

# 4. 打开安装菜单
if (-not $NoMenu) {
    Say ""
    Say "接下来打开安装菜单。第一次使用请选 1（一键安装）。" Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Target "setup.ps1")
}
