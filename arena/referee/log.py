"""One jsonl file per record class under <run>/logs/, flushed per line, safe to call from several threads."""
import json
import os
import threading

CHANNELS = ("events", "wakes", "queue", "releases", "audit", "fog", "clock", "scripts", "violations", "sync",
            "exercises", "score")


class Log:
    def __init__(self, directory):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self._files = {}
        self._lock = threading.Lock()

    def path(self, name):
        return os.path.join(self.dir, name + ".jsonl")

    def write(self, name, rec):
        if name not in CHANNELS:
            raise KeyError(f"unknown log channel {name}")
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            f = self._files.get(name)
            if f is None:
                f = self._files[name] = open(self.path(name), "a", encoding="utf-8", newline="\n")
            f.write(line)
            f.flush()

    def read(self, name):
        p = self.path(name)
        if not os.path.exists(p):
            return []
        with self._lock:
            with open(p, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]

    def close(self):
        with self._lock:
            for f in self._files.values():
                f.close()
            self._files.clear()
