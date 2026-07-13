import glob
import os
import pandas as pd

KEEPALIVE_MINUTES = 20
TOP_FUNCTIONS = 20
DAY_FILE = "d01"

files = glob.glob(f"data/**/invocations_per_function_md.anon.{DAY_FILE}.csv", recursive=True)

if not files:
    raise FileNotFoundError("No se encontró invocations_per_function_md.anon.d01.csv dentro de data/")

path = files[0]
print(f"Usando archivo de traza: {path}")

df = pd.read_csv(path)

minute_cols = [c for c in df.columns if str(c).isdigit()]
if not minute_cols:
    raise ValueError("No se encontraron columnas 1..1440 de invocaciones por minuto.")

df["total_invocations"] = df[minute_cols].sum(axis=1)
df = df.sort_values("total_invocations", ascending=False).head(TOP_FUNCTIONS)

events = []
last_seen_by_app = {}

for _, row in df.iterrows():
    app = row["HashApp"]
    func = row["HashFunction"]
    trigger = row.get("Trigger", "unknown")

    for c in minute_cols:
        count = int(row[c])
        if count <= 0:
            continue

        minute = int(c)
        previous = last_seen_by_app.get(app)

        is_cold = previous is None or (minute - previous) > KEEPALIVE_MINUTES

        events.append({
            "day": DAY_FILE,
            "minute": minute,
            "hash_app": app,
            "hash_function": func,
            "trigger": trigger,
            "invocations_in_minute": count,
            "is_cold_by_20min_keepalive": is_cold
        })

        last_seen_by_app[app] = minute

events_df = pd.DataFrame(events)
os.makedirs("data", exist_ok=True)
events_df.to_csv("data/events_sample.csv", index=False)

print("Eventos generados:", len(events_df))
print("Cold events estimados:", int(events_df["is_cold_by_20min_keepalive"].sum()))
print("Archivo generado: data/events_sample.csv")
print(events_df.head(10))
