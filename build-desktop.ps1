param(
    [string]$OutputDirectory = $PSScriptRoot,
    [switch]$SkipAndroid  # build TradingView.exe only
)
$ErrorActionPreference = 'Stop'

Push-Location -LiteralPath $PSScriptRoot
try {
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Create .venv with Python 3.10 or newer before building.'
    }

    & $python -m pip install --disable-pip-version-check -r requirements.txt 'pywebview==6.2.1' 'pyinstaller==6.22.3'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

    # Anaconda-based Pythons keep DLLs that _ctypes, _bz2 and _lzma need (ffi.dll, LIBBZ2.dll, liblzma.dll)
    # in Library\bin. PyInstaller looks for Windows DLLs on PATH, so add it for this build only.
    $basePrefix = & $python -c 'import sys; print(sys.base_prefix)'
    $condaBin = Join-Path $basePrefix 'Library\bin'
    if (Test-Path -LiteralPath $condaBin) { $env:PATH = "$condaBin;$env:PATH" }

    $buildDir = Join-Path $PSScriptRoot '.venv\desktop-build'
    # Keys and tokens never go into the bundle; the app stores them DPAPI-encrypted under data\ at run time.
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name TradingView `
        --icon "$PSScriptRoot\web\mint-candles.ico" `
        --distpath $OutputDirectory --workpath $buildDir --specpath $buildDir `
        --add-data "$PSScriptRoot\web:web" --collect-all webview `
        --collect-data yfinance --collect-all curl_cffi --copy-metadata yfinance `
        --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.lifespan.on `
        --hidden-import uvicorn.protocols.http.h11_impl `
        --hidden-import uvicorn.protocols.websockets.websockets_impl desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
} finally {
    Pop-Location
}

# Android packages the same server/ and web/ code.
$androidBuild = Join-Path $PSScriptRoot 'android\build-android.ps1'
if (-not $SkipAndroid -and (Test-Path -LiteralPath $androidBuild)) {
    try {
        & $androidBuild
    } catch {
        Write-Warning "TradingView.exe was built, but the Android build failed: $_"
    }
}
