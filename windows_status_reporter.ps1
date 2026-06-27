param(
    [string]$Endpoint = "http://127.0.0.1:8765/windows-status/report",
    [string]$Token = "",
    [int]$IntervalSeconds = 120
)

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;

public static class Win32Status {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }

    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [DllImport("kernel32.dll")]
    public static extern uint GetTickCount();
}
"@

function Get-ForegroundInfo {
    $hwnd = [Win32Status]::GetForegroundWindow()
    $builder = New-Object System.Text.StringBuilder 512
    [void][Win32Status]::GetWindowText($hwnd, $builder, $builder.Capacity)
    $processId = 0
    [void][Win32Status]::GetWindowThreadProcessId($hwnd, [ref]$processId)
    $proc = $null
    try { $proc = Get-Process -Id $processId -ErrorAction Stop } catch {}
    return @{
        title = $builder.ToString()
        process = if ($proc) { $proc.ProcessName } else { "unknown" }
        pid = [int]$processId
        path = if ($proc) { try { $proc.Path } catch { "" } } else { "" }
    }
}

function Get-IdleSeconds {
    $info = New-Object Win32Status+LASTINPUTINFO
    $info.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($info)
    if ([Win32Status]::GetLastInputInfo([ref]$info)) {
        return [int](([Win32Status]::GetTickCount() - $info.dwTime) / 1000)
    }
    return $null
}

function Get-VisibleWindows {
    $currentPid = (Get-ForegroundInfo).pid
    Get-Process |
        Where-Object { $_.MainWindowTitle -and $_.Id -ne $currentPid } |
        Select-Object -First 12 @{n='process';e={$_.ProcessName}}, @{n='pid';e={$_.Id}}, @{n='title';e={$_.MainWindowTitle}}
}

function Get-TopProcesses {
    Get-Process |
        Sort-Object WorkingSet64 -Descending |
        Select-Object -First 10 @{n='name';e={$_.ProcessName}}, @{n='pid';e={$_.Id}}, @{n='memory_mb';e={[math]::Round($_.WorkingSet64 / 1MB, 1)}}
}

while ($true) {
    try {
        $payload = @{
            hostname = $env:COMPUTERNAME
            user = $env:USERNAME
            timestamp = (Get-Date).ToString("o")
            foreground = Get-ForegroundInfo
            idle_seconds = Get-IdleSeconds
            background_windows = @(Get-VisibleWindows)
            top_processes = @(Get-TopProcesses)
            system = @{
                memory_percent = $null
                cpu_percent = $null
            }
        }
        $json = $payload | ConvertTo-Json -Depth 6 -Compress
        $headers = @{}
        if ($Token) { $headers["Authorization"] = "Bearer $Token" }
        Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $headers -ContentType "application/json; charset=utf-8" -Body $json | Out-Null
        Write-Host "reported $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    } catch {
        Write-Warning "report failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSeconds
}
