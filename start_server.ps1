<#
    一键启动脚本：运行 Claude Agent FastAPI 服务
    用法：
        .\start_server.ps1 [-Host 0.0.0.0] [-Port <端口>] [-Reload]
    - 默认优先使用项目根目录下 .venv\Scripts\python.exe
    - 如果不存在则回落到系统 PATH 中的 python
    - 端口可在 config.yaml 中添加 `port: <number>`，未配置时默认 8207
#>

param(
    [string]$Host = "0.0.0.0",
    [Nullable[int]]$Port,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $scriptRoot "config.yaml"
$defaultPort = 8207
$portProvided = $PSBoundParameters.ContainsKey("Port") -and $Port -ne $null

function ConvertTo-Hashtable {
    param([object]$Object)

    $table = @{}
    if ($null -eq $Object) {
        return $table
    }

    if ($Object -is [System.Collections.IDictionary]) {
        foreach ($key in $Object.Keys) {
            $table[$key] = $Object[$key]
        }
        return $table
    }

    if ($Object -is [PSCustomObject]) {
        foreach ($prop in $Object.PSObject.Properties) {
            $table[$prop.Name] = $prop.Value
        }
    }

    return $table
}

function Invoke-SimpleYamlParse {
    param([string]$Raw)

    $result = @{}
    if (-not $Raw) {
        return $result
    }

    $lines = $Raw -split "`n"
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }

        $colonIndex = $trimmed.IndexOf(":")
        if ($colonIndex -lt 0) { continue }

        $key = $trimmed.Substring(0, $colonIndex).Trim()
        $value = $trimmed.Substring($colonIndex + 1).Trim()
        if (-not $key) { continue }

        $value = [System.Text.RegularExpressions.Regex]::Replace($value, "\s+#.*$", "")
        $value = $value.Trim()

        if ($value.StartsWith("'") -and $value.EndsWith("'") -and $value.Length -ge 2) {
            $value = $value.Substring(1, $value.Length - 2)
        } elseif ($value.StartsWith('"') -and $value.EndsWith('"') -and $value.Length -ge 2) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        $result[$key] = $value
    }

    return $result
}

function Load-AppConfig {
    param([string]$Path)

    $config = @{}
    if (-not (Test-Path $Path)) {
        return $config
    }

    try {
        $raw = Get-Content -Path $Path -Raw -Encoding UTF8
        if (-not $raw.Trim()) {
            return $config
        }

        $yamlCmd = Get-Command ConvertFrom-Yaml -ErrorAction SilentlyContinue
        if ($yamlCmd) {
            try {
                $parsed = $raw | ConvertFrom-Yaml
                return (ConvertTo-Hashtable -Object $parsed)
            } catch {
                Write-Warning "解析 config.yaml 失败，使用简易解析：$($_.Exception.Message)"
            }
        }

        return (Invoke-SimpleYamlParse -Raw $raw)
    } catch {
        Write-Warning "读取 config.yaml 失败：$($_.Exception.Message)"
        return $config
    }
}

function Resolve-Port {
    param(
        [hashtable]$Config,
        [Nullable[int]]$CliPort,
        [int]$Fallback
    )

    if ($CliPort -ne $null) {
        return [int]$CliPort
    }

    if ($null -ne $Config -and $Config.ContainsKey("port")) {
        $rawPort = $Config["port"]
        if ($rawPort -ne $null -and "$rawPort".Trim()) {
            try {
                return [int]$rawPort
            } catch {
                Write-Warning "config.yaml 中的 port 无法解析，使用默认端口：$Fallback"
            }
        }
    }

    return $Fallback
}

function Get-LocalBaseUrls {
    param([int]$Port)

    $urls = @()
    try {
        $interfaces = [System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()
        foreach ($nic in $interfaces) {
            if ($nic.OperationalStatus -ne [System.Net.NetworkInformation.OperationalStatus]::Up) { continue }
            if ($nic.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) { continue }

            foreach ($ip in $nic.GetIPProperties().UnicastAddresses) {
                if ($ip.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
                    $urls += "http://{0}:{1}" -f $ip.Address.ToString(), $Port
                }
            }
        }
    } catch {
        Write-Warning "获取局域网 IP 信息失败：$($_.Exception.Message)"
    }

    if (-not $urls) {
        $urls = @("http://127.0.0.1:{0}" -f $Port)
    }

    return ($urls | Sort-Object -Unique)
}

$configData = Load-AppConfig -Path $configPath

$venvPython = Join-Path $scriptRoot ".venv\Scripts\python.exe"
$pythonExe = $venvPython
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

if (-not (Get-Command $pythonExe -ErrorAction SilentlyContinue)) {
    throw "无法找到 Python 可执行文件：$pythonExe"
}

$resolvedPort = Resolve-Port -Config $configData -CliPort ($(if ($portProvided) { $Port } else { $null })) -Fallback $defaultPort
$Port = $resolvedPort

$uvicornArgs = @(
    "-m", "uvicorn",
    "cc_B.main:app",
    "--host", $Host,
    "--port", $Port
)

if ($Reload.IsPresent) {
    $uvicornArgs += "--reload"
}

$baseUrls = @("http://{0}:{1}" -f $Host, $Port)
$lanUrls = Get-LocalBaseUrls -Port $Port
foreach ($url in $lanUrls) {
    if ($baseUrls -notcontains $url) {
        $baseUrls += $url
    }
}

Write-Host ""
Write-Host "================ Claude Backend Launcher ================" -ForegroundColor Cyan
Write-Host "Python :" $pythonExe
Write-Host "Host   :" $Host
Write-Host "Port   :" $Port
Write-Host "Reload :" ($(if ($Reload.IsPresent) { "On" } else { "Off" }))
Write-Host "Config :" ($(if ($configData.Count) { "已加载" } else { "未检测到" }))
Write-Host ""
Write-Host "Base URL(s):" -ForegroundColor Yellow
foreach ($url in $baseUrls) {
    Write-Host ("  - {0}" -f $url) -ForegroundColor Green
}
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

Push-Location $scriptRoot
try {
    & $pythonExe @uvicornArgs
}
finally {
    Pop-Location
}
