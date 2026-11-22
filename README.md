<p align="center">
  <a href="https://alvecomm.com">
    <img src="./assets/brand/logo.svg" alt="Alvecomm logo" width="120">
  </a>
</p>

<h1 align="center">Windows GPU Unlock: RTX 3060 12 GB, no USB</h1>

<p align="center">
  <strong>
    Unlock full-rate RTX 3060 tensor performance on Windows from a dedicated EFI partition on your internal SSD. No USB stick.
  </strong>
</p>

<p align="center">
  <a href="docs/STEP-BY-STEP-MSI-B850-RTX3060.md">Step-by-step guide</a>
  ·
  <a href="#results-on-the-tested-machine">Results</a>
  ·
  <a href="#faq">FAQ</a>
  ·
  <a href="#support-us">Support us</a>
  ·
  <a href="https://github.com/sponsors/Alvecomm">Sponsor</a>
  ·
  <a href="https://alvecomm.com">Website</a>
</p>

---

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

## Other cards and setups

Everything from upstream [Windows-GPU-UNLOCK](https://github.com/windows-GPU-UNLOCK/Windows-GPU-UNLOCK)
is still here and works exactly as upstream: all its reviewed profiles (e.g. RTX 3090, 3090 Ti,
3070 Ti, the USB RTX 3060 profiles) and its LOCAL profile pipeline for other machines, using the
upstream USB or internal routes. Follow [UPSTREAM-README.md](UPSTREAM-README.md) and the [docs](docs/)
for those; this fork did not change any of them.

What this fork adds is the **no-USB internal-SSD route for a new machine**, hardware-verified only on
the MSI B850 + RTX 3060 above. Other cards/boards can try the same route through their own read-only
probe captures and the same safety gates ([guide](docs/STEP-BY-STEP-MSI-B850-RTX3060.md)), but that is
untested: the gates refuse anything they cannot verify.

## Results on the tested machine

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

## FAQ

- **Q: What does this actually unlock?**  
  A: NVIDIA runs GeForce tensor cores at half rate for FP16, BF16 and TF32 math with FP32
  accumulation (what most AI training and unquantized inference uses). The unlock removes that
  cap at boot. On the tested RTX 3060 that is ≈1.84–1.93× for those modes; FP32 is unchanged.
- **Q: Why "no USB"?**  
  A: The upstream Windows project boots the unlock from a USB stick. This fork boots the same
  code from a separate 1 GiB EFI partition on your internal SSD, picked once with F11.
  Your Windows EFI partition and Windows Boot Manager are never modified.
- **Q: Does it work on my PC / other cards?**  
  A: The upstream cards and profiles work as upstream (see [Other cards and setups](#other-cards-and-setups)).
  The no-USB internal-SSD route is tested only on the machine above; other machines need their own
  read-only probe captures and profile, and every check refuses what it cannot verify.
- **Q: Does this flash my VBIOS or patch the NVIDIA driver?**  
  A: No. It changes the GPU's running state once per cold boot, before Windows loads. No VBIOS
  flash, no patched driver, nothing to redo after NVIDIA driver updates. A full shutdown resets it.
- **Q: Does it survive a restart?**  
  A: Plan on choosing the F11 entry after each full shutdown. A non-cold GPU is detected and
  skipped safely (Windows starts unchanged).
- **Q: Does this hurt my GPU?**  
  A: It is unlikely: it removes a market-segmentation limit, it does not raise voltage, clocks or
  the power limit, and the card's own thermal and power management stays fully active. The card
  can use more power in tensor-heavy work because it does more work. The included array test
  stays within stock limits and stops itself at set temperature/power thresholds.
- **Q: What if it goes wrong?**  
  A: If Windows doesn't come back: hold power 5 s, switch the PSU off for 10 s, power on,
  F11 → Windows Boot Manager. Full rollback steps are in the guide.
- **Q: Can I make it automatic?**  
  A: Not yet recommended. It has been tested in supervised runs only; keep it a manual F11 choice.
- **Q: Why does BitLocker / Secure Boot need to be off?**  
  A: The unlock EFI is unsigned, and BitLocker would ask for recovery after boot-path changes.
- **Q: How can I help?**  
  A: Report results (with logs, no personal data) in Issues, or [support us](#support-us).

## Support us

This project is free and open source, and stays that way.

If it saves you time or money on GPU compute, consider supporting its ongoing development via
GitHub Sponsors. Even **$1/month** helps keep it maintained, tested on more hardware, and available to everyone.

👉 https://github.com/sponsors/Alvecomm

Other ways to help: star the repository, share it, report your results, and open issues with
feedback. Please also consider supporting the upstream authors listed under [Credits](#credits),
whose work this builds on. See [SUPPORT.md](SUPPORT.md).

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

---

## About Alvecomm

Alvecomm builds tools and systems at the intersection of software engineering, automation,
and AI-assisted workflows. Learn more at https://alvecomm.com
