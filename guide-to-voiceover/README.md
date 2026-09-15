# guide-to-voiceover

**一篇游戏攻略 → 口播文稿 + 图片素材与图文对应关系。**

```
攻略文档  ──►  ① 口播文稿  ──►  ② 图片素材 + 图文对应表
```

## 快速开始

```bash
SKILL=<本 skill 目录>          # 即本文件所在目录

# ① 抓取攻略（URL / HTML / Markdown / txt / stdin 都行）
python3 "$SKILL/scripts/fetch_guide.py" "<攻略URL>" -o work/guide

# ② 看 guide/guide.md 拟口播稿，看图配素材 → 产出 script.json / assets.plan.json …

# ③ 机器闸（--strict 把质量警告也当失败，交付前跑）
python3 "$SKILL/scripts/check_output.py" --dir work > work/check_report.json
python3 "$SKILL/scripts/check_output.py" --dir work --strict

# ④ 配音文本 → xfyun-long-tts → 音频 → video-talkcraft
python3 "$SKILL/scripts/make_tts_text.py" --dir work
```

## 产出

| 文件 | 内容 |
|---|---|
| `script.json` | 口播稿，`sentences` 零标点，对接 `video-talkcraft` 做字级时间戳 |
| `口播文稿.md` | 口播稿人读版：分段、时长、事实核对、未采用内容 |
| `tts.txt` | 配音文本（**带标点**、一句一行），喂给 `xfyun-long-tts` |
| `assets.plan.json` | 图文对应：每段用哪些图、怎么用、还缺什么 |
| `图文对应表.md` | 图文对应人读版：一张表读完 |
| `guide/guide.md` | 抓取到的攻略正文（图片内联在正确位置） |
| `guide/images/` | 下载好的原图 |

## 四个关键设计

1. **抓取有兜底闸**：HTML 抓不到正文直接报错退出（exit 3），不产出空稿。
   SPA 站点自动回退无头 Chrome；抓不动就让用户另存/粘贴，不写站点专用爬虫。
2. **图必须真的看过**：`assets.plan.json` 的 `what` 只能来自实际看图
   （`read_image` 或视觉桥），不许靠文件名和位置猜——攻略截图常常长得极像。
3. **缺口是一等产出**：攻略图少、口播段多，缺的画面写成 `kind:"need"` 的素材条目
   带优先级，而不是拿不相干的图硬凑。
4. **一份稿子出两个文本**：`tts.txt` 带标点（TTS 要标点才念得有起伏），
   `script.json` 零标点（逐字时间戳 + 字幕直出要求零标点）。
   两版必须逐字一致，`make_tts_text.py` 会当场核对。

## 目录

```
guide-to-voiceover/
├── SKILL.md                        五步流程 + 输出契约
├── scripts/
│   ├── fetch_guide.py              攻略 → 有序 blocks + 本地图片（纯标准库）
│   ├── check_output.py             口播稿 / 素材计划 机器闸
│   └── make_tts_text.py            生成 TTS 配音文本并核对两版一致
└── references/
    ├── voiceover-spec.md           攻略→口播 的改写规范（结构/句长/取舍/事实红线）
    ├── asset-mapping-spec.md       配图规范（素材类型/覆盖规则/用法/缺口）
    └── site-adapters.md            抓取排障（已验证站点 + 无头 Chrome 三个坑）
```

## 已验证样例

`https://ds.163.com/article/6957e43c4e32d25fb91cf5dd/`（网易大神 · 梦幻西游牧场篇）

抓取结果：静态 HTML 只有 16 字符 → 无头渲染 452KB → 60 个块、1362 字、9 张图，
顺序与原文完全一致。产出 27 句 / 535 字 / 107 秒口播稿，
12 段全部配上素材（9 张原图 + 5 项补拍），机器闸 0 错 0 警。

## 下游链路

`video-talkcraft` **要音频但不合成音频**（原文：「配音是输入，不是本 skill 的产物」），
所以这一环由 `xfyun-long-tts` 补上：

```
tts.txt ──► xfyun-long-tts ──► audio/voice.mp3 ──┐
                                                   ▼
script.json ─────────────────────► video-talkcraft ② 字级时间戳 → 分镜 → 成片
```

mp3 可直接用（talkcraft 内部重采样），TTS 配音还能跳过 ②-0 预剪。

## 不做什么

- 不合成配音本身 → 只出 `tts.txt`，合成走 `xfyun-long-tts`
- 不做分镜与成片 → `video-talkcraft`
- 不给单篇文章写专用爬虫
- 不编攻略里没有的信息
