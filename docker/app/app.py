import os
import time
import json
import hashlib
import ctypes

t0 = time.perf_counter_ns()

mode = os.environ.get("MODE", "cpu").lower()
result = {
    "mode": mode,
    "ready": False,
    "gpu_available": False,
    "cuda_init_ok": False,
    "cuda_device_count": None,
    "error": None
}

try:
    # Trabajo común para simular init de aplicación.
    payload = b"cold-start-preliminary-test" * 10000
    digest = hashlib.sha256(payload).hexdigest()

    if mode == "gpu":
        # Inicialización directa del driver CUDA sin instalar PyTorch ni CUDA Toolkit.
        libcuda = ctypes.CDLL("libcuda.so.1")
        rc = libcuda.cuInit(0)

        count = ctypes.c_int()
        rc_count = libcuda.cuDeviceGetCount(ctypes.byref(count))

        result["gpu_available"] = True
        result["cuda_init_ok"] = (rc == 0 and rc_count == 0)
        result["cuda_device_count"] = int(count.value)
        result["cuda_rc"] = int(rc)
        result["cuda_count_rc"] = int(rc_count)

    result["digest_prefix"] = digest[:12]
    result["ready"] = True

except Exception as e:
    result["error"] = repr(e)

t1 = time.perf_counter_ns()
result["app_init_ms"] = (t1 - t0) / 1_000_000

print(json.dumps(result), flush=True)
