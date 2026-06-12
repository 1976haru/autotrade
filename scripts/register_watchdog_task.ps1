<#
  AgentTraderWatchdog 작업 스케줄러 등록 — ★관리자 권한으로 1회 실행.
  (Register-ScheduledTask 의 루트 폴더 등록은 elevation 필요 — 그래서 운영자 1회 실행.)

  효과(무인 self-survival):
    - onlogon 트리거: 재부팅/재로그온 시 watchdog 자동 시작.
    - 실패 시 1분마다 재시작(RestartCount 99): watchdog 프로세스가 죽어도 부활.
    - MultipleInstances=IgnoreNew: 이중 watchdog 방지.
    - ExecutionTimeLimit 0: 무한 실행(장기 프로세스).

  안전: 백엔드 프로세스만 관리(봇 start 0건, 코드상 watchdog 는 백엔드만 복구).
  ENABLE_* / KIS 안전 플래그 변경 0건.

  실행:  우클릭 → 'PowerShell 관리자로 실행' 후
         powershell -ExecutionPolicy Bypass -File "scripts\register_watchdog_task.ps1"
#>
$ErrorActionPreference = 'Stop'
$bat = Join-Path $PSScriptRoot 'run_watchdog.bat'
if (-not (Test-Path $bat)) { throw "run_watchdog.bat 없음: $bat" }

# 이중 watchdog 방지 — 기존 수동/구 인스턴스 정리(스케줄러가 단일 소스가 되도록).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*watchdog.py*' } |
  ForEach-Object {
    Write-Host ("기존 watchdog 정리: PID " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }

$action  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $bat + '"')
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
  -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 99 `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName 'AgentTraderWatchdog' -Action $action -Trigger $trigger `
  -Settings $settings -Description 'Agent Trader backend watchdog (unmanned auto-recovery)' -Force | Out-Null

Start-ScheduledTask -TaskName 'AgentTraderWatchdog'
Start-Sleep -Seconds 5
$t = Get-ScheduledTask -TaskName 'AgentTraderWatchdog'
$wd = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*watchdog.py*' }
Write-Host ("등록 완료 — Task State: " + $t.State + " | watchdog PID: " + (($wd.ProcessId) -join ','))
