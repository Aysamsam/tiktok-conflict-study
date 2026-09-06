"""Combine all 10 experiment runs into one deduplicated dataset with provenance."""
import csv
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="backslashreplace")

RUNS = [f"run_{i:02d}_posts.csv" for i in range(1, 11)]
OUT = "combined_all_runs.csv"

seen = {}
fieldnames = None
order = []

for run_file in RUNS:
    run_id = run_file.replace("_posts.csv", "")
    with open(run_file, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if fieldnames is None:
            fieldnames = reader.fieldnames + ["source_run"]
        for row in reader:
            pid = row["post_id"]
            row["source_run"] = run_id
            if pid not in seen:
                seen[pid] = row
                order.append(pid)

with open(OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for pid in order:
        writer.writerow(seen[pid])

total_rows = 0
for run_file in RUNS:
    with open(run_file, encoding="utf-8") as f:
        total_rows += sum(1 for _ in f) - 1

print(f"Combined {len(RUNS)} runs -> {len(order)} unique posts (from {total_rows} total rows across runs, {total_rows - len(order)} cross-run duplicates removed)")

ai_count = sum(1 for pid in order if seen[pid]["has_ai_label"] == "1")
print(f"AI-labeled: {ai_count} ({ai_count/len(order)*100:.1f}%)")
