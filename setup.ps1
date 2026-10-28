#Requires -Version 5.1
<#
  AIPlays 安装和启动工具 —— 让 AI 自己玩博德之门 3
  双击 AIPlays.bat 打开菜单；也可以直接带参数：
      powershell -ExecutionPolicy Bypass -File setup.ps1 install   # 一键安装 / 修复
      powershell -ExecutionPolicy Bypass -File setup.ps1 start     # 启动
      powershell -ExecutionPolicy Bypass -File setup.ps1 doctor    # 检查状态
#>
param([string]$Action = "")

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dir       = Join-Path $Root "director"
$Venv      = Join-Path $Dir "bg3-env"
$Py        = Join-Path $Venv "Scripts\python.exe"
$PyW       = Join-Path $Venv "Scripts\pythonw.exe"
$Pak       = Join-Path $Root "mod\AIPlays.pak"
$KeysFile  = Join-Path $Dir "keys.env"
$LocalCfg  = Join-Path $Root "aiplays-local.json"
$Larian    = Join-Path $env:LOCALAPPDATA "Larian Studios\Baldur's Gate 3"
$ModsDir   = Join-Path $Larian "Mods"
$ModSet    = Join-Path $Larian "PlayerProfiles\Public\modsettings.lsx"
$ModUUID   = "9e5f5584-0f24-4635-b786-b36efe037f6f"
$OverlayUrl = "http://localhost:8765/overlay.html"

# API key：服务、环境变量名、申请网址、说明
$Services = @(
    @{ Name = "Claude";   Env = "ANTHROPIC_API_KEY"; Url = "https://console.anthropic.com/settings/keys"; Note = "Anthropic" },
    @{ Name = "GPT";      Env = "OPENAI_API_KEY";    Url = "https://platform.openai.com/api-keys";        Note = "OpenAI" },
    @{ Name = "Gemini";   Env = "GEMINI_API_KEY";    Url = "https://aistudio.google.com/apikey";          Note = "Google AI Studio" },
    @{ Name = "DeepSeek"; Env = "DEEPSEEK_API_KEY";  Url = "https://platform.deepseek.com/api_keys";      Note = "也用来总结长期记忆，建议填" },
    @{ Name = "Grok";     Env = "XAI_API_KEY";       Url = "https://console.x.ai";                        Note = "xAI" },
    @{ Name = "Qwen";     Env = "DASHSCOPE_API_KEY"; Url = "https://www.alibabacloud.com/product/model-studio"; Note = "阿里云百炼 / QwenCloud；国内账号见 README" },
    @{ Name = "Mistral";  Env = "MISTRAL_API_KEY";   Url = "https://console.mistral.ai/api-keys";         Note = "默认没分配角色，可不填" },
    @{ Name = "MiniMax 语音"; Env = "MINIMAX_API_KEY"; Url = "https://www.minimax.io";                    Note = "可选：情感丰富的语音；不填就用免费的微软语音" }
)

# ---------------------------------------------------------------- 输出小工具
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Bad($t)  { Write-Host "  [X]  $t" -ForegroundColor Red }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Step($t) { Write-Host ""; Write-Host "== $t ==" -ForegroundColor Cyan }
function PauseKey { Write-Host ""; Read-Host "按回车继续" | Out-Null }

function Read-Local {
    if (Test-Path $LocalCfg) { try { return Get-Content $LocalCfg -Raw -Encoding UTF8 | ConvertFrom-Json } catch {} }
    return [pscustomobject]@{ bg3 = "" }
}
function Save-Local($obj) { $obj | ConvertTo-Json | Set-Content $LocalCfg -Encoding UTF8 }

# ---------------------------------------------------------------- Python
function Get-PyVersion($exe, $extra) {
    try {
        $args2 = @()
        if ($extra) { $args2 += $extra }
        $args2 += @("-c", "import sys;print('%d.%d' % sys.version_info[:2])")
        $v = & $exe @args2 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) { return $v }
    } catch {}
    return $null
}

function Find-SystemPython {
    # 1) py 启动器  2) PATH 里的 python（跳过微软商店的假 python）  3) 常见安装位置
    foreach ($ver in @("-3.12", "-3.13", "-3.11", "-3")) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            if (Get-PyVersion "py" $ver) { return @{ Exe = "py"; Extra = $ver } }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") {
        if (Get-PyVersion $cmd.Source $null) { return @{ Exe = $cmd.Source; Extra = $null } }
    }
    $cands = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    foreach ($c in $cands) { if (Get-PyVersion $c.FullName $null) { return @{ Exe = $c.FullName; Extra = $null } } }
    return $null
}

function Install-Python {
    $p = Find-SystemPython
    if ($p) { Ok "已找到 Python"; return $p }
    Warn "没找到 Python 3.10 以上的版本"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "  正在用 winget 安装 Python 3.12（只装给当前用户，不需要管理员）……"
        & winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $p = Find-SystemPython
        if ($p) { Ok "Python 安装好了"; return $p }
    }
    Bad "没能自动安装 Python。"
    Say "  请打开 https://www.python.org/downloads/ 下载 Python 3.12，安装时勾选“Add python.exe to PATH”，装好后重新运行本工具。" Yellow
    Start-Process "https://www.python.org/downloads/"
    return $null
}

function Setup-Venv {
    Step "Python 环境"
    if (-not (Test-Path $Py)) {
        $p = Install-Python
        if (-not $p) { return $false }
        Say "  创建虚拟环境 director\bg3-env ……"
        $a = @(); if ($p.Extra) { $a += $p.Extra }; $a += @("-m", "venv", $Venv)
        & $p.Exe @a
        if (-not (Test-Path $Py)) { Bad "虚拟环境创建失败"; return $false }
    } else { Ok "虚拟环境已存在" }
    Say "  安装 / 更新依赖（第一次要一两分钟）……"
    & $Py -m pip install --disable-pip-version-check -q --upgrade pip
    & $Py -m pip install --disable-pip-version-check -q -r (Join-Path $Dir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Bad "依赖安装失败：检查网络（国内可能需要代理或镜像），然后再运行一次“一键安装”"; return $false }
    Ok "依赖安装好了"
    return $true
}

# ---------------------------------------------------------------- 博德之门 3
function Test-BG3Folder($p) { return ($p -and (Test-Path (Join-Path $p "bin\bg3.exe"))) }

function Find-BG3 {
    $local = Read-Local
    if (Test-BG3Folder $local.bg3) { return $local.bg3 }
    $roots = @()
    foreach ($k in @("HKCU:\Software\Valve\Steam", "HKLM:\SOFTWARE\WOW6432Node\Valve\Steam", "HKLM:\SOFTWARE\Valve\Steam")) {
        try {
            $item = Get-ItemProperty $k -ErrorAction Stop
            foreach ($n in @("SteamPath", "InstallPath")) { if ($item.$n) { $roots += ($item.$n -replace "/", "\") } }
        } catch {}
    }
    $libs = @()
    foreach ($r in ($roots | Select-Object -Unique)) {
        $libs += $r
        $vdf = Join-Path $r "steamapps\libraryfolders.vdf"
        if (Test-Path $vdf) {
            foreach ($m in [regex]::Matches((Get-Content $vdf -Raw), '"path"\s+"([^"]+)"')) {
                $libs += ($m.Groups[1].Value -replace "\\\\", "\")
            }
        }
    }
    foreach ($l in ($libs | Select-Object -Unique)) {
        $p = Join-Path $l "steamapps\common\Baldurs Gate 3"
        if (Test-BG3Folder $p) { return $p }
    }
    # GOG
    foreach ($k in @("HKLM:\SOFTWARE\WOW6432Node\GOG.com\Games\1456460669", "HKLM:\SOFTWARE\GOG.com\Games\1456460669")) {
        try { $p = (Get-ItemProperty $k -ErrorAction Stop).path; if (Test-BG3Folder $p) { return $p } } catch {}
    }
    return $null
}

function Get-BG3 {
    $p = Find-BG3
    while (-not $p) {
        Warn "没自动找到博德之门 3 的安装位置。"
        Say "  在 Steam 里右键博德之门 3 → 管理 → 浏览本地文件，把打开的文件夹地址复制过来。"
        $in = (Read-Host "  粘贴游戏文件夹（里面有 bin 文件夹；直接回车跳过）").Trim('" ')
        if (-not $in) { return $null }
        if (Test-BG3Folder $in) { $p = $in } else { Bad "这个文件夹里没有 bin\bg3.exe" }
    }
    $local = Read-Local; $local | Add-Member -NotePropertyName bg3 -NotePropertyValue $p -Force; Save-Local $local
    return $p
}

function Test-GameRunning {
    return [bool](Get-Process -Name "bg3", "bg3_dx11" -ErrorAction SilentlyContinue)
}

function Wait-GameClosed {
    while (Test-GameRunning) {
        Warn "博德之门 3 正在运行。mod 文件被游戏锁着，请先完全退出游戏。"
        Read-Host "  退出游戏后按回车" | Out-Null
    }
}

# ---------------------------------------------------------------- Script Extender
function Install-ScriptExtender($bg3) {
    Step "Script Extender（mod 需要它）"
    $dll = Join-Path $bg3 "bin\DWrite.dll"
    if (Test-Path $dll) { Ok "已安装（它会在游戏启动时自动更新自己）"; return $true }
    Say "  正在从 GitHub 下载 Norbyte 的 BG3 Script Extender ……"
    try {
        $rel = Invoke-RestMethod "https://api.github.com/repos/Norbyte/bg3se/releases/latest" -UseBasicParsing -Headers @{ "User-Agent" = "AIPlays-setup" }
        $asset = $rel.assets | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
        if (-not $asset) { throw "最新版本里没找到 zip 文件" }
        $tmp = Join-Path $env:TEMP ("bg3se_" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        $zip = Join-Path $tmp $asset.name
        Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $tmp -Force
        $found = Get-ChildItem $tmp -Recurse -Filter "DWrite.dll" | Select-Object -First 1
        if (-not $found) { throw "压缩包里没有 DWrite.dll" }
        Wait-GameClosed
        Copy-Item $found.FullName $dll -Force
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Ok "Script Extender 装好了（$($rel.tag_name)）。第一次进游戏时它会自己下载完整组件。"
        return $true
    } catch {
        Bad "自动安装失败：$($_.Exception.Message)"
        Say "  请手动安装：打开下面的网页，下载最新的 zip，把里面的 DWrite.dll 放进" Yellow
        Say "  $(Join-Path $bg3 'bin')" Yellow
        Start-Process "https://github.com/Norbyte/bg3se/releases"
        return $false
    }
}

# ---------------------------------------------------------------- mod
function Get-Hash($p) { if (Test-Path $p) { return (Get-FileHash $p -Algorithm SHA256).Hash } return "" }

function Test-ModRegistered {
    if (-not (Test-Path $ModSet)) { return $false }
    return (Get-Content $ModSet -Raw -Encoding UTF8) -match $ModUUID
}

# 先写到临时文件再替换，写一半出错也不会把原文件弄坏
function Save-Xml($xml, $path) {
    $tmp = "$path.tmp"
    $settings = New-Object System.Xml.XmlWriterSettings
    $settings.Encoding = New-Object System.Text.UTF8Encoding($false)
    $settings.Indent = $true
    $w = [System.Xml.XmlWriter]::Create($tmp, $settings)
    try { $xml.PSBase.Save($w) } finally { $w.Close() }   # 根元素叫 <save>，会盖住 Save 方法，所以走 PSBase
    Move-Item $tmp $path -Force
}

function Register-Mod {
    if (-not (Test-Path $ModSet)) {
        Warn "还没有 modsettings.lsx：先启动一次博德之门 3 到主菜单，再退出游戏，然后重新运行“一键安装”。"
        return $false
    }
    if (Test-ModRegistered) { Ok "mod 已在 modsettings.lsx 里启用"; return $true }
    $backup = $ModSet -replace "\.lsx$", (".backup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".lsx")
    Copy-Item $ModSet $backup
    $xml = New-Object System.Xml.XmlDocument
    $xml.PreserveWhitespace = $true
    $xml.Load($ModSet)
    $mods = $xml.SelectSingleNode("//node[@id='Mods']")
    if (-not $mods) { Bad "modsettings.lsx 的格式看不懂（没有 Mods 节点），请用 BG3 Mod Manager 手动启用 AIPlays"; return $false }
    $children = $mods.SelectSingleNode("children")
    if (-not $children) { $children = $xml.CreateElement("children"); [void]$mods.AppendChild($children) }
    $node = $xml.CreateElement("node"); $node.SetAttribute("id", "ModuleShortDesc")
    $attrs = @(
        @("Folder", "LSString", "AIPlays"), @("MD5", "LSString", ""), @("Name", "LSString", "AIPlays"),
        @("PublishHandle", "uint64", "0"), @("UUID", "guid", $ModUUID), @("Version64", "int64", "36028797018963968")
    )
    foreach ($a in $attrs) {
        $e = $xml.CreateElement("attribute"); $e.SetAttribute("id", $a[0]); $e.SetAttribute("type", $a[1]); $e.SetAttribute("value", $a[2])
        [void]$node.AppendChild($e)
    }
    [void]$children.AppendChild($node)
    Save-Xml $xml $ModSet
    Ok "已在 modsettings.lsx 里启用 AIPlays（原文件备份为 $(Split-Path $backup -Leaf)）"
    return $true
}

function Install-Mod {
    Step "安装 AIPlays mod"
    if (-not (Test-Path $Pak)) { Bad "找不到 mod\AIPlays.pak（下载的文件不完整？）"; return $false }
    $dest = Join-Path $ModsDir "AIPlays.pak"
    if ((Get-Hash $dest) -eq (Get-Hash $Pak)) {
        Ok "mod 文件已是最新"
    } else {
        Wait-GameClosed
        New-Item -ItemType Directory -Path $ModsDir -Force | Out-Null
        Copy-Item $Pak $dest -Force
        Ok "mod 文件已复制到 $ModsDir"
    }
    return (Register-Mod)
}

function Uninstall-Mod {
    Step "卸载 AIPlays mod"
    Wait-GameClosed
    $dest = Join-Path $ModsDir "AIPlays.pak"
    if (Test-Path $dest) { Remove-Item $dest -Force; Ok "已删除 AIPlays.pak" } else { Ok "mod 文件本来就不在" }
    if (Test-ModRegistered) {
        Copy-Item $ModSet ($ModSet -replace "\.lsx$", ".before-uninstall.lsx") -Force
        $xml = New-Object System.Xml.XmlDocument; $xml.PreserveWhitespace = $true; $xml.Load($ModSet)
        foreach ($n in @($xml.SelectNodes("//node[@id='ModuleShortDesc'][attribute[@id='UUID' and @value='$ModUUID']]"))) {
            [void]$n.ParentNode.RemoveChild($n)
        }
        Save-Xml $xml $ModSet
        Ok "已从 modsettings.lsx 移除"
    }
    Say "  （Python 环境、API key 等文件留在本文件夹，不需要了直接删除整个文件夹即可）"
}

# ---------------------------------------------------------------- API key
function Read-Keys {
    $h = @{}
    if (Test-Path $KeysFile) {
        foreach ($line in Get-Content $KeysFile -Encoding UTF8) {
            if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$') { $h[$Matches[1]] = $Matches[2].Trim('"', "'") }
        }
    }
    return $h
}

function Write-Keys($h) {
    $lines = @("# AIPlays 的 API key（只存在你自己电脑上，不会上传；千万别把这个文件发给别人或者在直播里打开）")
    foreach ($s in $Services) { if ($h[$s.Env]) { $lines += "$($s.Env)=$($h[$s.Env])" } }
    $known = @($Services | ForEach-Object { $_.Env })
    foreach ($k in $h.Keys) { if ($known -notcontains $k -and $h[$k]) { $lines += "$k=$($h[$k])" } }   # 自己加的其他 key 保留
    $enc = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllLines($KeysFile, $lines, $enc)
}

function Read-Secret($prompt) {
    $sec = Read-Host $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

function Set-Keys {
    Step "填写 API key"
    Say "  key 会保存到 director\keys.env（只在你电脑上）。输入时不显示字符，防止直播时泄露。"
    Say "  每一项：粘贴 key 后回车；直接回车 = 不改；输入 o = 打开申请网页；输入 - = 删除这个 key。"
    $h = Read-Keys
    foreach ($s in $Services) {
        $state = "未填"; if ($h[$s.Env] -or [Environment]::GetEnvironmentVariable($s.Env)) { $state = "已填" }
        Say ""
        Say ("  {0}（{1}）—— {2}" -f $s.Name, $state, $s.Note) White
        while ($true) {
            $v = (Read-Secret "    key").Trim()
            if ($v -eq "o" -or $v -eq "O") { Start-Process $s.Url; continue }
            if ($v -eq "-") { $h.Remove($s.Env); Say "    已删除"; break }
            if ($v) { $h[$s.Env] = $v; Say "    已保存" Green }
            break
        }
    }
    Write-Keys $h
    Ok "已保存到 director\keys.env"
    Say "  可以用菜单里的“测试连接”看看 key 能不能用。"
}

# ---------------------------------------------------------------- 运行
function Need-Venv {
    if (Test-Path $Py) { return $true }
    Warn "还没安装 Python 环境，先运行菜单 1（一键安装）"
    return $false
}

function Write-Launchers {
    # 给不想打开菜单的人：director 里两个可以直接双击的 bat
    $enc = New-Object System.Text.ASCIIEncoding
    $start = "@echo off`r`nrem AIPlays director - double-click to start`r`ncd /d `"%~dp0`"`r`nbg3-env\Scripts\python.exe director.py %*`r`npause`r`n"
    $hint  = "@echo off`r`nrem AIPlays producer hint window`r`ncd /d `"%~dp0`"`r`nstart `"`" bg3-env\Scripts\pythonw.exe hint_box.py`r`n"
    [IO.File]::WriteAllText((Join-Path $Dir "start_director.bat"), $start, $enc)
    [IO.File]::WriteAllText((Join-Path $Dir "hint_box.bat"), $hint, $enc)
}

function Start-AIPlays {
    if (-not (Need-Venv)) { return }
    Write-Launchers
    Step "启动"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "`"$(Join-Path $Dir 'start_director.bat')`"" -WorkingDirectory $Dir
    Ok "调度器已在新窗口启动（那个黑窗口别关，玩完按 Ctrl+C 或直接关掉）"
    if (Test-Path $PyW) {
        Start-Process -FilePath $PyW -ArgumentList "hint_box.py" -WorkingDirectory $Dir
        Ok "导演提示小窗口已打开（AI 卡关时写一句提示给它们）"
    }
    try { Set-Clipboard $OverlayUrl; $clip = "（已复制到剪贴板）" } catch { $clip = "" }
    Say ""
    Say "  OBS 字幕地址：$OverlayUrl $clip" Cyan
    Say "  现在启动博德之门 3、读档，轮到 AI 的角色时它们就会自己行动。"
}

function Show-OBS {
    Step "OBS 设置（字幕 + 语音）"
    Say "  1. OBS 里“来源”点 + → 浏览器"
    Say "  2. URL 填：$OverlayUrl"
    Say "     宽 1920，高 1080"
    Say "  3. 勾选“通过 OBS 控制音频”（AI 的语音就会进直播 / 录像）"
    Say "  4. 想自己也听到：混音器里这个浏览器源 → 高级音频属性 → 音频监听选“监听并输出”"
    Say "  5. 字幕不出来：先确认调度器窗口开着；再右键浏览器源 → 属性 → 刷新当前页面的缓存"
    try { Set-Clipboard $OverlayUrl; Say "  （地址已复制到剪贴板）" Green } catch {}
}

function Run-Py($script) {
    if (-not (Need-Venv)) { return }
    Push-Location $Dir
    try { & $Py $script } finally { Pop-Location }
}

function Update-AIPlays {
    Step "更新"
    $inst = Join-Path $Root "install.ps1"
    if (-not (Test-Path $inst)) { Bad "找不到 install.ps1"; return }
    # install.ps1 没有 BOM（方便 irm | iex），这里按 UTF-8 读进来再执行，中文才不会乱码
    $code = [IO.File]::ReadAllText($inst, [Text.Encoding]::UTF8)
    & ([scriptblock]::Create($code)) -Target $Root -NoMenu
    Say "  更新完成。如果 mod 有变化，退出游戏后运行菜单 1 重新安装 mod。" Cyan
}

# ---------------------------------------------------------------- 检查状态
function Show-Doctor {
    Step "检查状态"
    if (Test-Path $Py) {
        & $Py -c "import yaml, anthropic, openai, edge_tts" 2>$null
        if ($LASTEXITCODE -eq 0) { Ok "Python 环境和依赖" } else { Bad "依赖不完整 → 运行菜单 1" }
    } else { Bad "Python 环境没装 → 运行菜单 1" }
    $bg3 = Find-BG3
    if ($bg3) { Ok "博德之门 3：$bg3" } else { Bad "没找到博德之门 3 → 运行菜单 1 手动指定" }
    if ($bg3 -and (Test-Path (Join-Path $bg3 "bin\DWrite.dll"))) { Ok "Script Extender" } elseif ($bg3) { Bad "Script Extender 没装 → 运行菜单 1" }
    $dest = Join-Path $ModsDir "AIPlays.pak"
    if (-not (Test-Path $dest)) { Bad "mod 没装 → 运行菜单 1" }
    elseif ((Get-Hash $dest) -ne (Get-Hash $Pak)) { Warn "mod 不是最新版 → 退出游戏后运行菜单 1" }
    else { Ok "mod 已是最新版" }
    if (Test-ModRegistered) { Ok "mod 已启用" } else { Bad "mod 没在 modsettings.lsx 里启用 → 运行菜单 1" }
    $modLog = Join-Path $Larian "Script Extender\AIPlays_log.txt"
    if (Test-Path $modLog) { Ok ("mod 在游戏里运行过（最近：" + (Get-Item $modLog).LastWriteTime.ToString("MM-dd HH:mm") + "）") }
    else { Warn "还没检测到 mod 在游戏里运行过（装好后进一次游戏读档再看）" }
    $h = Read-Keys
    $have = @($Services | Where-Object { $h[$_.Env] -or [Environment]::GetEnvironmentVariable($_.Env) } | ForEach-Object { $_.Name })
    if ($have.Count -gt 0) { Ok ("已填 key：" + ($have -join "、")) } else { Bad "一个 API key 都没填 → 运行菜单 2" }
    if (Test-GameRunning) { Say "  游戏正在运行" } else { Say "  游戏没在运行" }
}

# ---------------------------------------------------------------- 一键安装
function Do-Install {
    $ok = Setup-Venv
    if ($ok) { Write-Launchers }
    $bg3 = Get-BG3
    if ($bg3) {
        Ok "博德之门 3：$bg3"
        [void](Install-ScriptExtender $bg3)
    } else { Bad "没有游戏路径，跳过 Script Extender" }
    [void](Install-Mod)
    if (-not (Test-Path $KeysFile)) {
        Say ""
        $ans = Read-Host "还没有填 API key，现在填吗？(Y/n)"
        if ($ans -ne "n" -and $ans -ne "N") { Set-Keys }
    }
    Say ""
    Say "安装完成。下一步：菜单 3 选择谁玩哪个角色（可跳过，用默认），菜单 4 测试连接，菜单 5 启动。" Cyan
}

# ---------------------------------------------------------------- 菜单
function Show-Menu {
    while ($true) {
        Clear-Host
        Say "============================================" Cyan
        Say "   AIPlays —— 让 AI 自己玩博德之门 3" Cyan
        Say "============================================" Cyan
        Say "  1. 一键安装 / 修复（Python、依赖、Script Extender、mod）"
        Say "  2. 填写 API key"
        Say "  3. 选择谁来玩哪个角色"
        Say "  4. 测试 AI 和语音的连接"
        Say "  5. 启动！（调度器 + 导演提示窗口）"
        Say "  6. 检查状态"
        Say "  7. OBS 字幕设置说明"
        Say "  8. 卸载 mod"
        Say "  9. 更新到最新版（保留你的 key、角色分配和记忆）"
        Say "  0. 退出"
        $c = Read-Host "`n选一个数字"
        try {
            switch ($c) {
                "1" { Do-Install; PauseKey }
                "2" { Set-Keys; PauseKey }
                "3" { Run-Py "setup_party.py"; PauseKey }
                "4" { Run-Py "check_keys.py"; PauseKey }
                "5" { Start-AIPlays; PauseKey }
                "6" { Show-Doctor; PauseKey }
                "7" { Show-OBS; PauseKey }
                "8" { Uninstall-Mod; PauseKey }
                "9" { Update-AIPlays; PauseKey }
                "0" { return }
                default { }
            }
        } catch {
            Bad "出错了：$($_.Exception.Message)"
            Say "  把这段红字截图发到 GitHub Issues，方便排查。" Yellow
            PauseKey
        }
    }
}

switch ($Action.ToLower()) {
    "install" { Do-Install }
    "start"   { Start-AIPlays }
    "doctor"  { Show-Doctor }
    "keys"    { Set-Keys }
    "party"   { Run-Py "setup_party.py" }
    "check"   { Run-Py "check_keys.py" }
    "uninstall" { Uninstall-Mod }
    "lib"     { }   # 只加载函数（测试用）
    default   { Show-Menu }
}
