<#
.SYNOPSIS
  Builds the Android app from the current desktop app and (if a device is connected) installs it.

.DESCRIPTION
  The Android app runs the desktop app's own server/ and web/ code, taken from the desktop
  project every time this script runs. So after changing the desktop app, running this script
  (or the desktop's build-desktop.ps1, which calls it) brings the Android app up to date.

    1. Compatibility check  tools/check_compat.py runs the desktop code on this PC with the
                            Python version and packages the Android app uses.
    2. Build                Gradle copies server/, web/ and desktop_login.py and builds the APK.
    3. Output               TradingView.apk in -OutputDirectory.
    4. Install              adb install -r, when exactly one device or emulator is connected.

  Run tools\setup-toolchain.ps1 once before the first build.

.PARAMETER DebugBuild
  Build a debuggable APK (WebView can be inspected from chrome://inspect). It installs as a separate
  app (com.kistradingview.app.debug) with its own data, next to the release app.

.PARAMETER DeviceTest
  Before building, run the on-device tests (Keystore encryption, server start-up, access control) on
  the connected device or emulator, and stop if one fails. They run in the separate debug app and do
  not touch the release app or its data. tools\start-emulator.ps1 starts an emulator to run them on.

.PARAMETER NoInstall
  Do not install on a connected device.

.PARAMETER SkipCheck
  Skip the compatibility check.
#>
param(
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$ConfigDirectory = $PSScriptRoot,
    [switch]$DebugBuild,
    [switch]$DeviceTest,
    [switch]$NoInstall,
    [switch]$SkipCheck
)
$ErrorActionPreference = 'Stop'

function Read-Properties([string]$Path) {
    $values = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [IO.File]::ReadAllLines($Path)) {
            if ($line -match '^\s*([^#=\s][^=]*?)\s*=\s*(.*)$') {
                # Undo the .properties escapes that matter for paths: "C\:/x" and "C\:\\x".
                $values[$Matches[1]] = $Matches[2].Trim().Replace('\:', ':').Replace('\\', '\')
            }
        }
    }
    return $values
}

$desktop = Split-Path -Parent $PSScriptRoot
# Reuse the installed SDK and signing identity when upgrading the original two-folder layout.
$previous = Join-Path (Split-Path -Parent $desktop) 'TradingView_AndroidApp'
if ($ConfigDirectory -eq $PSScriptRoot -and -not (Test-Path -LiteralPath (Join-Path $ConfigDirectory 'local.properties'))) {
    $previousConfig = Read-Properties (Join-Path $previous 'local.properties')
    if ($previousConfig['kis.desktopDir'] -and [IO.Path]::GetFullPath($previousConfig['kis.desktopDir']) -eq $desktop) {
        $ConfigDirectory = $previous
    }
}
$ConfigDirectory = (Resolve-Path -LiteralPath $ConfigDirectory).Path
$local = Read-Properties (Join-Path $ConfigDirectory 'local.properties')
foreach ($name in 'sdk.dir', 'kis.jdkDir', 'kis.buildPython', 'kis.desktopDir') {
    if (-not $local[$name]) { throw "local.properties has no $name. Run tools\setup-toolchain.ps1 first." }
}
if ($ConfigDirectory -eq $PSScriptRoot) { $desktop = $local['kis.desktopDir'] }
$python = $local['kis.buildPython']
$adb = Join-Path $local['sdk.dir'] 'platform-tools\adb.exe'

$savedEnvironment = @{}
foreach ($name in 'JAVA_HOME', 'GRADLE_USER_HOME', 'ANDROID_USER_HOME', 'ANDROID_HOME', 'PYTHONUTF8') {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
}

Push-Location -LiteralPath $PSScriptRoot
try {
    $env:JAVA_HOME = $local['kis.jdkDir']
    $env:ANDROID_HOME = $local['sdk.dir']
    if ($local['kis.gradleUserHome']) { $env:GRADLE_USER_HOME = $local['kis.gradleUserHome'] }
    if ($local['kis.androidUserHome']) { $env:ANDROID_USER_HOME = $local['kis.androidUserHome'] }
    $env:PYTHONUTF8 = '1'

    # --- 1. Compatibility check -------------------------------------------
    if (-not $SkipCheck) {
        $venv = Join-Path $ConfigDirectory '.venv-compat'
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        $stamp = Join-Path $venv 'requirements.sha256'
        $wanted = (Get-FileHash -LiteralPath 'requirements-android.txt' -Algorithm SHA256).Hash
        $current = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
        if ($wanted -ne $current -or -not (Test-Path -LiteralPath $venvPython)) {
            Write-Host 'Preparing the compatibility-check environment...'
            if (-not (Test-Path -LiteralPath $venvPython)) {
                & $python -m venv $venv
                if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv-compat.' }
            }
            & $venvPython -m pip install --disable-pip-version-check --quiet -r requirements-android.txt
            if ($LASTEXITCODE -ne 0) { throw 'Could not install requirements-android.txt.' }
            Set-Content -LiteralPath $stamp -Value $wanted -Encoding ascii
        }
        & $venvPython 'tools\check_compat.py' --desktop $desktop
        if ($LASTEXITCODE -ne 0) {
            throw 'The desktop code does not run with the Android setup (see the failed checks above). The APK was not built.'
        }
    }

    # --- 2. Build ---------------------------------------------------------
    $variant = if ($DebugBuild) { 'debug' } else { 'release' }
    if (-not $DebugBuild -and $ConfigDirectory -ne $PSScriptRoot -and -not (Test-Path -LiteralPath (Join-Path $ConfigDirectory 'signing\signing.properties'))) {
        throw "The existing release signing configuration is missing from $ConfigDirectory."
    }
    if (-not $DebugBuild -and $ConfigDirectory -eq $PSScriptRoot -and -not (Test-Path -LiteralPath 'signing\signing.properties')) {
        # The APK must be signed with the same key every time, or Android refuses to install it over
        # the previous build. This key stays on this PC: back up the signing folder.
        Write-Host 'Creating the signing key (signing\release.jks). Keep a backup of the signing folder.'
        New-Item -ItemType Directory -Force 'signing' | Out-Null
        $bytes = New-Object byte[] 24
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $password = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
        & (Join-Path $env:JAVA_HOME 'bin\keytool.exe') -genkeypair -keystore 'signing\release.jks' -storetype PKCS12 `
            -alias 'kis-tradingview' -keyalg RSA -keysize 3072 -validity 10950 `
            -storepass $password -keypass $password -dname 'CN=KIS TradingView'
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the signing key.' }
        $signing = @('storeFile=signing/release.jks', "storePassword=$password", 'keyAlias=kis-tradingview', "keyPassword=$password")
        [IO.File]::WriteAllLines((Join-Path $PSScriptRoot 'signing\signing.properties'), $signing, (New-Object Text.UTF8Encoding $false))
    }

    # A version code that grows with every build lets the new APK install over the old one.
    $now = [DateTime]::UtcNow
    $versionCode = [int]($now - [DateTime]::new(2026, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)).TotalMinutes
    $versionName = $now.ToLocalTime().ToString('yyyy.MM.dd.HHmm')
    $revision = 'unknown'
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $head = & $git.Source -C $desktop rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and $head) {
            $changed = & $git.Source -C $desktop status --porcelain -- server web desktop_login.py android 2>$null
            $revision = if ($changed) { "$head+local" } else { "$head" }
        }
    }

    $gradle = Join-Path $PSScriptRoot 'gradlew.bat'
    $versionArguments = @("-Pkis.versionCode=$versionCode", "-Pkis.versionName=$versionName", "-Pkis.desktopRevision=$revision")
    $versionArguments += @("-Pkis.configDir=$ConfigDirectory", "-Pkis.desktopDir=$desktop")
    if ($DeviceTest) {
        Write-Host 'Running the on-device tests...'
        & $gradle ':app:connectedDebugAndroidTest' --no-daemon --console=plain --quiet @versionArguments
        if ($LASTEXITCODE -ne 0) {
            throw 'The on-device tests failed or no device is connected (report: app\build\reports\androidTests\connected\debug\index.html). The APK was not built.'
        }
        Write-Host 'On-device tests passed.'
    }

    Write-Host "Building the $variant APK $versionName (desktop $revision)..."
    $task = ':app:assemble' + $variant.Substring(0, 1).ToUpper() + $variant.Substring(1)
    & $gradle $task --no-daemon --console=plain --quiet @versionArguments
    if ($LASTEXITCODE -ne 0) { throw 'Android build failed.' }

    # --- 3. Output --------------------------------------------------------
    $built = Join-Path $PSScriptRoot "app\build\outputs\apk\$variant\app-$variant.apk"
    if (-not (Test-Path -LiteralPath $built)) { throw "The build did not produce $built" }
    $apk = Join-Path $OutputDirectory $(if ($DebugBuild) { 'TradingView-debug.apk' } else { 'TradingView.apk' })
    Copy-Item -LiteralPath $built -Destination $apk -Force
    Write-Host ("APK: {0} ({1:N1} MB)" -f $apk, ((Get-Item -LiteralPath $apk).Length / 1MB))

    # --- 4. Install -------------------------------------------------------
    if (-not $NoInstall -and (Test-Path -LiteralPath $adb)) {
        # adb leaves a background server running. Start it detached, or it inherits this script's
        # output pipe and whoever is reading our output (build-desktop.ps1, a CI log) never sees it end.
        $adbWasRunning = [bool](Get-Process adb -ErrorAction SilentlyContinue)
        if (-not $adbWasRunning) {
            $server = Start-Process -FilePath $adb -ArgumentList 'start-server' -WindowStyle Hidden -PassThru
            $server.WaitForExit(20000) | Out-Null
        }
        try {
            $devices = @(& $adb devices | Select-Object -Skip 1 | Where-Object { $_ -match '^\S+\s+device\s*$' } |
                ForEach-Object { ($_ -split '\s+')[0] })
            if ($devices.Count -eq 1) {
                Write-Host "Installing on $($devices[0])..."
                & $adb -s $devices[0] install -r $apk
                if ($LASTEXITCODE -ne 0) {
                    throw 'Install failed. If the device has a build signed with another key, uninstall the app there and run this again.'
                }
            } elseif ($devices.Count -gt 1) {
                Write-Host "Several devices are connected ($($devices -join ', ')); not installing. Copy the APK yourself or disconnect all but one."
            } else {
                Write-Host 'No device connected; not installing. Copy the APK to the phone, or enable USB debugging and run this again.'
            }
        } finally {
            if (-not $adbWasRunning) { & $adb kill-server 2>$null | Out-Null }
        }
    }
} finally {
    Pop-Location
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name])
    }
}
