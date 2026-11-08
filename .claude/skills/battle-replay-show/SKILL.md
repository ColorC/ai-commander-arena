---
name: battle-replay-show
description: 把录下来的兵棋对局做成解说演出节目：分段节拍表、镜头、台词、确定性逐帧渲染出片、TTS 配音与回听校对、混音、按节目顺序拼接与章节、背景音乐铺设与人声压低、预览 / 审阅页。以本仓 tools/video 的 three.js 沙盘管线为例。用户要排一段镜头、改节拍表、加台词、配音、出片、拼成片、铺音乐，或排查画面 / 声音对不上时使用。
---

# 把对局做成解说演出

管线全在 `tools/video/`，素材和产物在 `$ARENA_DATA/video/`。前置：已有某局的沙盘数据 `data-<局>.js`（做法见 `wargame-ai-commander-arena` skill 第 7 节），已运行 `python -I -X utf8 tools/video/fetch_vendor.py`。

## 1. 先定节目结构

先写台本（每段讲什么、谁说、配什么画面），再分段。每段一份**节拍表**（JSON），一段对应一个渲染出来的片段。节目顺序、每段的章节名由拼接脚本决定（`tools/video/voice/assemble.py`）。

两种写法：

- 开场、串场这类手排的段落：直接写节拍表（第 2 节）。
- 整局解说：写 **staging** 文件（`tools/video/staging/STANDARD.md`），由编译器生成节拍表（第 3 节）。

## 2. 节拍表 → 镜头表

格式在 `tools/video/make_segment.py` 文件头，最小示例 `tools/video/examples/segment-example.json`（两拍）：

1. 顶层：`base`（基础镜头模块：帧率、分辨率、皮肤、模型套件、单位名表，示例 `examples/shot-example-base.js`），`out_shot`、`timeline`、`h0`（开局时刻）、`cpm`（每分钟读字数），`cams`（命名机位：`at` 格号或 [x,z] 公里，`dist`、`pitch`、`yaw`、`focus`、`blur`）。
2. 每拍：`dur` 视频秒（或 `"auto"`：最后一句台词后留 `tail` 秒），`g` 本拍结束时的游戏秒（与上拍相同就是定格），`cam: [起, 止]`（起点不同于上拍终点就是硬切），`ease: "linear"` 慢推。
3. 拍内元素用拍内相对时间 `at: [从, 到]`：`captions`、`panels`、`orders`、`people`（人物卡，说话时高亮）、`title`、`dim`、`slides`、`boards`、`keys`（要存关键帧的秒）。
4. 台词：`lines: [{speaker, text}]`，依次从 `lines_start` 排，时长 = 已配音的音频长度，否则按字数估（中文 1 字 1 单位，拉丁词和数字 1.5）。

```
cd tools/video
python -I -X utf8 make_segment.py examples/segment-example.json
```

产出镜头表模块（`out_shot`）、时间表 md（每拍起止、游戏时钟、机位、画面内容）和台词轨 `<out_shot>.voice.json`（每句的起点和音频文件）。**配音之后要重跑一次**，让节拍按真实音频长度重排。

## 3. 整局解说：staging

1. 写 `$ARENA_DATA/video/program/staging/<case>.json`：一个镜头一拍，`time.at` 定格或 `time.from/to` 播一段战斗，`focus` 取景，`cues` 按需信息（单位代号、(列,行) 格、HH:MM 时刻、从第几句台词起）。规范和交付要求见 `staging/STANDARD.md`。
2. 校验：`python -I -X utf8 tools/video/staging/staging_check.py <staging.json> --data <data-*.js>`（每句解说恰好安排一次、单位代号存在、格在图内、时刻在局内；有 errors 必须改）。
3. 编译：`python -I -X utf8 tools/video/staging/compile_staging.py <case> [段...] [--keys]`，产出每段的节拍表、镜头表、时间表。
4. 批量出关键帧审阅：`python -I -X utf8 tools/video/staging/render_all.py <case>`，再 `staging/build_review.py` 出逐镜头审阅页；定稿后 `render_all.py <case> --video` 出视频。

## 4. 渲染出片

```
python -I -X utf8 tools/video/render.py --data <data-*.js> --shot <shot.js> --out-dir <目录> --stills-only   # 先看关键帧
python -I -X utf8 tools/video/render.py --data <data-*.js> --shot <shot.js> --out-dir <目录>                 # 出 mp4
```

- 确定性：每帧是帧号的纯函数，`renderFrame(i)` 再 `encodeFrame(i)`，不用 `requestAnimationFrame` 和实时钟；GSAP 摘掉自身时钟，每帧 `gsap.updateRoot(视频秒)`。改了画面就重出，结果可复现。
- 叠加层（字幕、卡片、时钟、份额条）是 HTML/CSS，每帧经 SVG `foreignObject` 栅格化后与 WebGL 画面合成，交给浏览器自己的 `VideoEncoder`（H.264），mp4-muxer 封装。不截图、不用 ffmpeg 拼帧。
- `--range a-b` 只编码一段（先空渲前 90 帧，保证逐帧平滑的东西接得上）。
- `<out-dir>/render-log.json` 记 GPU、编码器、每帧耗时、浏览器 PID 和是否已退出：确认浏览器都关掉了再走。
- 素材根是 `$ARENA_DATA/video/`（`--video` 可另指），页面参数是 `?data=...&video=...`。

## 5. 配音

在 TTS 的虚拟环境里跑（`TTS_LAB`，Qwen3-TTS）：

1. 选角：`tools/video/voice/cast.py` 写每个角色的声音描述和试读句；`design_voices.py` 用 VoiceDesign 为每个角色按各条描述出候选，再按声纹距离挑出彼此最不像的一组写进 `refs/cast.json`；之后每句都从选定的参考音克隆（1.7B-Base），同一角色每句音色一致。两位解说要一听就分得清（语速、音高）。
2. 合成：`<TTS venv python> -X utf8 tools/video/voice/synth_lines.py`。按 (声音, 字幕文本) 缓存（`tools/video/voice_cache.py`），只合成新句和被退回的句子。
3. 回听：`<ASR venv python> -X utf8 tools/video/voice/asr_check.py [--max-cer 0.25]`，faster-whisper 听写后按中文字错误率退回（数字、英文读法太多，不比）；被退回的改名 `.bad.wav`，再跑一次 synth 换种子重配。faster-whisper 找不到 cuBLAS 时设 `TORCH_LIB` 指向 torch 的 CUDA DLL 目录。
4. 提速：`speed_lines.py` 把句内过长停顿压到上限、整句按 `cast.TEMPO` 提速保持音高（ffmpeg rubberband）。
5. 回到第 2 节重跑 `make_segment.py` / `compile_staging.py`，按真实时长重排节拍，再重新渲染。

## 6. 混音、拼接、章节

1. 单段：`<TTS venv python> -X utf8 tools/video/voice/mix.py <clip.mp4> <shot.voice.json> <out.mp4>`，每句放在字幕起点、统一响度、视频流直接复制。有台词没音频就停下（否则会出无声字幕），确认是故意的才加 `--allow-missing`。
2. 整片：`voice/assemble.py` 按节目顺序混音并拼接，写 `chapters.md` / `chapters.json`。节目顺序和输入位置写在文件头，换节目时改这里。

## 7. 背景音乐与压低

`voice/music.py`：读 `$ARENA_DATA/video/music/plan.json`（章节名 → [(在本章的起点比例, 曲目名)]，章节名来自 `chapters.json`），曲目放 `$ARENA_DATA/video/music/tracks/<曲目>.wav`（48 kHz，自行取得授权）。

- 每首去掉首尾静音、归一到单位 RMS，不够长就循环并交叉淡化（`XF`），每段起止淡入淡出（`FADE`）；
- 间隙里音乐 `BED_DB`，有人声时压到 `DUCK_DB`，按人声包络平滑压下和释放（人声统一在 -20 dBFS RMS）；
- 输出音乐床、带音乐的成片和 B 站格式的章节。

## 8. 预览与交付

- `staging/build_preview.py` 生成集中预览页（成片、各段、章节、铺曲表；链接和问题清单可选地从 `links.json` / `issues.json` 读）。
- 单段审阅页：`tools/video/make_site.py`。
- 原版游戏画面：`fpc/remote/job-vcr-capture.ps1` 截游戏自带回放的帧，`tools/video/raw_footage/` 拼成快进片段并合成脱敏后的指挥官控制台文字轨（片段表格式见 `examples/raw-footage-clips.example.json`）。

## 常见坑

- 台词改了一个字，缓存键就变了，要重新合成、回听、重排节拍、重渲染；先定稿文字再配音。
- 估算时长和真实音频差很多：配音后一定重跑节拍表，否则字幕和声音错位。
- 字幕里的三四位数会被当成格号改写成 (列,行)；比分之类写成 `1,711`、`102.0`。
- 只出关键帧（`--stills-only`）先看构图，满意了再出整段视频，省时间。
- 需要逐帧平滑的效果（尾迹、积累量）依赖顺序渲染，跳帧出片会不一致。
- 音乐、立绘、模型等第三方素材本仓不附带，权利与来源见 `NOTICE.md`。
