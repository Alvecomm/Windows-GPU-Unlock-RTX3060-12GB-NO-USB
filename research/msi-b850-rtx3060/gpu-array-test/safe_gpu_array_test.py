"""Safe NumPy-style array test on the RTX 3060 (CuPy), with a watchdog.

Read-only towards the GPU: changes no clocks, voltages, power limits or fans.
Stops the calculations (cleanly, between small steps) when any limit is hit.
"""
import argparse, subprocess, sys, threading, time

import numpy as np

try:
    import cupy as cp
    import pynvml as nv
except ImportError as e:
    sys.exit(f"Missing package ({e.name}). Run: py -m pip install cupy-cuda12x nvidia-ml-py")

p = argparse.ArgumentParser()
p.add_argument("--seconds", type=float, default=60, help="max run time per mode")
p.add_argument("--max-volts", type=float, default=1.20, help="abort at or above this core voltage (if readable)")
p.add_argument("--max-temp", type=int, default=80, help="abort at or above this GPU temperature (C)")
p.add_argument("--max-watts", type=float, default=160, help="abort when 2-second average board power reaches this (W)")
p.add_argument("--hard-watts", type=float, default=175, help="abort at once if a single reading reaches this (W)")
p.add_argument("--max-vram-gib", type=float, default=4.0, help="max VRAM this test may use")
p.add_argument("--duty", type=float, default=0.5, help="fraction of time the GPU computes (rest is idle) to keep power down")
p.add_argument("--min-free-gib", type=float, default=2.0, help="always leave this much VRAM free")
a = p.parse_args()

nv.nvmlInit()
h = None
for i in range(nv.nvmlDeviceGetCount()):
    d = nv.nvmlDeviceGetHandleByIndex(i)
    name = nv.nvmlDeviceGetName(d)
    name = name.decode() if isinstance(name, bytes) else name
    if name == "NVIDIA GeForce RTX 3060":
        h = d
if h is None:
    sys.exit("RTX 3060 not found; stopping.")

def read_volts():
    """GeForce drivers usually do not expose core voltage. Return None if unreadable."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=voltage.graphics", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5)
        v = float(out.stdout.strip().splitlines()[0])
        return v / 1000 if v > 10 else v  # mV -> V
    except Exception:
        return None

BAD_THROTTLE = (getattr(nv, "nvmlClocksThrottleReasonHwSlowdown", 0x8)
                | getattr(nv, "nvmlClocksThrottleReasonHwThermalSlowdown", 0x40)
                | getattr(nv, "nvmlClocksThrottleReasonHwPowerBrakeSlowdown", 0x80))

stop = threading.Event()
reason = ["finished normally"]
peak = {"temp": 0, "watts": 0.0, "vram_gib": 0.0, "volts": None}
volts_readable = read_volts() is not None

def trip(msg):
    if not stop.is_set():
        reason[0] = msg
        stop.set()

def watchdog():
    last_v = 0.0
    recent = []  # (time, watts) over the last 2 s
    while not stop.is_set():
        try:
            t = nv.nvmlDeviceGetTemperature(h, nv.NVML_TEMPERATURE_GPU)
            w = nv.nvmlDeviceGetPowerUsage(h) / 1000
            m = nv.nvmlDeviceGetMemoryInfo(h)
            thr = nv.nvmlDeviceGetCurrentClocksThrottleReasons(h)
        except Exception as e:
            trip(f"sensor read failed ({e})"); return
        peak["temp"] = max(peak["temp"], t); peak["watts"] = max(peak["watts"], w)
        if t >= a.max_temp: trip(f"temperature {t} C >= {a.max_temp} C")
        now = time.time()
        recent.append((now, w)); recent[:] = [(t_, w_) for t_, w_ in recent if now - t_ <= 2.0]
        avg = sum(w_ for _, w_ in recent) / len(recent)
        if w >= a.hard_watts: trip(f"power spike {w:.0f} W >= {a.hard_watts:.0f} W")
        if len(recent) >= 8 and avg >= a.max_watts: trip(f"2 s average power {avg:.0f} W >= {a.max_watts:.0f} W")
        if m.free / 2**30 < a.min_free_gib: trip(f"free VRAM {m.free/2**30:.1f} GiB < {a.min_free_gib} GiB")
        if thr & BAD_THROTTLE: trip(f"hardware slowdown flag set (0x{thr:x})")
        if volts_readable and time.time() - last_v > 1:
            last_v = time.time()
            v = read_volts()
            if v is not None:
                peak["volts"] = max(peak["volts"] or 0, v)
                if v >= a.max_volts: trip(f"voltage {v:.3f} V >= {a.max_volts} V")
        time.sleep(0.2)

# Cap memory: never more than --max-vram-gib, and never eat into --min-free-gib.
free = nv.nvmlDeviceGetMemoryInfo(h).free / 2**30
budget = min(a.max_vram_gib, free - a.min_free_gib)
if budget < 0.5:
    sys.exit(f"Not enough free VRAM ({free:.1f} GiB). Close GPU apps and retry.")
cp.get_default_memory_pool().set_limit(size=int(budget * 2**30))
print(f"VRAM budget {budget:.1f} GiB (free {free:.1f} GiB); voltage readable: {volts_readable}")
if not volts_readable:
    print("Note: this driver does not report core voltage. Using temperature/power/slowdown limits instead.")

threading.Thread(target=watchdog, daemon=True).start()

def run(dtype, label):
    # Square size from budget: 3 matrices, keep 40% spare.
    n = int(((budget * 0.6 * 2**30) / (3 * np.dtype(dtype).itemsize)) ** 0.5) // 256 * 256
    n = max(256, min(n, 4096))  # small bursts (~20 ms) keep average power low
    rng = np.random.default_rng(0)
    a_h = rng.standard_normal((n, n)).astype(dtype); b_h = rng.standard_normal((n, n)).astype(dtype)
    A, B = cp.asarray(a_h), cp.asarray(b_h)
    # Correctness check on a small block against NumPy (CPU).
    k = 256
    ref = a_h[:k].astype(np.float64) @ b_h[:, :k].astype(np.float64)
    # Contiguous host-made blocks: uses cuBLAS only, no runtime-compiled kernels.
    got = cp.asnumpy(cp.asarray(np.ascontiguousarray(a_h[:k])) @ cp.asarray(np.ascontiguousarray(b_h[:, :k]))).astype(np.float64)
    err = float(np.max(np.abs(got - ref)) / np.max(np.abs(ref)))
    duty = min(max(a.duty, 0.05), 1.0)
    flops, t0, steps, busy = 0, time.time(), 0, 0.0
    reps = 1
    ev0, ev1 = cp.cuda.Event(), cp.cuda.Event()
    while not stop.is_set() and time.time() - t0 < a.seconds:
        ev0.record()
        for _ in range(reps):
            C = A @ B
            del C
        ev1.record(); ev1.synchronize()
        step = cp.cuda.get_elapsed_time(ev0, ev1) / 1000  # GPU time only, no Python overhead
        busy += step; flops += 2 * n**3 * reps; steps += reps
        if step < 0.02 and reps < 64: reps *= 2  # bursts of ~20-40 ms
        if duty < 1.0:
            stop.wait(step * (1 - duty) / duty)  # idle gap; returns early if a limit trips
    dt = time.time() - t0
    print(f"{label:5s} n={n:5d} steps={steps:4d} {flops/max(busy,1e-9)/1e12:6.1f} TFLOPS while computing  "
          f"duty={duty:.0%}  rel.err={err:.1e}  ({dt:.1f}s)")
    del A, B
    cp.get_default_memory_pool().free_all_blocks()

try:
    for dt_, lbl in ((np.float32, "FP32"), (np.float16, "FP16")):
        if stop.is_set(): break
        run(dt_, lbl)
except cp.cuda.memory.OutOfMemoryError:
    trip("memory limit reached (stopped safely)")
except KeyboardInterrupt:
    trip("stopped by you (Ctrl+C)")
except Exception as e:
    trip(f"error, stopped safely: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
finally:
    stop.set()
    cp.get_default_memory_pool().free_all_blocks()
    v = f"{peak['volts']:.3f} V" if peak["volts"] is not None else "not readable"
    print(f"STOP: {reason[0]}")
    print(f"Peaks: {peak['temp']} C, {peak['watts']:.0f} W, voltage {v}")
    nv.nvmlShutdown()
