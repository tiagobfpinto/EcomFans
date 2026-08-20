.PHONY: dev

dev:
	powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$$projectRoot = (Get-Location).Path; Start-Process powershell.exe -WorkingDirectory $$projectRoot -ArgumentList '-NoExit', '-Command', 'python -m flask run'; Start-Process powershell.exe -WorkingDirectory $$projectRoot -ArgumentList '-NoExit', '-Command', 'python -m flask worker'"
