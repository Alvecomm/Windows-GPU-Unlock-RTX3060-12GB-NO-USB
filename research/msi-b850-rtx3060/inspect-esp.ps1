# GPL-2.0-only. READ-ONLY inspection for the MSI B850 / RTX 3060 internal-boot experiment.
# Reports the disk layout, the Windows ESP, an optional candidate dedicated ESP, Secure Boot,
# BitLocker and SMBIOS identity. It never creates, shrinks, formats, assigns letters to or
# retypes a partition, never touches EFI files, BCD, NVRAM or firmware settings, and never
# changes encryption. The only write is the JSON report (-Output, default under build/).
# Candidate rules mirror internal-boot/Configure.ps1: separate, non-system, GPT ESP type,
# FAT32, at least 1 GiB, with a volume path; the Windows ESP must contain bootmgfw.efi.
# Select the dedicated ESP by -UnlockDisk/-UnlockPartition (numbers as in diskpart "list partition").
# Windows usually hides drive letters of EFI-type partitions, so a letter is never required;
# -UnlockDriveLetter is accepted only when Windows still reports one.
param([Nullable[int]]$UnlockDisk,[Nullable[int]]$UnlockPartition,[ValidatePattern('^[D-Zd-z]$')][string]$UnlockDriveLetter,[string]$Output)
$ErrorActionPreference='Stop'
$espType='{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}'
if(!$Output){$Output=Join-Path $PSScriptRoot 'build/esp-config.json'}
$problems=@()
$isAdmin=$false;try{$isAdmin=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)}catch{}
if(!$isAdmin){$problems+='not running as administrator: ESP contents, Secure Boot and BitLocker cannot be read (right-click PowerShell, Run as administrator)'}

$parts=@(Get-Partition | Sort-Object DiskNumber,PartitionNumber)
$layout=@($parts | ForEach-Object {[ordered]@{Disk=$_.DiskNumber;Partition=$_.PartitionNumber;Letter=[string]$_.DriveLetter;
 SizeMiB=[math]::Round($_.Size/1MB,1);GptType=[string]$_.GptType;IsSystem=[bool]$_.IsSystem;IsBoot=[bool]$_.IsBoot}})

$system=@($parts | Where-Object {$_.IsSystem})
$windows=$null
if($system.Count -ne 1){$problems+='expected exactly one Windows system ESP'}
else{
 $wv=$system[0] | Get-Volume
 $hasBootmgr=$null
 if($isAdmin -and $wv.Path){try{$hasBootmgr=[bool](Get-Item -LiteralPath (Join-Path $wv.Path 'EFI\Microsoft\Boot\bootmgfw.efi') -Force -ErrorAction Stop)}catch [System.Management.Automation.ItemNotFoundException]{$hasBootmgr=$false}catch{}}
 if($hasBootmgr -eq $false){$problems+='Windows Boot Manager not found on the system ESP'}
 elseif($null -eq $hasBootmgr){$problems+='Windows ESP contents unreadable; Windows Boot Manager presence unknown'}
 $windows=[ordered]@{Guid=[string]$system[0].Guid;Disk=$system[0].DiskNumber;SizeMiB=[math]::Round($system[0].Size/1MB,1);
  FreeMiB=[math]::Round($wv.SizeRemaining/1MB,1);FileSystem=[string]$wv.FileSystem;HasBootManager=$hasBootmgr}
}

$candidate=$null;$separate=$false
$selected=$null
if($UnlockDisk -ne $null -or $UnlockPartition -ne $null){
 if($UnlockDisk -eq $null -or $UnlockPartition -eq $null){throw 'Give both -UnlockDisk and -UnlockPartition'}
 $selected=@($parts | Where-Object {$_.DiskNumber -eq $UnlockDisk -and $_.PartitionNumber -eq $UnlockPartition})
 if($selected.Count -ne 1){throw ('No partition '+$UnlockPartition+' on disk '+$UnlockDisk)}
} elseif($UnlockDriveLetter){
 $root=$UnlockDriveLetter.ToUpperInvariant()+':\'
 $selected=@($parts | Where-Object {([string]$_.DriveLetter).ToUpperInvariant() -eq $UnlockDriveLetter.ToUpperInvariant() -or (@($_.AccessPaths) -contains $root)})
 if($selected.Count -ne 1){throw ('Windows reports no partition with letter '+$root+' (normal for EFI partitions). Use -UnlockDisk and -UnlockPartition instead.')}
}
if($selected){
 $p=$selected[0];$d=Get-Disk -Number $p.DiskNumber
 $v=$null;try{$v=$p | Get-Volume -ErrorAction Stop}catch{}
 if(!$v){
  # fallback: the volume GUID path from the partition's access paths, read through WMI
  $gp=@(@($p.AccessPaths) | Where-Object {$_ -like '\\?\Volume{*'})
  if($gp){$w=@(Get-CimInstance Win32_Volume | Where-Object {$_.DeviceID -eq $gp[0]});if($w){$v=[pscustomobject]@{FileSystem=[string]$w[0].FileSystem;Path=[string]$w[0].DeviceID}}}
 }
 if(!$v){$v=[pscustomobject]@{FileSystem='';Path=$null}}
 $reasons=@()
 if($p.IsSystem){$reasons+='is the Windows system ESP'}
 if($windows -and [guid]$p.Guid -eq [guid]$windows.Guid){$reasons+='same GUID as the Windows ESP'}
 if([string]$p.GptType -ne $espType){$reasons+='GPT type is not EFI System Partition'}
 if($v.FileSystem -ne 'FAT32'){$reasons+='file system is not FAT32'}
 if($p.Size -lt 1GB){$reasons+='smaller than 1 GiB'}
 if(!$v.Path){$reasons+='volume has no path'}
 $separate=($reasons.Count -eq 0 -and $windows -ne $null -and $isAdmin -and $windows.HasBootManager -eq $true)
 $candidate=[ordered]@{Partition=$p.PartitionNumber;Letter=[string]$p.DriveLetter;Guid=[string]$p.Guid;Disk=$p.DiskNumber;
  DiskUniqueId=[string]$d.UniqueId;Offset=$p.Offset;Size=$p.Size;FileSystem=[string]$v.FileSystem;Refusals=$reasons}
 $problems+=$reasons | ForEach-Object {'candidate '+$_}
} else {
 $problems+='no dedicated ESP given: none is created by any tool here'
 $others=@($parts | Where-Object {!$_.IsSystem -and [string]$_.GptType -eq $espType})
 foreach($o in $others){$problems+=('other EFI-type partition present: -UnlockDisk '+$o.DiskNumber+' -UnlockPartition '+$o.PartitionNumber+' ('+[math]::Round($o.Size/1MB)+' MiB)')}
}

$secure=$null;try{$secure=[bool](Confirm-SecureBootUEFI)}catch{$problems+='Secure Boot state unreadable (needs administrator)'}
if($secure){$problems+='Secure Boot is enabled; unsigned experimental EFI would not run (setting is never changed here)'}
$bitlocker=$null
try{$b=Get-BitLockerVolume -MountPoint C:;$bitlocker=[ordered]@{VolumeStatus=[string]$b.VolumeStatus;ProtectionStatus=[string]$b.ProtectionStatus}
 if([int]$b.VolumeStatus -ne 0 -or [int]$b.ProtectionStatus -ne 0){$problems+='C: is not fully decrypted with protection off (the tools never change this)'}}
catch{$problems+='BitLocker state unreadable (needs administrator)'}
$board=Get-CimInstance Win32_BaseBoard;$bios=Get-CimInstance Win32_BIOS

$report=[ordered]@{Format=1;ReadOnly=$true;SeparateEsp=$separate;
 UnlockPartitionGuid=$(if($candidate){$candidate.Guid}else{$null});WindowsPartitionGuid=$(if($windows){$windows.Guid}else{$null});
 WindowsEsp=$windows;Candidate=$candidate;Layout=$layout;SecureBootEnabled=$secure;BitLocker=$bitlocker;
 Board=[string]$board.Product;BiosVersion=[string]$bios.SMBIOSBIOSVersion;Problems=$problems;
 InspectedUtc=[DateTime]::UtcNow.ToString('o')}
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($Output)))
[IO.File]::WriteAllText([IO.Path]::GetFullPath($Output),($report | ConvertTo-Json -Depth 5),(New-Object Text.UTF8Encoding $false))
$report | ConvertTo-Json -Depth 5
Write-Output ('Read-only inspection written to '+$Output+'. Nothing on the disk, ESP, BCD or firmware was changed.')
