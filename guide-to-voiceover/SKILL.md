---
name: guide-to-voiceover
description: "把一篇游戏攻略文档变成短视频口播稿：输入攻略 URL / HTML / Markdown / 纯文本，输出 ①可直接配音的口播文稿 ②图片素材清单及口播段落与图片的逐段对应关系。当用户说「给这篇攻略写口播」「攻略转口播稿」「攻略做成视频文案」「配一下图和文案」「这篇攻略的图文对应」时使用。产出对接 video-talkcraft 的 ①口播稿 与 ③素材 两个阶段。不负责：配音合成（走 xfyun-long-tts）、分镜与成片（走 video-talkcraft）。"
metadata:
  requires:
    bins: ["python3"]
  optional:
    bins: ["Google Chrome 或 Chromium（SPA 站点需要）", "PIL/Pillow（拼 contact sheet 省 token）"]
  cliHelp: "cd <本 skill 目录> && python3 scripts/fetch_guide.py --help"
---

# guide-to-voiceover — 攻略转口播

```
攻略文档  ──►  ① 口播文稿  ──►  ② 图片素材 + 图文对应表
                 (script.json)      (assets.plan.json)
```

**输入**：一篇攻略的 URL / 保存的 HTML / Markdown / 粘贴的纯文本
**输出**：两样东西，外加机器可读版和机器闸报告

三份规范，**必须先读**：
- `references/voiceover-spec.md` — 怎么把"查阅型攻略"改写成"线性口播"；
  **§8 是互动与叙事规则**（点赞钩子、第一人称背书、因果/悬念/看点），别只顾 §1–§5 的"说得对"
- `references/asset-mapping-spec.md` — 怎么配图、怎么标缺口（§6 讲段级叙事字段怎么落到画面）
- `references/site-adapters.md` — 抓取排障与已验证站点

---

## 流程（七步，别跳步）

```
① 抓取   fetch_guide.py     → guide.json + guide.md + images/
② 看图   真的看图             → 每张图画的是什么（写进 assets.plan.json 的 what）
③ 拟稿   voiceover-spec     → script.json + 口播文稿.md
④ 配图   asset-mapping-spec → assets.plan.json + 图文对应表.md
⑤ 自检   check_output.py    → 机器闸；过了再交付
⑥ 配音文本 make_tts_text.py  → tts.txt（带标点，喂 TTS）
⑦ 交接   见 §⑦               → xfyun-long-tts 合成 → video-talkcraft 时间戳
```

**为什么②必须在③④之前**：不看图就没法判断哪张图能接哪句话。
攻略里的截图常常长得极像（同一个面板的不同页签），
靠文件名和原文位置猜必然配错。

## ① 抓取

> **路径约定**：下文 `<本 skill 目录>` 指本文件（`SKILL.md`）所在目录 —— repo clone 到哪儿它就是哪儿。

```bash
SKILL=<本 skill 目录>          # 即本文件所在目录
cd <工作目录>
python3 "$SKILL/scripts/fetch_guide.py" "<攻略URL>" -o work/guide
```

产出：

| 文件 | 用途 |
|---|---|
| `work/guide/guide.json` | 机器可读：有序 text/image 块 + 图片元数据 |
| `work/guide/guide.md` | **给 AI 读的正文**，图片以 `![](images/…)` 内联在正确位置 |
| `work/guide/images/` | 下载好的原图 |
| `work/guide/fetch.log` | 用了哪个容器、有没有走无头渲染 |

stdout 是一行 JSON 摘要。**先看 `meta.textChars` 和 `imageCount` 合不合理**，
再往下走。常见情况：

- SPA 站点（如大神 ds.163.com）自动走无头 Chrome，`rendered: true` 是正常的
- 抓不到正文时脚本**直接报错退出**（不产出空稿），按提示换 `--selector` / 本地文件 / 粘贴

常用参数：`--selector`、`--render always|never`、`--chrome`、`--max-images`、`--no-images`

**然后读 `guide.md` 全文**——这是③的输入。别读 `guide.json`，太长。

## ② 看图（不可跳过）

**必须真的看到图的内容**，不能靠文件名或位置猜。

- 模型支持图像输入 → `read_image` 逐张看
- 不支持 → 用视觉桥 `modlens_read_image`；图多时先用 PIL 拼一张
  **带编号的 contact sheet**（每格左上角标红字 `01`..`09`）再一次问完，
  提问时明确要求"逐格转写画面上的可读文字和数字"

游戏截图的价值几乎全在**画面上的文字**：NPC 名字、坐标、面板菜单、数值表。
把这些写进每张图的 `what` 和 `onScreenText`。

## ③ 拟口播稿

按 `references/voiceover-spec.md` 写，要点：

1. **先定篇幅再写**：目标秒数 ÷ 5 字/秒 = 字数（30s≈150字，60s≈300字，3min≈900字）
2. **按操作顺序重排**，不要照抄攻略的小节顺序
3. **一句一个信息点，≤42 字**
4. **`sentences` 零标点**——配音逐字锚定、字幕整句直出都要求零标点；
   标点只写在人读的 `口播文稿.md` 里，两版文字必须逐字一致
5. **数字一律汉字**（`5000`→`五千`）——下游配音时间戳按文本逐字锚定
6. **砍掉查阅型内容**（对照表、大全），一集只讲一件事
7. **事实红线**：等级/成本/坐标/NPC 名原样保留；攻略与截图冲突时以截图为准并标注
8. **互动：每个硬数据交付点后挂一次价值兑现**（`role:"like"` 段，四段式：
   这招是什么 → 量化好处 → 点赞请求 → 下集钩子）。≥20 句至少 1 次，别只在结尾伸手
9. **背书：至少一处"我"字句**（时长/结果/情绪 + 验证方式）。
   **句里的数字必须真人验过**，没验过就换成表里算得出来的客观表述，不许编
10. **叙事：长稿（≥8 段）要有 `meta.axis` 主轴 + `outro` 观点收束**；
    body/tip 段标 `logic` / `suspense` / `highlight`，段级 `meme` 留热点位
    （**不写死热点名**）。全部见 `voiceover-spec.md` §8

产出 `script.json`：

```json
{
  "sentences": ["五十级加一套房子就能开牧场", "..."],
  "meta": { "title": "...", "source": "...", "target": "douyin",
            "estimatedSec": 62.5, "charsPerSec": 5.0, "axis": "time" },
  "segments": [
    { "id": "seg01", "role": "hook", "heading": "开场钩子",
      "sentences": [0, 1], "assets": ["img01"] },
    { "id": "seg05", "role": "body", "heading": "七天模式怎么操作",
      "logic": "cause→effect", "suspense": "藏住时间：先不说第几天收",
      "highlight": "data", "meme": "数字落地时上当期热梗 BGM",
      "sentences": [10, 11, 12, 13], "assets": ["img02"] },
    { "id": "seg06", "role": "like", "heading": "价值兑现",
      "sentences": [14, 15], "assets": ["card04"] }
  ]
}
```

`sentences` 是**给下游工具的契约**（`video-talkcraft` 的 `make_timing.py`
直接读这个字段逐句做时间戳），额外字段不影响它。句号范围必须**无重叠、无遗漏**地
覆盖所有句子（`check_output.py` E008 强制无遗漏；重叠覆盖只报 W208 警告）。

`role` 取值：`hook` / `intro` / `body` / `like` / `tip` / `outro` / `cta`。
`like` 与 `outro` 是**独立成段**的（不塞进 body 段尾），分镜才能给它们专门镜头。

段级叙事字段 `logic` / `suspense` / `highlight` / `meme` 与 `meta.axis`
全部可选，但缺了会在 `--strict` 下报 W225–W229——它们提醒的是"这段还没设计过"。

同时产出人读的 `口播文稿.md`（格式见 voiceover-spec.md §6）。

## ④ 配图

按 `references/asset-mapping-spec.md` 配，要点：

1. **每段至少一张素材**（E013 强制）
2. **关键信息必配图**：等级门槛、成本、坐标、NPC、操作路径
3. `usage` 标清 `full` / `crop` / `zoom` / `blur-bg` / `reference-only`
   —— 游戏截图分辨率普遍低（实测 392×181 到 1266×632），竖屏多半要 `zoom` 局部
4. **缺口是正常产出**：攻略通常只有 8–12 张图而口播有 12–18 段，
   缺的画面写成 `"kind": "need"` 的素材条目（不给 `file`，带 `need`/`how`/`priority`），
   **不要拿不相干的图硬凑**。缺口清单 = `assets` 里 `kind=="need"` 的那批。
   **用户不打算补拍的段，改成 `"kind": "text"`（纯文字/动效镜）**——
   文字卡是一等公民，不是凑数（`asset-mapping-spec.md` §2.1）。
   不是每段都需要图：讲对比/反差用对比卡，讲机制用线稿示意图。
5. 没用上的原图写进 `unused` 并说明原因（W212 会提醒）

产出 `assets.plan.json` + `图文对应表.md`。

## ⑤ 自检

```bash
python3 "$SKILL/scripts/check_output.py" --dir <产出目录>
```

机器判定的部分：结构完整性、句子索引覆盖、图片文件存在性、每段配图、
与原攻略图片对账、阿拉伯数字、标点、句长、素材词表、段序、估算时长，
以及**人读版 `口播文稿.md` 与 `script.json` 是否逐字一致**（E017–E020）。

还有 **§8 的互动与叙事闸**（W222–W229）：点赞钩子、第一人称背书、观点收束、
因果方向、叙事主轴、段首悬念、看点密度与单调、热点元素。
这几项是**规模阈值触发**的（≥20 句查互动、≥8 段查长稿结构、≥5 个 body 段查叙事三件套），
短视频不会被长稿规则误伤。

```bash
python3 "$SKILL/scripts/check_output.py" --dir <产出目录> > check_report.json
python3 "$SKILL/scripts/check_output.py" --dir <产出目录> --strict   # 交付前再跑一遍
```

**跑两遍**：第一遍看 E（结构性错误，必须为 0）；第二遍 `--strict`
把 W 也当失败——因为"数字一律汉字""零标点"这类**事实红线是警告级别的**，
不加 `--strict` 会带着 `5000`、`（207,102）` 一路放行到配音那步才炸。
交付标准是 **`--strict` 也全过**。

## ⑥ 生成配音文本（交给 TTS）

```bash
python3 "$SKILL/scripts/make_tts_text.py" --dir <产出目录>
```

**一份稿子要出两个文本，文字逐字相同、只差标点**——这是整个链路最容易踩的坑：

| 文件 | 标点 | 给谁 | 为什么 |
|---|---|---|---|
| `tts.txt` | **带标点**，一句一行 | `xfyun-long-tts` 合成音频 | 标点决定语气和停顿；喂零标点会念成一口气的平串 |
| `script.json` | **零标点** | `video-talkcraft` 做字级时间戳 | 逐字锚定 + 字幕整句直出都要求零标点 |

这个脚本会当场核对两版逐字一致，不一致就报错、不产出 `tts.txt`。

## ⑦ 下游链路（配音是 talkcraft 的**输入**，它不合成音频）

```
guide-to-voiceover ①口播稿
        │
        ├─ tts.txt ──► xfyun-long-tts ──► audio/voice.mp3 ──┐
        │                                                    ▼
        └─ script.json ──────────────────────► video-talkcraft ②字级时间戳
                                                    │
                                              ③素材 → ④SHOTBOOK → … → ⑧成片
```

`video-talkcraft` 明确写着「配音是输入，不是本 skill 的产物…skill 不含合成技术」，
**所以音频必须自己准备**，本 skill 用 `xfyun-long-tts` 补上这一环：

```bash
# ① 合成配音（xfyun 默认输出 mp3）
python3 "<xfyun-long-tts skill 目录>/scripts/xfyun_tts.py" synth \
    -f <产出目录>/tts.txt -v x4_mingge -o audio/voice.mp3

# ② 直接进 talkcraft 做字级时间戳
python3 scripts/timestamps_cpu.py audio/voice.mp3 script.json audio/timestamps.json
```

三点注意：

- **不用转格式**：talkcraft 两个 ASR 后端都内部重采样，`wav/mp3 均可`
- **TTS 配音可跳过 ②-0 预剪**（talkcraft 原文：TTS 只会压气口）
- **时长以 talkcraft 实测为准**：`estimatedSec` 是按 5 字/秒估的**排期用**数字，
  真实时长由音频决定。**实测讯飞 `x4_mingge` 默认 `--speed 50` 只有 4.30 字/秒
  （535 字 → 124.5s，比估算慢 16%）**；要卡目标时长就把 `--speed` 上调
  （提到 ~5 字/秒约需 `--speed 58`），再用 `--chars-per-sec` 复核

---

## 产出清单

```
<产出目录>/
├── guide/                    ← ①阶段的原料
│   ├── guide.json  guide.md  images/  fetch.log
├── script.json               ← 口播稿（机器可读，对接下游做时间戳）
├── 口播文稿.md                ← 口播稿（人读）
├── tts.txt                   ← 配音文本（带标点，喂给 xfyun-long-tts）
├── assets.plan.json          ← 图文对应（机器可读）
├── 图文对应表.md              ← 图文对应（人读）
└── check_report.json         ← ⑤的机器闸报告
```

## 交给下游（video-talkcraft）

这个 skill 覆盖 talkcraft 流程的 **①口播稿** 和 **③素材**，并用 `xfyun-long-tts`
补上 talkcraft 不做的 **②配音合成**（见上面 §⑦）：

- `tts.txt` → `xfyun-long-tts` → `audio/voice.mp3`
- `audio/voice.mp3` + `script.json` → talkcraft ② 字级时间戳
- `assets.plan.json` 每项 → SHOTBOOK 每镜的 `素材：` 行：
  `- 素材：图（public/stills/01_19c6bedf.png）`
- 图片拷进 `remotion/public/stills/`，**保留原文件名**便于追溯
- `kind:"need"` 且 `priority:"P0"` 的项在分镜前必须补齐，
  否则那一镜只能做纯文字镜

## 常见问题

| 现象 | 处理 |
|---|---|
| `fetch_guide.py` 报"正文只抽到 N 个字符" | 正常兜底。换 `--selector`，或干脆让用户另存 HTML / 粘贴正文 |
| 无头渲染超时 | 去掉 `--chrome-profile`；确认没加 `--run-all-compositor-stages-before-draw`（见 site-adapters §2） |
| 抓到一堆头像/图标 | 已经是自动过滤的；漏网时用 `--max-images` 收紧或 `--selector` 缩容器 |
| 图片糊 | 正常。`usage: zoom` 局部放大，或立一条 `kind:"need"` 素材去补拍 |
| 口播稿太长 | 拆上下集。**别压缩语速**，观众听得出 |
| 稿子里的数字被改写了 | 绝对不行。攻略数字是事实红线 |
| 报 W222 / W223 | 缺点赞钩子或第一人称背书。补 `like` 段 / "我"字句；**别为凑情绪编造"我养了半个月"** |
| 报 W224–W229 | 长稿缺叙事设计。补 `meta.axis`、`outro` 段，给 body 段标 `logic`/`suspense`/`highlight`/`meme` |

## 不做的事

- **不合成配音本身**——只产出喂给 TTS 的 `tts.txt`，合成走 `xfyun-long-tts`
- **不做分镜和成片**（走 `video-talkcraft`）
- **不给单篇文章写专用爬虫**——抓不动就让用户另存或粘贴，成本更低
- **不编攻略里没有的信息**，不把"具体见游戏内提示"填成确定数字
