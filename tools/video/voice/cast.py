"""The programme's voices (11-07): one voice per model, designed with Qwen3-TTS VoiceDesign, then every line is cloned
from that reference (1.7B-Base), so a character sounds the same in every line.

The characters (materials/26-video-program-round4-script.md §5-6): the two commentators must be easy to tell apart
(GPT quicker, Claude a little slower); the commanders are two more voices. The referee is Claude in another role
(its card shows Claude's portrait), so it speaks with Claude's voice.

Paths: tts-lab (models, venv) $TTS_LAB; voice data $ARENA_DATA/video/voice
(refs/cand/<role>-<i>.wav candidates, refs/cast.json the chosen cast, lines/ the voiced lines)."""
import os
import pathlib
import re

# data root (README): ARENA_DATA, default <repo>/data
ARENA_DATA = pathlib.Path(os.environ.get("ARENA_DATA") or pathlib.Path(__file__).resolve().parents[3] / "data")

TTS = pathlib.Path(os.environ.get("TTS_LAB") or ARENA_DATA / "tts-lab")
MODELS = TTS / "models"
ROOT = ARENA_DATA / "video/voice"
REFS = ROOT / "refs"
CAST = REFS / "cast.json"
LINES = ROOT / "lines"
# every voiced line is played this much faster, pitch kept (speed_lines.py); 11-08: the generated pace was 293
# characters a minute on average, slow for commentary (the owner: "语音是不是太慢了一点")
# 11-08: "可以更快…慢的离谱". The benchmark of 8 high-view Bilibili videos of the kind (research/pace-benchmark) says the
# slowness was the pauses, not the speaking rate: their voiced rate is 316-335 a minute (fastest 351), ours 297 with
# pauses at 0.48 s median. With the inner pauses cut (PAUSE_MAX) the plain voice reads ~300 within a line; 1.15x makes ~340 within a line (~360 counting voiced time only, the benchmark's fastest).
TEMPO = 1.15
# silences inside a line are cut to this before the speed-up (TTS comma pauses ran 0.24 s median, up to 0.9 s)
PAUSE_MAX = 0.16
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")          # ffmpeg on PATH unless FFMPEG names the executable
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# role -> the sample it reads (becomes the clone reference) and candidate voice descriptions
ROLES = {
    "claude": {
        "who": "Claude（解说，文学少女：暖橙长发、手里拿书）",
        "text": "这一步其实很有意思。坦克营没有急着冲，而是先让炮兵把河对岸的高地压住，这和一九七三年戈兰高地的打法，几乎如出一辙。",
        "instruct": [
            "温暖知性的年轻女声，音色柔和，中音，语速中等偏慢，娓娓道来，带一点笑意，像爱读书的文学少女在讲历史故事",
            "温柔沉静的少女声，中音偏低，咬字清楚，语速舒缓，语气亲切而有分寸，像图书馆里讲解战史的学姐",
            "知性温和的年轻女声，中音，语调有起伏，语速适中偏慢，自信从容，略带书卷气",
        ],
    },
    "gpt": {
        "who": "GPT（解说，白发龙娘）",
        "text": "数据很直接：三分钟内损失两个排，份额掉了四个点。说白了，这不是运气差，是把装甲车开进了反坦克导弹的射界。",
        "instruct": [
            "清冷干练的少女声，音调偏高，吐字干净利落，语速偏快，理性冷静，带一点冷幽默",
            "清亮的年轻女声，音色偏冷，语速快，节奏紧凑，像数据分析师一样冷静犀利",
            "高冷的少女声，音调偏高，声音清脆，语速较快，语气淡定理性，偶尔带点调侃",
        ],
    },
    "kimi": {
        "who": "Kimi（指挥官，银发、月亮元素）",
        "text": "全体注意。第一装甲营沿公路向西推进，十分钟内拿下渡口。不要恋战，侧翼交给侦察连，我要的是速度。",
        "instruct": [
            "成熟稳重的女军官声，音调低沉，略带沙哑，语速偏慢，语气果断有威严",
            "低沉冷静的成熟女声，嗓音醇厚，语速慢而坚定，像正在下达命令的指挥官",
            "御姐音，低音，沉稳有磁性，语速偏慢，语气冷静从容，不容置疑",
        ],
    },
    "deepseek": {
        "who": "DeepSeek（指挥官，蓝发鲸鱼元素、元气）",
        "text": "收到！我先把直升机派到北边的林子侦察一下，看看对面是不是藏了坦克。如果没有的话，主力就从这里过河，对吧？",
        "instruct": [
            "元气活泼的少女声，音调高，声音明亮甜美，语速中等，认真又充满好奇，句尾略微上扬",
            "开朗的少女声，音调偏高，清脆明亮，语气热情积极，像认真汇报工作的新人",
            "活泼可爱的少女声，高音，声音清亮，语速稍快，充满干劲",
        ],
    },
}

# who speaks a line -> voice
VOICE_OF = {"裁判": "claude", "Claude": "claude", "GPT": "gpt", "Kimi": "kimi", "DeepSeek": "deepseek"}


def spoken(text):
    """What the voice reads for a subtitle line (the subtitle itself stays as written)."""
    t = text
    t = re.sub(r"\bMi-(\d+)", r"米\1", t)                                    # Mi-24 -> 米24
    t = re.sub(r"\b([A-Z]{1,4})-(\d+)", r"\1 \2", t)                          # T-80, BMP-3
    t = re.sub(r"[（(](\d+)\s*[,，]\s*(\d+)[）)]", r"\1、\2", t)              # (17,16) -> 17、16
    t = re.sub(r"\b0?(\d{1,2}):00\b", r"\1点整", t)                            # 08:00 -> 8点整
    t = re.sub(r"\b0?(\d{1,2}):(\d{2})\b", lambda m: f"{m.group(1)}点{int(m.group(2))}分", t)
    t = t.replace("/", "、").replace("…", "，").replace("——", "，").replace("—", "，")
    t = t.replace("WEGO", "We-Go")
    return t
