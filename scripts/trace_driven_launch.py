#!/usr/bin/env python3
"""
trace_driven_launch.py

Analiza la traza Azure Functions 2019 y reproduce lanzamientos de contenedores
siguiendo el patrón de llegadas observado en la traza.

Modos:
  1) prepare : genera un CSV de eventos a partir de invocations_per_function_md.anon.dXX.csv
  2) run     : reproduce el calendario de eventos y ejecuta docker run por configuración
  3) summary : calcula métricas agregadas por configuración
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

CONFIGS: Dict[str, Dict[str, object]] = {
    "runc_cpu": {"runtime_args": [], "gpu": False, "mode": "cpu"},
    "crun_cpu": {"runtime_args": ["--runtime=crun"], "gpu": False, "mode": "cpu"},
    "runsc_cpu": {"runtime_args": ["--runtime=runsc"], "gpu": False, "mode": "cpu"},
    "runc_gpu": {"runtime_args": [], "gpu": True, "mode": "gpu"},
    "crun_gpu": {"runtime_args": ["--runtime=crun"], "gpu": True, "mode": "gpu"},
    # Puede fallar si gVisor/nvproxy no está completamente habilitado.
    "runsc_gpu": {"runtime_args": ["--runtime=runsc"], "gpu": True, "mode": "gpu"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run_cmd(cmd: List[str], timeout: Optional[int] = None, capture: bool = True, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
        timeout=timeout,
        check=check,
    )


def trace_day_number(path: str) -> int:
    name = Path(path).name
    for part in name.split("."):
        if part.startswith("d") and len(part) == 3 and part[1:].isdigit():
            return int(part[1:])
    return 1


def find_invocation_files(trace_dir: str, days: str) -> List[str]:
    requested = [d.strip() for d in days.split(",") if d.strip()]
    files: List[str] = []
    for d in requested:
        pattern = str(Path(trace_dir) / "**" / f"invocations_per_function_md.anon.{d}.csv")
        files.extend(glob.glob(pattern, recursive=True))
    files = sorted(set(files), key=lambda p: (trace_day_number(p), p))
    if not files:
        die(f"No encontré invocations_per_function_md.anon.<día>.csv en {trace_dir}. Revisa --trace-dir y --days.")
    return files


def minute_columns(df: pd.DataFrame) -> List[str]:
    cols = [str(c) for c in df.columns if str(c).isdigit()]
    cols = sorted(cols, key=lambda x: int(x))
    if not cols:
        die("No encontré columnas numéricas 1..1440 en el CSV de la traza.")
    return cols


def choose_rows(df: pd.DataFrame, minute_cols: List[str], top_functions: int, selection: str, keepalive_min: int) -> pd.DataFrame:
    df = df.copy()
    df["total_invocations"] = df[minute_cols].sum(axis=1)
    df["active_minutes"] = (df[minute_cols] > 0).sum(axis=1)

    if selection == "top_invocations":
        return df.sort_values("total_invocations", ascending=False).head(top_functions)

    if selection != "top_cold":
        die("--selection debe ser top_invocations o top_cold")

    cold_counts = []
    for _, row in df.iterrows():
        last_seen: Optional[int] = None
        cold_count = 0
        for c in minute_cols:
            minute = int(c)
            count = int(row[c])
            if count <= 0:
                continue
            if last_seen is None or (minute - last_seen) > keepalive_min:
                cold_count += 1
            last_seen = minute
        cold_counts.append(cold_count)

    df["estimated_cold_events"] = cold_counts
    return df.sort_values(["estimated_cold_events", "total_invocations"], ascending=False).head(top_functions)


def prepare_schedule(args: argparse.Namespace) -> None:
    files = find_invocation_files(args.trace_dir, args.days)
    all_events: List[Dict[str, object]] = []
    global_event_id = 0
    last_seen_by_key: Dict[str, int] = {}

    for fpath in files:
        day_no = trace_day_number(fpath)
        print(f"[prepare] leyendo {fpath}")
        df = pd.read_csv(fpath)
        mcols = minute_columns(df)
        selected = choose_rows(df, mcols, args.top_functions, args.selection, args.keepalive_min)

        for _, row in selected.iterrows():
            app = str(row.get("HashApp", "NA"))
            fn = str(row.get("HashFunction", "NA"))
            trigger = str(row.get("Trigger", "NA"))
            cold_key_value = app if args.cold_key == "app" else f"{app}/{fn}"

            for c in mcols:
                minute_in_day = int(c)
                invocations = int(row[c])
                if invocations <= 0:
                    continue

                absolute_minute = (day_no - 1) * 1440 + minute_in_day
                previous = last_seen_by_key.get(cold_key_value)
                is_cold = previous is None or (absolute_minute - previous) > args.keepalive_min
                last_seen_by_key[cold_key_value] = absolute_minute

                global_event_id += 1
                trace_second = (absolute_minute - 1) * 60
                all_events.append({
                    "event_id": global_event_id,
                    "trace_file": Path(fpath).name,
                    "trace_day": f"d{day_no:02d}",
                    "minute_in_day": minute_in_day,
                    "absolute_minute": absolute_minute,
                    "trace_second": trace_second,
                    "hash_app": app,
                    "hash_function": fn,
                    "function_id": f"{app}/{fn}",
                    "cold_key": args.cold_key,
                    "cold_key_value": cold_key_value,
                    "trigger": trigger,
                    "invocations_in_minute": invocations,
                    "cold_by_keepalive": int(is_cold),
                    "top_selection": args.selection,
                })

    events = pd.DataFrame(all_events)
    if events.empty:
        die("La traza no produjo eventos. Revisa la selección de días y funciones.")

    events = events.sort_values(["absolute_minute", "function_id"]).reset_index(drop=True)
    events["event_id"] = range(1, len(events) + 1)

    if args.only_cold:
        events = events[events["cold_by_keepalive"] == 1].copy()

    if args.max_events and len(events) > args.max_events:
        events = events.head(args.max_events).copy()

    if args.max_cold_events:
        cold = events[events["cold_by_keepalive"] == 1].copy().head(args.max_cold_events)
        warm = events[events["cold_by_keepalive"] == 0].copy()
        events = pd.concat([cold, warm], ignore_index=True)
        events = events.sort_values(["absolute_minute", "function_id"]).reset_index(drop=True)
        events["event_id"] = range(1, len(events) + 1)

    first_second = int(events["trace_second"].min())
    events["relative_trace_second"] = events["trace_second"] - first_second
    events["scaled_second_default"] = events["relative_trace_second"] * args.time_scale

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(args.out, index=False)

    total = len(events)
    cold = int(events["cold_by_keepalive"].sum())
    print(f"[prepare] schedule generado: {args.out}")
    print(f"[prepare] eventos totales: {total}")
    print(f"[prepare] eventos cold según keep-alive={args.keepalive_min} min: {cold}")
    print(events.head(5).to_string(index=False))


def drop_caches(enabled: bool) -> None:
    if not enabled:
        return
    subprocess.run(["sync"], check=False)
    subprocess.run(["bash", "-lc", "echo 3 > /proc/sys/vm/drop_caches"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def docker_remove_image(image: str) -> None:
    run_cmd(["docker", "image", "rm", "-f", image], check=False, timeout=120)


def docker_load_image(image_tar: str) -> Tuple[float, int, str, str]:
    t0 = time.perf_counter_ns()
    proc = run_cmd(["docker", "load", "-i", image_tar], check=False, timeout=300)
    t1 = time.perf_counter_ns()
    return (t1 - t0) / 1_000_000, proc.returncode, proc.stdout or "", proc.stderr or ""


def docker_run_container(config_name: str, image: str, timeout: int) -> Tuple[float, int, str, str, Dict[str, object], List[str]]:
    if config_name not in CONFIGS:
        die(f"Configuración desconocida: {config_name}. Opciones: {','.join(CONFIGS)}")

    cfg = CONFIGS[config_name]
    cmd: List[str] = ["docker", "run", "--rm", "--pull=never"]
    cmd += list(cfg["runtime_args"])  # type: ignore[arg-type]
    if bool(cfg["gpu"]):
        cmd += ["--gpus", "all"]
    cmd += ["-e", f"MODE={cfg['mode']}", image]

    t0 = time.perf_counter_ns()
    proc = run_cmd(cmd, check=False, timeout=timeout)
    t1 = time.perf_counter_ns()

    ready_ms = (t1 - t0) / 1_000_000
    out = proc.stdout or ""
    err = proc.stderr or ""

    parsed: Dict[str, object] = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                pass

    return ready_ms, proc.returncode, out, err, parsed, cmd


def sleep_until(target_s: float, t0: float, no_sleep: bool) -> None:
    if no_sleep:
        return
    while True:
        now = time.perf_counter() - t0
        remaining = target_s - now
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))


def run_schedule(args: argparse.Namespace) -> None:
    schedule = pd.read_csv(args.schedule)
    if schedule.empty:
        die("El schedule está vacío.")

    if args.only_cold:
        schedule = schedule[schedule["cold_by_keepalive"] == 1].copy()
    if args.limit_events:
        schedule = schedule.head(args.limit_events).copy()
    if schedule.empty:
        die("No hay eventos para ejecutar luego del filtrado.")

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    for c in configs:
        if c not in CONFIGS:
            die(f"Config inválida: {c}. Opciones: {','.join(CONFIGS.keys())}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "timestamp_utc", "run_id", "config", "event_id", "trace_day", "minute_in_day", "absolute_minute",
        "relative_trace_second", "scheduled_scaled_second", "hash_app", "hash_function", "function_id", "trigger",
        "invocations_in_minute", "cold_by_keepalive", "storage_mode", "image_load_ms", "ready_ms", "app_init_ms",
        "runtime_sandbox_approx_ms", "mode", "gpu", "cuda_init_ok", "cuda_device_count", "status", "returncode",
        "docker_cmd", "stdout_tail", "stderr_tail",
    ]

    file_exists = Path(args.out).exists()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    with open(args.out, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()

        for config in configs:
            print(f"\n[run] reproduciendo schedule para config={config}")
            start_wall = time.perf_counter()

            for _, row in schedule.iterrows():
                relative_trace_second = float(row.get("relative_trace_second", 0))
                target_scaled_second = relative_trace_second * float(args.time_scale)
                sleep_until(target_scaled_second, start_wall, args.no_sleep)

                image_load_ms = ""
                if args.storage_mode == "cold-image":
                    if not args.image_tar:
                        die("--image-tar es obligatorio cuando --storage-mode cold-image")
                    docker_remove_image(args.image)
                    drop_caches(args.drop_caches)
                    load_ms, load_rc, load_out, load_err = docker_load_image(args.image_tar)
                    image_load_ms = load_ms
                    if load_rc != 0:
                        writer.writerow({
                            "timestamp_utc": utc_now(), "run_id": run_id, "config": config, "event_id": int(row["event_id"]),
                            "trace_day": row.get("trace_day", ""), "minute_in_day": row.get("minute_in_day", ""),
                            "absolute_minute": row.get("absolute_minute", ""), "relative_trace_second": relative_trace_second,
                            "scheduled_scaled_second": target_scaled_second, "hash_app": row.get("hash_app", ""),
                            "hash_function": row.get("hash_function", ""), "function_id": row.get("function_id", ""),
                            "trigger": row.get("trigger", ""), "invocations_in_minute": row.get("invocations_in_minute", ""),
                            "cold_by_keepalive": row.get("cold_by_keepalive", ""), "storage_mode": args.storage_mode,
                            "image_load_ms": image_load_ms, "ready_ms": "", "app_init_ms": "", "runtime_sandbox_approx_ms": "",
                            "mode": CONFIGS[config]["mode"], "gpu": CONFIGS[config]["gpu"], "cuda_init_ok": "", "cuda_device_count": "",
                            "status": "image_load_failed", "returncode": load_rc, "docker_cmd": "docker load -i " + args.image_tar,
                            "stdout_tail": load_out[-1000:], "stderr_tail": load_err[-1000:],
                        })
                        continue

                drop_caches(args.drop_caches)

                try:
                    ready_ms, rc, out, err, parsed, cmd = docker_run_container(config, args.image, args.timeout)
                except subprocess.TimeoutExpired as e:
                    writer.writerow({
                        "timestamp_utc": utc_now(), "run_id": run_id, "config": config, "event_id": int(row["event_id"]),
                        "trace_day": row.get("trace_day", ""), "minute_in_day": row.get("minute_in_day", ""),
                        "absolute_minute": row.get("absolute_minute", ""), "relative_trace_second": relative_trace_second,
                        "scheduled_scaled_second": target_scaled_second, "hash_app": row.get("hash_app", ""),
                        "hash_function": row.get("hash_function", ""), "function_id": row.get("function_id", ""),
                        "trigger": row.get("trigger", ""), "invocations_in_minute": row.get("invocations_in_minute", ""),
                        "cold_by_keepalive": row.get("cold_by_keepalive", ""), "storage_mode": args.storage_mode,
                        "image_load_ms": image_load_ms, "ready_ms": "", "app_init_ms": "", "runtime_sandbox_approx_ms": "",
                        "mode": CONFIGS[config]["mode"], "gpu": CONFIGS[config]["gpu"], "cuda_init_ok": "", "cuda_device_count": "",
                        "status": "timeout", "returncode": "", "docker_cmd": "", "stdout_tail": str(e)[:1000], "stderr_tail": "",
                    })
                    continue

                app_init_ms = parsed.get("app_init_ms", "")
                runtime_sandbox = ""
                if isinstance(app_init_ms, (int, float)):
                    runtime_sandbox = max(0.0, ready_ms - float(app_init_ms))
                status = "ok" if rc == 0 and parsed.get("ready") is True else "failed"

                writer.writerow({
                    "timestamp_utc": utc_now(), "run_id": run_id, "config": config, "event_id": int(row["event_id"]),
                    "trace_day": row.get("trace_day", ""), "minute_in_day": row.get("minute_in_day", ""),
                    "absolute_minute": row.get("absolute_minute", ""), "relative_trace_second": relative_trace_second,
                    "scheduled_scaled_second": target_scaled_second, "hash_app": row.get("hash_app", ""),
                    "hash_function": row.get("hash_function", ""), "function_id": row.get("function_id", ""),
                    "trigger": row.get("trigger", ""), "invocations_in_minute": row.get("invocations_in_minute", ""),
                    "cold_by_keepalive": row.get("cold_by_keepalive", ""), "storage_mode": args.storage_mode,
                    "image_load_ms": image_load_ms, "ready_ms": ready_ms, "app_init_ms": app_init_ms,
                    "runtime_sandbox_approx_ms": runtime_sandbox, "mode": CONFIGS[config]["mode"], "gpu": CONFIGS[config]["gpu"],
                    "cuda_init_ok": parsed.get("cuda_init_ok", ""), "cuda_device_count": parsed.get("cuda_device_count", ""),
                    "status": status, "returncode": rc, "docker_cmd": " ".join(cmd), "stdout_tail": out[-1000:], "stderr_tail": err[-1000:],
                })

                print(f"[run] {config} event={int(row['event_id'])} cold={row.get('cold_by_keepalive')} status={status} ready_ms={ready_ms:.2f}")

    print(f"\n[run] resultados guardados en: {args.out}")


def percentile(vals: List[float], p: float) -> float:
    if not vals:
        return float("nan")
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c - k) + vals[c] * (k - f)


def ci95(vals: List[float]) -> Tuple[float, float]:
    vals = [v for v in vals if not math.isnan(v)]
    if len(vals) < 2:
        return float("nan"), float("nan")
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals)
    half = 1.96 * sd / math.sqrt(len(vals))
    return mean - half, mean + half


def summarize(args: argparse.Namespace) -> None:
    df = pd.read_csv(args.raw)
    df = df[df["status"] == "ok"].copy()
    if df.empty:
        die("No hay filas status=ok para resumir.")

    metrics = ["image_load_ms", "ready_ms", "app_init_ms", "runtime_sandbox_approx_ms"]
    for m in metrics:
        df[m] = pd.to_numeric(df[m], errors="coerce")

    rows: List[Dict[str, object]] = []
    for config, g in df.groupby("config"):
        row: Dict[str, object] = {
            "config": config,
            "n": len(g),
            "storage_mode": ",".join(sorted(set(str(x) for x in g["storage_mode"].dropna()))),
            "gpu": ",".join(sorted(set(str(x) for x in g["gpu"].dropna()))),
        }
        for metric in metrics:
            vals = [float(x) for x in g[metric].dropna().tolist()]
            if vals:
                low, high = ci95(vals)
                row[f"{metric}_mean"] = statistics.mean(vals)
                row[f"{metric}_p50"] = percentile(vals, 0.50)
                row[f"{metric}_p95"] = percentile(vals, 0.95)
                row[f"{metric}_p99"] = percentile(vals, 0.99)
                row[f"{metric}_ci95_low"] = low
                row[f"{metric}_ci95_high"] = high
            else:
                row[f"{metric}_mean"] = ""
                row[f"{metric}_p50"] = ""
                row[f"{metric}_p95"] = ""
                row[f"{metric}_p99"] = ""
                row[f"{metric}_ci95_low"] = ""
                row[f"{metric}_ci95_high"] = ""
        rows.append(row)

    out = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(out.round(3).to_string(index=False))
    print(f"\n[summary] resumen guardado en: {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parsea Azure Functions 2019 y reproduce lanzamientos Docker por patrón de llegadas.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="Generar CSV de eventos desde la traza Azure Functions.")
    p.add_argument("--trace-dir", default="data")
    p.add_argument("--days", default="d01")
    p.add_argument("--top-functions", type=int, default=20)
    p.add_argument("--selection", choices=["top_invocations", "top_cold"], default="top_cold")
    p.add_argument("--cold-key", choices=["app", "function"], default="app")
    p.add_argument("--keepalive-min", type=int, default=20)
    p.add_argument("--only-cold", action="store_true")
    p.add_argument("--max-events", type=int, default=0)
    p.add_argument("--max-cold-events", type=int, default=60)
    p.add_argument("--time-scale", type=float, default=0.02)
    p.add_argument("--out", default="data/arrival_schedule_d01.csv")
    p.set_defaults(func=prepare_schedule)

    p = sub.add_parser("run", help="Reproducir eventos del schedule y ejecutar contenedores.")
    p.add_argument("--schedule", default="data/arrival_schedule_d01.csv")
    p.add_argument("--configs", default="runc_cpu,crun_cpu,runsc_cpu,runc_gpu")
    p.add_argument("--image", default="coldstart-app:prelim")
    p.add_argument("--image-tar", default="images/coldstart-app-prelim.tar")
    p.add_argument("--storage-mode", choices=["cached", "cold-image"], default="cached")
    p.add_argument("--time-scale", type=float, default=0.02, help="0.02: 1 minuto de traza = 1.2 segundos reales.")
    p.add_argument("--only-cold", action="store_true", default=True)
    p.add_argument("--include-warm", dest="only_cold", action="store_false")
    p.add_argument("--limit-events", type=int, default=0)
    p.add_argument("--drop-caches", action="store_true")
    p.add_argument("--no-sleep", action="store_true")
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--out", default="results/raw_trace_driven.csv")
    p.set_defaults(func=run_schedule)

    p = sub.add_parser("summary", help="Resumir raw_trace_driven.csv por configuración.")
    p.add_argument("--raw", default="results/raw_trace_driven.csv")
    p.add_argument("--out", default="results/summary_trace_driven.csv")
    p.set_defaults(func=summarize)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
