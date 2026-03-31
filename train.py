"""
train.py — QLoRA fine-tune CodeLlama-7B-Instruct on refactoring dataset.

Usage:
  pkill ollama                        # free VRAM first
  python train.py
  python train.py --lr 1e-4           # if NaN loss
  python train.py --epochs 5          # more training
  python train.py --resume            # resume from checkpoint

Monitor:  tail -f logs/train.log
"""
import argparse, json, os, warnings, torch
os.environ["WANDB_DISABLED"] = "true"
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
warnings.filterwarnings("ignore")
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer
from datasets import load_from_disk, Dataset

def main():
    p = argparse.ArgumentParser(description="QLoRA fine-tune CodeLlama-7B-Instruct.")
    p.add_argument("--model", default="codellama/CodeLlama-7b-Instruct-hf",
                   help="Base model (default: CodeLlama-7B-Instruct)")
    p.add_argument("--dataset", default="data/finetune_dataset",
                   help="Dataset dir (default: data/finetune_dataset)")
    p.add_argument("--output", default="models/lora-v1",
                   help="Output dir for LoRA adapter (default: models/lora-v1)")
    p.add_argument("--epochs", type=int, default=3,
                   help="Training epochs (default: 3)")
    p.add_argument("--lr", type=float, default=2e-4,
                   help="Learning rate (default: 2e-4, use 1e-4 if NaN)")
    p.add_argument("--batch-size", type=int, default=1,
                   help="Batch size (default: 1 for 8.5GB VRAM)")
    p.add_argument("--grad-accum", type=int, default=16,
                   help="Gradient accumulation steps (default: 16)")
    p.add_argument("--max-seq-length", type=int, default=512,
                   help="Max sequence length (default: 512, reduce if OOM)")
    p.add_argument("--lora-r", type=int, default=16,
                   help="LoRA rank (default: 16)")
    p.add_argument("--resume", action="store_true",
                   help="Resume from last checkpoint")
    args = p.parse_args()

    # 4-bit quantization config
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4"
    )

    print(f"Loading tokenizer from {args.model}...")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    print(f"Loading model in 4-bit...")
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=bnb, device_map="auto"
    )

    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_r * 2,
        target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
        lora_dropout=0.05,
        task_type=TaskType.CAUSAL_LM
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    # Load dataset: supports HuggingFace dir or JSONL file
    if os.path.isfile(args.dataset) and args.dataset.endswith(".jsonl"):
        records = []
        with open(args.dataset) as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
        ds = Dataset.from_list(records)
        print(f"Dataset (JSONL): {len(ds)} samples")
    else:
        ds = load_from_disk(args.dataset)
        print(f"Dataset (HF): {len(ds)} samples")

    trainer = SFTTrainer(
        model=model,
        train_dataset=ds,
        args=TrainingArguments(
            output_dir=args.output,
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            learning_rate=args.lr,
            fp16=True,
            logging_steps=1,
            save_steps=50,
            save_total_limit=3,         # keep only last 3 checkpoints
            warmup_ratio=0.05,
            report_to="none",
        ),
        max_seq_length=args.max_seq_length,
        dataset_text_field="text",
    )

    # resume_from_checkpoint=True auto-finds latest checkpoint in output_dir
    trainer.train(resume_from_checkpoint=True if args.resume else None)
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"Training complete. Adapter saved to {args.output}")

if __name__ == "__main__":
    main()
