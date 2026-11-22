# MSI B850 / RTX 3060 GA106 — first supervised internal unlock (2026-11-22)

Owner-run, single F11-selected internal-ESP boot. Child log: `FINAL status=0 stage=transaction attempted=1 clean=1`;
selectors SS0 0x04527103→0x88888888, SS1 0x7→0x8; wrapper verified and started Windows Boot Manager.
Windows: RTX 3060 Status OK / CM_PROB_NONE, nvidia-smi normal (driver 621.24).

Benchmark (`benchmark/compute-benchmark-3060.cu`, CUDA 12.6, VS2019 MSVC, same binary; comparison-context identical):

| Mode | Normal boot (TFLOPS) | Unlocked (TFLOPS) | Ratio |
|---|---|---|---|
| FP32 control | 8.08 | 8.09 | 1.00 |
| FP16 Tensor, FP32 acc | 25.99 | 47.90 | 1.84 |
| BF16 Tensor, FP32 acc | 25.90 | 49.11 | 1.90 |
| TF32 Tensor | 13.12 | 25.30 | 1.93 |

Unlocked run first (same power session as the unlock), normal run after a full shutdown and normal boot.
One machine, one session; repeated boots, stability and long-run behaviour are not yet tested.

## CuPy array test with watchdog (`research/msi-b850-rtx3060/gpu-array-test/`)

Read-only watchdog (temp ≥80 °C, 2 s avg power ≥160 W, spike ≥175 W, HW slowdown, VRAM ≤4 GiB with ≥2 GiB free;
1.2 V check only if the driver reports voltage — this GeForce driver does not). n=4096, 50% duty, 60 s per mode,
256×256 block checked against NumPy float64.

| Boot | FP32 TFLOPS | FP16 (FP32 acc) TFLOPS | Peak temp / power |
|---|---|---|---|
| Normal (v3 timing, includes host overhead) | 7.8 | 22.1 | 64 °C / 102 W |
| Unlocked (v4, CUDA-event timing) | 7.9 | 47.9 | 67 °C / 119 W |

Both runs finished normally; rel. error FP32 3.3e-7, FP16 4.0e-4.
