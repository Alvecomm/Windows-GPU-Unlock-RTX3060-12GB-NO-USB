# GPU Unlock: internal-SSD boot for MSI B850 + RTX 3060 (experimental)

This fork runs the Ampere GPU unlock from the **internal SSD** (a separate, dedicated
EFI partition chosen with F11) instead of a USB stick, on one specific machine:

| Part | Exact model |
|---|---|
| Motherboard | MSI B850 GAMING PLUS WIFI6E (MS-7E80), BIOS 1.A20 |
| CPU | AMD Ryzen 7 9700X (iGPU present) |
| GPU | RTX 3060 12 GB, GA106 `10de:2504`, subsystem `1462:397d` |
| OS | Windows 11, UEFI, Secure Boot off, BitLocker off |

> **Status: experimental.** Hardware-tested on this one machine only, in one supervised
> boot plus follow-up benchmarks (2026-11-22). Repeated boots, long-term stability and
> any other motherboard, BIOS or card are **untested**. It writes to the GPU at boot;
> use at your own risk. The profile is exact-match: other hardware is refused, not supported.

## Result on the tested machine

Same benchmark binary, same driver and settings ([evidence](research/msi-b850-rtx3060/evidence/20261122-benchmark/README.md)):

| Mode | Normal boot | Unlocked | Ratio |
|---|---|---|---|
| FP32 (control) | 8.08 TFLOPS | 8.09 TFLOPS | 1.00× |
| FP16 Tensor, FP32 accumulate | 25.99 | 47.90 | 1.84× |
| BF16 Tensor, FP32 accumulate | 25.90 | 49.11 | 1.90× |
| TF32 Tensor | 13.12 | 25.30 | 1.93× |

Safe GPU array test ([`safe_gpu_array_test.py`](research/msi-b850-rtx3060/gpu-array-test/safe_gpu_array_test.py),
NumPy-style 4096×4096 matrices on the GPU via CuPy, results checked against NumPy on the CPU):

| Mode | Normal boot | Unlocked | Accuracy (rel. error) |
|---|---|---|---|
| FP32 | 7.8 TFLOPS | 7.9 TFLOPS | 3.3e-7 |
| FP16 Tensor | 22.1 | **47.9** | 4.0e-4 |

Peak during the unlocked run: 67 °C, 119 W (card limit 170 W). Both runs finished normally.

The unlock lasts until a full shutdown; it is applied again by choosing the entry with F11.

### Verify it on your own machine

Run the same array test after an unlocked boot and after a normal boot, and compare the FP16 line:

```powershell
py -m pip install numpy cupy-cuda12x nvidia-ml-py
py research\msi-b850-rtx3060\gpu-array-test\safe_gpu_array_test.py
```

It is a verification test with failsafes, not a stress test: it computes in short bursts (50% of
the time), changes no clocks, voltages or power limits, and stops cleanly at ≥80 °C, a 2-second
average ≥160 W, any reading ≥175 W, hardware slowdown, more than 4 GiB VRAM or less than 2 GiB free.
It checks 1.2 V only if the driver reports core voltage (GeForce drivers usually don't, and it says so).

## How it works and how to use it

- **Start here: [step-by-step guide](docs/STEP-BY-STEP-MSI-B850-RTX3060.md)**, the exact sequence used on the tested machine, with warnings and rollback.
- Plan, audit, diagram and recovery: [MSI-B850-RTX3060-INTERNAL-BOOT-PLAN.md](MSI-B850-RTX3060-INTERNAL-BOOT-PLAN.md)
- Step-by-step tools (identity gate, ESP inspector, build, tests): [research/msi-b850-rtx3060/README.md](research/msi-b850-rtx3060/README.md)
- Safe GPU array test with temperature/power/memory watchdog: [research/msi-b850-rtx3060/gpu-array-test/](research/msi-b850-rtx3060/gpu-array-test/safe_gpu_array_test.py)
- Benchmark: `benchmark\build.ps1 -Card 3060` then `benchmark\run-benchmark.ps1 -Card 3060` (needs VS 2019 C++ Build Tools)
- Upstream documentation for the other supported setups: [UPSTREAM-README.md](UPSTREAM-README.md)

Nothing here installs boot files, changes BCD, partitions or firmware settings automatically.
Every disk/boot step is a command you run yourself after reading it.

**Not included:** NVIDIA firmware files, VBIOS/PROM dumps and NVIDIA packages. They are
not redistributable; you generate the five firmware assets yourself
(see [docs/ASSETS.en.md](docs/ASSETS.en.md)) and the tools verify their SHA256 hashes.

## Credits

This is a derivative of, and would not exist without:

- **[Zagitta/gpu-unlock](https://github.com/Zagitta/gpu-unlock)**: the original Linux GPU unlock
  (kernel module, `unlock_610` / `prep_610` / `layout_610` / `vbios_610` core). GPL-2.0-only.
- **[windows-GPU-UNLOCK/Windows-GPU-UNLOCK](https://github.com/windows-GPU-UNLOCK/Windows-GPU-UNLOCK)**:
  the Windows/UEFI port, internal-boot handoff, profiles, tests and benchmark this fork is built on. GPL-2.0-only.
- [rough2515-hub/rtx3080-unlock-public](https://github.com/rough2515-hub/rtx3080-unlock-public): the UEFI
  port the Windows project started from, and the upstream GEMM benchmark. GPL-2.0-only.
- [TianoCore EDK2](https://github.com/tianocore/edk2) headers (BSD-2-Clause-Patent) and NVIDIA
  open-gpu-kernel-modules headers (MIT); see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [LICENSES/](LICENSES).

Changes made in this fork are listed in [CHANGES.md](CHANGES.md).

## License

GPL-2.0-only, the same as the upstream projects ([LICENSE](LICENSE)). Third-party parts keep their own
licenses as listed above. No warranty.
