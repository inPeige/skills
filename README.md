# skills

个人 agent skills 合集。围绕一条链路：**把一篇攻略/长文变成短视频的口播稿，再配成音频。**

## 包含的 skill

| skill | 作用 | 依赖 |
| --- | --- | --- |
| [`guide-to-voiceover`](./guide-to-voiceover) | 攻略 URL / HTML / Markdown / 纯文本 → ① 可直接配音的口播文稿 ② 图片素材清单 + 口播段落与图片的逐段对应关系 | `python3`；可选 Google Chrome / Chromium（SPA 站点）、Pillow（拼 contact sheet 省 token） |
| [`xfyun-long-tts`](./xfyun-long-tts) | 讯飞开放平台「长文本语音合成」：长文稿（单次约 10 万字符）→ 音频，覆盖 HMAC 签名、自动切段、任务轮询、音频下载与排障 | `python3`（只用标准库）；需自备讯飞 AppID / APIKey / APISecret |

配合关系：

```
攻略文档 ──► guide-to-voiceover ──► ①口播稿 + ③素材 ──► video-talkcraft（分镜/成片）
                     │
                     └─► tts.txt ──► xfyun-long-tts ──► audio/voice.mp3 ──► video-talkcraft
```

（`video-talkcraft` / `video-shotcraft` 是另外的项目，不在本仓库。）

## 安装

clone 下来后，把需要的 skill 目录放到你的 agent skills 目录即可（以 `~/.agents/skills/` 为例）：

```bash
git clone https://github.com/inPeige/skills.git
cp -r skills/guide-to-voiceover skills/xfyun-long-tts ~/.agents/skills/
```

两份 `SKILL.md` 是入口，`references/` 是展开规范，`scripts/` 是可直接跑的工具。
文档里的 `<本 skill 目录>` 指该 skill 自己的目录（`SKILL.md` 所在处），clone 到哪儿它就是哪儿。

## 凭据

`xfyun-long-tts` 需要讯飞凭据，二选一：

```bash
# 方式 1：环境变量（推荐）
export XFYUN_APP_ID=... XFYUN_API_KEY=... XFYUN_API_SECRET=...

# 方式 2：配置文件
cp xfyun-long-tts/config.example.json xfyun-long-tts/config.json
chmod 600 xfyun-long-tts/config.json      # 然后填入自己的凭据
```

优先级：命令行参数 > 环境变量 > `config.json`。
真实的 `config.json` 已被 `.gitignore` 忽略，**不会进仓库**；仓库里只有占位模板 `config.example.json`。
`guide-to-voiceover` 不需要凭据。

## 快速验证

```bash
cd xfyun-long-tts
python3 scripts/xfyun_tts.py --help
python3 scripts/xfyun_tts.py check        # 验签名与授权，不消耗额度
```

## 许可

各 skill 目录内文件随本仓库提供，按需自取。
