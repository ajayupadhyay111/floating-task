$ws = New-Object -ComObject WScript.Shell
$startupPath = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup\FloatTask.lnk")
$shortcut = $ws.CreateShortcut($startupPath)
$shortcut.TargetPath = "C:\Users\ajay7\AppData\Local\Programs\Python\Python314\pythonw.exe"
$shortcut.Arguments = "floating_tasks.py"
$shortcut.WorkingDirectory = "C:\Users\ajay7\Desktop\developement\FloatTask"
$shortcut.WindowStyle = 7
$shortcut.Save()
Write-Host "Startup shortcut created at: $startupPath"
