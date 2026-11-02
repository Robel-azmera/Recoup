# Runs Firestore emulator + API + web together (see Procfile).
Set-Location (Join-Path $PSScriptRoot '..')
$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'
uvx honcho start @args
