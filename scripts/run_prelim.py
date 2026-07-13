import csv
import json
import os
import subprocess
import time
from datetime import datetime

IMAGE = "coldstart-app:prelim"
IMAGE_TAR = "images/coldstart-app-prelim.tar"
RESULTS = "results/raw_coldstart.csv"

REPETITIONS = 5

CONFIGS = [
    {
        "name": "runc_cpu",
        "runtime_args": [],
        "gpu": False,
        "mode": "cpu"
    },
    {
        "name": "crun_cpu",
        "runtime_args": ["--runtime=crun"],
        "gpu": False,
        "mode": "cpu"
    },
    {
        "name": "runsc_cpu",
        "runtime_args": ["--runtime=runsc"],
        "gpu": False,
        "mode": "cpu"
    },
    {
        "name": "runc_gpu",
        "runtime_args": [],
        "gpu": True,
        "mode": "gpu"
    },
    {
        "name": "crun_gpu_optional",
        "runtime_args": ["--runtime=crun"],
        "gpu": True,
        "mode": "gpu"
    }
]

def run_cmd(cmd, check=False, capture=True, timeout=None):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        check=check,
        timeout=timeout
    )

def drop_caches():
    # Requiere ejecutar el script con sudo para tener efecto completo.
    subprocess.run(["sync"])
    subprocess.run(["bash", "-lc", "echo 3 > /proc/sys/vm/drop_caches"], check=False)

def remove_image():
    run_cmd(["docker", "image", "rm", "-f", IMAGE], check=False)

def load_image():
    t0 = time.perf_counter_ns()
    proc = run_cmd(["docker", "load", "-i", IMAGE_TAR], check=False, timeout=180)
    t1 = time.perf_counter_ns()
    return (t1 - t0) / 1_000_000, proc.returncode, proc.stdout, proc.stderr

def run_container(config):
    cmd = ["docker", "run", "--rm", "--pull=never"]

    cmd += config["runtime_args"]

    if config["gpu"]:
        cmd += ["--gpus", "all"]

    cmd += ["-e", f"MODE={config['mode']}"]
    cmd += [IMAGE]

    t0 = time.perf_counter_ns()
    proc = run_cmd(cmd, check=False, timeout=120)
    t1 = time.perf_counter_ns()

    ready_ms = (t1 - t0) / 1_000_000

    parsed = {}
    if proc.stdout:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    pass

    return ready_ms, proc.returncode, proc.stdout, proc.stderr, parsed, cmd

def main():
    os.makedirs("results", exist_ok=True)

    fields = [
        "timestamp",
        "config",
        "repetition",
        "status",
        "image_load_ms",
        "ready_ms",
        "app_init_ms",
        "runtime_sandbox_approx_ms",
        "mode",
        "gpu",
        "cuda_init_ok",
        "cuda_device_count",
        "returncode",
        "command",
        "stdout",
        "stderr"
    ]

    write_header = not os.path.exists(RESULTS)

    with open(RESULTS, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if write_header:
            writer.writeheader()

        for config in CONFIGS:
            for rep in range(1, REPETITIONS + 1):
                print(f"\n=== {config['name']} rep {rep}/{REPETITIONS} ===")

                drop_caches()
                remove_image()

                image_load_ms, load_rc, load_out, load_err = load_image()

                if load_rc != 0:
                    writer.writerow({
                        "timestamp": datetime.utcnow().isoformat(),
                        "config": config["name"],
                        "repetition": rep,
                        "status": "image_load_failed",
                        "image_load_ms": image_load_ms,
                        "ready_ms": "",
                        "app_init_ms": "",
                        "runtime_sandbox_approx_ms": "",
                        "mode": config["mode"],
                        "gpu": config["gpu"],
                        "cuda_init_ok": "",
                        "cuda_device_count": "",
                        "returncode": load_rc,
                        "command": "docker load",
                        "stdout": load_out[-500:],
                        "stderr": load_err[-500:]
                    })
                    continue

                drop_caches()

                ready_ms, rc, out, err, parsed, cmd = run_container(config)

                app_init_ms = parsed.get("app_init_ms", "")
                runtime_sandbox = ""

                if isinstance(app_init_ms, (int, float)):
                    runtime_sandbox = max(0, ready_ms - float(app_init_ms))

                status = "ok" if rc == 0 and parsed.get("ready") is True else "failed"

                writer.writerow({
                    "timestamp": datetime.utcnow().isoformat(),
                    "config": config["name"],
                    "repetition": rep,
                    "status": status,
                    "image_load_ms": image_load_ms,
                    "ready_ms": ready_ms,
                    "app_init_ms": app_init_ms,
                    "runtime_sandbox_approx_ms": runtime_sandbox,
                    "mode": config["mode"],
                    "gpu": config["gpu"],
                    "cuda_init_ok": parsed.get("cuda_init_ok", ""),
                    "cuda_device_count": parsed.get("cuda_device_count", ""),
                    "returncode": rc,
                    "command": " ".join(cmd),
                    "stdout": out[-1000:] if out else "",
                    "stderr": err[-1000:] if err else ""
                })

                print("status:", status)
                print("image_load_ms:", round(image_load_ms, 2))
                print("ready_ms:", round(ready_ms, 2))
                print("app_init_ms:", app_init_ms)
                print("runtime_sandbox_approx_ms:", runtime_sandbox)

if __name__ == "__main__":
    main()
