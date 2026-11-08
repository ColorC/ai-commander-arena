"""Commander quote cards in Chinese, written by the commander model itself: every `card` cue of a staging file goes back
to the model that said it (Kimi -> Kimi Code CLI k3.1-pro, DeepSeek -> deepseek-v4.1-flash on the OpenAI-compatible proxy route, as in the matches), which says it again in Chinese in its own voice;
the result is stored as the cue's `zh` (the original `text` stays). compile_staging.py shows `zh` when present.

    python -I -X utf8 voice_cards.py <case> [case...] [--force] [--redo] [--ids <shot id,shot id>]

--redo: only the cards whose Chinese still carries jargon / internal ids (needs_redo); --ids: these shots' cards again.

Kimi Code needs a live CLI login (`kimi -p ok` refreshes it)."""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from model_probe import ROUTES, post  # noqa: E402

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

ST = ARENA_DATA / "video/program/staging"
MAN = {m["case"]: m for m in json.loads((ST / "manifest.json").read_text(encoding="utf-8"))}


# the routes the matches ran on (agents/side*/transcript.jsonl "model"): Kimi = Kimi Code CLI, kimi-code/k3.1-pro (the CLI
# itself: its OAuth token on the bare HTTP API gets 403); DeepSeek = proxy/deepseek-v4.1-flash (opencode's proxy provider)
KIMI_MODEL = "kimi-code/k3.1-pro"


def route_of(who):
    return "kimi-cli" if "Kimi" in who else "deepseek-proxy" if "DeepSeek" in who else None


def kimi_cli(prompt):
    # node + the CLI's main.mjs (as arena/harness/clis.py): the npm .cmd shim cuts a multi-line -p prompt at its first line
    exe = [shutil.which("node"), os.path.join(os.environ["APPDATA"], "npm", "node_modules", "@moonshot-ai", "kimi-code", "dist", "main.mjs")]
    env = {k: v for k, v in os.environ.items() if k not in ("KIMI_API_KEY",)}
    cwd = ST / "kimi-cwd"; cwd.mkdir(exist_ok=True)
    t0 = time.time()
    r = subprocess.run([*exe, "-m", KIMI_MODEL, "-p", prompt], cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    return r.returncode, r.stdout + (r.stderr if r.returncode else ""), time.time() - t0


def hexes(text):
    return {h: f"({int(h[:2])},{int(h[2:])})" for h in re.findall(r"(?<!\d)(\d{4})(?!\d)", text) if 1 <= int(h[:2]) <= 60 and 1 <= int(h[2:]) <= 40}


# FPC / match jargon -> how the card says it in Chinese (weapon designations like T-80, BMP, M1A1, 2S6, AH-64, Mi-24 stay)
GLOSSARY = {
    "Hasty": "仓促进攻", "Deliberate": "准备进攻", "Hunt": "搜索攻击", "Screen": "警戒", "Recce / Recon": "侦察",
    "Barrage": "炮击", "Withdraw": "撤退", "Mtr": "迫击炮", "FA": "野战炮兵", "RA": "皇家炮兵", "RAG": "团属炮兵群",
    "MRR": "摩步团", "AA / AD": "防空", "VP": "目标格", "sop": "标准作战程序", "acceptable_losses": "可接受损失",
    "fire_discipline": "射击纪律", "Maximum Range": "最大射程", "Apache": "阿帕奇",
    "AH 加字母（AH C 等）": "武装直升机分队（蓝军的是阿帕奇，红军的是雌鹿，按说话这一方算）", "Kiowa / OH58": "基奥瓦（OH-58）",
    "Tunguska": "通古斯卡", "Igla": "针式", "Milan": "米兰", "TOW": "陶式", "Chieftain": "酋长坦克", "Bradley": "布雷德利",
    "c108、c125 这类 c 加三位数": "敌情接触编号（发现的敌方目标），不要出现编号，说成“那个目标”“那股敌军”",
    "c0040 这类 c 加四位数": "自己已下达命令的编号，不要出现编号，说成“刚才那道命令”“那次炮击任务”",
}
# a card needs another pass when its Chinese still carries jargon or an internal id (weapon designations are fine)
ALLOWED = re.compile(r"T-?\d+\w*|BMP-?\d*|BRDM-?\d*|BTR-?\d*|M1A1|M1|M2|M109|AH-64A?|Mi-24|OH-58|2S\d|BM-21|MLRS|Mi-8|\d+")
LEFTOVER = re.compile(r"[A-Za-z][A-Za-z0-9/_-]*")


def needs_redo(zh):
    return bool(re.search(r"\bc\d{2,}", zh or "")) or any(not ALLOWED.fullmatch(w) for w in LEFTOVER.findall(zh or ""))


def ask(route, side_zh, who, cards, names):
    base, model, keyfn = ROUTES.get(route, (None, None, None))
    items = []
    for k, c in enumerate(cards):
        units = {u: n["short"] for u, n in names.items() if len(u) > 2 and u in c["text"]}
        items.append({"n": k, "time": c.get("time"), "original": c["text"], "hexes": hexes(c["text"]), "units": units})
    prompt = (
        f"你是这局兵棋《Flashpoint Campaigns》里执{side_zh}的指挥官（{who}）。下面是你当时在会话里亲口写下的话（original）。"
        "节目画面上会把它放进“指挥官原话卡”，配你的头像。请你自己把每一条重新说成一句中文：\n"
        "- 用你当时的语气、口吻和判断，意思不增不减，不加解释，不加引号；\n"
        "- 单位番号改成 units 里给的中文简称；四位格子编号改成 hexes 里给的 (列,行) 写法；\n"
        "- 游戏术语和缩写说成中文，对照表：" + "；".join(f"{k}→{v}" for k, v in GLOSSARY.items()) + "。"
        "除了 T-80、BMP、M1A1、2S6、AH-64 这类武器型号，不要留英文字母；\n"
        "- 时刻写完整（如 07:02），算式说成一句人话（谁拿下哪格、份额多少、结果如何）；\n"
        "- 每条 40 个字以内，像指挥官的一句原话。\n"
        "只输出一个 JSON 数组，每项 {\"n\": 序号, \"zh\": \"…\"}，顺序与输入一致。\n\n"
        + json.dumps(items, ensure_ascii=False, indent=1))
    if route == "kimi-cli":
        rc, txt, dt = kimi_cli(prompt)
        if rc or "[" not in txt:
            print(f"kimi cli exit {rc}: {txt[-400:]}")
            return {}, KIMI_MODEL, round(dt, 1)
        arr = json.loads(txt[txt.index("["):txt.rindex("]") + 1])
        return {int(a["n"]): a["zh"].strip().strip("“”\"") for a in arr}, KIMI_MODEL, round(dt, 1)
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    extra = {"x-opencode-session": "voice-cards"} if route.endswith("-go") else None
    status, _, data, dt = post(base, keyfn(), body, extra)
    if status != 200:
        print(f"{route} {status}: {str(data)[:400]}")
        return {}, model, round(dt, 1)
    txt = data["choices"][0]["message"].get("content") or ""
    arr = json.loads(txt[txt.index("["):txt.rindex("]") + 1])
    return {int(a["n"]): a["zh"].strip().strip("“”\"") for a in arr}, model, round(dt, 1)


def main():
    a = sys.argv[1:]
    force, redo = "--force" in a, "--redo" in a
    ids = set(a[a.index("--ids") + 1].split(",")) if "--ids" in a else set()
    cases = [x for k, x in enumerate(a) if not x.startswith("--") and (k == 0 or a[k - 1] != "--ids")]
    for case in cases:
        m, p = MAN[case], ST / f"{case}.json"
        st = json.loads(p.read_text(encoding="utf-8"))
        names = json.loads(pathlib.Path(m["names"]).read_text(encoding="utf-8"))["units"]
        by = {"blue": [], "red": []}
        for S in st["segments"]:
            for sh in S["shots"]:
                for c in sh.get("cues", []):
                    if c["type"] == "card" and (force or not c.get("zh") or (redo and needs_redo(c.get("zh"))) or sh["id"] in ids):
                        by["red" if c.get("side") in ("red", 1) else "blue"].append(c)
        for side, cards in by.items():
            who = m[side]
            r = route_of(who)
            if not cards or not r:
                continue
            got, model, dt = ask(r, "蓝军" if side == "blue" else "红军", who, cards, names)
            for k, c in enumerate(cards):
                if k in got:
                    c["zh"], c["zh_by"] = got[k], model
            print(case, side, who, model, f"{dt}s", f"{len(got)}/{len(cards)}")
            for c in cards:
                print("   ", c["text"][:60], "->", c.get("zh"))
        p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        print(p.as_posix())


if __name__ == "__main__":
    main()
