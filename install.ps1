param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ProjectDir = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd("\")
$VenvDir = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir ".venv"))
$ExpectedVenvDir = $ProjectDir + "\.venv"
if (-not $VenvDir.Equals($ExpectedVenvDir, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe virtual environment path: $VenvDir"
}

function Test-PythonCandidate {
    param([string]$Candidate)

    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    try {
        & $Candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-SystemPython {
    $candidates = New-Object System.Collections.Generic.List[string]
    $versions = @("314", "313", "312", "311", "310", "39")

    foreach ($version in $versions) {
        if ($env:LOCALAPPDATA) {
            $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"))
        }
        if ($env:ProgramFiles) {
            $candidates.Add((Join-Path $env:ProgramFiles "Python$version\python.exe"))
        }
        if (${env:ProgramFiles(x86)}) {
            $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "Python$version\python.exe"))
        }
    }

    $commands = Get-Command python.exe -All -ErrorAction SilentlyContinue
    foreach ($command in $commands) {
        if ($command.Source -and $command.Source -notmatch "\\Microsoft\\WindowsApps\\python\.exe$") {
            $candidates.Add($command.Source)
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if (-not $candidate) {
            continue
        }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        if (Test-PythonCandidate $candidate) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Find-WinGet {
    if ($env:LOCALAPPDATA) {
        $localWinget = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe"
        if (Test-Path -LiteralPath $localWinget -PathType Leaf) {
            return $localWinget
        }
    }
    $command = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }
    return $null
}

function Install-PythonWithWinGet {
    $winget = Find-WinGet
    if (-not $winget) {
        return $false
    }

    Write-Host "Python 3.9+ was not found. Installing Python 3.11 with WinGet..."
    try {
        & $winget install --id Python.Python.3.11 --exact --source winget --scope user --silent --disable-interactivity --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -eq 0) {
            Write-Host "WinGet reported that Python installation completed."
            return $true
        }
        Write-Warning "WinGet returned exit code $LASTEXITCODE. The official installer fallback will be used."
    }
    catch {
        Write-Warning "WinGet could not install Python: $($_.Exception.Message)"
    }
    return $false
}

function Install-PythonFromOfficialSite {
    $version = "3.11.9"
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($architecture -eq "ARM64") {
        $fileName = "python-$version-arm64.exe"
    }
    elseif ($architecture -eq "x86") {
        $fileName = "python-$version.exe"
    }
    else {
        $fileName = "python-$version-amd64.exe"
    }

    $url = "https://www.python.org/ftp/python/$version/$fileName"
    $installerPath = Join-Path $env:TEMP "youtube-lark-$fileName"
    Write-Host "Downloading the official Python $version installer from python.org..."

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $installerPath

        $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
        if ($signature.Status -ne "Valid") {
            throw "The downloaded installer signature is not valid: $($signature.Status)"
        }
        if ($signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
            throw "The downloaded installer is not signed by the Python Software Foundation."
        }

        Write-Host "The Python Software Foundation signature is valid. Installing for the current user..."
        $arguments = @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_pip=1",
            "Include_launcher=1",
            "InstallLauncherAllUsers=0",
            "Include_test=0"
        )
        $process = Start-Process -FilePath $installerPath -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -notin @(0, 3010)) {
            throw "The official Python installer returned exit code $($process.ExitCode)."
        }
        Write-Host "Official Python installation completed."
        return $true
    }
    catch {
        Write-Warning "Official Python installation failed: $($_.Exception.Message)"
        return $false
    }
    finally {
        if (Test-Path -LiteralPath $installerPath -PathType Leaf) {
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }
}

try {
    Write-Host "[1/4] Checking Python..."
    $systemPython = Find-SystemPython
    if (-not $systemPython) {
        $null = Install-PythonWithWinGet
        $systemPython = Find-SystemPython
        if (-not $systemPython) {
            $null = Install-PythonFromOfficialSite
            $systemPython = Find-SystemPython
        }
    }

    if (-not $systemPython) {
        throw "Python could not be installed automatically. Install Python 3.11 manually from https://www.python.org/downloads/release/python-3119/ and run install.bat again."
    }

    $pythonVersion = & $systemPython -c "import sys; print(sys.version.split()[0])"
    Write-Host "Python found: $pythonVersion"
    Write-Host "Interpreter: $systemPython"

    if ($CheckOnly) {
        Write-Host "Check-only mode completed. No environment or packages were changed."
        exit 0
    }

    Write-Host "[2/4] Preparing the project virtual environment..."
    $venvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-PythonCandidate $venvPython)) {
        if (Test-Path -LiteralPath $VenvDir) {
            Write-Host "The copied virtual environment is not portable. Rebuilding it for this PC..."
            & $systemPython -m venv --clear $VenvDir
        }
        else {
            & $systemPython -m venv $VenvDir
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the project virtual environment."
        }
    }

    if (-not (Test-PythonCandidate $venvPython)) {
        throw "The project virtual environment is not usable: $venvPython"
    }

    Write-Host "[3/4] Installing project dependencies..."
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip upgrade failed. Check the network or proxy, then run install.bat again."
    }
    & $venvPython -m pip install -r (Join-Path $ProjectDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed. Check the network or proxy, then run install.bat again."
    }

    Write-Host "[4/4] Starting first-run configuration..."
    & $venvPython (Join-Path $ProjectDir "setup.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Initial configuration did not complete."
    }

    Write-Host "Installation completed. Double-click doctor.bat before installing scheduled tasks."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
