# 长文本语音合成 —— 接口规范

来源：https://www.xfyun.cn/doc/tts/long_text_tts/API.html
主域名：`api-dx.xf-yun.com`（服务器 IP 不固定，**必须用域名**，不要绑 IP）

## 1. 接口总览

| 项 | 值 |
| --- | --- |
| 传输 | HTTPS（推荐）/ HTTP。**不支持跨域，不能在浏览器里直接调** |
| 创建任务 | `POST /v1/private/dts_create HTTP/1.1` |
| 查询任务 | `POST /v1/private/dts_query HTTP/1.1` |
| 字符编码 | UTF-8 |
| 响应 | JSON |
| 请求体 | JSON 字符串，`Content-Type: application/json` |
| 文本长度 | 单次约 10 万字符；`text` 字段 0–1M |
| 音频格式 | pcm / mp3 / speex / opus |
| 结果留存 | 云端保留 **7 天**，需及时下载 |
| 结果获取 | 主动轮询 `dts_query` 或 `callback_url` 回调 |

## 2. 鉴权

签名参数放在 **URL query** 上（不是 Header）：`host`、`date`、`authorization`。

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `host` | 请求主机 | `api-dx.xf-yun.com` |
| `date` | RFC1123 格式，**必须是 GMT/UTC+0** | `Thu, 09 Feb 2023 03:37:55 GMT` |
| `authorization` | base64 编码的签名信息 | 见下 |

### 签名推导

```
1. signature_origin = "host: " + host + "\n"
                   + "date: " + date + "\n"
                   + "POST " + path + " HTTP/1.1"

   # 注意：冒号后有一个空格；第三行是真实的 request-line（方法与路径要和实际请求一致）

2. signature_sha  = HMAC-SHA256(signature_origin, APISecret)     # 原始字节
3. signature      = base64(signature_sha)                        # 正常 44 字节

4. authorization_origin =
   'api_key="<APIKey>", algorithm="hmac-sha256", headers="host date request-line", signature="<signature>"'
   # headers 是"参与签名的参数名"列表，是固定字符串，不是这些参数的值

5. authorization = base64(authorization_origin)

6. 最终 URL = https://api-dx.xf-yun.com/v1/private/dts_create
              ?host=api-dx.xf-yun.com&date=<urlencode(date)>&authorization=<urlencode(authorization)>
```

`date` 是唯一带空格/逗号的参数，**必须 urlencode**（`%2C`、`+`）。

服务端校验时钟偏移，**最大允许 300 秒**。`hmac.new(key, msg)` 里 `key = APISecret`、`msg = signature_origin`。

### 鉴权失败返回

| HTTP | message | 原因 |
| --- | --- | --- |
| 401 | `Unauthorized` | 缺 `authorization` 参数 |
| 401 | `HMAC signature cannot be verified` | 签名参数缺失/格式错，尤其 api_key 复制错 |
| 401 | `HMAC signature does not match` | api_key/api_secret 错、签名拼接格式错、base64 长度异常（应 44 字节） |
| 403 | `HMAC signature cannot be verified, a valid date or x-date header is required…` | 本机时间与标准时间相差 > 5 分钟 |

> 鉴权层的失败响应**没有** `header.code`，只有顶层 `{"message": "..."}`；业务层的失败才有 `header.code`。解析时要区分。

## 3. 创建任务 `/v1/private/dts_create`

### 请求体

```json
{
  "header": { "app_id": "your_appid" },
  "parameter": {
    "dts": {
      "vcn": "x4_mingge",
      "language": "zh",
      "speed": 50,
      "volume": 50,
      "pitch": 50,
      "rhy": 1,
      "audio": { "encoding": "lame", "sample_rate": 16000 },
      "pybuf": { "encoding": "utf8", "compress": "raw", "format": "plain" }
    }
  },
  "payload": {
    "text": {
      "encoding": "utf8",
      "compress": "raw",
      "format": "plain",
      "text": "6L+Z5piv5LiA5q615rWL6K+V5paH5pys"
    }
  }
}
```

### `header`

| 参数 | 类型 | 必传 | 说明 |
| --- | --- | --- | --- |
| `app_id` | string | 是 | 应用唯一标识 |
| `callback_url` | string | 否 | 任务结果回调地址 |
| `request_id` | string | 否 | 客户端任务标记，≤64 字符，唯一性由客户端保证；回调时会带回 |

### `parameter.dts`

| 参数 | 类型 | 必传 | 说明 |
| --- | --- | --- | --- |
| `vcn` | string | 是 | 发音人，见 `voices.md` |
| `language` | string | 否 | `zh`(默认) / `en` |
| `speed` | int | 否 | 语速 `[0,100]`，默认 50 |
| `volume` | int | 否 | 音量 `[0,100]`，默认 50 |
| `pitch` | int | 否 | 语调 `[0,100]`，默认 50 |
| `ram` | string | 否 | 是否读出标点：`0` 不读(默认) / `1` 全读 |
| `rhy` | string | 否 | `0` 不返回拼音(默认) / `1` 返回拼音标注 |

`rhy=1` 时支持的引擎：`xtts1.0-cpu`、`xtts1.0-gpu`、`xtts2.0-gpu`、`xtts2-gpu`（每句话一次性返回）；
**`xtts2.0` 的拼音标注时间戳要乘以 5ms**。

官方 demo 里还出现了 `bgs`、`reg`、`rdn`、`scn`、`frame_size` 等字段，均为未公开文档的可选项，脚本默认不发送。

### `parameter.dts.pybuf`

| 参数 | 必传 | 取值 |
| --- | --- | --- |
| `encoding` | 否 | `utf8`(默认) / `gb2312` |
| `compress` | 否 | `raw` |
| `format` | 否 | `plain` |

### `parameter.dts.audio`

| 参数 | 必传 | 取值 |
| --- | --- | --- |
| `encoding` | **是** | 见表下 |
| `sample_rate` | 否 | `16000`(默认) / `8000` / `24000` |

`encoding` 取值：

| encoding | 含义 | 建议后缀 |
| --- | --- | --- |
| `raw` | 原始 PCM | `.pcm` |
| `lame` | MP3 | `.mp3` |
| `opus` | opus 8K | `.opus` |
| `opus-wb` | opus 16K | `.opus` |
| `speex-org-nb;8` | speex 8K（分号后为压缩等级 1–10，缺省 8） | `.spx` |
| `speex-org-wb;8` | speex 16K | `.spx` |
| `speex;7` | 讯飞定制 8K speex（分号后等级 1–10，缺省 7） | `.spx` |
| `speex-wb;7` | 讯飞定制 16K speex | `.spx` |

下载文件的**后缀必须与 `encoding` 一致**。

### `payload.text`

| 参数 | 必传 | 取值 |
| --- | --- | --- |
| `encoding` | 是 | `utf8`(默认) / `gb2312` |
| `compress` | 否 | `raw`(默认) / `gzip` |
| `format` | 否 | `plain` / `json` / `xml` |
| `text` | 是 | **base64 编码后的文本**，最大 10 万字，0–1M |

### 返回

成功：

```json
{
  "header": {
    "code": 0,
    "message": "success",
    "sid": "dts000e81e2@dx184a8c91edf738d882",
    "task_id": "221124163743668851981200"
  },
  "payload": null
}
```

失败（业务层）：

```json
{ "header": { "code": 10313, "message": "appid cannot be empty", "sid": "dts000e61dd@dx…" } }
```

`code == 0` 才是成功；`task_id` 用于后续查询，务必保存。

## 4. 查询任务 `/v1/private/dts_query`

### 请求体

```json
{ "header": { "app_id": "your_appid", "task_id": "221124174214587600971201" } }
```

| 参数 | 类型 | 必传 | 说明 |
| --- | --- | --- | --- |
| `header.app_id` | string | 是 | 应用 app id |
| `header.task_id` | string | 是 | 创建接口返回的任务 id |

### 返回

```json
{
  "header": {
    "code": 0,
    "message": "success",
    "sid": "dts000fa2e7@dx184a90433d96f19882",
    "task_id": "221124174214587600971201",
    "task_status": "5"
  },
  "payload": {
    "audio": {
      "audio": "aHR0cDov……",
      "bit_depth": "16",
      "channels": "1",
      "encoding": "lame",
      "sample_rate": "16000"
    },
    "pybuf": { "encoding": "utf8", "text": "aHR0cDov……" }
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `header.task_status` | **字符串**：`1` 创建成功 / `2` 派发失败 / `3` 处理中 / `4` 处理失败 / `5` 处理成功 |
| `payload.audio.audio` | **base64 编码的音频下载链接**（解码后是一个 https URL） |
| `payload.audio.encoding` | `lame` / `speex` / `opus` / `opus-wb` / `speex-wb` |
| `payload.audio.sample_rate` | `16000` / `8000` / `24000` |
| `payload.audio.channels` | `1` / `2` |
| `payload.audio.bit_depth` | `16` / `8` |
| `payload.pybuf.text` | 音素/拼音标注文件的地址，同样 base64 编码 |
| `payload` | 任务未完成（status ≠ 5）时为 `null` |

失败示例（参数校验）：

```json
{ "header": { "code": 10163,
  "message": "parameter schema validate error: '$.header.task_id' length must be larger or equal than 1; ",
  "sid": "dts000ef953@dx184ac8e8ce4738d882" } }
```

### 取结果的三步

```python
audio_url = base64.b64decode(resp["payload"]["audio"]["audio"]).decode()   # 1. base64 解码出 URL
data      = requests.get(audio_url).content                                # 2. 下载
open("tts.mp3", "wb").write(data)                                          # 3. 后缀对齐 encoding
```

## 5. 常见错误码

| code | message | 含义与处理 |
| --- | --- | --- |
| `0` | `success` | 成功 |
| `10163` | `parameter schema validate error: …` | 参数校验失败，message 会指出具体字段。**`vcn` 非法时会直接列出允许的全集** |
| `10313` | `appid cannot be empty` / `app_id and api_key does not match` | app_id 缺失，或 AppID 与 APIKey 不同属一个应用 |
| `11200` / `11201` | `licc limit` | **授权/额度不足**：应用未开通长文本语音合成、免费额度未领/已用尽、或该发音人未开通 |
| `13001` | `task not found` | task_id 不存在或已过期（云端保留 7 天） |

## 6. 参考实现（官方 python demo 的关键部分）

```python
format_date = format_date_time(mktime(datetime.now().timetuple()))   # 生成 RFC1123
signature_origin  = "host: " + host + "\n"
signature_origin += "date: " + format_date + "\n"
signature_origin += "POST " + path + " HTTP/1.1"
signature_sha = hmac.new(secret.encode('utf-8'),
                         signature_origin.encode('utf-8'),
                         digestmod=hashlib.sha256).digest()
signature_sha = base64.b64encode(signature_sha).decode()
authorization_origin = 'api_key="%s", algorithm="%s", headers="%s", signature="%s"' % (
    api_key, "hmac-sha256", "host date request-line", signature_sha)
authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode()
```

> 官方 demo 用 `mktime(datetime.now().timetuple())` 生成时间戳，在非 UTC 时区会算错，
> 只是恰好落在 300 秒容差里。**推荐用 `email.utils.formatdate(usegmt=True)`**（本 skill 的实现即如此）。

demo 下载地址：
- Python：http://xfyun-doc.xfyun.cn/lc-sp-long_text_tts_python_demo-1711008241700.zip
- Java：https://xfyun-doc.xfyun.cn/static/16693613721083781/long_text_tts_java_demo.zip
