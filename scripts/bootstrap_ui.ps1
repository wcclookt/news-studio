[CmdletBinding()]
param(
    [string]$ProjectRoot = '',
    [string]$PythonPath = $env:NEWS_STUDIO_PYTHON,
    [string]$FallbackIndex = $(if ($env:NEWS_STUDIO_PIP_FALLBACK) { $env:NEWS_STUDIO_PIP_FALLBACK } else { 'https://pypi.tuna.tsinghua.edu.cn/simple' }),
    [switch]$NoMirror,
    [switch]$NoPythonDownload,
    [switch]$NoLaunch,
    [switch]$DryRun
)

# Windows PowerShell 5.1; saved as UTF-8 with BOM for readable Chinese progress.
# Official release/hash verified 2026-10-17:
# https://www.python.org/downloads/release/python-31316/
# Installer options: https://docs.python.org/3/using/windows.html
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$PythonVersion = '3.13.16'
$PythonUrl = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe'
$PythonSha256 = 'fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
# Microsoft official evergreen x64 runtime, authenticated again before execution:
# https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist
$VcRuntimeUrl = 'https://aka.ms/vc14/vc_redist.x64.exe'
$OfficialIndex = 'https://pypi.org/simple'
if (-not $ProjectRoot) { $ProjectRoot = Split-Path -Parent $PSScriptRoot }
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$Checker = Join-Path $ProjectRoot 'scripts\check_runtime.py'
$Requirements = Join-Path $ProjectRoot 'requirements-ui.txt'
$VenvPath = Join-Path $ProjectRoot '.venv'
$RuntimePath = Join-Path $ProjectRoot '.runtime'
$LogPath = Join-Path $ProjectRoot 'logs\bootstrap-ui.log'
$TranscriptStarted = $false
$BootstrapMutex = $null
$OwnMutex = $false
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'

function Show-Step([string]$Message) {
    Write-Host ('[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $Message)
}

function Assert-ProjectChild([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path)
    $rootPrefix = $ProjectRoot.TrimEnd('\') + '\'
    if (-not $absolute.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw '环境备份目标不在当前项目内，已取消操作。'
    }
}

function Quote-NativeArgument([string]$Value) {
    # CommandLineToArgvW quoting, including paths ending in a slash.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-Capture([string]$Executable, [string[]]$Arguments, [int]$TimeoutSeconds = 40) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = (($Arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $info.WorkingDirectory = $ProjectRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            return $null
        }
        $process.WaitForExit()
        return [PSCustomObject]@{ Code = $process.ExitCode; Output = $stdout.Result; Error = $stderr.Result }
    } catch {
        return $null
    } finally {
        $process.Dispose()
    }
}

function Test-Python([string]$Executable, [switch]$Dependencies) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    # Avoid Windows Store execution aliases opening the Store during discovery.
    if ($Executable -match '\\Microsoft\\WindowsApps\\') { return $null }
    $arguments = @('-I', '-B', $Checker, '--probe')
    if ($Dependencies) { $arguments = @('-I', '-B', $Checker, '--dependencies', '--requirements', $Requirements) }
    $result = Invoke-Capture $Executable $arguments
    if ($null -eq $result) { return $null }
    try { return ($result.Output.Trim() | ConvertFrom-Json) } catch { return $null }
}

function Get-PythonCandidates {
    $paths = New-Object 'Collections.Generic.List[string]'
    if ($PythonPath) { $paths.Add($PythonPath) }
    $paths.Add((Join-Path $VenvPath 'Scripts\python.exe'))
    $paths.Add((Join-Path $ProjectRoot 'venv\Scripts\python.exe'))
    $paths.Add((Join-Path $RuntimePath "python-$PythonVersion\python.exe"))
    foreach ($hive in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        if (Test-Path $hive) {
            foreach ($version in (Get-ChildItem $hive -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending)) {
                $key = Join-Path $version.PSPath 'InstallPath'
                if (Test-Path $key) {
                    $item = Get-Item $key
                    $exe = $item.GetValue('ExecutablePath')
                    if ($exe) { $paths.Add([string]$exe) }
                    $directory = $item.GetValue('')
                    if ($directory) { $paths.Add((Join-Path $directory 'python.exe')) }
                }
            }
        }
    }
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        $listed = Invoke-Capture $launcher.Source @('-0p') 8
        if ($listed) {
            foreach ($line in ($listed.Output -split '\r?\n')) {
                if ($line -match '([A-Za-z]:\\.+?python(?:w)?\.exe)\s*$') { $paths.Add($Matches[1]) }
            }
        }
    }
    foreach ($version in @('315', '314', '313', '312', '311')) {
        if ($env:LOCALAPPDATA) { $paths.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe")) }
        if ($env:ProgramFiles) { $paths.Add((Join-Path $env:ProgramFiles "Python$version\python.exe")) }
    }
    foreach ($command in @('python.exe', 'python3.exe')) {
        foreach ($found in (Get-Command $command -CommandType Application -All -ErrorAction SilentlyContinue)) { $paths.Add($found.Source) }
    }
    return @($paths | Select-Object -Unique)
}

function Find-Python {
    foreach ($candidate in (Get-PythonCandidates)) {
        $report = Test-Python $candidate
        if ($report -and $report.ok) {
            Show-Step "发现兼容 Python $($report.version)：$candidate"
            return [PSCustomObject]@{ Path = $candidate; Report = $report }
        }
    }
    return $null
}

function Install-LocalPython {
    if ($NoPythonDownload) { throw '未找到兼容 Python，已设置禁止下载。请安装 Windows x64 Python 3.11～3.15 后重试。' }
    if ($DryRun) {
        Show-Step "[预演] 将从 python.org 下载 Python $PythonVersion，校验 SHA256 和 PSF 签名后在本项目安装。"
        return $null
    }
    $directory = Join-Path $RuntimePath 'downloads'
    $null = New-Item -ItemType Directory -Path $directory -Force
    $installer = Join-Path $directory "python-$PythonVersion-amd64.exe"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        Show-Step "未找到 Python，正在从 python.org 下载固定版本 $PythonVersion（约 29 MB）……"
        $partial = $installer + '.partial'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            Invoke-WebRequest -Uri $PythonUrl -OutFile $partial -UseBasicParsing -TimeoutSec 180 -MaximumRedirection 0
            Move-Item -LiteralPath $partial -Destination $installer -Force
        } catch {
            throw 'Python 官方下载失败。请检查网络后重新双击启动；也可手动安装 python.org 的 x64 Python 3.11～3.15。'
        }
    }
    Show-Step '正在验证 Python 官方文件 SHA256 和数字签名……'
    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
    if ($hash -ne $PythonSha256) {
        Remove-Item -LiteralPath $installer -Force
        throw 'Python 安装文件校验失败，已丢弃该文件。请重新启动下载。'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)(?:CN|O)=Python Software Foundation(?:,|$)') {
        throw 'Python 安装程序数字签名验证失败，未执行安装。请检查系统日期、证书和网络后重试。'
    }
    $target = Join-Path $RuntimePath "python-$PythonVersion"
    Show-Step '正在本项目安装 Python，无需管理员权限，不修改全局 PATH……'
    $arguments = @('/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'AppendPath=0', 'Include_launcher=0',
                   'InstallLauncherAllUsers=0', 'AssociateFiles=0', 'Shortcuts=0', 'Include_test=0',
                   'Include_doc=0', 'Include_tcltk=0', 'Include_pip=1', 'Include_dev=1',
                   ('TargetDir=' + $target), '/log', (Join-Path $directory 'python-install.log'))
    $joined = (($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    $process = Start-Process -FilePath $installer -ArgumentList $joined -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(600000)) { throw 'Python 安装等待超过 10 分钟，请查看 .runtime\downloads\python-install.log 后重试。' }
    if ($process.ExitCode -notin @(0, 3010)) { throw "Python 安装失败（代码 $($process.ExitCode)），请查看 .runtime\downloads\python-install.log。" }
    $python = Join-Path $target 'python.exe'
    $report = Test-Python $python
    if (-not $report -or -not $report.ok) { throw '安装后的 Python 仍无法运行，请查看安装日志；未继续安装依赖。' }
    return [PSCustomObject]@{ Path = $python; Report = $report }
}

function Invoke-InstallCommand([string]$Python, [string[]]$Arguments) {
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python @Arguments 2>&1 | ForEach-Object { Write-Host ([string]$_) }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $previous }
}

function Install-Requirements([string]$Python, [switch]$ForceRepair) {
    $baseArgs = @('-I', '-m', 'pip', '--isolated', '--disable-pip-version-check', '--no-input', 'install',
                  '--only-binary=PySide6,PySide6-Addons,PySide6-Essentials,PySide6-WebEngine,PySide6-Pdf,shiboken6,lxml',
                  '--progress-bar', 'off', '--retries', '1', '--timeout', '20',
                  '--upgrade', '--upgrade-strategy', 'only-if-needed', '-r', $Requirements)
    if ($ForceRepair) { $baseArgs += '--force-reinstall' }
    Show-Step '正在安装或修复界面依赖（PySide6 约数百 MB，首次下载可能需要几分钟）……'
    $code = Invoke-InstallCommand $Python ($baseArgs + @('--index-url', $OfficialIndex))
    if ($code -eq 0) { return }
    if ($NoMirror -or -not $FallbackIndex) { throw '官方 PyPI 安装失败；请检查网络后重新双击启动。详细信息见启动日志。' }
    $uri = $null
    if (-not [Uri]::TryCreate($FallbackIndex, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query) {
        throw '备用 pip 源必须是无账号密码、无查询参数的 HTTPS 地址。'
    }
    Show-Step "官方 PyPI 未成功，本次改用备用源：$FallbackIndex"
    $code = Invoke-InstallCommand $Python ($baseArgs + @('--index-url', $FallbackIndex))
    if ($code -ne 0) { throw '依赖安装失败，已停止自动重试。请查看 logs\bootstrap-ui.log，修复网络后重新双击启动。' }
}

function Install-VcRuntime {
    if ($DryRun) {
        Show-Step '[预演] 如 Qt 检测到缺少 VC++ 系统 DLL，将从微软官方下载并校验签名后修复；Windows 可能要求管理员确认。'
        return
    }
    $directory = Join-Path $RuntimePath 'downloads'
    $null = New-Item -ItemType Directory -Path $directory -Force
    $installer = Join-Path $directory 'VC_redist.x64.exe'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        Show-Step 'Qt 检测到缺少 VC++ 系统运行库，正在从微软官方下载修复程序……'
        $partial = $installer + '.partial'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        try {
            Invoke-WebRequest -Uri $VcRuntimeUrl -OutFile $partial -UseBasicParsing -TimeoutSec 180 -MaximumRedirection 5
            Move-Item -LiteralPath $partial -Destination $installer -Force
        } catch { throw '微软 VC++ 运行库下载失败。请检查网络后重试，或从微软官网下载 x64 VC++ Redistributable。' }
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or $signature.SignerCertificate.Subject -notmatch '(?:^|,\s*)(?:CN|O)=Microsoft Corporation(?:,|$)') {
        Remove-Item -LiteralPath $installer -Force
        throw 'VC++ 运行库数字签名验证失败，已丢弃文件，未执行安装。'
    }
    Show-Step '将启动微软官方运行库安装器；Windows 可能要求管理员确认（UAC），请按系统提示操作。'
    $arguments = @('/install', '/quiet', '/norestart', '/log', (Join-Path $directory 'vc-runtime-install.log'))
    $joined = (($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
    try {
        $process = Start-Process -FilePath $installer -ArgumentList $joined -Verb RunAs -WindowStyle Hidden -PassThru
        if (-not $process.WaitForExit(600000)) { throw '运行库安装等待超过 10 分钟，请检查安装窗口和日志。' }
        if ($process.ExitCode -notin @(0, 3010)) { throw "VC++ 运行库安装未成功（代码 $($process.ExitCode)），请查看 .runtime\downloads\vc-runtime-install.log。" }
    } catch [System.ComponentModel.Win32Exception] {
        throw 'VC++ 系统运行库安装未获授权或被取消。请使用有管理员权限的账户运行微软安装器，再重新启动。'
    }
}

# Dot sourcing loads the helpers for read-only/mocked validation without starting
# a bootstrap. Double-click and normal -File entry points execute the block below.
if ($MyInvocation.InvocationName -eq '.') { return }

try {
    if (-not (Test-Path -LiteralPath $Checker -PathType Leaf) -or -not (Test-Path -LiteralPath $Requirements -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config_ui.py') -PathType Leaf)) {
        throw '项目文件不完整。请把 start_ui.bat、scripts、news_agent 和 requirements 文件保留在同一项目中。'
    }
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw '此启动流程支持 Windows 10/11 x64，当前系统架构不适用。'
    }
    if (-not $DryRun) {
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force
        Start-Transcript -LiteralPath $LogPath -Append | Out-Null
        $TranscriptStarted = $true
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $identity = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant()))).Replace('-', '') } finally { $sha.Dispose() }
        $BootstrapMutex = New-Object Threading.Mutex($false, "Local\NewsStudioBootstrap-$identity")
        try { $OwnMutex = $BootstrapMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $OwnMutex = $true }
        if (-not $OwnMutex) { throw '另一个窗口正在准备此项目的运行环境，请等待它完成后再试。' }
    }
    Show-Step '星闻配置中心：正在检查 Python、依赖和 Qt 原生界面……'
    if ($DryRun) { Show-Step '[预演模式] 只检测并显示计划，不下载、不安装、不更改文件、不打开主界面。' }
    $selected = Find-Python
    if (-not $selected) { $selected = Install-LocalPython }
    if ($DryRun -and -not $selected) { exit 0 }
    $python = $selected.Path
    $isProjectVenv = $selected.Report.venv -and ($python.StartsWith($VenvPath + '\', [StringComparison]::OrdinalIgnoreCase) -or $python.StartsWith((Join-Path $ProjectRoot 'venv') + '\', [StringComparison]::OrdinalIgnoreCase))
    if (-not $isProjectVenv) {
        if ($DryRun) {
            Show-Step "[预演] 将用已检测的 Python 创建本项目 .venv，再检查并按需安装 requirements-ui.txt。"
            exit 0
        }
        if (Test-Path -LiteralPath $VenvPath) {
            $archived = $VenvPath + '.invalid-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
            Assert-ProjectChild $VenvPath
            Assert-ProjectChild $archived
            Show-Step '现有 .venv 已失效，保留为 .venv.invalid-*，重新建立环境。'
            Move-Item -LiteralPath $VenvPath -Destination $archived
        }
        Show-Step '正在创建项目专用 .venv，保持系统 Python 和其他项目独立……'
        $code = Invoke-InstallCommand $python @('-I', '-m', 'venv', $VenvPath)
        if ($code -ne 0) { throw '创建 .venv 失败。请确认项目目录可写，且 Python 包含 venv/ensurepip。' }
        $python = Join-Path $VenvPath 'Scripts\python.exe'
    }
    $status = Test-Python $python -Dependencies
    if ($DryRun) {
        if ($status -and $status.ok) { Show-Step '[预演] 项目环境完整，正常启动无需 pip 联网，可以直接打开配置中心。' }
        else { Show-Step '[预演] 将按 requirements-ui.txt 安装或修复缺失依赖，并再次检查 Qt DLL。' }
        exit 0
    }
    $vcRepairAttempted = $false
    if ($status -and -not $status.ok -and @($status.missing).Count -eq 0 -and @($status.missing_vc_runtime).Count -gt 0) {
        Install-VcRuntime
        $vcRepairAttempted = $true
        $status = Test-Python $python -Dependencies
    }
    if (-not $status -or -not $status.ok) {
        if ($status -and -not $status.requirements_valid) { throw ($status.errors -join ' ') }
        $pip = Invoke-Capture $python @('-I', '-m', 'pip', '--version')
        if (-not $pip -or $pip.Code -ne 0) {
            Show-Step '正在恢复 Python 自带 pip……'
            $code = Invoke-InstallCommand $python @('-I', '-m', 'ensurepip', '--upgrade')
            if ($code -ne 0) { throw '无法恢复 pip，请重新安装完整 Python 后重试。' }
        }
        $repair = $status -and @($status.missing).Count -eq 0 -and @($status.broken).Count -gt 0
        Install-Requirements $python -ForceRepair:$repair
        $status = Test-Python $python -Dependencies
        if ($status -and -not $status.ok -and -not $vcRepairAttempted -and @($status.missing_vc_runtime).Count -gt 0) {
            Install-VcRuntime
            $vcRepairAttempted = $true
            $status = Test-Python $python -Dependencies
        }
        if (-not $status -or -not $status.ok) {
            throw '依赖安装已完成，但实际模块或 Qt DLL 仍未通过检查。请查看日志并检查杀毒隔离、系统运行库和 Windows 版本；本次已停止，避免反复安装。'
        }
    } else {
        Show-Step 'Python、依赖版本和 Qt DLL 检查通过，无需联网安装。'
    }
    if ($OwnMutex) { $BootstrapMutex.ReleaseMutex(); $OwnMutex = $false }
    if ($NoLaunch) { Show-Step '运行环境检查完成（已设置不打开主界面）。'; exit 0 }
    Show-Step '正在打开星闻配置中心……'
    Set-Location -LiteralPath $ProjectRoot
    # Keep the script directory on sys.path so config_ui can import news_agent.
    $code = Invoke-InstallCommand $python @('-E', '-s', '-B', '-X', 'utf8', (Join-Path $ProjectRoot 'config_ui.py'))
    if ($code -ne 0) { throw "配置界面退出异常（代码 $code），请查看启动日志。" }
    exit 0
} catch {
    Show-Step ('启动失败：' + $_.Exception.Message)
    if (-not $DryRun) { Write-Host "日志：$LogPath" }
    exit 1
} finally {
    if ($OwnMutex) { $BootstrapMutex.ReleaseMutex() }
    if ($BootstrapMutex) { $BootstrapMutex.Dispose() }
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
