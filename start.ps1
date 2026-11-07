$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Test-Path -LiteralPath '.\STOP') { throw 'STOP 文件存在。确认需要新开 CPU 测试后移走 STOP，再运行。' }
& '.\.venv\Scripts\python.exe' -X utf8 -u run.py @args
