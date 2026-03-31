"""
refactor_agent.py — LangGraph node: generate refactored Java code.

Two modes controlled by REFACTOR_MODE env var:
  - "ollama": zero-shot via Ollama REST API (llama3:8b)
  - "lora":   fine-tuned CodeLlama-7B + LoRA adapter from models/lora-v1/
"""
import gc, os, re, signal, sys, warnings, requests, torch
warnings.filterwarnings("ignore", category=FutureWarning, module="huggingface_hub")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from build_dataset import TEMPLATE, format_for_codellama

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

# Training prompt template used by run_experiment.py build_training_dataset()
# Must match at inference time for LoRA to work correctly
LORA_TEMPLATE = (
    "Refactor this Java code. Apply: {types}.\n"
    "Return only the refactored Java code.\n\n"
    "```java\n{before}\n```"
)

# ── Lazy-loaded LoRA model singleton ────────────────────────────────
_model = None
_tokenizer = None


def _load_lora_model():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer

    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )
    base_model = "codellama/CodeLlama-7b-Instruct-hf"
    adapter_path = os.environ.get("LORA_MODEL_PATH",
                                    os.path.join(PROJECT_ROOT, "models", "lora-v1"))

    print(f"  Loading {base_model} in 4-bit + LoRA adapter...")
    _tokenizer = AutoTokenizer.from_pretrained(adapter_path)
    _tokenizer.pad_token = _tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        base_model, quantization_config=bnb, device_map="auto"
    )
    _model = PeftModel.from_pretrained(model, adapter_path)
    _model.eval()
    print("  LoRA model loaded.")
    return _model, _tokenizer


def unload_model():
    """Free VRAM — call before switching to Ollama mode."""
    global _model, _tokenizer
    del _model, _tokenizer
    _model = _tokenizer = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ── Java extraction from LLM output ────────────────────────────────

def _extract_java(text):
    """Extract Java code from markdown fences or raw output."""
    # Match ```java, ```[java], ```Java, etc.
    m = re.search(r'```\[?[Jj]ava\]?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Match plain ``` fences
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: everything after [/INST]
    idx = text.find("[/INST]")
    if idx >= 0:
        return text[idx + 7:].strip()
    # Fallback: if it looks like Java code (starts with package/import/class), use it
    for marker in ('package ', 'import ', 'public class ', 'public interface '):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:].strip()
    # If no Java-like content found, return empty to trigger retry
    return ""


# ── Prompt builder ──────────────────────────────────────────────────

def _build_prompt(before_code, rminer_types, mode=None):
    types_str = ", ".join(rminer_types[:5])
    if mode is None:
        mode = os.environ.get("REFACTOR_MODE", "ollama")
    if mode == "lora":
        # Must match the training prompt format from run_experiment.py
        return LORA_TEMPLATE.format(types=types_str, before=before_code)
    return TEMPLATE.format(types=types_str, before=before_code)


# ── Ollama mode ─────────────────────────────────────────────────────

def _refactor_ollama(instruction, temperature=0.2):
    prompt = f"[INST] {instruction} [/INST]"
    try:
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3:8b",
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": 2048},
            },
            timeout=300,
        )
        r.raise_for_status()
        return _extract_java(r.json()["response"])
    except Exception as e:
        print(f"  Ollama error: {e}")
        return ""


# ── LoRA mode ───────────────────────────────────────────────────────

class _GenerationTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _GenerationTimeout("LoRA generation timed out")


def _refactor_lora(instruction, temperature=0.2, timeout=300):
    model, tokenizer = _load_lora_model()
    # Must match training format exactly: [INST] {instruction}[/INST]
    prompt = f"[INST] {instruction}[/INST]\n"

    inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=1024)
    inputs = {k: v.to(model.device) for k, v in inputs.items()}

    # Set alarm to prevent hanging on generation
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=1024,
                temperature=temperature,
                do_sample=True,
                repetition_penalty=1.15,
                pad_token_id=tokenizer.eos_token_id,
            )
        signal.alarm(0)
    except _GenerationTimeout:
        print(f"  LoRA generation timed out after {timeout}s")
        return ""
    finally:
        signal.signal(signal.SIGALRM, old_handler)

    # Decode only new tokens
    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    response = tokenizer.decode(new_tokens, skip_special_tokens=True)
    if not response.strip():
        print(f"  [DEBUG] LoRA returned empty response. Prompt length: {inputs['input_ids'].shape[1]} tokens")
    else:
        print(f"  [DEBUG] LoRA response length: {len(response)} chars, first 100: {response[:100]!r}")
    return _extract_java(response)


# ── pass@k: generate multiple candidates ──────────────────────────

def refactor_k_candidates(before_code, rminer_types, k=5, temperature=0.8, mode=None):
    """Generate k candidate refactorings at higher temperature. Returns list of Java strings."""
    if mode is None:
        mode = os.environ.get("REFACTOR_MODE", "ollama")
    instruction = _build_prompt(before_code, rminer_types, mode=mode)
    candidates = []
    for i in range(k):
        if mode == "lora":
            code = _refactor_lora(instruction, temperature=temperature)
        else:
            code = _refactor_ollama(instruction, temperature=temperature)
        if code.strip():
            candidates.append(code)
    return candidates


# ── LangGraph node ──────────────────────────────────────────────────

def refactor_node(state: dict) -> dict:
    mode = os.environ.get("REFACTOR_MODE", "ollama")
    instruction = _build_prompt(state["before_code"], state["rminer_types"], mode=mode)

    if mode == "lora":
        refactored = _refactor_lora(instruction)
    else:
        refactored = _refactor_ollama(instruction)

    # If LLM returned nothing, fall back to original code
    if not refactored.strip():
        refactored = state["before_code"]

    return {"refactored_code": refactored, "attempt": state.get("attempt", 0) + 1}
