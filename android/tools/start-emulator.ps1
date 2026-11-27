<#
.SYNOPSIS
  Starts the test emulator (creating the virtual phone on first use) and waits until Android has booted.

.DESCRIPTION
  Needs the emulator and a system image (tools\setup-toolchain.ps1 -AcceptLicenses -WithEmulator) and
  hardware acceleration. On a PC without Hyper-V that means the Android Emulator Hypervisor Driver:
  install it once, as administrator, with
      <sdk>\extras\google\Android_Emulator_Hypervisor_Driver\silent_install.bat
  (sdkmanager package "extras;google;Android_Emulator_Hypervisor_Driver").

  With the emulator running, build-android.ps1 installs the app on it, and
  build-android.ps1 -DeviceTest runs the on-device tests.

.PARAMETER ShowWindow
  Show the phone in a window. By default it runs without one; use adb or screenshots to look at it.

.PARAMETER Stop
  Shut the emulator down instead of starting it.
#>
param(
    [switch]$ShowWindow,
    [switch]$Stop,
    [string]$Name = 'kis_phone',
    [string]$SystemImage = 'system-images;android-35;default;x86_64'
)
$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $PSScriptRoot
$local = @{}
foreach ($line in [IO.File]::ReadAllLines((Join-Path $ProjectDir 'local.properties'))) {
    if ($line -match '^\s*([^#=\s][^=]*?)\s*=\s*(.*)$') { $local[$Matches[1]] = $Matches[2].Trim().Replace('\:', ':').Replace('\\', '\') }
}
$sdk = $local['sdk.dir']
if (-not $sdk) { throw 'local.properties has no sdk.dir. Run tools\setup-toolchain.ps1 first.' }
$emulator = Join-Path $sdk 'emulator\emulator.exe'
$adb = Join-Path $sdk 'platform-tools\adb.exe'
if (-not (Test-Path -LiteralPath $emulator)) { throw 'The emulator is not installed. Run tools\setup-toolchain.ps1 -AcceptLicenses -WithEmulator.' }

$env:JAVA_HOME = $local['kis.jdkDir']
$env:ANDROID_SDK_ROOT = $sdk
if ($local['kis.androidUserHome']) {
    $env:ANDROID_USER_HOME = $local['kis.androidUserHome']
    $env:ANDROID_AVD_HOME = Join-Path $local['kis.androidUserHome'] 'avd'
    New-Item -ItemType Directory -Force $env:ANDROID_AVD_HOME | Out-Null
}

# adb's background server must not inherit our output pipe (see build-android.ps1).
if (-not (Get-Process adb -ErrorAction SilentlyContinue)) {
    (Start-Process -FilePath $adb -ArgumentList 'start-server' -WindowStyle Hidden -PassThru).WaitForExit(20000) | Out-Null
}
function Get-RunningEmulator {
    @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '^(emulator-\d+)\s+device' } | ForEach-Object { ($_ -split '\s+')[0] }) | Select-Object -First 1
}

if ($Stop) {
    $running = Get-RunningEmulator
    if ($running) { & $adb -s $running emu kill | Out-Null; Write-Host "Stopped $running." } else { Write-Host 'No emulator is running.' }
    return
}

$running = Get-RunningEmulator
if ($running) { Write-Host "An emulator is already running: $running"; return }

& $emulator -accel-check | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Hardware acceleration is not available, and the emulator does not work without it. See the notes at the top of this script.'
}

if (-not (& $emulator -list-avds | Where-Object { $_ -eq $Name })) {
    if (-not (Test-Path -LiteralPath (Join-Path $sdk ($SystemImage -replace ';', '\')))) {
        throw "System image $SystemImage is not installed. Run tools\setup-toolchain.ps1 -AcceptLicenses -WithEmulator."
    }
    Write-Host "Creating the virtual phone '$Name'..."
    'no' | & (Join-Path $sdk 'cmdline-tools\latest\bin\avdmanager.bat') create avd -n $Name -k ('"' + $SystemImage + '"') -d pixel_6 --force | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual phone.' }
}

$arguments = @('-avd', $Name, '-no-audio', '-no-boot-anim', '-no-snapshot', '-memory', '4096', '-cores', '4')
if (-not $ShowWindow) { $arguments += @('-no-window', '-gpu', 'swiftshader_indirect') }
# No output redirection here: with it, Start-Process lets the emulator inherit this script's own
# output pipe, and a caller that reads our output would wait until the emulator exits.
Start-Process -FilePath $emulator -ArgumentList $arguments -WindowStyle Hidden | Out-Null

Write-Host 'Waiting for Android to boot...'
$deadline = (Get-Date).AddMinutes(5)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    $running = Get-RunningEmulator
    if ($running -and ("$(& $adb -s $running shell getprop sys.boot_completed 2>$null)".Trim() -eq '1')) {
        # Keep the screen on and unlocked so that screenshots show the app.
        & $adb -s $running shell 'settings put system screen_off_timeout 1800000; svc power stayon true; input keyevent 82' | Out-Null
        Write-Host "Emulator ready: $running"
        return
    }
}
throw 'The emulator did not boot within 5 minutes. Run it with -ShowWindow to see what it is doing.'
