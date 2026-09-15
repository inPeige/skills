# xfyun-long-tts

讯飞开放平台「长文本语音合成」(Long Text TTS) 的 agent skill。

- **入口**：`SKILL.md`
- **工具**：`scripts/xfyun_tts.py`（只用 Python 标准库，无第三方依赖）
- **参考**：`references/api.md`（接口规范）、`references/voices.md`（发音人）、`references/troubleshooting.md`（排障）
- **凭据**：`config.example.json` 复制为 `config.json` 填入自己的凭据（建议权限 600），或设 `XFYUN_APP_ID` / `XFYUN_API_KEY` / `XFYUN_API_SECRET`；优先级：命令行参数 > 环境变量 > `config.json`。`config.json` 已被 `.gitignore` 忽略。

## 30 秒上手

```bash
SKILL=<本 skill 目录>                            # 即本文件所在目录
cp $SKILL/config.example.json $SKILL/config.json # 首次：填入自己的讯飞凭据

python3 "$SKILL/scripts/xfyun_tts.py" check                     # 验密钥 + 授权（不花额度）
python3 "$SKILL/scripts/xfyun_tts.py" voices                    # 看发音人
python3 "$SKILL/scripts/xfyun_tts.py" synth -f 文稿.txt -o out.mp3   # 合成
```

## 覆盖的接口能力

| 能力 | 状态 |
| --- | --- |
| `dts_create` 创建任务 / `dts_query` 查询任务 | ✅ |
| HMAC-SHA256 签名（`host`/`date`/`authorization`，GMT 时间，300s 容差） | ✅ |
| 文本 base64 / gzip 编码上传 | ✅ |
| 轮询到 `task_status=5` 并解析 base64 下载链接 | ✅ |
| 音频下载 + 后缀自动对齐 encoding | ✅ |
| 超长文本自动切段（段落→句子边界）+ 并发 + 可选拼接 | ✅ |
| 拼音标注(`rhy=1`) 结果地址输出 | ✅ |
| 回调地址 `callback_url` / 自定义 `request_id` | ✅（透传） |
| 结构化 JSON 输出 + 错误码提示 + 非 0 退出码 | ✅ |

详细参数见 `SKILL.md`，逐项字段说明见 `references/api.md`。
