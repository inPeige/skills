#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""讯飞开放平台「长文本语音合成」(Long Text TTS) 命令行工具。

接口文档: https://www.xfyun.cn/doc/tts/long_text_tts/API.html
接口地址: https://api-dx.xf-yun.com/v1/private/dts_create  (创建任务)
          https://api-dx.xf-yun.com/v1/private/dts_query   (查询任务)

只用 Python 标准库, 无第三方依赖。

用法示例:
    xfyun_tts.py check                       # 校验密钥 + 服务授权状态(不消耗额度)
    xfyun_tts.py voices                      # 打印发音人列表
    xfyun_tts.py synth -f book.txt -o out.mp3
    xfyun_tts.py synth --text "你好世界" -v x4_mingge -o hi.mp3
    xfyun_tts.py create -f book.txt          # 只创建任务, 打印 task_id
    xfyun_tts.py query <task_id> --download -o out.mp3
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

HOST = "api-dx.xf-yun.com"
CREATE_PATH = "/v1/private/dts_create"
QUERY_PATH = "/v1/private/dts_query"

# 官方文档给出的单次上限: 约 10 万字符 / 文本 0-1M
DEFAULT_MAX_CHARS = 90000
DEFAULT_POLL_INTERVAL = 10.0
DEFAULT_TIMEOUT = 3600.0

DEFAULT_VOICE = "x4_mingge"
DEFAULT_ENCODING = "lame"
DEFAULT_SAMPLE_RATE = 16000

# 音频编码 -> 文件后缀
ENCODING_SUFFIX = {
    "raw": ".pcm",
    "lame": ".mp3",
    "opus": ".opus",
    "opus-wb": ".opus",
    "speex-org-nb;8": ".spx",
    "speex-org-wb;8": ".spx",
    "speex;7": ".spx",
    "speex-wb;7": ".spx",
}

# 可以按字节直接拼接、拼完仍可播放的编码
BYTE_CONCATENABLE = {"raw", "lame"}

# 任务状态
TASK_STATUS = {
    "1": "任务创建成功",
    "2": "任务派发失败",
    "3": "任务处理中",
    "4": "任务处理失败",
    "5": "任务处理成功",
}

# 常见错误码 -> 可操作提示
ERROR_HINTS = {
    10313: "app_id 缺失或 app_id 与 api_key 不匹配：核对控制台的 AppID / APIKey 是否同属一个应用。",
    11200: (
        "licc limit：该应用没有可用的长文本语音合成授权/额度。"
        "请到 https://console.xfyun.cn/services/long_text 领取免费额度或购买套餐；"
        "发音人(x4_/x5_ 系列)也可能需要在控制台单独开通。"
    ),
    10163: "参数校验失败：按 message 指出的字段修正请求参数(常见是 vcn 不在允许列表内)。",
    10165: "参数校验失败：检查 header/parameter/payload 结构。",
    13001: "task not found：task_id 不存在或已过期(音频/任务在云端仅保留 7 天)。",
    11201: "licc limit：授权许可不足，同 11200。",
}

# 文档附录中的发音人表
VOICES_DOCUMENTED = [
    ("x4_yeting", "希涵", "女", "中文/普通话", "游戏影视解说"),
    ("x4_guanshan", "关山-专题", "男", "中文/普通话", "专题片纪录片"),
    ("x4_pengfei", "小鹏", "男", "中文/普通话", "新闻播报"),
    ("x4_qianxue", "千雪", "女", "中文/普通话", "阅读听书"),
    ("x4_lingbosong", "聆伯松-老年男声", "男", "中文/普通话", "阅读听书"),
    ("x4_xiuying", "秀英-老年女声", "女", "中文/普通话", "阅读听书"),
    ("x4_mingge", "明哥", "男", "中文/普通话", "阅读听书"),
    ("x4_doudou", "豆豆", "男", "中文/男童", "阅读听书"),
    ("x4_lingxiaoshan_profnews", "聆小珊", "女", "中文/普通话", "新闻播报"),
    ("x4_xiaoguo", "小果", "女", "中文/普通话", "新闻播报"),
    ("x4_xiaozhong", "小忠", "男", "中文/普通话", "新闻播报"),
    ("x4_yezi", "小露", "女", "中文/普通话", "通用场景"),
    ("x4_chaoge", "超哥", "男", "中文/普通话", "新闻播报"),
    ("x4_feidie", "飞碟哥", "男", "中文/普通话", "游戏影视解说"),
    ("x4_lingfeihao_upbeatads", "聆飞皓-广告", "男", "中文/普通话", "直播广告"),
    ("x4_wangqianqian", "嘉欣", "女", "中文/普通话", "直播广告"),
    ("x4_lingxiaozhen_eclives", "聆小臻", "女", "中文/普通话", "直播广告"),
]

# 服务端 schema 实际接受的完整 vcn 列表(含文档未列出的)
VOICES_ACCEPTED = [
    "x3_xiaoyue",
    "x4_EnUs_Catherine_profnews",
    "x5_lingfeizhe",
    "x5_lingxiaoxue",
    "x4_lingfeihong_document",
    "x4_lingfeichen_assist",
    "x4_pengfei",
    "x4_yeting",
    "x4_qianxue",
    "x4_guanshan",
    "x4_lingxiaoqi_assist",
    "x4_lingfeihong_document_n",
    "x4_lingbosong",
    "x4_xiuying",
    "x4_mingge",
    "x4_doudou",
    "x4_lingxiaoshan_profnews",
    "x4_xiaoguo",
    "x4_xiaozhong",
    "x4_yezi",
    "x4_chaoge",
    "x4_feidie",
    "x4_lingfeihao_upbeatads",
    "x4_wangqianqian",
    "x4_lingxiaozhen_eclives",
]


# --------------------------------------------------------------------------
# 错误
# --------------------------------------------------------------------------


class XfyunError(RuntimeError):
    """一次接口调用失败(带平台错误码)。"""

    def __init__(self, message, code=None, sid=None, http_status=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.sid = sid
        self.http_status = http_status

    @property
    def hint(self):
        if self.code in ERROR_HINTS:
            return ERROR_HINTS[self.code]
        return None

    def to_dict(self):
        out = {"ok": False, "error": self.message}
        if self.code is not None:
            out["error_code"] = self.code
        if self.sid:
            out["sid"] = self.sid
        if self.http_status:
            out["http_status"] = self.http_status
        hint = self.hint
        if hint:
            out["hint"] = hint
        return out


def log(msg):
    """人类可读的进度信息一律走 stderr, stdout 只留结构化结果。"""
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# 凭据
# --------------------------------------------------------------------------

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(SKILL_ROOT, "config.json")


class Credentials:
    def __init__(self, app_id, api_key, api_secret, source):
        self.app_id = app_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.source = source


def load_credentials(args):
    """优先级: 命令行参数 > 环境变量 > 配置文件。"""
    app_id = getattr(args, "app_id", None)
    api_key = getattr(args, "api_key", None)
    api_secret = getattr(args, "api_secret", None)
    if app_id and api_key and api_secret:
        return Credentials(app_id, api_key, api_secret, "cli")

    env_id = os.environ.get("XFYUN_APP_ID")
    env_key = os.environ.get("XFYUN_API_KEY")
    env_secret = os.environ.get("XFYUN_API_SECRET")
    if not (app_id or api_key or api_secret) and env_id and env_key and env_secret:
        return Credentials(env_id, env_key, env_secret, "env")

    config_path = (
        getattr(args, "config", None)
        or os.environ.get("XFYUN_TTS_CONFIG")
        or DEFAULT_CONFIG_PATH
    )
    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise XfyunError(f"读取配置文件失败 {config_path}: {exc}")

    creds = Credentials(
        app_id or env_id or data.get("app_id"),
        api_key or env_key or data.get("api_key"),
        api_secret or env_secret or data.get("api_secret"),
        f"config:{config_path}",
    )
    missing = [
        name
        for name, value in (
            ("app_id", creds.app_id),
            ("api_key", creds.api_key),
            ("api_secret", creds.api_secret),
        )
        if not value
    ]
    if missing:
        raise XfyunError(
            "缺少凭据: "
            + ", ".join(missing)
            + f"。请设置环境变量 XFYUN_APP_ID/XFYUN_API_KEY/XFYUN_API_SECRET, "
            + f"或写入 {DEFAULT_CONFIG_PATH}。"
        )
    return creds


# --------------------------------------------------------------------------
# 鉴权 + HTTP
# --------------------------------------------------------------------------


def build_auth_url(path, creds):
    """按官方规则生成带鉴权参数的 URL。

    signature_origin = "host: $host\\ndate: $date\\nPOST $path HTTP/1.1"
    signature        = base64(hmac_sha256(signature_origin, APISecret))
    authorization    = base64('api_key="..", algorithm="hmac-sha256",
                               headers="host date request-line", signature=".."')
    """
    date = formatdate(timeval=None, localtime=False, usegmt=True)  # RFC1123 GMT
    signature_origin = f"host: {HOST}\ndate: {date}\nPOST {path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(
            creds.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    authorization_origin = (
        f'api_key="{creds.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    params = {
        "host": HOST,
        "date": date,
        "authorization": base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8"),
    }
    return f"https://{HOST}{path}?" + urllib.parse.urlencode(params)


def call_api(path, body, creds, timeout=60):
    """POST 一个 JSON body, 返回解析后的 dict; 失败抛 XfyunError。"""
    url = build_auth_url(path, creds)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise XfyunError(f"网络请求失败: {exc.reason}")

    try:
        parsed = json.loads(raw)
    except ValueError:
        raise XfyunError(f"响应不是合法 JSON (HTTP {status}): {raw[:500]}", http_status=status)

    header = parsed.get("header") or {}
    code = header.get("code")
    if code not in (0, "0"):
        # 鉴权层失败时没有 header.code, 只有顶层 message
        message = header.get("message") or parsed.get("message") or raw[:300]
        raise XfyunError(
            message,
            code=code,
            sid=header.get("sid"),
            http_status=status,
        )
    return parsed


# --------------------------------------------------------------------------
# 文本切分
# --------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;…])|(?<=\.\s)|(?<=\n)")


def split_text(text, max_chars):
    """把超长文本切成不超过 max_chars 的片段, 优先在段落/句子边界切。"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = ""

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in text.split("\n"):
        block = para + "\n"
        if len(block) > max_chars:
            flush()
            for piece in _split_long_block(block, max_chars):
                chunks.append(piece)
            continue
        if len(current) + len(block) > max_chars:
            flush()
        current += block
    flush()
    return [c for c in chunks if c.strip()]


def _split_long_block(block, max_chars):
    pieces = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(block):
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current.strip():
                pieces.append(current.strip())
                current = ""
            for i in range(0, len(sentence), max_chars):
                pieces.append(sentence[i : i + max_chars].strip())
            continue
        if len(current) + len(sentence) > max_chars:
            pieces.append(current.strip())
            current = ""
        current += sentence
    if current.strip():
        pieces.append(current.strip())
    return [p for p in pieces if p]


# --------------------------------------------------------------------------
# 业务封装
# --------------------------------------------------------------------------


def encode_text(text, use_gzip=False):
    raw = text.encode("utf-8")
    if use_gzip:
        raw = gzip.compress(raw)
    return base64.b64encode(raw).decode("ascii")


def build_create_body(
    creds,
    text,
    voice=DEFAULT_VOICE,
    language="zh",
    speed=50,
    volume=50,
    pitch=50,
    rhy=0,
    encoding=DEFAULT_ENCODING,
    sample_rate=DEFAULT_SAMPLE_RATE,
    channels=1,
    bit_depth=16,
    callback_url=None,
    request_id=None,
    use_gzip=False,
):
    body = {
        "header": {"app_id": creds.app_id},
        "parameter": {
            "dts": {
                "vcn": voice,
                "language": language,
                "speed": speed,
                "volume": volume,
                "pitch": pitch,
                "rhy": rhy,
                "audio": {
                    "encoding": encoding,
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "bit_depth": bit_depth,
                },
                "pybuf": {"encoding": "utf8", "compress": "raw", "format": "plain"},
            }
        },
        "payload": {
            "text": {
                "encoding": "utf8",
                "compress": "gzip" if use_gzip else "raw",
                "format": "plain",
                "text": encode_text(text, use_gzip),
            }
        },
    }
    if callback_url:
        body["header"]["callback_url"] = callback_url
    if request_id:
        body["header"]["request_id"] = request_id
    return body


def create_task(creds, text, **kwargs):
    """创建合成任务, 返回 (task_id, raw_response)。"""
    body = build_create_body(creds, text, **kwargs)
    resp = call_api(CREATE_PATH, body, creds)
    header = resp.get("header", {})
    task_id = header.get("task_id")
    if not task_id:
        raise XfyunError("创建任务成功但未返回 task_id", sid=header.get("sid"))
    return task_id, resp


def query_task(creds, task_id):
    body = {"header": {"app_id": creds.app_id, "task_id": task_id}}
    return call_api(QUERY_PATH, body, creds)


def wait_for_task(creds, task_id, interval=DEFAULT_POLL_INTERVAL, timeout=DEFAULT_TIMEOUT, quiet=False):
    """轮询直到任务完成, 返回最终的 query 响应。"""
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        resp = query_task(creds, task_id)
        header = resp.get("header", {})
        status = str(header.get("task_status", ""))
        if status == "5":
            return resp
        if status in ("2", "4"):
            raise XfyunError(
                f"任务失败: {TASK_STATUS.get(status, status)}",
                code=header.get("code"),
                sid=header.get("sid"),
            )
        if not quiet:
            log(f"[{attempt}] 任务状态 {status} ({TASK_STATUS.get(status, '未知')}), {interval:.0f}s 后重试…")
        if time.monotonic() + interval > deadline:
            raise XfyunError(f"等待任务超时 (>{timeout:.0f}s), 可稍后用 query {task_id} 继续查询")
        time.sleep(interval)
        # 长任务加长轮询间隔, 上限 30s
        interval = min(interval * 1.5, 30.0)


def extract_result(resp):
    """从成功的 query 响应里取出音频链接与拼音链接。"""
    payload = resp.get("payload") or {}
    audio_obj = payload.get("audio") or {}
    audio_b64 = audio_obj.get("audio")
    if not audio_b64:
        raise XfyunError("任务已完成但响应中没有音频数据")
    audio_url = base64.b64decode(audio_b64).decode("utf-8", "replace").strip()

    pybuf_url = None
    pybuf_obj = payload.get("pybuf") or {}
    if pybuf_obj.get("text"):
        pybuf_url = base64.b64decode(pybuf_obj["text"]).decode("utf-8", "replace").strip()

    return {
        "audio_url": audio_url,
        "pybuf_url": pybuf_url,
        "encoding": audio_obj.get("encoding"),
        "sample_rate": audio_obj.get("sample_rate"),
        "channels": audio_obj.get("channels"),
        "bit_depth": audio_obj.get("bit_depth"),
    }


def download(url, dest, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": "xfyun-long-tts-skill/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
            total = 0
            while True:
                block = resp.read(1 << 16)
                if not block:
                    break
                fh.write(block)
                total += len(block)
    except urllib.error.URLError as exc:
        raise XfyunError(f"下载音频失败: {exc}")
    return total


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------


def read_text_arg(args):
    if args.text is not None and args.file is not None:
        raise XfyunError("--text 与 --file 只能二选一")
    if args.text is not None:
        return args.text
    if args.file is not None:
        if args.file == "-":
            return sys.stdin.read()
        with open(args.file, encoding="utf-8") as fh:
            return fh.read()
    data = sys.stdin.read()
    if not data.strip():
        raise XfyunError("没有输入文本: 用 --text、--file 或通过 stdin 传入")
    return data


def add_common_voice_flags(p):
    p.add_argument("-v", "--voice", default=DEFAULT_VOICE, help=f"发音人 vcn (默认 {DEFAULT_VOICE})")
    p.add_argument("--language", default="zh", choices=["zh", "en"], help="合成文本语言, 默认 zh")
    p.add_argument("--speed", type=int, default=50, help="语速 [0-100], 默认 50")
    p.add_argument("--volume", type=int, default=50, help="音量 [0-100], 默认 50")
    p.add_argument("--pitch", type=int, default=50, help="语调 [0-100], 默认 50")
    p.add_argument("--rhy", type=int, default=0, choices=[0, 1], help="1=同时返回拼音标注, 默认 0")
    p.add_argument(
        "-e",
        "--encoding",
        default=DEFAULT_ENCODING,
        help="音频编码: raw(pcm)|lame(mp3)|opus|opus-wb|speex-org-nb;8|speex-org-wb;8|speex;7|speex-wb;7, 默认 lame",
    )
    p.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE, help="采样率 16000/8000/24000, 默认 16000")
    p.add_argument("--channels", type=int, default=1, help="声道数 1/2, 默认 1")
    p.add_argument("--bit-depth", type=int, default=16, help="位深 8/16, 默认 16")
    p.add_argument("--callback-url", help="任务完成回调地址")
    p.add_argument("--request-id", help="客户端任务标记 id (<=64 字符)")
    p.add_argument("--gzip-text", action="store_true", help="文本用 gzip 压缩后再 base64 上传")


def cmd_check(args):
    """校验凭据与授权状态: 只调用 dts_query 且用一个不存在的 task_id。

    鉴权通过时服务端返回 13001 (task not found) —— 这证明确名与密钥都正确,
    同时不消耗任何合成额度。
    """
    creds = load_credentials(args)
    probe_id = "000000000000000000000000"
    result = {"ok": True, "credentials_source": creds.source, "app_id": creds.app_id, "auth": "unknown"}

    try:
        query_task(creds, probe_id)
        result["auth"] = "ok"
        result["license"] = "ok (未触发授权错误)"
    except XfyunError as exc:
        if exc.code == 13001:
            result["auth"] = "ok"
            result["license"] = "unknown"
            result["note"] = (
                "签名校验通过(服务端返回 task not found 属于预期结果)。"
                "但授权/额度状态需实际发一次合成请求才能确认, "
                "可用 `check --deep` 或直接跑 synth。"
            )
        elif exc.code in (11200, 11201):
            result["auth"] = "ok"
            result["license"] = "missing"
            result["note"] = ERROR_HINTS[exc.code]
        elif exc.code == 10313:
            result["auth"] = "failed"
            result["ok"] = False
            result["note"] = ERROR_HINTS[10313]
        else:
            result["auth"] = "failed"
            result["ok"] = False

    if getattr(args, "deep", False):
        try:
            task_id, _ = create_task(
                creds,
                "授权检查。",
                voice=args.voice,
                encoding="lame",
                sample_rate=16000,
                channels=1,
                bit_depth=16,
            )
            result["license"] = "ok"
            result["deep_task_id"] = task_id
        except XfyunError as exc:
            result["license"] = "missing" if exc.code in (11200, 11201) else "error"
            result["deep_error"] = exc.message
            result["deep_error_code"] = exc.code
            result["ok"] = False
            if exc.hint:
                result["note"] = exc.hint

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def cmd_voices(args):
    payload = {
        "accepted_vcn": VOICES_ACCEPTED,
        "documented": [
            {
                "vcn": v,
                "name": n,
                "gender": g,
                "language": lang,
                "style": s,
            }
            for v, n, g, lang, s in VOICES_DOCUMENTED
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_create(args):
    creds = load_credentials(args)
    text = read_text_arg(args)
    task_id, resp = create_task(
        creds,
        text,
        voice=args.voice,
        language=args.language,
        speed=args.speed,
        volume=args.volume,
        pitch=args.pitch,
        rhy=args.rhy,
        encoding=args.encoding,
        sample_rate=args.sample_rate,
        channels=args.channels,
        bit_depth=args.bit_depth,
        callback_url=args.callback_url,
        request_id=args.request_id,
        use_gzip=args.gzip_text,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "task_id": task_id,
                "chars": len(text),
                "sid": resp.get("header", {}).get("sid"),
                "task_status": "1",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_query(args):
    creds = load_credentials(args)
    resp = query_task(creds, args.task_id)
    header = resp.get("header", {})
    status = str(header.get("task_status", ""))
    out = {
        "ok": True,
        "task_id": args.task_id,
        "task_status": status,
        "task_status_meaning": TASK_STATUS.get(status),
    }
    if status == "5":
        info = extract_result(resp)
        out.update({k: v for k, v in info.items() if k != "pybuf_url"})
        if info.get("pybuf_url"):
            out["pybuf_url"] = info["pybuf_url"]
        if args.download:
            dest = args.out or args.task_id + ENCODING_SUFFIX.get(info.get("encoding") or "", ".bin")
            out["bytes"] = download(info["audio_url"], dest)
            out["output"] = os.path.abspath(dest)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _synth_one(creds, text, args, index=None, total=None, request_id=None):
    task_id, _ = create_task(
        creds,
        text,
        voice=args.voice,
        language=args.language,
        speed=args.speed,
        volume=args.volume,
        pitch=args.pitch,
        rhy=args.rhy,
        encoding=args.encoding,
        sample_rate=args.sample_rate,
        channels=args.channels,
        bit_depth=args.bit_depth,
        callback_url=args.callback_url,
        request_id=request_id,
        use_gzip=args.gzip_text,
    )
    label = f"[{index}/{total}] " if index else ""
    log(f"{label}已创建任务 {task_id} ({len(text)} 字), 等待合成…")
    resp = wait_for_task(
        creds,
        task_id,
        interval=args.interval,
        timeout=args.timeout,
        quiet=args.quiet,
    )
    info = extract_result(resp)
    info["task_id"] = task_id
    info["chars"] = len(text)
    return info


def cmd_synth(args):
    creds = load_credentials(args)
    text = read_text_arg(args)
    if not text.strip():
        raise XfyunError("输入文本为空")

    suffix = ENCODING_SUFFIX.get(args.encoding, ".bin")
    chunks = split_text(text, args.max_chars)

    if args.dry_run:
        body = build_create_body(
            creds,
            chunks[0],
            voice=args.voice,
            language=args.language,
            speed=args.speed,
            volume=args.volume,
            pitch=args.pitch,
            rhy=args.rhy,
            encoding=args.encoding,
            sample_rate=args.sample_rate,
            channels=args.channels,
            bit_depth=args.bit_depth,
            callback_url=args.callback_url,
            request_id=args.request_id,
            use_gzip=args.gzip_text,
        )
        body["payload"]["text"]["text"] = f"<base64 of {len(chunks[0])} chars>"
        print(
            json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "auth_url": build_auth_url(CREATE_PATH, creds),
                    "total_chars": len(text),
                    "chunks": len(chunks),
                    "chunk_sizes": [len(c) for c in chunks],
                    "body": body,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    out_base = args.out
    if out_base is None:
        out_base = "output" + suffix
    if len(chunks) > 1 and not out_base.endswith(suffix):
        out_base += suffix

    if not args.quiet:
        log(f"文本 {len(text)} 字, 切分为 {len(chunks)} 段, 编码 {args.encoding}, 发音人 {args.voice}")

    results = []
    started = time.monotonic()

    if len(chunks) == 1:
        info = _synth_one(creds, chunks[0], args, request_id=args.request_id)
        dest = out_base if out_base.endswith(suffix) else out_base + suffix
        info["bytes"] = download(info["audio_url"], dest)
        info["output"] = os.path.abspath(dest)
        results.append(info)
    else:
        if args.request_id and not args.quiet:
            log("多段合成会拆成多个任务, 忽略 --request-id (它要求全局唯一)")

        def work(item):
            index, chunk = item
            return _synth_one(creds, chunk, args, index, len(chunks))

        if args.concurrency > 1:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                results = list(pool.map(work, list(enumerate(chunks, 1))))
        else:
            results = [work(item) for item in enumerate(chunks, 1)]

        stem, ext = os.path.splitext(out_base)
        if not ext:
            ext = suffix
        for i, info in enumerate(results, 1):
            dest = f"{stem}_part{i:02d}{ext}"
            info["bytes"] = download(info["audio_url"], dest)
            info["output"] = os.path.abspath(dest)

        if args.concat:
            if args.encoding in BYTE_CONCATENABLE:
                with open(out_base, "wb") as out_fh:
                    for info in results:
                        with open(info["output"], "rb") as part_fh:
                            while True:
                                block = part_fh.read(1 << 16)
                                if not block:
                                    break
                                out_fh.write(block)
                log(f"已按字节拼接为 {out_base} (仅 {args.encoding} 支持可靠拼接)")
            else:
                log(f"警告: 编码 {args.encoding} 不能可靠地按字节拼接, 已跳过 --concat")

    out = {
        "ok": True,
        "chars": len(text),
        "voice": args.voice,
        "encoding": args.encoding,
        "sample_rate": args.sample_rate,
        "chunks": len(chunks),
        "elapsed_sec": round(time.monotonic() - started, 1),
        "parts": [
            {
                "task_id": r["task_id"],
                "chars": r["chars"],
                "audio_url": r["audio_url"],
                "output": r["output"],
                "bytes": r["bytes"],
                **({"pybuf_url": r["pybuf_url"]} if r.get("pybuf_url") else {}),
            }
            for r in results
        ],
    }
    if len(results) == 1:
        out.update(out["parts"][0])
    if args.concat and len(chunks) > 1 and args.encoding in BYTE_CONCATENABLE:
        out["concatenated"] = os.path.abspath(out_base)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="xfyun_tts.py",
        description="讯飞长文本语音合成 (Long Text TTS) CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="凭据优先级: 命令行 > 环境变量(XFYUN_APP_ID/XFYUN_API_KEY/XFYUN_API_SECRET) > config.json",
    )
    parser.add_argument("--app-id", help="AppID")
    parser.add_argument("--api-key", help="APIKey")
    parser.add_argument("--api-secret", help="APISecret")
    parser.add_argument("--config", help=f"凭据配置文件路径 (默认 {DEFAULT_CONFIG_PATH})")

    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="校验密钥与服务授权状态(不消耗额度)")
    p_check.add_argument("--deep", action="store_true", help="额外发一次真实的合成请求以确认授权")
    p_check.add_argument("-v", "--voice", default=DEFAULT_VOICE)
    p_check.set_defaults(func=cmd_check)

    p_voices = sub.add_parser("voices", help="列出可用发音人")
    p_voices.set_defaults(func=cmd_voices)

    p_create = sub.add_parser("create", help="只创建合成任务")
    p_create.add_argument("-t", "--text")
    p_create.add_argument("-f", "--file")
    add_common_voice_flags(p_create)
    p_create.set_defaults(func=cmd_create)

    p_query = sub.add_parser("query", help="查询任务状态/结果")
    p_query.add_argument("task_id")
    p_query.add_argument("--download", action="store_true", help="成功后立即下载音频")
    p_query.add_argument("-o", "--out", help="下载保存路径")
    p_query.set_defaults(func=cmd_query)

    p_synth = sub.add_parser("synth", help="创建任务 -> 轮询 -> 下载音频 (一步到位)")
    p_synth.add_argument("-t", "--text", help="待合成文本")
    p_synth.add_argument("-f", "--file", help="文本文件路径, 用 - 表示 stdin")
    p_synth.add_argument("-o", "--out", help="输出文件路径 (默认 output.<后缀>)")
    add_common_voice_flags(p_synth)
    p_synth.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help=f"单任务最大字符数, 默认 {DEFAULT_MAX_CHARS}")
    p_synth.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL, help="首次轮询间隔秒数, 默认 10")
    p_synth.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="等待总超时秒数, 默认 3600")
    p_synth.add_argument("--concurrency", type=int, default=1, help="多段并发的任务数, 默认 1(串行)")
    p_synth.add_argument("--concat", action="store_true", help="多段时把结果按字节拼接成一个文件(mp3/pcm)")
    p_synth.add_argument("--dry-run", action="store_true", help="只打印将要发送的请求, 不真正调用")
    p_synth.add_argument("-q", "--quiet", action="store_true", help="不打印进度")
    p_synth.set_defaults(func=cmd_synth)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except XfyunError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "已中断"}, ensure_ascii=False))
        return 130
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "error": f"文件不存在: {exc.filename}"}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
