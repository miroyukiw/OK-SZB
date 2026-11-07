$ErrorActionPreference = 'Stop'
$panelRoot = $PSScriptRoot
$panelPython = Join-Path $panelRoot '.panel-runtime\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $panelPython)) { throw 'OK-SZB panel Python runtime is missing.' }
$panelScript = Join-Path $panelRoot 'ok_panel_bootstrap.py'
Start-Process -FilePath $panelPython -ArgumentList @('-B','-X','utf8',('"' + $panelScript + '"')) -WorkingDirectory $panelRoot -WindowStyle Hidden
