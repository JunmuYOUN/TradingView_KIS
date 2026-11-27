<#
.SYNOPSIS
  Installs the Android build tools (JDK 17, Android SDK, build-time Python 3.13) into one folder.

.DESCRIPTION
  Needs no administrator rights and does not touch the system PATH or environment variables.
  Everything, including the Gradle and Android caches, lives under -InstallDir, so deleting that
  folder removes it all. (One exception: Google's sdkmanager keeps a copy of its own command-line
  program in %USERPROFILE%\.android, which can be deleted too.) The locations are written to
  local.properties, which build-android.ps1 and Gradle read.

  Downloads from
    JDK 17 (Eclipse Temurin)        github.com/adoptium
    Android SDK command-line tools  dl.google.com
    Python 3.13 (build time only)   python.org, extracted by the Python install manager

.PARAMETER AcceptLicenses
  Accept the Android SDK licenses (https://developer.android.com/studio/terms).
  Without this switch no SDK packages are installed.

.PARAMETER WithEmulator
  Also install the emulator and a system image. See tools\start-emulator.ps1 for running it.
#>
param(
    [string]$InstallDir = (Join-Path $env:USERPROFILE 'AndroidDev'),
    [string]$DesktopDir = '',
    [switch]$AcceptLicenses,
    [switch]$WithEmulator,
    # Not an "aosp_atd" image: those render nothing, so the screen cannot be looked at.
    [string]$SystemImage = 'system-images;android-35;default;x86_64'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ProjectDir = Split-Path -Parent $PSScriptRoot

# Pinned versions and checksums. To upgrade, change them here.
$Jdk = @{
    Url    = 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21.1%2B2/OpenJDK17U-jdk_x64_windows_hotspot_17.0.21.1_2.zip'
    Sha256 = 'eafba0944f777abdc74d5aad16049c9b5a1275b6ce99e2939ba9f7f3e6f08c87'
}
$CmdlineTools = @{
    Url  = 'https://dl.google.com/android/repository/commandlinetools-win-16111833_latest.zip'
    Sha1 = '57d04f2d75eb8e8fffc5000a987e5de4b5a63e9d'
}
$SdkPackages = @('platform-tools', 'platforms;android-36', 'build-tools;36.0.0')
$PythonTag = '3.13'  # must match chaquopy.defaultConfig.version in app/build.gradle.kts

$Downloads = Join-Path $InstallDir 'downloads'
$JdkDir = Join-Path $InstallDir 'jdk-17'
$SdkDir = Join-Path $InstallDir 'sdk'
$PythonDir = Join-Path $InstallDir 'python313'
New-Item -ItemType Directory -Force $Downloads | Out-Null

function Get-Verified([string]$Url, [string]$Algorithm, [string]$Expected) {
    $file = Join-Path $Downloads ([Uri]::UnescapeDataString(($Url -split '/')[-1]))
    $cached = (Test-Path -LiteralPath $file) -and
        ((Get-FileHash -LiteralPath $file -Algorithm $Algorithm).Hash -eq $Expected.ToUpper())
    if (-not $cached) {
        Write-Host "Downloading $Url"
        & curl.exe --fail --location --silent --show-error --retry 3 --output $file $Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
        $actual = (Get-FileHash -LiteralPath $file -Algorithm $Algorithm).Hash
        if ($actual -ne $Expected.ToUpper()) {
            Remove-Item -LiteralPath $file -Force
            throw "Checksum mismatch for ${Url}: $actual"
        }
    }
    return $file
}

function Expand-Zip([string]$Zip, [string]$Destination) {
    # The bsdtar that ships with Windows is much faster than Expand-Archive.
    New-Item -ItemType Directory -Force $Destination | Out-Null
    & "$env:SystemRoot\System32\tar.exe" -xf $Zip -C $Destination
    if ($LASTEXITCODE -ne 0) { throw "Could not extract $Zip" }
}

# --- JDK 17 ---------------------------------------------------------------
if (-not (Test-Path -LiteralPath (Join-Path $JdkDir 'bin\java.exe'))) {
    $zip = Get-Verified $Jdk.Url 'SHA256' $Jdk.Sha256
    $staging = Join-Path $InstallDir 'jdk-staging'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Expand-Zip $zip $staging
    $inner = Get-ChildItem -LiteralPath $staging -Directory | Select-Object -First 1
    Move-Item -LiteralPath $inner.FullName -Destination $JdkDir
    Remove-Item -LiteralPath $staging -Recurse -Force
}
Write-Host "JDK: $JdkDir"
$env:JAVA_HOME = $JdkDir
# Keep what the SDK tools write for themselves (their own CLI, settings, adb key) out of the user profile.
$env:ANDROID_USER_HOME = Join-Path $InstallDir '.android'

# --- Android SDK ----------------------------------------------------------
$SdkManager = Join-Path $SdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'
if (-not (Test-Path -LiteralPath $SdkManager)) {
    $zip = Get-Verified $CmdlineTools.Url 'SHA1' $CmdlineTools.Sha1
    $staging = Join-Path $InstallDir 'cmdline-staging'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Expand-Zip $zip $staging
    New-Item -ItemType Directory -Force (Join-Path $SdkDir 'cmdline-tools') | Out-Null
    Move-Item -LiteralPath (Join-Path $staging 'cmdline-tools') -Destination (Join-Path $SdkDir 'cmdline-tools\latest')
    Remove-Item -LiteralPath $staging -Recurse -Force
}
Write-Host "Android SDK: $SdkDir"

$packages = $SdkPackages
if ($WithEmulator) { $packages += @('emulator', $SystemImage) }
$missing = @($packages | Where-Object { -not (Test-Path -LiteralPath (Join-Path $SdkDir ($_ -replace ';', '\'))) })
if ($missing.Count -gt 0) {
    if (-not $AcceptLicenses) {
        throw "SDK packages are missing ($($missing -join ', ')). Re-run with -AcceptLicenses if you accept the Android SDK licenses."
    }
    $yes = ("y`r`n" * 60)
    $yes | & $SdkManager "--sdk_root=$SdkDir" --licenses | Out-Null
    # Quote the names so that cmd does not read the ';' in them as an argument separator.
    $quoted = $missing | ForEach-Object { '"' + $_ + '"' }
    $yes | & $SdkManager "--sdk_root=$SdkDir" @quoted
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the SDK packages.' }
    foreach ($package in $missing) {
        if (-not (Test-Path -LiteralPath (Join-Path $SdkDir ($package -replace ';', '\')))) {
            throw "SDK package was not installed: $package"
        }
    }
}

# --- Build-time Python ----------------------------------------------------
# Chaquopy needs a Python on the build PC with the same major.minor version as the one in the app.
$PythonExe = Join-Path $PythonDir 'python.exe'
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $manager = Get-Command pymanager -ErrorAction SilentlyContinue
    if (-not $manager) {
        throw "The Python install manager (pymanager) was not found. Install it from https://www.python.org/downloads/ or install Python $PythonTag yourself and set kis.buildPython in local.properties."
    }
    & $manager.Source install -y "--target=$PythonDir" $PythonTag
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonExe)) { throw "Could not install Python $PythonTag." }
}
Write-Host "Build Python: $PythonExe"

# --- local.properties -----------------------------------------------------
if (-not $DesktopDir) { $DesktopDir = Split-Path -Parent $ProjectDir }
# Java .properties syntax: forward slashes need no escaping, the drive letter's colon does.
function ConvertTo-PropertyPath([string]$Path) { ($Path -replace '\\', '/') -replace ':', '\:' }
$lines = @(
    '# Written by tools/setup-toolchain.ps1. Paths on this PC; not committed to git.',
    "sdk.dir=$(ConvertTo-PropertyPath $SdkDir)",
    "kis.jdkDir=$(ConvertTo-PropertyPath $JdkDir)",
    "kis.buildPython=$(ConvertTo-PropertyPath $PythonExe)",
    "kis.desktopDir=$(ConvertTo-PropertyPath $DesktopDir)",
    "kis.gradleUserHome=$(ConvertTo-PropertyPath (Join-Path $InstallDir 'gradle-home'))",
    "kis.androidUserHome=$(ConvertTo-PropertyPath (Join-Path $InstallDir '.android'))"
)
[IO.File]::WriteAllLines((Join-Path $ProjectDir 'local.properties'), $lines, (New-Object Text.UTF8Encoding $false))
Write-Host 'Wrote local.properties.'
