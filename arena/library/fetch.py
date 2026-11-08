"""Build the agents' read-only library (arena/DESIGN.md section 1): text of the game's own manuals FM01-FM07, the
FAQ and the hotkey list, one .txt per PDF with a "=== page N ===" line before every page, plus manifest.json (sha256
of every file, so both sides provably read the same library). The texts are the publisher's material and stay out
of Git (.gitignore).
  python -I -X utf8 arena/library/fetch.py [--src <Documents dir of the game, copied from the game folder>]
Default --src: $ARENA_DATA/fpc-docs/Documents (a copy of the Documents folder of your own game installation).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import fitz  # PyMuPDF

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FILES = ["FMs/FM01 Game Operations.pdf", "FMs/FM02 Battlefield Primer.pdf", "FMs/FM03A Tutorial Operations Basic.pdf",
         "FMs/FM03B Tutorial Operations Intermediate.pdf", "FMs/FM03C Tutorial Operations Advanced.pdf",
         "FMs/FM04 Scenario Design.pdf", "FMs/FM05 Battle Planning.pdf", "FMs/FM06 Campaign Design.pdf",
         "FMs/FM07 Map Construction.pdf", "FCCW Game FAQ.pdf", "FCCW Hotkeys.pdf"]


def tidy(text):
    """Trailing spaces off, runs of blank lines down to one."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        line = line.rstrip()
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path(os.environ.get("ARENA_DATA") or ROOT / "data") / "fpc-docs" / "Documents"))
    a = ap.parse_args()
    manifest = {"source": "game Documents folder", "files": {}}
    for rel in FILES:
        pdf = Path(a.src) / rel
        with fitz.open(pdf) as doc:
            pages = [f"=== page {i + 1} ===\n" + tidy(p.get_text()) + "\n" for i, p in enumerate(doc)]
        text = "\n".join(pages)
        out = HERE / (pdf.stem + ".txt")
        out.write_text(text, encoding="utf-8", newline="\n")
        data = out.read_bytes()
        manifest["files"][out.name] = {"pages": len(pages), "lines": text.count("\n"), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest(), "pdf_sha256":
                                       hashlib.sha256(pdf.read_bytes()).hexdigest()}
        print(f"{out.name}: {len(pages)} pages, {len(data)} bytes")
    (HERE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
