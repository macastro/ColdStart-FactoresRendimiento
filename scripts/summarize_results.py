import os
import numpy as np
import pandas as pd

RAW = "results/raw_coldstart.csv"
OUT = "results/summary_coldstart.csv"

df = pd.read_csv(RAW)
df_ok = df[df["status"] == "ok"].copy()

numeric_cols = [
    "image_load_ms",
    "ready_ms",
    "app_init_ms",
    "runtime_sandbox_approx_ms"
]

for c in numeric_cols:
    df_ok[c] = pd.to_numeric(df_ok[c], errors="coerce")

def ci95_mean(x):
    x = pd.Series(x).dropna().to_numpy()
    if len(x) < 2:
        return (np.nan, np.nan)
    mean = np.mean(x)
    se = np.std(x, ddof=1) / np.sqrt(len(x))
    return (mean - 1.96 * se, mean + 1.96 * se)

rows = []

for config, g in df_ok.groupby("config"):
    row = {
        "config": config,
        "n": len(g)
    }

    for metric in numeric_cols:
        s = g[metric].dropna()

        row[f"{metric}_mean"] = s.mean()
        row[f"{metric}_p50"] = s.quantile(0.50)
        row[f"{metric}_p95"] = s.quantile(0.95)
        row[f"{metric}_p99"] = s.quantile(0.99)

        lo, hi = ci95_mean(s)
        row[f"{metric}_ci95_low"] = lo
        row[f"{metric}_ci95_high"] = hi

    rows.append(row)

summary = pd.DataFrame(rows)
os.makedirs("results", exist_ok=True)
summary.to_csv(OUT, index=False)

print(summary.round(3).to_string(index=False))
print(f"\nArchivo generado: {OUT}")
