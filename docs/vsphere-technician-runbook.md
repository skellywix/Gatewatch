# vSphere Technician Deployment Runbook

Last reviewed: 2026-06-28

This runbook gives a technician the command path for deploying the current Access Register app on a single Windows Server VM in vSphere.

Use this with `docs/vsphere-deployment.md`. The deployment spec explains the target architecture, VM sizing, user accounts, network rules, and production gaps. This runbook is the operational checklist.

## Assumptions

- vCenter access is available from an admin workstation with VMware PowerCLI.
- A patched Windows Server 2022 or Windows Server 2025 template exists.
- The VM will be domain joined through the template customization spec or normal site process.
- Python 3.12 or newer is available from an approved internal software source.
- The current pilot deployment uses one writable app VM and a local SQLite database.
- The app service account is either a gMSA named `DOMAIN\gmsa-ar-app$` or a vaulted fallback domain account named `DOMAIN\svc-ar-app`.
- Commands are run in elevated PowerShell unless a section says otherwise.

Replace placeholder values before running any command.

## Deployment Variables

On the vSphere admin workstation:

```powershell
$vCenter = "vcenter.example.local"
$VmName = "AR-APP01"
$TemplateName = "TEMPLATE-WIN2025-STANDARD"
$CustomizationSpecName = "OSCUSTOM-WIN-DOMAIN"
$DatastoreName = "DATASTORE-PROD-01"
$ResourcePoolName = "RP-APP-INTERNAL"
$FolderName = "Access Register"
$PortGroupName = "VLAN120-APP-INTERNAL"
$CpuCount = 4
$MemoryGB = 8
$DataDiskGB = 100
```

Inside the new VM:

```powershell
$Root = "D:\AccessRegister"
$AppPath = "$Root\app"
$DataPath = "$Root\data"
$BackupPath = "$DataPath\backups"
$LogPath = "$Root\logs"
$ImportDropPath = "$Root\import-drop"
$RunScript = "$Root\run-access-register.ps1"
$PythonExe = "C:\Program Files\Python312\python.exe"
$PythonInstaller = "\\fileserver\packages\python-3.12-amd64.exe"
$AppServiceAccount = "DOMAIN\gmsa-ar-app$"
$FallbackServiceAccount = "DOMAIN\svc-ar-app"
$AppAdminsGroup = "DOMAIN\AccessRegister-Admins"
$AllowedRemoteAddress = "10.20.30.0/24"
$AppPort = 8087
```

## 1. Create the vSphere VM

Run from the vSphere admin workstation:

```powershell
Import-Module VMware.VimAutomation.Core
Connect-VIServer -Server $vCenter

$Template = Get-Template -Name $TemplateName
$Datastore = Get-Datastore -Name $DatastoreName
$ResourcePool = Get-ResourcePool -Name $ResourcePoolName
$Folder = Get-Folder -Name $FolderName
$CustomizationSpec = Get-OSCustomizationSpec -Name $CustomizationSpecName

$Vm = New-VM `
  -Name $VmName `
  -Template $Template `
  -Datastore $Datastore `
  -ResourcePool $ResourcePool `
  -Location $Folder `
  -OSCustomizationSpec $CustomizationSpec `
  -DiskStorageFormat Thin

$Vm | Set-VM -NumCpu $CpuCount -MemoryGB $MemoryGB -Confirm:$false

Get-NetworkAdapter -VM $Vm |
  Set-NetworkAdapter -NetworkName $PortGroupName -Type Vmxnet3 -StartConnected:$true -Confirm:$false

New-HardDisk -VM $Vm -CapacityGB $DataDiskGB -StorageFormat Thin -Persistence Persistent | Out-Null

Start-VM -VM $Vm
```

Confirm the VM is powered on:

```powershell
Get-VM -Name $VmName | Select-Object Name,PowerState,NumCpu,MemoryGB
```

## 2. Prepare the Data Disk

Run inside `AR-APP01` after Windows sees the second disk:

```powershell
Get-Disk | Where-Object PartitionStyle -eq "RAW" |
  Initialize-Disk -PartitionStyle GPT -PassThru |
  New-Partition -DriveLetter D -UseMaximumSize |
  Format-Volume -FileSystem NTFS -NewFileSystemLabel "AccessRegisterData" -Confirm:$false
```

If `D:` already exists and is formatted by the template, verify it:

```powershell
Get-Volume -DriveLetter D | Select-Object DriveLetter,FileSystemLabel,FileSystem,SizeRemaining,Size
```

## 3. Create Folders

```powershell
New-Item -ItemType Directory -Force -Path `
  $Root, `
  $AppPath, `
  $DataPath, `
  $BackupPath, `
  $LogPath, `
  $ImportDropPath | Out-Null
```

## 4. Apply NTFS Permissions

Keep inheritance from the server baseline, then add only the application-specific access:

```powershell
icacls "$AppPath" /grant "${AppServiceAccount}:(OI)(CI)(RX)" /T
icacls "$DataPath" /grant "${AppServiceAccount}:(OI)(CI)(M)" /T
icacls "$LogPath" /grant "${AppServiceAccount}:(OI)(CI)(M)" /T
icacls "$ImportDropPath" /grant "${AppServiceAccount}:(OI)(CI)(RX)" /T

icacls "$Root" /grant "${AppAdminsGroup}:(OI)(CI)(M)" /T
icacls "$Root" /grant "Administrators:(OI)(CI)(F)" /T
```

Verify effective ACL entries:

```powershell
icacls "$Root"
icacls "$AppPath"
icacls "$DataPath"
icacls "$LogPath"
```

## 5. Install Python

Use the approved internal Python 3.12 installer path for your environment:

```powershell
Start-Process `
  -FilePath $PythonInstaller `
  -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" `
  -Wait

& $PythonExe --version
```

Expected result: Python 3.12 or newer.

## 6. Copy the App Files

Run from a protected internal source path that contains the checked-out app:

```powershell
$SourceAppPath = "\\fileserver\deploy\access-inventory-app"
Copy-Item -Path "$SourceAppPath\*" -Destination $AppPath -Recurse -Force
```

Verify required files:

```powershell
Test-Path "$AppPath\app.py"
Test-Path "$AppPath\web\index.html"
Test-Path "$AppPath\tests\test_app.py"
```

## 7. Set Runtime Environment

```powershell
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_HOST", "0.0.0.0", "Machine")
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_PORT", "$AppPort", "Machine")
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_DB", "$DataPath\access_register.db", "Machine")
[Environment]::SetEnvironmentVariable("ACCESS_REGISTER_SCHEDULER", "1", "Machine")
```

Confirm values:

```powershell
[Environment]::GetEnvironmentVariable("ACCESS_REGISTER_HOST", "Machine")
[Environment]::GetEnvironmentVariable("ACCESS_REGISTER_PORT", "Machine")
[Environment]::GetEnvironmentVariable("ACCESS_REGISTER_DB", "Machine")
[Environment]::GetEnvironmentVariable("ACCESS_REGISTER_SCHEDULER", "Machine")
```

## 8. Create the Run Script

```powershell
@"
`$ErrorActionPreference = "Stop"
`$env:ACCESS_REGISTER_HOST = "0.0.0.0"
`$env:ACCESS_REGISTER_PORT = "$AppPort"
`$env:ACCESS_REGISTER_DB = "$DataPath\access_register.db"
`$env:ACCESS_REGISTER_SCHEDULER = "1"
Set-Location "$AppPath"
& "$PythonExe" "app.py" *> "$LogPath\access-register.log"
"@ | Set-Content -Path $RunScript -Encoding UTF8

icacls "$RunScript" /grant "${AppServiceAccount}:(R)"
```

Review the generated script:

```powershell
Get-Content $RunScript
```

## 9. Install the gMSA on the VM

Skip this section if the site uses the fallback domain service account.

```powershell
Install-WindowsFeature RSAT-AD-PowerShell
Install-ADServiceAccount -Identity "gmsa-ar-app"
Test-ADServiceAccount -Identity "gmsa-ar-app"
```

Expected result:

```text
True
```

## 10. Register the Startup Task with gMSA

```powershell
$TaskName = "Access Register"
$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""

$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal `
  -UserId $AppServiceAccount `
  -LogonType ServiceAccount `
  -RunLevel Limited

$Settings = New-ScheduledTaskSettingsSet `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Principal $Principal `
  -Settings $Settings `
  -Description "Runs the Access Register internal web app."
```

Start and verify the task:

```powershell
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
Get-ScheduledTaskInfo -TaskName $TaskName
```

## 11. Fallback Scheduled Task with Domain Service Account

Use this only when gMSA is not available.

```powershell
$TaskName = "Access Register"
$Credential = Get-Credential -UserName $FallbackServiceAccount -Message "Enter the Access Register service account password."

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`""

$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -User $Credential.UserName `
  -Password $Credential.GetNetworkCredential().Password `
  -Description "Runs the Access Register internal web app."
```

Start and verify the fallback task:

```powershell
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5
Get-ScheduledTaskInfo -TaskName $TaskName
```

## 12. Configure Windows Firewall

Limit inbound app access to the approved admin subnet or reverse proxy.

```powershell
New-NetFirewallRule `
  -DisplayName "Access Register HTTP $AppPort" `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort $AppPort `
  -RemoteAddress $AllowedRemoteAddress `
  -Profile Domain
```

Verify the rule:

```powershell
Get-NetFirewallRule -DisplayName "Access Register HTTP $AppPort" |
  Get-NetFirewallPortFilter

Get-NetFirewallRule -DisplayName "Access Register HTTP $AppPort" |
  Get-NetFirewallAddressFilter
```

## 13. Initialize and Validate the App

Run locally on the VM:

```powershell
Invoke-WebRequest "http://127.0.0.1:$AppPort/api/summary" -UseBasicParsing
```

Run automated checks:

```powershell
Set-Location $AppPath
& $PythonExe -m py_compile app.py
& $PythonExe -m unittest discover -s tests
& $PythonExe -m unittest tests.test_ui_smoke
```

Confirm the database and log files exist:

```powershell
Test-Path "$DataPath\access_register.db"
Test-Path "$LogPath\access-register.log"
Get-ChildItem $DataPath
Get-ChildItem $LogPath
```

From an allowed workstation:

```powershell
Invoke-WebRequest "http://AR-APP01.example.local:$AppPort/api/summary" -UseBasicParsing
```

## 14. Set Initial Security Settings in the App

Open the app from an allowed workstation:

```text
http://AR-APP01.example.local:8087
```

In the Security view, set:

| Field | Value |
| --- | --- |
| Provider | Active Directory or Microsoft Entra ID |
| Require real login when provider is wired | Checked |
| Admin group | `DOMAIN\AccessRegister-Admins` |
| Reviewer group | `DOMAIN\AccessRegister-Reviewers` |
| HR group | `DOMAIN\AccessRegister-HR` |
| Read-only group | `DOMAIN\AccessRegister-ReadOnly` |

Current limitation: these settings are stored as planning data until real authentication is implemented. Network restriction remains mandatory for the pilot.

## 15. Configure Optional AD Export Drop

If a separate AD export job writes a CSV or JSON file for manual import, restrict its output folder:

```powershell
icacls "$ImportDropPath" /grant 'DOMAIN\gmsa-ar-adsync$:(OI)(CI)(M)' /T
icacls "$ImportDropPath" /grant "${AppServiceAccount}:(OI)(CI)(RX)" /T
```

Example AD CSV export command on the approved AD export host:

```powershell
Get-ADUser -Filter * -Properties EmployeeID,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName,DistinguishedName,LastLogonDate |
  Select-Object EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName,DistinguishedName,LastLogonDate |
  Export-Csv "\\AR-APP01\D$\AccessRegister\import-drop\ad-users.csv" -NoTypeInformation
```

Treat this export as sensitive data.

## 16. Backup Check

Create the first in-app backup from the Governance view, then verify it on disk:

```powershell
Get-ChildItem "$BackupPath" -Filter "*.db" | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

Confirm the backup folder is included in enterprise backup scope:

```powershell
Get-ChildItem "$Root" -Recurse -Depth 2 | Select-Object FullName,Length,LastWriteTime
```

## 17. Restart Test

```powershell
Restart-Computer -Force
```

After restart:

```powershell
Get-ScheduledTaskInfo -TaskName "Access Register"
Invoke-WebRequest "http://127.0.0.1:$AppPort/api/summary" -UseBasicParsing
Get-Content "$LogPath\access-register.log" -Tail 50
```

## 18. Rollback Commands

Use only if the deployment needs to be removed from the pilot VM.

```powershell
Stop-ScheduledTask -TaskName "Access Register" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "Access Register" -Confirm:$false
Remove-NetFirewallRule -DisplayName "Access Register HTTP $AppPort" -ErrorAction SilentlyContinue
```

Preserve the database and logs for evidence unless the data owner approves deletion:

```powershell
Get-ChildItem "$DataPath"
Get-ChildItem "$LogPath"
```

## Completion Evidence

Capture these outputs in the deployment ticket:

```powershell
Get-VM -Name $VmName | Select-Object Name,PowerState,NumCpu,MemoryGB
Get-ScheduledTaskInfo -TaskName "Access Register"
Invoke-WebRequest "http://127.0.0.1:$AppPort/api/summary" -UseBasicParsing
Get-ChildItem "$DataPath\access_register.db"
Get-NetFirewallRule -DisplayName "Access Register HTTP $AppPort"
```

Also attach:

- Screenshot of the Access Register dashboard.
- Screenshot of the Security view group mappings.
- Output from `python -m unittest discover -s tests`.
- Confirmation that the VM is in the backup policy.

## References

Broadcom. "New-VM." *VMware PowerCLI Reference*, https://developer.broadcom.com/powercli/latest/vmware.vimautomation.core/commands/new-vm. Accessed 28 June 2026.

Broadcom. "New-HardDisk." *VMware PowerCLI Reference*, https://developer.broadcom.com/powercli/latest/vmware.vimautomation.core/commands/new-harddisk. Accessed 28 June 2026.

Microsoft. "Install-ADServiceAccount." *Microsoft Learn*, https://learn.microsoft.com/en-us/powershell/module/activedirectory/install-adserviceaccount. Accessed 28 June 2026.

Microsoft. "New-NetFirewallRule." *Microsoft Learn*, https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule. Accessed 28 June 2026.

Microsoft. "Register-ScheduledTask." *Microsoft Learn*, https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask. Accessed 28 June 2026.

Python Software Foundation. "Using Python on Windows." *Python 3 Documentation*, https://docs.python.org/3/using/windows.html. Accessed 28 June 2026.
