# MSI B850 GAMING PLUS WIFI6E / RTX 3060 GA106: internal-ESP experiment

**Status: experimental, hardware untested, not a reviewed profile.** Full audit, plan,
evidence requirements and recovery: [MSI-B850-RTX3060-INTERNAL-BOOT-PLAN.md](../../MSI-B850-RTX3060-INTERNAL-BOOT-PLAN.md).

Nothing here installs, registers, stages or runs anything, and no existing file or
profile is changed.

```powershell
# Read-only checks
python -I .\tools\collect_compatibility.py > inventory.json
python research\msi-b850-rtx3060\msi_b850_gate.py inventory inventory.json
.\research\msi-b850-rtx3060\inspect-esp.ps1 -UnlockDisk 0 -UnlockPartition 5   # administrator; read-only; numbers from diskpart "list partition"
python research\msi-b850-rtx3060\msi_b850_gate.py readiness --esp-config research\msi-b850-rtx3060\build\esp-config.json

# Build only (LLVM 20.1.8). Output: research\msi-b850-rtx3060\build\
python research\msi-b850-rtx3060\build_internal.py probe  --internal-esp-guid <dedicated ESP> --windows-esp-guid <Windows ESP> --clang C:\TOOLS\LLVM20\bin\clang.exe --lld-link C:\TOOLS\LLVM20\bin\lld-link.exe
python research\msi-b850-rtx3060\build_internal.py unlock --internal-esp-guid <dedicated ESP> --windows-esp-guid <Windows ESP> --clang C:\TOOLS\LLVM20\bin\clang.exe --lld-link C:\TOOLS\LLVM20\bin\lld-link.exe

# Host tests (no hardware)
python research\msi-b850-rtx3060\test_msi_b850.py
```

`unlock` refuses unless the generated LOCAL profile matches this machine and
`research/profile/test-profile.ps1` evidence passes before and after compiling.
