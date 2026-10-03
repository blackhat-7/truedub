# Installs the TrueDub companion and starts it hidden at every login.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv sync --directory $dir
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

# pythonw runs without a console window; logs go to ~\.cache\truedub\truedub.log
$python = Join-Path $dir ".venv\Scripts\pythonw.exe"
$arguments = '-c "from truedub.app import main; main()"'
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut("$startup\TrueDub.lnk")
$shortcut.TargetPath = $python
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $dir
$shortcut.Save()

Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $dir
Write-Host "TrueDub companion installed and running on http://127.0.0.1:7861"
