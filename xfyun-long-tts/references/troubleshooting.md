# 排障清单

按“先定位是鉴权、授权、参数还是任务”的顺序排查。**先跑 `check`，它不消耗额度。**

```bash
SKILL=<本 skill 目录>          # 即该 skill 的 SKILL.md 所在目录
python3 "$SKILL/scripts/xfyun_tts.py" check          # 只验签名
python3 "$SKILL/scripts/xfyun_tts.py" check --deep   # 顺带验证授权/额度
python3 "$SKILL/scripts/xfyun_tts.py" synth -t "测试" --dry-run   # 看将要发送的请求体与签名 URL
```

## A. 鉴权类（HTTP 401 / 403，响应只有顶层 message）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `Unauthorized` | URL 上缺 `authorization` | 检查 query 参数是否被 urlencode/截断 |
| `HMAC signature does not match` | api_key 或 api_secret 错 | 核对控制台；确认 AppID/APIKey/APISecret 属于**同一个应用** |
| 同上 | 签名拼接格式错 | `signature_origin` 必须恰好是 `host: H\ndate: D\nPOST P HTTP/1.1`，`:` 后**一个空格**，路径要含 `/v1/private/dts_create` |
| 同上 | `headers` 写错 | 固定字符串 `host date request-line`，不是参数值 |
| 同上 | signature base64 长度不是 44 | 说明签名算法/密钥长度不对 |
| 403 时钟偏移 | 本机时间偏差 > 300 秒 | `sudo sntp -sS time.apple.com` 或开启自动对时；容器里注意时区与 `date` 取值 |
| 403 时钟偏移 | `date` 不是 GMT | 必须 RFC1123 GMT，如 `Thu, 09 Feb 2023 03:37:55 GMT`；不要传本地时区时间 |

`--dry-run` 会打印完整签名 URL，可直接用 curl 复现：签名 URL 有时效，5 分钟内用完。

## 先分清“SDK 接入”还是“WebAPI 接入”（凭据到底用哪几个）

讯飞控制台的服务页常写一句 **“SDK调用方式只需APPID。APIKey或APISecret适用于WebAPI调用方式。”**
那是**平台通用说明**，指的是同一个服务有两种接入姿势，不是说凭据可以二选一：

| 接入方式 | 需要的凭据 | 谁来做鉴权 |
| --- | --- | --- |
| SDK 集成（MSC/离线 SDK 等） | 只要 `AppID` | SDK 内部完成鉴权，调用方不感知密钥 |
| **WebAPI（HTTP/WebSocket）** | **`AppID` + `APIKey` + `APISecret` 三者都要** | 调用方自己算 HMAC-SHA256 签名 |

**长文本语音合成只有 WebAPI 这一种方式**（官方文档：*目前支持 Web API 应用平台*），没有 SDK 版本。
所以三个凭据缺一不可，不存在“只用 AppID”或“APIKey 与 APISecret 二选一”的用法：

- `header.app_id` —— 标识应用（业务参数）
- `authorization` 里的 `api_key=` —— 标识密钥对
- 用 `APISecret` 做 HMAC-SHA256 算 `signature`

本 skill 的实现就是**纯 WebAPI**：直接 `POST https://api-dx.xf-yun.com/v1/private/dts_create`，
不依赖任何讯飞 SDK、不加载任何 SDK 库（脚本只用 Python 标准库的 `hmac`/`hashlib`/`urllib`）。

**怎么快速判断凭据是不是用对了**——看报错发生在哪一层：

| 报错 | 层级 | 说明 |
| --- | --- | --- |
| `401 HMAC signature cannot be verified: apikey not found` | 签名层 | APIKey 错 |
| `401 HMAC signature does not match` | 签名层 | APISecret 错 |
| `10313 app_id and api_key does not match` | 应用层 | AppID 与 APIKey 不配套 |
| `11200 licc limit` | **授权/额度层（已经过了签名和应用校验）** | 凭据类型是对的，是没开通/没额度 |

也就是说：**如果凭据少给或用错，报的会是 401/10313，绝不会是 11200。**
看到 `11200` 就说明 AppID/APIKey/APISecret 三者都被服务端接受并按 WebAPI 规则校验通过了。

## B. 授权/额度类

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `11200` / `11201` `licc limit` | 应用未开通长文本语音合成，或免费额度未领/用尽，或该发音人未开通 | 去 https://console.xfyun.cn/services/long_text 领免费额度或购买套餐；确认你的 AppID 对应应用已开通本服务 |
| `10313` `app_id and api_key does not match` | AppID 与 APIKey 不属于同一应用 | 换回配套的三元组 |

> 注意：**签名正确 + `licc limit` 说明代码没问题，是账号权限问题**。四路对照可自证：
>
> | 请求 | 返回 |
> | --- | --- |
> | 正确三元组 | `11200 licc limit`（过了签名，卡在授权） |
> | APIKey 改错 | `401 HMAC signature cannot be verified: apikey not found` |
> | APISecret 改错 | `401 HMAC signature does not match` |
> | AppID 换掉 | `10313 app_id and api_key does not match` |
>
> 换错任何一个凭据都会变成 401/10313，只有“凭据全对但没授权”才会稳定复现 11200。

## C. 参数类（`10163` / `10165`）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `$.parameter.dts.vcn must be one of [...]` | 发音人非法 | 从报错里列的集合选，见 `voices.md` |
| `$.payload.text.text` 相关 | 传了明文而非 base64 | `text` 必须是 base64；脚本已自动处理 |
| 文本超限 | > 10 万字符或 > 1M | 用 `--max-chars` 自动切段，或自己分段后合成再拼接 |
| 请求体过大 | base64 后约 1.33×，10 万字≈0.3–0.5MB，仍应远小于 1M | 若确实超了，切段 |

## D. 任务与结果类

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `13001 task not found` | task_id 错、或结果已过 7 天保质期 | 重新建任务。**`check` 探活时返回它属于正常** |
| 长时间停在 `task_status=3` | 文本很长，合成慢 | 加大 `--timeout`（默认 3600s）；轮询间隔会自动从 10s 递增到 30s |
| `task_status=2` / `4` | 派发失败 / 处理失败 | 通常是文本内容或发音人问题；缩短文本、换发音人重试 |
| 下载下来是**文本文件** | 把 `payload.audio.audio`（base64 的 URL）当成音频字节了 | 先 `base64.b64decode` 得到 URL，再 GET 该 URL |
| 文件能下载但播放器不认 | 后缀与 `encoding` 不一致 | `lame`→`.mp3`、`raw`→`.pcm`、`opus*`→`.opus`、`speex*`→`.spx` |
| PCM 无法直接播放 | `raw` 是无头裸流 | 用 ffmpeg 封装：`ffmpeg -f s16le -ar 16000 -ac 1 -i out.pcm out.mp3` |

## E. 多段与拼接

- 超过 `--max-chars`（默认 90000）会自动切段，优先在**段落 → 句子**边界切，输出 `xxx_part01.mp3`、`xxx_part02.mp3`…
- `--concat` 只在 `lame`(mp3) 与 `raw`(pcm) 下可靠（按字节顺序追加）。**opus/speex 不能这样拼**，脚本会拒绝并保留分段文件。
- 更保真的长音频拼接建议用 ffmpeg：

```bash
printf "file '%s'\n" part*.mp3 > list.txt
ffmpeg -f concat -safe 0 -i list.txt -c copy merged.mp3
```

- `--concurrency N` 可加速多段合成，但会同时占用 N 份并发额度，遇 `11200` 请降回 1。

## F. 编码/采样率

- `--sample-rate 24000` 音质更好、体积更大；`8000` 适合电话场景。
- 部分发音人/编码组合可能不受支持，报参数错误时先退回 `lame` + `16000` 验证通路。
- `rhy=1` 会额外返回拼音/音素文件地址（`payload.pybuf.text`，同样 base64 编码的 URL）；
  `xtts2.0` 引擎的时间戳需 ×5ms。

## G. 网络环境

- 服务器 IP 不固定，**不要用 IP 直连或写死 IP**，用域名 `api-dx.xf-yun.com`。
- 不支持跨域，**不能在前端浏览器直接调用**；必须在服务端/本机 CLI 调用。
- 若走代理，确保 HTTPS 直连可达；`curl -sv https://api-dx.xf-yun.com` 可快速验证连通性。
