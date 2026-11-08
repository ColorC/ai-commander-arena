"""A player's workspace folder: notes, and the reflex scripts as scripts/<name>.json.

The workspace belongs to the player, not to the side of a game (materials/10 P4: notes and scripts go from training
through the exercises into the match), so a referee gets one folder per side from its caller (--workspace0/1) and in
an exercise against oneself both sides may share one. Notes are any path of up to 6 safe parts except under
scripts/, which holds the script store: {"name", "source", "trigger"} per file, written on put, removed on delete,
loaded by Scripts.load when a referee starts. Nothing here ever reads another player's folder.
"""
import json
import os
import re

from . import rules
from .errors import ApiError

NOTE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")
RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\.|$)", re.I)
SCRIPTS_DIR = "scripts"


class Workspace:
    def __init__(self, base):
        self.base = os.path.abspath(base)

    # --- notes ---
    def note_path(self, rel):
        parts = [p for p in rel.split("/") if p != ""]
        if len(parts) > 6 or any(not NOTE_PART.match(p) or RESERVED.match(p) or p.endswith((" ", ".")) for p in parts):
            raise ApiError(400, "note path: up to 6 parts of letters, digits, . _ - and space")
        return os.path.join(self.base, *parts) if parts else self.base, parts

    def notes(self, method, rel, body):
        path, parts = self.note_path(rel)
        os.makedirs(self.base, exist_ok=True)
        if method == "GET":
            if os.path.isdir(path):
                return {"entries": sorted(os.listdir(path))}
            if not os.path.isfile(path):
                raise ApiError(404, "no such note")
            with open(path, encoding="utf-8") as f:
                return {"content": f.read()}
        text = (body or {}).get("content")
        if not isinstance(text, str) or len(text.encode("utf-8")) > rules.NOTE_MAX_BYTES:
            raise ApiError(400, f"content must be text of at most {rules.NOTE_MAX_BYTES} bytes")
        if parts and parts[0].lower() == SCRIPTS_DIR:
            raise ApiError(400, f"{SCRIPTS_DIR}/ holds your reflex scripts: change them with the scripts tools")
        if path == self.base or os.path.isdir(path):
            raise ApiError(400, "that is a directory")
        used = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(self.base) for f in fs)
        if used + len(text.encode("utf-8")) > rules.NOTES_TOTAL_BYTES:
            raise ApiError(413, "workspace is full")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return {"ok": True}

    # --- script store (names are checked by Scripts before they get here) ---
    def _script_file(self, name):
        return os.path.join(self.base, SCRIPTS_DIR, name + ".json")

    def save_script(self, name, rec):
        d = os.path.join(self.base, SCRIPTS_DIR)
        os.makedirs(d, exist_ok=True)
        tmp = self._script_file(name) + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, self._script_file(name))

    def remove_script(self, name):
        try:
            os.remove(self._script_file(name))
            return True
        except FileNotFoundError:
            return False

    def stored_scripts(self):
        """[(name, file path)] of the stored scripts, by name."""
        d = os.path.join(self.base, SCRIPTS_DIR)
        if not os.path.isdir(d):
            return []
        return [(f[:-5], os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.endswith(".json")]
