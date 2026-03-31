"""Save partial results from terminal output for the stuck LoRA run."""
import json, os

# Ollama results (all 35) — from terminal output
ollama = [
    {"sha": "07ca6af", "compile_ok": False, "srr": 12.9, "attempts": 3},
    {"sha": "14144d0", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "14963cc", "compile_ok": False, "srr": 34.1, "attempts": 3},
    {"sha": "2682d3f", "compile_ok": False, "srr": 44.2, "attempts": 3},
    {"sha": "274e530", "compile_ok": False, "srr": 100.0, "attempts": 3},
    {"sha": "28fd7bf", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "3ae018f", "compile_ok": False, "srr": 0.0, "attempts": 3},
    {"sha": "3b10622", "compile_ok": False, "srr": 24.4, "attempts": 3},
    {"sha": "3f91d68", "compile_ok": False, "srr": 13.5, "attempts": 3},
    {"sha": "3fa0c1c", "compile_ok": False, "srr": 100.0, "attempts": 3},
    {"sha": "48d5424", "compile_ok": True, "srr": None, "attempts": 1},
    {"sha": "4bb4b99", "compile_ok": True, "srr": 67.7, "attempts": 3},
    {"sha": "52973b5", "compile_ok": False, "srr": 85.7, "attempts": 3},
    {"sha": "6f226e0", "compile_ok": False, "srr": 71.4, "attempts": 3},
    {"sha": "7713368", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "7c8eaa8", "compile_ok": True, "srr": None, "attempts": 2},
    {"sha": "7e1ea5d", "compile_ok": False, "srr": 0.0, "attempts": 3},
    {"sha": "8cb56be", "compile_ok": False, "srr": 48.4, "attempts": 3},
    {"sha": "8e7112a", "compile_ok": False, "srr": 50.0, "attempts": 3},
    {"sha": "907113a", "compile_ok": False, "srr": 41.4, "attempts": 3},
    {"sha": "9b991d6", "compile_ok": False, "srr": 37.8, "attempts": 3},
    {"sha": "9bfce07", "compile_ok": False, "srr": 36.0, "attempts": 3},
    {"sha": "a79dc22", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "b0be601", "compile_ok": False, "srr": -100.0, "attempts": 3},
    {"sha": "b712b0f", "compile_ok": False, "srr": 85.7, "attempts": 3},
    {"sha": "ba7215a", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "bf38b30", "compile_ok": False, "srr": 27.6, "attempts": 3},
    {"sha": "d20a5c5", "compile_ok": False, "srr": 39.6, "attempts": 3},
    {"sha": "d284d59", "compile_ok": False, "srr": 40.0, "attempts": 3},
    {"sha": "dd688e8", "compile_ok": False, "srr": 0.0, "attempts": 3},
    {"sha": "ddec358", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "df8fc9d", "compile_ok": False, "srr": 0.0, "attempts": 3},
    {"sha": "f2539d0", "compile_ok": False, "srr": 52.9, "attempts": 3},
    {"sha": "f2f7e58", "compile_ok": False, "srr": 0.0, "attempts": 3},
    {"sha": "f56fd57", "compile_ok": False, "srr": 18.5, "attempts": 3},
]

# LoRA results (first 13) — from terminal output
lora = [
    {"sha": "07ca6af", "compile_ok": False, "srr": 12.9, "attempts": 3},
    {"sha": "14144d0", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "14963cc", "compile_ok": False, "srr": 34.1, "attempts": 3},
    {"sha": "2682d3f", "compile_ok": False, "srr": 44.2, "attempts": 3},
    {"sha": "274e530", "compile_ok": False, "srr": 100.0, "attempts": 3},
    {"sha": "28fd7bf", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "3ae018f", "compile_ok": False, "srr": 12.5, "attempts": 3},
    {"sha": "3b10622", "compile_ok": False, "srr": 29.3, "attempts": 3},
    {"sha": "3f91d68", "compile_ok": False, "srr": 13.5, "attempts": 3},
    {"sha": "3fa0c1c", "compile_ok": False, "srr": 100.0, "attempts": 3},
    {"sha": "48d5424", "compile_ok": False, "srr": None, "attempts": 3},
    {"sha": "4bb4b99", "compile_ok": False, "srr": 67.7, "attempts": 3},
    {"sha": "52973b5", "compile_ok": False, "srr": 100.0, "attempts": 3},
]

# Add missing fields
for lst in [ollama, lora]:
    for r in lst:
        r.setdefault("smells_before", 0)
        r.setdefault("smells_after", 0)
        r.setdefault("test_pass_rate", None)

os.makedirs("results", exist_ok=True)

# Save full ollama results
with open("results/results.json", "w") as f:
    json.dump({"ollama": ollama}, f, indent=2)

# Save partial lora for resume
with open("results/partial_lora.json", "w") as f:
    json.dump(lora, f, indent=2)

print(f"Saved {len(ollama)} ollama results and {len(lora)} partial lora results")
