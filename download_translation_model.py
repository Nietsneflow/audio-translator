"""
download_translation_model.py

One-time setup: installs argostranslate and downloads the English->Russian
language package (~80 MB).  After running this script the "Translate
English -> Russian" toggles in the app will work fully offline.

Run once:
    python download_translation_model.py
"""

import subprocess
import sys


def main() -> None:
    # ── 1. Ensure argostranslate is installed ─────────────────────────────────
    try:
        import argostranslate.package  # noqa: F401
        print("argostranslate already installed.")
    except ImportError:
        print("Installing argostranslate...")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "argostranslate>=1.9.0", "-q",
        ])
        print("argostranslate installed.")

    from argostranslate import package

    # ── 2. Check whether the en→ru package is already installed ──────────────
    installed = package.get_installed_packages()
    if any(p.from_code == "en" and p.to_code == "ru" for p in installed):
        print("English->Russian translation package already installed. Nothing to do.")
        return

    # ── 3. Download package index and install en→ru ───────────────────────────
    print("Fetching argostranslate package index...")
    package.update_package_index()

    available = package.get_available_packages()
    pkg = next(
        (p for p in available if p.from_code == "en" and p.to_code == "ru"),
        None,
    )
    if pkg is None:
        sys.exit("ERROR: en->ru package not found in the argostranslate index.")

    print("Downloading en->ru language pack (~80 MB)...  Please wait.")
    package.install_from_path(pkg.download())

    # ── 4. Quick smoke-test ───────────────────────────────────────────────────
    from argostranslate import translate
    result = translate.translate("Hello", "en", "ru")
    print(f"\nSmoke-test: 'Hello' -> '{result}'")
    print("\nDone. You can now enable 'Translate English -> Russian' in the app.")


if __name__ == "__main__":
    main()
