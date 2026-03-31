"""Show all eval results across all 30 repos."""
import json, os, statistics

results_dir = "results"
projects = []

# Camel main
if os.path.isfile("results/results.json"):
    with open("results/results.json") as f:
        data = json.load(f)
    for key, items in data.items():
        n = len(items)
        compiled = sum(1 for i in items if i.get("compile_ok"))
        srrs = [i["srr"] for i in items if i.get("srr") is not None]
        med_srr = statistics.median(srrs) if srrs else None
        projects.append(("camel", key, n, compiled, med_srr))

# Sub-projects
for entry in sorted(os.listdir(results_dir)):
    path = os.path.join(results_dir, entry, "results.json")
    if os.path.isfile(path):
        with open(path) as f:
            data = json.load(f)
        for key, items in data.items():
            n = len(items)
            compiled = sum(1 for i in items if i.get("compile_ok"))
            srrs = [i["srr"] for i in items if i.get("srr") is not None]
            med_srr = statistics.median(srrs) if srrs else None
            projects.append((entry, key, n, compiled, med_srr))

print("Project                        Mode         N  Compiled  Rate   MedSRR")
print("-" * 72)
total_n = 0
total_compiled = 0
for proj, mode, n, comp, med in sorted(projects):
    rate = str(int(comp/n*100)) + "%" if n > 0 else "N/A"
    srr_str = str(round(med, 1)) + "%" if med is not None else "N/A"
    print(f"{proj:<30s} {mode:<12s} {n:>3d} {comp:>9d} {rate:>6s} {srr_str:>8s}")
    total_n += n
    total_compiled += comp

print("-" * 72)
if total_n > 0:
    pct = round(total_compiled/total_n*100, 1)
    print(f"TOTAL: {total_n} commits, {total_compiled} compiled ({pct}%)")
