# install.ps1 - Windows 安装脚本 for wtt-match
# 用法: 右键 -> 使用 PowerShell 运行，或在终端中执行:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# 代理支持:
#   - 自动检测 Windows 系统代理设置（IE/系统代理）并配置 HTTP_PROXY/HTTPS_PROXY
#   - 手动指定代理: powershell -ExecutionPolicy Bypass -File install.ps1 --proxy http://127.0.0.1:7890
#   - 如已设置 HTTP_PROXY/HTTPS_PROXY 环境变量，脚本会保持不变

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 切换到脚本所在目录（右键运行时 $PWD 可能不是项目目录）
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $scriptDir

function Write-Step { param([string]$msg) Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Write-Err  { param([string]$msg) Write-Host "    [X] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  wtt-match Windows 安装脚本" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── 0. 检测系统代理并配置环境变量 ────────────────────────────────
Write-Step "检测系统代理设置..."
$proxyConfigured = $false
try {
    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    $proxyEnabled = (Get-ItemProperty -Path $regPath -Name ProxyEnable -ErrorAction SilentlyContinue).ProxyEnable
    $proxyServer  = (Get-ItemProperty -Path $regPath -Name ProxyServer -ErrorAction SilentlyContinue).ProxyServer

    if ($proxyEnabled -and $proxyServer) {
        # 规范化代理地址：如果没有协议前缀则加上 http://
        $proxyUrl = $proxyServer
        if ($proxyUrl -notmatch "^https?://") {
            $proxyUrl = "http://$proxyUrl"
        }

        Write-Ok "检测到系统代理: $proxyUrl"

        # 设置环境变量（覆盖已有值前先检查）
        $envVars = @("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
        foreach ($var in $envVars) {
            $existing = [System.Environment]::GetEnvironmentVariable($var)
            if ($existing) {
                Write-Ok "环境变量 $var 已设置为 $existing（保持不变）"
            } else {
                [System.Environment]::SetEnvironmentVariable($var, $proxyUrl)
                Write-Ok "已设置 $var=$proxyUrl"
            }
        }

        # 同时为 PowerShell 的 WebRequest 配置默认代理
        [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy($proxyUrl, $true)
        [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials

        # 读取代理排除列表（ProxyOverride），映射到 NO_PROXY
        $proxyOverride = (Get-ItemProperty -Path $regPath -Name ProxyOverride -ErrorAction SilentlyContinue).ProxyOverride
        if ($proxyOverride) {
            # Windows 用 ; 分隔，NO_PROXY 用 , 分隔；去掉 <local> 标记
            $noProxy = ($proxyOverride -replace "<local>", "" -split ";" | Where-Object { $_.Trim() }) -join ","
            if ($noProxy) {
                if (-not [System.Environment]::GetEnvironmentVariable("NO_PROXY")) {
                    [System.Environment]::SetEnvironmentVariable("NO_PROXY", $noProxy)
                    [System.Environment]::SetEnvironmentVariable("no_proxy", $noProxy)
                    Write-Ok "已设置 NO_PROXY=$noProxy"
                }
            }
        }

        $proxyConfigured = $true
    } else {
        Write-Ok "未检测到系统代理，使用直连"
    }
} catch {
    Write-Warn "代理检测失败: $_（将使用直连）"
}

# 支持用户通过参数手动指定代理
if ($args -contains "--proxy") {
    $idx = [array]::IndexOf($args, "--proxy")
    if ($idx -lt $args.Count - 1) {
        $manualProxy = $args[$idx + 1]
        if ($manualProxy -notmatch "^https?://") {
            $manualProxy = "http://$manualProxy"
        }
        foreach ($var in @("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")) {
            [System.Environment]::SetEnvironmentVariable($var, $manualProxy)
        }
        [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy($manualProxy, $true)
        [System.Net.WebRequest]::DefaultWebProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
        Write-Ok "已使用手动指定代理: $manualProxy"
        $proxyConfigured = $true
    }
}

# ── 1. 检查 Python ──────────────────────────────────────────────
Write-Step "检查 Python 版本..."
$python = $null
foreach ($cmd in @("python3", "python")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 11) {
                $python = $cmd
                Write-Ok "$ver (使用命令: $cmd)"
                break
            } else {
                Write-Warn "$ver 版本过低，需要 Python 3.11+"
            }
        }
    } catch {
        continue
    }
}
if (-not $python) {
    Write-Err "未找到 Python 3.11+，请先安装: https://www.python.org/downloads/"
    Write-Host "    安装时请勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

# ── 2. 检查/安装 uv ─────────────────────────────────────────────
Write-Step "检查 uv 包管理器..."
$uvInstalled = $false
try {
    $uvVer = & uv --version 2>&1
    Write-Ok "已安装 uv ($uvVer)"
    $uvInstalled = $true
} catch {
    Write-Warn "未找到 uv，正在安装..."
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        # 刷新 PATH，使当前会话能找到 uv
        $uvPath = "$env:USERPROFILE\.local\bin"
        if (Test-Path $uvPath) {
            $env:PATH = "$uvPath;$env:PATH"
        }
        $uvVer = & uv --version 2>&1
        Write-Ok "uv 安装成功 ($uvVer)"
        $uvInstalled = $true
    } catch {
        Write-Err "uv 安装失败: $_"
        Write-Host "    请手动安装: https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Yellow
        exit 1
    }
}

# ── 3. 检查/安装 ffmpeg ─────────────────────────────────────────
Write-Step "检查 ffmpeg..."
$ffmpegOk = $false
try {
    $ffmpegVer = & ffmpeg -version 2>&1 | Select-Object -First 1
    Write-Ok "已安装 ($ffmpegVer)"
    $ffmpegOk = $true
} catch {
    Write-Warn "未找到 ffmpeg"
}

if (-not $ffmpegOk) {
    # 尝试 winget
    $wingetAvailable = $false
    try {
        & winget --version 2>&1 | Out-Null
        $wingetAvailable = $true
    } catch {}

    if ($wingetAvailable) {
        Write-Host "    正在通过 winget 安装 ffmpeg..." -ForegroundColor Yellow
        try {
            & winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
            Write-Ok "ffmpeg 已通过 winget 安装（可能需要重启终端使 PATH 生效）"
            $ffmpegOk = $true
        } catch {
            Write-Warn "winget 安装 ffmpeg 失败"
        }
    }

    if (-not $ffmpegOk) {
        Write-Err "请手动安装 ffmpeg:"
        Write-Host "    方式1: winget install Gyan.FFmpeg" -ForegroundColor Yellow
        Write-Host "    方式2: 从 https://www.gyan.dev/ffmpeg/builds/ 下载并添加到 PATH" -ForegroundColor Yellow
    }
}

# ── 4. 检查/安装 yt-dlp ─────────────────────────────────────────
Write-Step "检查 yt-dlp..."
$ytdlpOk = $false
try {
    $ytdlpVer = & yt-dlp --version 2>&1
    Write-Ok "已安装 (v$ytdlpVer)"
    $ytdlpOk = $true
} catch {
    Write-Warn "未找到 yt-dlp"
}

if (-not $ytdlpOk) {
    $wingetAvailable = $false
    try {
        & winget --version 2>&1 | Out-Null
        $wingetAvailable = $true
    } catch {}

    if ($wingetAvailable) {
        Write-Host "    正在通过 winget 安装 yt-dlp..." -ForegroundColor Yellow
        try {
            & winget install --id yt-dlp.yt-dlp -e --accept-source-agreements --accept-package-agreements
            Write-Ok "yt-dlp 已通过 winget 安装（可能需要重启终端使 PATH 生效）"
            $ytdlpOk = $true
        } catch {
            Write-Warn "winget 安装 yt-dlp 失败"
        }
    }

    if (-not $ytdlpOk) {
        Write-Err "请手动安装 yt-dlp:"
        Write-Host "    方式1: winget install yt-dlp.yt-dlp" -ForegroundColor Yellow
        Write-Host "    方式2: pip install yt-dlp" -ForegroundColor Yellow
    }
}

# ── 5. 安装项目依赖 ─────────────────────────────────────────────
Write-Step "安装项目 Python 依赖..."
try {
    & uv sync
    Write-Ok "项目依赖安装完成"
} catch {
    Write-Err "依赖安装失败: $_"
    exit 1
}

# ── 6. 汇总 ─────────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  安装完成!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if ($proxyConfigured) {
    Write-Host "  代理: $([System.Environment]::GetEnvironmentVariable('HTTP_PROXY'))" -ForegroundColor Gray
    Write-Host ""
}

if (-not $ffmpegOk -or -not $ytdlpOk) {
    Write-Warn "部分外部依赖未安装成功，请参照上方提示手动安装后再使用。"
    Write-Host ""
}

Write-Host "使用方法:" -ForegroundColor Cyan
Write-Host '  uv run wtt-match --url "https://www.youtube.com/watch?v=VIDEO_ID"'
Write-Host ""
if ($proxyConfigured) {
    Write-Host "提示: 运行 wtt-match 时如需代理，请确保已设置环境变量:" -ForegroundColor Yellow
    Write-Host '  $env:HTTP_PROXY="http://127.0.0.1:7890"' -ForegroundColor Gray
    Write-Host '  $env:HTTPS_PROXY="http://127.0.0.1:7890"' -ForegroundColor Gray
    Write-Host ""
}

# 右键运行时防止窗口一闪而过
if (-not $env:TERM -and -not $env:WT_SESSION -and -not $env:ConEmuPID) {
    Write-Host "按任意键退出..." -ForegroundColor Gray
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
