"""
build_dataset.py — Build fine-tuning dataset from commit pairs.

Reads data/commits.jsonl, finds the primary changed Java file per commit,
and produces a HuggingFace Dataset formatted for CodeLlama-7B-Instruct QLoRA.

Usage:
  python scripts/build_dataset.py
  python scripts/build_dataset.py --input data/commits.jsonl --output data/finetune_dataset
  python scripts/build_dataset.py --max-tokens 3000
"""
import json, os, sys, argparse
sys.path.insert(0, os.path.dirname(__file__))
from find_primary_java import find_primary_changed_java
from datasets import Dataset

# CoT prompt template (Cordeiro et al. Figure 10)
TEMPLATE = (
    "You are a powerful model specialized in refactoring Java code.\n"
    "Explain the steps you took and why you selected the refactoring types.\n\n"
    "# Suggested refactoring types: {types}\n\n"
    "# Unrefactored code:\n```java\n{before}\n```\n\n"
    "# Refactored version:\n"
)

def format_for_codellama(instruction, output):
    """Format as CodeLlama-Instruct chat: [INST] ... [/INST] response"""
    return f"[INST] {instruction} [/INST]\n```java\n{output}\n```"

def main():
    p = argparse.ArgumentParser(description="Build fine-tuning dataset from commit pairs.")
    p.add_argument("--input", default="data/commits.jsonl",
                   help="Input JSONL (default: data/commits.jsonl)")
    p.add_argument("--output", default="data/finetune_dataset",
                   help="Output dataset dir (default: data/finetune_dataset)")
    p.add_argument("--max-chars", type=int, default=3000,
                   help="Max chars of source code to include (default: 3000)")
    args = p.parse_args()

    samples = []
    skipped = 0
    for line in open(args.input):
        c = json.loads(line)
        before_file, after_file = find_primary_changed_java(
            c["before_dir"], c["after_dir"]
        )
        if not after_file:
            print(f"  SKIP {c['sha'][:7]}: no changed Java files found")
            skipped += 1
            continue

        after_code = open(after_file).read()[:args.max_chars]
        if before_file:
            before_code = open(before_file).read()[:args.max_chars]
        else:
            before_code = "// (new file)"

        types = ", ".join(c["rminer_types"][:5])

        instruction = TEMPLATE.format(types=types, before=before_code)
        text = format_for_codellama(instruction, after_code)

        samples.append({
            "instruction": instruction,
            "output": after_code,
            "text": text,
            "sha": c["sha"][:7],
        })

    ds = Dataset.from_list(samples)
    ds.save_to_disk(args.output)
    print(f"\nDataset: {len(ds)} samples ({skipped} skipped)")
    print(f"Saved to {args.output}")

    # Print sample lengths for sanity check
    lengths = [len(s["text"]) for s in samples]
    if lengths:
        print(f"Text lengths: min={min(lengths)}, max={max(lengths)}, "
              f"avg={sum(lengths)//len(lengths)}")

if __name__ == "__main__":
    main()
