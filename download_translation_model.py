"""
download_translation_model.py

One-time setup: downloads Helsinki-NLP/opus-mt-en-ru from HuggingFace,
converts it to CTranslate2 INT8 format, and saves the tokenizer locally.

Run this once before using the "Translate English → Russian" toggle:
    python download_translation_model.py
"""

import os
import subprocess
import sys

MODEL_ID = "Helsinki-NLP/opus-mt-en-ru"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "opus-mt-en-ru")


def main() -> None:
    if os.path.isfile(os.path.join(OUT_DIR, "model.bin")):
        print("Translation model already exists at:", OUT_DIR)
        return

    # ── 1. Install required packages ──────────────────────────────────────────
    print("Installing required packages (transformers, sentencepiece)…")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "transformers>=4.20.0", "sentencepiece>=0.1.99", "-q",
    ])

    # ── 2. Convert model weights to CTranslate2 INT8 ─────────────────────────
    print(f"\nDownloading and converting {MODEL_ID}…")
    print("This is a one-time download of ~80 MB. Please wait…\n")

    result = subprocess.run([
        "ct2-transformers-converter",
        "--model", MODEL_ID,
        "--output_dir", OUT_DIR,
        "--quantization", "int8",
        "--force",
    ])
    if result.returncode != 0:
        sys.exit("Conversion failed — see errors above.")

    # ── 3. Save tokenizer files alongside the model ───────────────────────────
    print("\nSaving tokenizer…")
    from transformers import AutoTokenizer  # noqa: PLC0415 (late import intentional)
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    tok.save_pretrained(OUT_DIR)

    print(f"\nDone.  Translation model ready at:\n  {OUT_DIR}")
    print("You can now enable 'Translate English → Russian' in the app's Options menu.")


if __name__ == "__main__":
    main()
