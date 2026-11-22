# Step by step: internal-SSD unlock (MSI B850 + RTX 3060 12 GB)

This is the exact sequence used on the one tested machine, including the problems hit
along the way and their fixes. **Read each step before running it.** Steps marked
⚠️ change your disk or boot setup; nothing here does that automatically.

**Requirements:** exactly the hardware in the [README](../README.md) table, Windows 11 UEFI,
Secure Boot **off**, BitLocker **off/fully decrypted**, Python 3 on Windows, admin rights,
a full backup or restore point. The tools refuse any other hardware. Do not edit them
to "make them fit": the checks exist because a wrong value can hang the GPU.

Placeholders used below (replace them with your own values, braces included):

| Placeholder | What it is | Where you get it |
|---|---|---|
| `{UNLOCK-ESP-GUID}` | GUID of the new 1 GiB EFI partition | `esp-config.json` → `UnlockPartitionGuid` (step 3) |
| `{WINDOWS-ESP-GUID}` | GUID of your existing Windows EFI partition | `esp-config.json` → `WindowsPartitionGuid` |
| `{PROBE-ID}`, `{UNLOCK-ID}` | Boot entry IDs | printed by `bcdedit /copy` |
| `REPO` | the folder where you cloned/unzipped this repository | |

All PowerShell commands start in the repository folder: `cd "REPO"`.

---

## 1. Identity check (normal PowerShell, read-only)

```powershell
cmd /c "python -I .\tools\collect_compatibility.py > inventory.json"
python research\msi-b850-rtx3060\msi_b850_gate.py inventory inventory.json
```

It must report a match for board, BIOS 1.A20, CPU, RTX 3060 `1462:397d` and iGPU.
(`cmd /c` writes plain UTF-8; the checker also reads PowerShell's UTF-16 output.)

## 2. Disk report (admin PowerShell, read-only)

Right-click PowerShell → **Run as administrator** (the title must start with "Administrator:").

```powershell
powershell -ExecutionPolicy Bypass -File .\research\msi-b850-rtx3060\inspect-esp.ps1
```

Must show: Windows EFI partition with Boot Manager, Secure Boot off, BitLocker off.
Without admin rights, these show as "unknown"; that is not an error, just rerun as admin.

## 3. ⚠️ Create a separate 1 GiB EFI partition

Your Windows EFI partition is **never** touched. If the SSD has no free space, shrink C: by
1100 MB. Admin PowerShell → `diskpart`, then one line at a time:

```
list volume
select volume C
shrink desired=1100
select disk 0
create partition efi size=1040
format quick fs=fat32 label="GU_UNLOCK"
list partition
exit
```

Note the new partition's number from `list partition` **now** and immediately run:

```powershell
powershell -ExecutionPolicy Bypass -File .\research\msi-b850-rtx3060\inspect-esp.ps1 -UnlockDisk 0 -UnlockPartition <number>
python research\msi-b850-rtx3060\msi_b850_gate.py readiness --esp-config research\msi-b850-rtx3060\build\esp-config.json
```

The "separate dedicated ESP" gate must be satisfied. Open
`research\msi-b850-rtx3060\build\esp-config.json` and write down
`UnlockPartitionGuid` and `WindowsPartitionGuid`.

> **Important:** partition **numbers change after a reboot** (on the tested PC, the
> number pointed to the recovery partition after restarting). From here on, always
> address the new partition by its GUID, never by number. Windows also hides drive
> letters on EFI partitions from most commands; use `mountvol` as shown below.

Mount it as U: (admin, the single quotes matter in PowerShell) and verify:

```powershell
mountvol U: '\\?\Volume{UNLOCK-ESP-GUID}\'
mountvol U: /L
```

`/L` must print your `{UNLOCK-ESP-GUID}`. If it prints anything else, stop.
If U: is already taken, first run `mountvol U: /D`.

## 4. Install LLVM 20.1.8 (exact version required)

Download `LLVM-20.1.8-win64.exe` from the
[LLVM 20.1.8 release](https://github.com/llvm/llvm-project/releases/tag/llvmorg-20.1.8),
install to `C:\TOOLS\LLVM20`, then check `& "C:\TOOLS\LLVM20\bin\clang.exe" --version`.

## 5. Build the read-only probe

```powershell
python research\msi-b850-rtx3060\build_internal.py probe --internal-esp-guid "{UNLOCK-ESP-GUID}" --windows-esp-guid "{WINDOWS-ESP-GUID}" --clang C:\TOOLS\LLVM20\bin\clang.exe --lld-link C:\TOOLS\LLVM20\bin\lld-link.exe
```

Output: `research\msi-b850-rtx3060\build\probe\probe\PROBE-INTERNAL.EFI`.

## 6. ⚠️ Copy the probe and add a boot entry after Windows (admin)

```powershell
mountvol U: '\\?\Volume{UNLOCK-ESP-GUID}\'
mountvol U: /L
mkdir U:\EFI\WindowsGPUUnlock, U:\RTX3090-UNLOCK
Copy-Item research\msi-b850-rtx3060\build\probe\probe\PROBE-INTERNAL.EFI U:\EFI\WindowsGPUUnlock\
bcdedit /export "$env:USERPROFILE\Documents\BCD-backup-before-probe"
bcdedit /copy "{bootmgr}" /d "GPU Probe - READ ONLY"
```

Use the ID printed by the last command as `{PROBE-ID}`:

```powershell
bcdedit /set "{PROBE-ID}" device partition=U:
bcdedit /set "{PROBE-ID}" path \EFI\WindowsGPUUnlock\PROBE-INTERNAL.EFI
bcdedit /set "{fwbootmgr}" displayorder "{PROBE-ID}" /addlast
bcdedit /enum firmware
```

**Windows Boot Manager must stay first.** `/addlast` puts the new entry at the end.

## 7. Two cold probe boots, then collect the captures

Monitor on the RTX 3060. Twice: `shutdown /s /t 0` (full shutdown) → power on → tap **F11** →
**GPU Probe - READ ONLY**. It only reads hardware, saves files and continues to Windows.

Then (admin):

```powershell
mountvol U: '\\?\Volume{UNLOCK-ESP-GUID}\'
dir U:\RTX3090-UNLOCK
mkdir research\profile\local\capture
Copy-Item U:\RTX3090-UNLOCK\* research\profile\local\capture\
```

You should see `DIAG00.TXT`, `DIAG01.TXT`, `PROM.BIN` and `IMGnn.BIN`. An empty folder
usually means you were looking at the wrong partition: remount by GUID and check again.

## 8. Make the five NVIDIA firmware files (WSL Ubuntu)

These files are **not** in this repository and must not be redistributed. You build
them locally; every file is checked against a fixed SHA256.

Admin PowerShell: `wsl --install -d Ubuntu-24.04`, then in the Ubuntu window:

```bash
sudo apt update && sudo apt install -y git make tar xz-utils zstd curl
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env
uv python install 3.14
git clone https://github.com/Zagitta/gpu-unlock.git ~/gpu-unlock-firmware-source
cd ~/gpu-unlock-firmware-source && git checkout --detach 0ebdb1c6308b6a2072be5bf04513472942541956
make inputs firmware PYTHON="$(uv python find 3.14)" INPUTS="$PWD/inputs" ASSETS="$PWD/assets"
```

**If `make` stops with a SHA256 mismatch** for the CachyOS `nvidia-utils` package (on
2026-11-22 that archive served a 988-byte placeholder page instead of the package),
do **not** bypass the check. Take the one file it supplies, `gsp_ga10x.bin`, from
NVIDIA's official driver instead:

```bash
cd ~/gpu-unlock-firmware-source
PY="$(uv python find 3.14)"
curl -fL -o inputs/NVIDIA-Linux-x86_64-610.43.03.run https://download.nvidia.com/XFree86/Linux-x86_64/610.43.03/NVIDIA-Linux-x86_64-610.43.03.run
sh inputs/NVIDIA-Linux-x86_64-610.43.03.run --extract-only --target /tmp/nv610
sha256sum /tmp/nv610/firmware/gsp_ga10x.bin
```

Continue **only if** it prints `572373620a37418f24dc16b5031c39338778c3257e48e8408de9a57291b24f3a`:

```bash
rm -rf assets assets.src && mkdir -p assets assets.src
cp /tmp/nv610/firmware/gsp_ga10x.bin assets/
tar -xf inputs/NVIDIA-kernel-module-source-610.43.03.tar.xz -C assets.src
"$PY" firmware/extract_boot_assets.py assets.src/NVIDIA-kernel-module-source-610.43.03 assets
"$PY" -c "import sys,hashlib;sys.path.insert(0,'firmware');import generate_v67 as g;p=g.build_payload();assert hashlib.sha256(p).hexdigest()==g.PAYLOAD_SHA256;open('assets/v67-ga102-610.43.03.bin','wb').write(p)"
sha256sum assets/*.bin
```

All five hashes must equal the ones in `internal-boot/unlock/assets.json`
(`booter_load_ga102.bin 056cbc…`, `v67-ga102-610.43.03.bin 2a0aa1…`, `gsp_ga10x.bin 572373…`,
`gsp_boot_ga102.bin 3df651…`, `gsp_boot_desc_ga102.bin cc5a3f…`). Then:

```bash
mkdir -p /mnt/c/LOCAL-FIRMWARE/assets && cp assets/*.bin /mnt/c/LOCAL-FIRMWARE/assets/
```

## 9. Generate your profile and run the host tests (no hardware involved)

Needs Visual Studio C++ Build Tools ("Desktop development with C++"). Normal PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. .\research\profile\host.ps1
Import-GuHostCompiler
$loc = Build-GuFwsecLocate (Get-Location).Path 'C:\TOOLS\LLVM20\bin\clang.exe'
python -I research\profile\make-profile.py research\profile\local\capture $loc
Copy-Item research\msi-b850-rtx3060\build\probe\platform-config.h internal-boot\boot\platform-config.h
.\research\profile\test-profile.ps1 -Assets C:\LOCAL-FIRMWARE\assets -Clang C:\TOOLS\LLVM20\bin\clang.exe -Python python
python research\msi-b850-rtx3060\msi_b850_gate.py readiness --esp-config research\msi-b850-rtx3060\build\esp-config.json
```

`make-profile` prints a `LOCAL-250410de-397d1462-…` profile name. The `platform-config.h`
copy is required by the host tests (it only holds your two partition GUIDs and is git-ignored).
The readiness check must show 3 of 4 gates satisfied; the 4th is the real hardware run.

## 10. Build the unlock images

```powershell
python research\msi-b850-rtx3060\build_internal.py unlock --internal-esp-guid "{UNLOCK-ESP-GUID}" --windows-esp-guid "{WINDOWS-ESP-GUID}" --clang C:\TOOLS\LLVM20\bin\clang.exe --lld-link C:\TOOLS\LLVM20\bin\lld-link.exe --powershell powershell.exe
Get-Content research\msi-b850-rtx3060\build\unlock\manifest.json
```

It refuses unless your profile and host-test evidence pass before and after compiling.

## 11. ⚠️ Stage the unlock and add its boot entry (admin)

```powershell
mountvol U: '\\?\Volume{UNLOCK-ESP-GUID}\'
mountvol U: /L
$b='research\msi-b850-rtx3060\build\unlock'
Copy-Item "$b\wrapper\HEADLESS-INTERNAL.EFI" U:\EFI\WindowsGPUUnlock\HEADLESS.EFI
Copy-Item "$b\child\UNLOCK-HEADLESS-INTERNAL.EFI" U:\EFI\WindowsGPUUnlock\UNLOCK-HEADLESS.EFI
mkdir U:\RTX3090-UNLOCK\ASSETS
Copy-Item C:\LOCAL-FIRMWARE\assets\*.bin U:\RTX3090-UNLOCK\ASSETS\
Get-FileHash U:\EFI\WindowsGPUUnlock\*.EFI, U:\RTX3090-UNLOCK\ASSETS\*.bin | Format-Table Hash,Path -AutoSize
bcdedit /copy "{bootmgr}" /d "GPU Unlock - EXPERIMENTAL"
```

Compare the EFI hashes with `manifest.json` and the asset hashes with `assets.json`. Then:

```powershell
bcdedit /set "{UNLOCK-ID}" device partition=U:
bcdedit /set "{UNLOCK-ID}" path \EFI\WindowsGPUUnlock\HEADLESS.EFI
bcdedit /set "{fwbootmgr}" displayorder "{UNLOCK-ID}" /addlast
bcdedit /enum firmware
```

The child is loaded only if its SHA256 matches the one built into the wrapper.

## 12. ⚠️ The supervised unlock boot

This changes the GPU's running state (not the VBIOS, not Windows drivers, not Windows
boot files). A full power-off resets it.

1. Save your work. `shutdown /s /t 0`.
2. Power on, tap **F11**, choose **GPU Unlock - EXPERIMENTAL**. **Press no keys.**
   (Esc in the first 5 seconds cancels and boots Windows unchanged.)
3. The screen goes **black** while the display driver is released. Windows should come up
   within about 3 minutes.

**If Windows does not come back:** hold power 5 s, switch the PSU off for 10 s, power on,
F11 → **Windows Boot Manager**. Never retry the unlock in the same power session.

Check the result (admin):

```powershell
mountvol U: '\\?\Volume{UNLOCK-ESP-GUID}\'
Get-ChildItem U:\RTX3090-UNLOCK -Recurse -Include HLS*.TXT,REPORT.TXT | Sort LastWriteTime | Select -Last 2 | % { "== $($_.FullName)"; Get-Content $_ -Tail 5 }
Get-PnpDevice -Class Display | Select FriendlyName,Status,Problem
nvidia-smi
```

Success on the tested machine looked like `FINAL status=0 stage=transaction attempted=1 … clean=1`,
the RTX 3060 `Status OK / CM_PROB_NONE`, and a normal `nvidia-smi`. That still is not proof of
the speed-up; measure it (step 13). The unlock lasts until a full shutdown; repeat step 12
(F11) after each cold boot. Keep it a manual F11 choice.

## 13. Measure: benchmark and safe array test

**Benchmark** (same binary for both runs). CUDA 12.6's compiler crashes with Visual Studio
2026 (`cudafe++ ACCESS_VIOLATION`), so install the VS 2019 C++ Build Tools side by side:

```powershell
winget install --id Microsoft.VisualStudio.2019.BuildTools -e --override "--passive --wait --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmark\setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmark\build.ps1 -Card 3060
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmark\run-benchmark.ps1 -Card 3060
```

Run `run-benchmark.ps1 -Card 3060` once unlocked and once after a normal boot (no F11), then:
`python .\benchmark\compare_results.py .\benchmark\results\<normal> .\benchmark\results\<unlocked> --same-machine-confirmed`.
Expected on the tested card: FP32 unchanged, FP16/BF16/TF32 Tensor ≈1.8–1.9×.

**Safe array test** (CuPy, NumPy-style matrices on the GPU, checked against NumPy on the CPU):

```powershell
py -m pip install numpy cupy-cuda12x nvidia-ml-py
py research\msi-b850-rtx3060\gpu-array-test\safe_gpu_array_test.py
```

Watchdog stops cleanly on: ≥80 °C, 2-second average ≥160 W, any reading ≥175 W, hardware
slowdown, VRAM use >4 GiB or <2 GiB free, 60 s per mode, or Ctrl+C. It checks 1.2 V only if the
driver reports core voltage (GeForce drivers usually don't, and it says so). It changes no
clocks, voltages or power limits. Tested card: FP16 ≈48 TFLOPS unlocked vs ≈22–26 normal.

## Rollback

- Remove an entry: `bcdedit /delete "{PROBE-ID}"` / `bcdedit /delete "{UNLOCK-ID}"`.
- Restore all boot settings: `bcdedit /import "$env:USERPROFILE\Documents\BCD-backup-before-probe"`.
- Remove the partition (admin `diskpart`): `select disk 0`, `list partition`, select the
  1040 MB **GU_UNLOCK** partition (check the size and label carefully), `delete partition override`;
  then `select volume C`, `extend`.
- The Windows EFI partition, Windows Boot Manager, firmware settings and Secure Boot were
  never changed by any step above.
