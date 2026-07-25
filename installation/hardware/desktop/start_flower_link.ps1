$ErrorActionPreference = 'Stop'

$workspace = 'E:\AdventureX'
$python = 'C:\Python314\python.exe'
$collector = Join-Path $workspace 'hardware\desktop\collector.py'
$outputDir = Join-Path $workspace 'outputs\ADX_Flower_PointCloud'
$liveDir = Join-Path $outputDir 'live'
$liveCsv = Join-Path $liveDir 'sensor_live.csv'
$audioDir = Join-Path $liveDir 'audio'
$resetFile = Join-Path $liveDir 'reset_lifecycle.txt'
$toe = Join-Path $outputDir 'ADX_Flower_PointCloud.toe'
$touchDesigner = 'E:\Software\TouchDesigner.2023.12370 Pro\TouchDesigner.2023.12370\TouchDesigner.2023.12370\bin\TouchDesigner.exe'

foreach ($required in @($python, $collector, $toe, $touchDesigner)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required file not found: $required"
    }
}
New-Item -ItemType Directory -Path $liveDir -Force | Out-Null
New-Item -ItemType Directory -Path $audioDir -Force | Out-Null

# Resolve the gateway by the fixed USB serial number documented by hardware/README.md.
$desktopDir = Split-Path -Parent $collector
$detectCode = "import sys; sys.path.insert(0, r'$desktopDir'); import collector; print(collector.detect_esp32_port())"
$gatewayPort = (& $python -c $detectCode).Trim()
if (-not $gatewayPort) {
    throw 'ESP32-S3 gateway was not detected.'
}

# T5AI-Core is an independent USB PCM branch. It is optional so a missing
# microphone never prevents the gateway/CSV/flower chain from starting.
$t5Port = ''
$t5DetectCode = "import sys; sys.path.insert(0, r'$desktopDir'); import collector; print(collector.detect_t5_audio_port())"
try {
    $t5Port = (& $python -c $t5DetectCode).Trim()
    if ($t5Port) {
        Write-Host "T5 audio: $t5Port"
    }
} catch {
    Write-Warning 'T5AI-Core SERIAL-B audio port was not detected; continuing without WAV recording.'
}

# Stop only ADX producers that own this exact live CSV, plus the documented
# `python desktop\collector.py --full` form. The latter uses a timestamped
# default output and otherwise keeps COM5/COM7 open, preventing this launcher
# from starting the one collector that feeds TouchDesigner.
$producers = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^pythonw?(\.exe)?$' -and
    $_.CommandLine -and
    (
        (
            $_.CommandLine.IndexOf($liveCsv, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            ($_.CommandLine -match 'stream_raw_sensor\.py' -or $_.CommandLine -match 'collector\.py')
        ) -or
        $_.CommandLine -match '(?i)desktop[\\/]collector\.py\s+--full(?:\s|$)'
    )
}
foreach ($producer in $producers) {
    # A pyserial process blocked inside the Windows USB-CDC driver can ignore
    # Stop-Process long enough for the replacement collector to lose COM7.
    # taskkill is scoped to the exact producer PID selected above.
    & taskkill.exe /PID $producer.ProcessId /F 2>$null | Out-Null
}
Start-Sleep -Milliseconds 500

# Ensure exactly one hidden task TouchDesigner process exists. User-owned TD
# processes without this project path are deliberately left untouched.
$escapedToe = [regex]::Escape($toe)
$taskTouchDesigner = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'TouchDesigner.exe' -and $_.CommandLine -match $escapedToe
})
if ($taskTouchDesigner.Count -eq 0) {
    Start-Process -FilePath $touchDesigner -ArgumentList @('"' + $toe + '"') -WindowStyle Hidden | Out-Null
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$stdoutLog = Join-Path $liveDir "hardware_collector_${timestamp}.log"
$stderrLog = Join-Path $liveDir "hardware_collector_${timestamp}.error.log"
$collectorArgs = @(
    '-u',
    $collector,
    '--port', $gatewayPort,
    '--output', $liveCsv,
    '--live-bpm'
)
if ($t5Port) {
    $collectorArgs += @('--t5-port', $t5Port, '--audio-output-dir', $audioDir)
}
$collectorProcess = Start-Process `
    -FilePath $python `
    -ArgumentList $collectorArgs `
    -WorkingDirectory (Join-Path $workspace 'hardware') `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru
[System.IO.File]::WriteAllText(
    (Join-Path $liveDir 'hardware_collector.pid'),
    $collectorProcess.Id.ToString(),
    [System.Text.UTF8Encoding]::new($false)
)

Start-Sleep -Seconds 3
if ($collectorProcess.HasExited) {
    $errorText = if (Test-Path -LiteralPath $stderrLog) {
        Get-Content -LiteralPath $stderrLog -Raw -Encoding utf8
    } else {
        'No error log was produced.'
    }
    throw "Hardware collector exited early: $errorText"
}

# The collector has now truncated/initialized the live CSV. Emit the reset
# token afterwards so TouchDesigner cannot briefly latch rows from the old
# replay before the new physical session starts.
$sessionToken = [DateTimeOffset]::Now.ToUnixTimeMilliseconds().ToString()
[System.IO.File]::WriteAllText($resetFile, $sessionToken, [System.Text.UTF8Encoding]::new($false))

Write-Host "Gateway: $gatewayPort"
Write-Host "Collector PID: $($collectorProcess.Id)"
Write-Host "Live CSV: $liveCsv"
if ($t5Port) {
    Write-Host "T5 audio output: $audioDir"
    Write-Host 'T5 recording is armed by the same two-way interaction-zone enter/exit events as the flower.'
}
Write-Host "Log: $stdoutLog"
Write-Host 'TouchDesigner is listening. The plant now starts at zero and grows from physical sensor data.'
Write-Host 'Live flower + two-person dashboard: http://127.0.0.1:9987/'
Write-Host 'If the CSV remains header-only, power or reset both ESP32-C3 wearable nodes after this message.'
