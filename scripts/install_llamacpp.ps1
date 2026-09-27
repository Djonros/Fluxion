# Install llama-cpp-python (CPU, AVX2) into the current Python environment.
# Roadmap 18.5: no prebuilt wheels exist for Python 3.14, so we build from
# source with the VS2022 toolchain (cl.exe + bundled CMake).
# Interpreter: $env:FLUXION_PYTHON (set by fluxion-setup.bat) or python from
# PATH. Inside a virtual environment pip's --user is not allowed, so it is
# only used for a system interpreter.
param([switch]$Gpu)

$ErrorActionPreference = "Stop"
$py = if ($env:FLUXION_PYTHON) { $env:FLUXION_PYTHON } else { "python" }
$inVenv = (& $py -c "import sys; print(sys.prefix != sys.base_prefix)").Trim() -eq "True"
$userFlag = if ($inVenv) { "" } else { "--user" }
$vs = "C:\Program Files\Microsoft Visual Studio\2022\Community"
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$cmakeDir = Join-Path $vs "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found: $vcvars" }

# Windows MAX_PATH workaround: the sdist contains vendored llama.cpp web-ui
# paths deeper than 260 chars when extracted under a long TMP prefix.
New-Item -ItemType Directory -Force -Path "C:\t" | Out-Null

if ($Gpu) {
    # GPU pack (CUDA): prebuilt wheel from the llama-cpp-python extra index.
    # Requires a matching CUDA runtime; fails with a clear error if absent.
    Write-Host "[llamacpp] installing GPU pack (CUDA wheel)..." -ForegroundColor Cyan
    cmd /c "`"$vcvars`" && set PATH=$cmakeDir;%PATH% && set TMP=C:\t&& set TEMP=C:\t&& `"$py`" -m pip install $userFlag --upgrade llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124"
} else {
    Write-Host "[llamacpp] building CPU (AVX2) from source - 10-15 min..." -ForegroundColor Cyan
    cmd /c "`"$vcvars`" && set PATH=$cmakeDir;%PATH% && set TMP=C:\t&& set TEMP=C:\t&& `"$py`" -m pip install $userFlag --upgrade llama-cpp-python"
}
if ($LASTEXITCODE -ne 0) { throw "pip install failed with code $LASTEXITCODE" }

& $py -c "import llama_cpp; print('llama_cpp', llama_cpp.__version__, 'OK')"
