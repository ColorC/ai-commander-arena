"""Design the cast: every role reads its sample in each candidate description (Qwen3-TTS 1.7B VoiceDesign), then the
set of one candidate per role that sounds most unlike each other (speaker embeddings from the Base model's speaker encoder: the smallest
pairwise distance as large as possible) becomes the cast; its clips are the clone references for every line.

Run with the tts-lab venv:  <TTS venv python> -X utf8 design_voices.py [--redo]
Out: $ARENA_DATA/video/voice/refs/cand/<role>-<i>.wav, refs/cast.json, refs/cast.md (the table),
     refs/cast-demo.wav (the four chosen voices one after another)."""
import itertools
import json
import sys
import time

import numpy as np
import soundfile as sf
import torch

from cast import CAST, MODELS, REFS, ROLES

CAND = REFS / "cand"
MIN_ST = 2.0


def design(redo):
    CAND.mkdir(parents=True, exist_ok=True)
    todo = [(r, i) for r in ROLES for i in range(len(ROLES[r]["instruct"]))
            if redo or not (CAND / f"{r}-{i}.wav").exists()]
    if not todo:
        return
    from qwen_tts import Qwen3TTSModel
    m = Qwen3TTSModel.from_pretrained(str(MODELS / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"), device_map="cuda:0",
                                      dtype=torch.bfloat16, attn_implementation="sdpa")
    for r, i in todo:
        t0 = time.perf_counter()
        torch.manual_seed(1107 + i)
        text = ROLES[r]["text"]
        wavs, sr = m.generate_voice_design(text=text, language="Chinese", instruct=ROLES[r]["instruct"][i],
                                           max_new_tokens=len(text) * 7 + 80)
        sf.write(CAND / f"{r}-{i}.wav", np.asarray(wavs[0], dtype=np.float32), sr)
        print(f"{r}-{i} {len(wavs[0]) / sr:.1f}s in {time.perf_counter() - t0:.1f}s", flush=True)
    del m
    torch.cuda.empty_cache()


def measure():
    import librosa
    from qwen_tts import Qwen3TTSModel
    # the speaker embedding is the Base model's own speaker encoder (the x-vector a clone starts from); speechbrain's
    # ECAPA recurses forever in its lazy importer on this Python (11-07)
    base = Qwen3TTSModel.from_pretrained(str(MODELS / "Qwen3-TTS-12Hz-1.7B-Base"), device_map="cuda:0",
                                         dtype=torch.bfloat16, attn_implementation="sdpa")
    out = {}
    for r in ROLES:
        for i in range(len(ROLES[r]["instruct"])):
            p = CAND / f"{r}-{i}.wav"
            y, sr = librosa.load(p, sr=16000)
            yt, _ = librosa.effects.trim(y, top_db=40)
            item = base.create_voice_clone_prompt(ref_audio=str(p), x_vector_only_mode=True)[0]
            emb = item.ref_spk_embedding.float().cpu().numpy().reshape(-1)
            f0, _, _ = librosa.pyin(yt, fmin=70, fmax=600, sr=16000)
            rate = len(ROLES[r]["text"]) / (len(yt) / 16000)
            out[(r, i)] = {"emb": emb / np.linalg.norm(emb), "f0": float(np.nanmedian(f0)), "rate": rate,
                           "dur": len(yt) / 16000}
    return out


def main():
    design("--redo" in sys.argv)
    ms = measure()
    roles = list(ROLES)
    # a candidate whose speed is off (ran away / rushed) is not eligible
    ok = {k: v for k, v in ms.items() if 2.8 <= v["rate"] <= 7.5}
    # raw embeddings of young Chinese female voices all sit within ~0.04 cosine of each other: compare them centred
    # on the candidates' mean, and keep every pair at least MIN_ST semitones apart in pitch (what the ear hears first)
    mu = np.mean([v["emb"] for v in ms.values()], axis=0)
    for v in ms.values():
        c = v["emb"] - mu
        v["c"] = c / np.linalg.norm(c)
    dist = lambda a, b: 1 - float(ms[a]["c"] @ ms[b]["c"])
    semis = lambda a, b: abs(12 * np.log2(ms[a]["f0"] / ms[b]["f0"]))
    best = None
    for min_st in (MIN_ST, 1.0, 0.0):
        for combo in itertools.product(*[[i for i in range(len(ROLES[r]["instruct"])) if (r, i) in ok] for r in roles]):
            ks = list(zip(roles, combo))
            pairs = list(itertools.combinations(ks, 2))
            if min(semis(a, b) for a, b in pairs) < min_st:
                continue
            d = min(dist(a, b) for a, b in pairs)
            if best is None or d > best[0]:
                best = (d, ks)
        if best:
            break
    if best is None:
        raise SystemExit("no eligible candidate set")
    dmin, ks = best
    cast = {}
    for r, i in ks:
        cast[r] = {"ref_audio": str(CAND / f"{r}-{i}.wav"), "ref_text": ROLES[r]["text"], "instruct": ROLES[r]["instruct"][i],
                   "cand": i, "f0_hz": round(ms[(r, i)]["f0"], 1), "chars_per_s": round(ms[(r, i)]["rate"], 2)}
    CAST.write_text(json.dumps(cast, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 角色音色候选", "", f"入选组合两两最小声纹距离 {dmin:.3f}（Qwen3-TTS 说话人向量减去候选均值后的 1 − 余弦相似度，越大越分得开），两两音高至少差 2 个半音。", "",
          "| 角色 | 候选 | 入选 | 音高中位数 Hz | 语速 字/秒 | 描述 |", "|---|---|---|---|---|---|"]
    for r in roles:
        for i in range(len(ROLES[r]["instruct"])):
            v = ms[(r, i)]
            md.append(f"| {r} | {i} | {'★' if cast[r]['cand'] == i else ''} | {v['f0']:.0f} | {v['rate']:.1f} | "
                      f"{ROLES[r]['instruct'][i]} |")
    md += ["", "入选组合两两距离：", ""]
    for a, b in itertools.combinations(ks, 2):
        md.append(f"- {a[0]}–{b[0]}：声纹 {dist(a, b):.3f}，音高差 {semis(a, b):.1f} 个半音")
    (REFS / "cast.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    pieces = []
    for r, i in ks:
        y, sr = sf.read(CAND / f"{r}-{i}.wav", dtype="float32")
        pieces += [y, np.zeros(int(sr * 0.8), np.float32)]
    sf.write(REFS / "cast-demo.wav", np.concatenate(pieces), sr)
    print("\n".join(md))
    print("cast", CAST, REFS / "cast.md", REFS / "cast-demo.wav")


if __name__ == "__main__":
    main()
