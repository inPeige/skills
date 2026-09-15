---
name: xfyun-long-tts
description: "讯飞开放平台「长文本语音合成」(Long Text TTS) 的调用与排障：把长篇文稿(单次约 10 万字符/万字级)合成语音并下载音频，支持 mp3/pcm/opus/speex、语速语调音量、拼音标注、回调与任务轮询。当用户要「把文章/书稿/口播稿/长文转语音」、「批量配音」、「用讯飞做长文本 TTS」，或提到 dts_create / dts_query / api-dx.xf-yun.com / vcn 发音人 / 11200 licc limit 时使用。不负责：实时流式合成(在线语音合成 WebAPI)、超拟人合成、声音复刻训练。"
metadata:
  requires:
    bins: ["python3"]
  cliHelp: "cd <本 skill 目录> && python3 scripts/xfyun_tts.py --help"
  credentials: "需自备讯飞凭据：复制 config.example.json 为 config.json 填入，或设环境变量 XFYUN_APP_ID / XFYUN_API_KEY / XFYUN_API_SECRET"
---

# 讯飞长文本语音合成 (Long Text TTS)

把长文稿一次性合成音频。**两步异步接口**：`dts_create` 建任务拿 `task_id` → `dts_query` 轮询到 `task_status=5` → 响应里是 base64 编码的音频**下载链接** → 再下载真正的音频。

| 项 | 值 |
| --- | --- |
| 创建任务 | `POST https://api-dx.xf-yun.com/v1/private/dts_create` |
| 查询任务 | `POST https://api-dx.xf-yun.com/v1/private/dts_query` |
| 鉴权 | URL query 带 `host` / `date` / `authorization`（HMAC-SHA256 签名） |
| 单次上限 | 约 10 万字符，文本 0–1M |
| 音频留存 | 云端仅存 **7 天**，必须及时下载 |
| 官方文档 | https://www.xfyun.cn/doc/tts/long_text_tts/API.html |

## 工具：`scripts/xfyun_tts.py`

**任何合成请求都走这个脚本，不要手写签名。** 只用 Python 标准库，无第三方依赖。

> **路径约定**：下文 `<本 skill 目录>` 指本文件（`SKILL.md`）所在目录 —— repo clone 到哪儿它就是哪儿。

```bash
SKILL=<本 skill 目录>          # 即本文件所在目录
python3 "$SKILL/scripts/xfyun_tts.py" <命令> [选项]
```

### 命令

| 命令 | 用途 |
| --- | --- |
| `check [--deep]` | 校验密钥与授权状态。**不消耗额度**（用不存在的 task_id 探活）。`--deep` 会真发一次合成请求 |
| `voices` | 列出全部可用发音人 `vcn` |
| `synth` | **主力命令**：建任务 → 轮询 → 下载音频，一步到位 |
| `create` | 只建任务，输出 `task_id`（配合回调或稍后查询） |
| `query <task_id> [--download]` | 查任务状态；`--download` 成功后直接拉音频 |

### 常用参数

```
-t/--text      直接给文本          -f/--file   文本文件路径（- 表示 stdin）
-o/--out       输出路径(默认 output.<后缀>)     -v/--voice  发音人(默认 x4_mingge)
-e/--encoding  lame(mp3,默认)|raw(pcm)|opus|opus-wb|speex-*   
--sample-rate  16000(默认)|8000|24000          --speed/--volume/--pitch  0-100，默认 50
--language     zh(默认)|en                     --rhy 1 额外返回拼音标注
--max-chars    单任务字符上限(默认 90000)，超出自动切段
--interval/--timeout  轮询间隔(默认10s，递增至30s)/总超时(默认3600s)
--concurrency  多段并发数(默认1)               --concat  多段按字节拼接为一个文件(mp3/pcm)
--callback-url 完成回调地址                     --request-id 自定义任务标记(≤64字符)
--gzip-text    文本 gzip 压缩后上传             --dry-run  只打印请求体与签名 URL，不发送
-q/--quiet     不打印进度(stderr)
```

**输出约定**：stdout 只输出一个 JSON 对象（`ok` / `task_id` / `audio_url` / `output` / `bytes` / `parts` …），进度日志走 stderr。失败时输出 `{"ok": false, "error_code": …, "hint": …}` 并返回非 0 退出码。用 `--dry-run` 可以安全地检查将要发送的请求。

需要注意的两点：文本用 `-f 文件` 传更稳（避免 shell 转义/换行问题），脚本也支持 stdin；
多段合成会拆成多个任务，此时 `--request-id` 会被忽略（它要求全局唯一），脚本会在 stderr 提示。

## 标准流程

```bash
SKILL=<本 skill 目录>          # 即本文件所在目录
cd <工作目录>

# 0) 先探活（不花额度）——授权有问题时立刻能看出来
python3 "$SKILL/scripts/xfyun_tts.py" check

# 1) 长文合成（推荐：文本放文件，避免 shell 转义问题）
python3 "$SKILL/scripts/xfyun_tts.py" synth -f script.txt -v x4_mingge -o voice.mp3

# 2) 超长文本：自动切段，每段一个文件，并可选拼接
python3 "$SKILL/scripts/xfyun_tts.py" synth -f book.txt -o book.mp3 --concat --concurrency 2

# 3) 只要 task_id（例如已有回调服务）
python3 "$SKILL/scripts/xfyun_tts.py" create -f script.txt --callback-url https://example.com/hook

# 4) 事后取回（7 天内）
python3 "$SKILL/scripts/xfyun_tts.py" query 221124163743668851981200 --download -o out.mp3
```

## 硬性规则

1. **不要自己拼签名。** 签名串必须严格是 `host: $host\ndate: $date\nPOST $path HTTP/1.1`（`:` 后一个空格，`\n` 换行，`request-line` 必须与实际请求行一致）。`date` 必须是 RFC1123 的 **GMT**（`Thu, 09 Feb 2023 03:37:55 GMT`），服务端只容忍 **300 秒**时钟偏移——本机时间不准会直接 403。
2. **`payload.text.text` 是 base64 编码后的文本**，不是明文。这是最常见的踩坑点。
3. **响应里的 `payload.audio.audio` 也是 base64**，解码后才是音频下载 URL；拿它当音频字节写文件会得到一个文本文件。
4. **`task_status` 是字符串**：`"1"` 建好、`"2"` 派发失败、`"3"` 处理中、`"4"` 处理失败、`"5"` 成功。只有 `"5"` 才有音频。
5. **下载文件后缀必须与 `encoding` 一致**：`lame`→`.mp3`、`raw`→`.pcm`、`opus`/`opus-wb`→`.opus`、`speex-*`→`.spx`。
6. `x4_`/`x5_` 系列发音人通常需要在控制台单独开通；`vcn` 不在允许列表会被 `10163` 明确列出来。
7. 云端音频 **7 天**过期，长流程要及时下载。
8. **本 skill 是纯 WebAPI，不涉及任何讯飞 SDK。** 长文本语音合成只提供 WebAPI 接入，因此 `AppID` + `APIKey` + `APISecret` 三者缺一不可（控制台那句“SDK调用方式只需APPID，APIKey或APISecret适用于WebAPI”是平台通用说明，不是说可以二选一）。凭据用错一律是 401/10313；出现 `11200 licc limit` 说明凭据已全部通过校验，问题在授权/额度。

## 错误码处置

| code | message | 处理 |
| --- | --- | --- |
| `11200` / `11201` | `licc limit` | **授权/额度不足**。到 https://console.xfyun.cn/services/long_text 领取免费额度或购买套餐；确认该应用已开通长文本语音合成，且发音人已授权 |
| `10313` | `appid cannot be empty` / `app_id and api_key does not match` | AppID 与 APIKey 不属于同一应用，核对 `config.json` |
| `10163` | `parameter schema validate error` | 按 message 修正字段；若提示 `vcn must be one of [...]`，那个列表就是服务端实际允许的 vcn 全集（见 `references/voices.md`） |
| `10165` | 参数校验失败 | 检查 `header`/`parameter`/`payload` 结构 |
| `13001` | `task not found` | task_id 不存在或已过期（7 天）。**鉴权探活时返回它是正常的** |
| `401` | `HMAC signature does not match` | api_key/api_secret 错、签名拼接格式错、或 base64 长度异常（正常 44 字节） |
| `403` | 时钟偏移校验失败 | 本机时间与标准时间相差超过 5 分钟 |

## 参考文件

- `references/api.md` —— 完整接口规范：鉴权推导、全部请求/响应字段、编码与采样率取值、任务状态机
- `references/voices.md` —— 发音人全表（含文档未列出但服务端接受的 vcn）与选型建议
- `references/troubleshooting.md` —— 排障清单（鉴权、时钟、切段、音质、拼接）
- `config.example.json` —— 凭据模板。复制为 `config.json` 填入自己的 AppID/APIKey/APISecret（建议 `chmod 600`），或改用环境变量；优先级：`--app-id/--api-key/--api-secret` 参数 > 环境变量 > `config.json`。`config.json` 已被 `.gitignore` 忽略，不会入库。
