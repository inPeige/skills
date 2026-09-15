#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_guide.py — 攻略文档 → 结构化 blocks（文字/图片按原文顺序）+ 本地图片

纯标准库；只需要 python3。SPA 站点自动回退到无头 Chrome 渲染。

用法:
  python3 fetch_guide.py <url|文件|-> [选项]

选项:
  -o/--out DIR        输出目录（默认 ./guide）
  --selector SEL      指定正文容器（如 .c-feed-article__content / #content / article）
  --render auto|always|never   无头渲染策略（默认 auto：正文太短才渲染）
  --chrome PATH       指定 Chrome/Chromium 可执行文件
  --min-chars N       判定"正文太短"的阈值（默认 400）
  --no-images         只抓文字，不下载图片
  --max-images N      最多下载多少张（默认 60）
  --max-bytes N       单图字节上限（默认 12MB）
  --timeout N         单请求超时秒（默认 30）
  --title T           覆盖标题
  -q/--quiet          安静模式

输出（全部落在 --out）:
  guide.json    机器可读：meta + blocks（有序 text/image）+ images
  guide.md      人/AI 可读：正文顺序，图片以 ![](images/xx.png) 内联
  images/       下载的原图
  fetch.log     抓取日志（选了什么容器、用了什么渲染方式）

stdout 输出一行 JSON 摘要（路径 / 统计 / 警告）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# DOM 遍历（link_text_len / walk_top / walk）是递归的，深嵌套或大量未闭合标签
# 会把默认 1000 层用光。抬高上限，并在 main 里兜住 RecursionError。
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

VOID = {"img", "br", "hr", "meta", "link", "input", "source", "area",
        "base", "col", "embed", "param", "track", "wbr"}

# 这些标签整棵子树丢掉
DROP_TAGS = {"script", "style", "noscript", "svg", "canvas", "iframe",
             "form", "select", "textarea", "button", "video", "audio",
             "nav", "header", "footer", "aside"}

# 这些标签的内容单独成块
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li",
              "blockquote", "figcaption", "td", "th", "dd", "dt", "pre"}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# 明显是头像/图标/二维码的图片路径特征
JUNK_IMG_RE = re.compile(
    r"(avatar|icon|logo|qrcode|qr_|/sprite|placeholder|loading|blank\.|"
    r"emoji|badge|stamp|/btn|button_)", re.I)

# pick_container 往外递的警告（未命中的 selector 之类）
selectors_warned: list = []

SKIP_IMG_PARAM_RE = re.compile(r"[?&](imageView|thumbnail|tostatic|quality|"
                               r"x-oss-process|imageMogr2)[^&]*", re.I)


# --------------------------------------------------------------------------
# 极简 DOM
# --------------------------------------------------------------------------
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text", "idx")

    def __init__(self, tag, attrs=None, parent=None, text=None, idx=0):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = text
        self.idx = idx

    def cls(self):
        return self.attrs.get("class", "") or ""

    def get(self, name, default=""):
        return self.attrs.get(name, default)


class DomParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]
        self.skip_depth = 0
        self.counter = 0

    def _new(self, tag, attrs):
        self.counter += 1
        return Node(tag, attrs, self.stack[-1], idx=self.counter)

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag in ("script", "style"):
                self.skip_depth += 1
            return
        if tag in ("script", "style"):
            self.skip_depth = 1
            return
        node = self._new(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        if self.skip_depth:
            return
        self.stack[-1].children.append(self._new(tag, dict(attrs)))

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag in ("script", "style"):
                self.skip_depth -= 1
            return
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.skip_depth or not data.strip():
            return
        self.counter += 1
        self.stack[-1].children.append(
            Node(None, None, self.stack[-1], text=data, idx=self.counter))


# --------------------------------------------------------------------------
# 度量与容器选择
# --------------------------------------------------------------------------
def own_text_len(node, cap=200000):
    """节点内纯文本长度（不递归上限保护）"""
    if node.tag is None:
        return len(node.text or "")
    if node.tag in DROP_TAGS:
        return 0
    total = 0
    stack = list(node.children)
    while stack:
        n = stack.pop()
        if n.tag is None:
            total += len(n.text or "")
            if total > cap:
                return cap
        elif n.tag not in DROP_TAGS:
            stack.extend(n.children)
    return total


def link_text_len(node):
    if node.tag == "a":
        return own_text_len(node)
    total = 0
    for c in node.children:
        if c.tag is not None:
            total += link_text_len(c)
    return total


def iter_elements(root):
    stack = [root]
    while stack:
        n = stack.pop()
        if n.tag is not None:
            yield n
            stack.extend(n.children)


def match_selector(node, sel):
    """支持 tag / .class / #id / tag.class / tag#id（不递归组合）"""
    sel = sel.strip()
    if not sel:
        return False
    m = re.match(r"^([a-zA-Z0-9]+)?([.#][^.#]+)?$", sel)
    if not m:
        return False
    tag, suffix = m.group(1), m.group(2)
    if tag and (node.tag or "").lower() != tag.lower():
        return False
    if suffix:
        if suffix[0] == ".":
            if suffix[1:] not in node.cls().split():
                return False
        else:
            if node.get("id") != suffix[1:]:
                return False
    return True


def pick_container(root, selector=None):
    """返回 (容器节点, 说明, 是否由用户显式指定)"""
    selectors_warned.clear()
    if selector:
        hits = [n for n in iter_elements(root)
                if n.tag not in ("html", "body") and match_selector(n, selector)]
        if hits:
            best = max(hits, key=own_text_len)
            return best, f"selector {selector}（{len(hits)} 个命中，取最长）", True
        # 未命中不能悄悄降级：用户以为选准了，实际抓到的是整页（含导航/推荐位）
        selectors_warned.append(
            f"--selector「{selector}」未命中任何元素，已回退到全文。"
            f"支持的选择器只有 tag / .class / #id / tag.class（不支持空格、逗号、"
            f"组合选择器）；确认元素 class 拼写。")
        return root, f"selector {selector} 未命中，回退全文", False

    # 优先 <article> / <main>
    for tag in ("article", "main"):
        cands = [n for n in iter_elements(root) if n.tag == tag]
        if cands:
            best = max(cands, key=own_text_len)
            if own_text_len(best) >= 80:
                return best, f"<{tag}> 语义标签", False

    # 密度打分
    best, best_score = None, -1.0
    for n in iter_elements(root):
        if n.tag not in ("div", "section", "td", "article"):
            continue
        tl = own_text_len(n)
        if tl < 60:
            continue
        ll = link_text_len(n)
        # 链接密度惩罚：导航/推荐位成片是链接
        density = ll / max(tl, 1)
        score = tl * (1.0 - min(density, 1.0)) - 4.0 * density * tl
        # 结构奖：块级子元素多，说明是正文而不是一坨
        blocks = sum(1 for c in n.children
                     if c.tag in BLOCK_TAGS or c.tag in ("div", "section"))
        score += min(blocks, 60) * 3.0
        if score > best_score:
            best, best_score = n, score
    if best is None:
        return root, "密度打分失败，回退全文", False

    # 覆盖度守卫：小文档里如果最佳候选只占全文一小部分，说明这页本身就是
    # 一段片段（另存/摘录的 <ul>/<table>），整篇才是正文——否则只会抓到
    # 其中的一个 <li>。大文档不做这个回退，那里 body 会裹进导航和推荐位。
    total = own_text_len(root)
    if total < 5000:
        best_len = own_text_len(best)
        if best_len < 0.6 * total:
            return root, (f"覆盖度守卫（最佳候选 {best_len}/{total} 字符，"
                          f"按全文处理）"), False

    return best, f"密度打分（score={best_score:.0f}）", False


# --------------------------------------------------------------------------
# 有序块抽取
# --------------------------------------------------------------------------
def norm_text(s):
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", s).strip()


def collect_images(node, out, base_url, keep_junk=False):
    """把 node 子树里的 img 按文档顺序抽出来（用于块内）"""
    def walk(n):
        for c in n.children:
            if c.tag is None:
                continue
            if c.tag in DROP_TAGS:
                continue
            if c.tag == "img":
                info = image_info(c, base_url)
                if info and (keep_junk or not looks_junk(c, info)):
                    out.append(info)
            else:
                walk(c)
    walk(node)


def image_info(img, base_url):
    """从 <img> 取最佳 URL + 尺寸提示"""
    src_attr = (img.get("src") or "").strip()
    raw = None
    for key in ("data-origin", "data-original", "data-src", "data-lazy-src",
                "data-actualsrc", "src"):
        v = img.get(key)
        if v and not v.startswith("data:"):
            raw = v
            break
    from_srcset = False
    if not raw:
        # 只有 srcset 的图（<img srcset> / <picture><source srcset>）不能直接丢，
        # 取候选里声明宽度最大的那个
        cand = pick_srcset(img.get("srcset")) or pick_srcset(img.get("data-srcset"))
        if cand:
            raw, from_srcset = cand, True
    if not raw:
        return None
    raw = raw.strip()
    cand = raw
    # 常见站点：query 里带缩略图参数，去掉拿原图
    if "166.net" in cand or "126.net" in cand or "netease" in cand:
        cand = SKIP_IMG_PARAM_RE.sub("", cand)
    cand = urllib.parse.urljoin(base_url or "", cand)
    if cand.startswith("//"):
        cand = "https:" + cand
    try:
        w = int(re.sub(r"\D", "", img.get("width") or "0") or 0)
        h = int(re.sub(r"\D", "", img.get("height") or "0") or 0)
    except ValueError:
        w = h = 0
    return {"src": cand, "raw": raw, "origin": img.get("data-origin") or "",
            "src_attr": src_attr, "from_srcset": from_srcset,
            "alt": norm_text(img.get("alt") or ""),
            "declared_w": w, "declared_h": h}


def pick_srcset(value):
    """从 srcset 里挑声明宽度最大的候选 URL"""
    if not value:
        return None
    best, best_w = None, -1
    for part in value.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0
        if len(bits) > 1:
            m = re.match(r"^(\d+)w$", bits[1])
            if m:
                w = int(m.group(1))
        if w > best_w:
            best, best_w = bits[0], w
    return best


def looks_junk(img, info):
    src = info["src"]
    if src.lower().endswith(".svg"):
        return True
    if JUNK_IMG_RE.search(src):
        return True
    # 明确标了很小的缩略图（头像）
    if info["declared_w"] and info["declared_w"] < 120 and info["declared_h"] and info["declared_h"] < 120:
        return True
    # URL 里的缩略图参数说明这是头像/小图（如 thumbnail=100y100）。
    # 必须同时看原始 src 属性：166.net 的正文图带 data-origin（干净 URL），
    # 只查 raw 会漏掉——而头像恰恰就长这样。
    for probe in (info.get("raw") or "", info.get("src_attr") or ""):
        m = re.search(r"thumbnail=(\d+)y(\d+)", probe, re.I)
        if m and max(int(m.group(1)), int(m.group(2))) <= 200:
            return True
    return False


def extract_blocks(container, base_url, keep_junk=False):
    blocks = []

    def flush(buf, out):
        t = norm_text("".join(buf))
        if t:
            out.append({"type": "text", "text": t})
        buf.clear()

    def emit_block(node):
        """块级元素 → 按文档顺序返回块列表（返回值，不写全局）

        必须**返回**而不是直接 append 到 blocks：嵌套块（如 <li> 里的 <p>）
        要插在它出现的位置上，否则会出现"嵌套段落跑到父元素前面的文字之前"
        的错序，图片和句子的对应关系就假了。
        """
        local = []
        buf = []

        def flush_local():
            t = norm_text("".join(buf))
            if t:
                role = "heading" if node.tag in HEADING_TAGS else (
                    "list" if node.tag == "li" else "text")
                local.append({"type": "text", "text": t, "role": role})
            buf.clear()

        def walk(n):
            for c in n.children:
                if c.tag is None:
                    buf.append(c.text)
                elif c.tag in DROP_TAGS:
                    continue
                elif c.tag == "img":
                    flush_local()
                    info = image_info(c, base_url)
                    if info and (keep_junk or not looks_junk(c, info)):
                        local.append({"type": "image", "src": info["src"],
                                      "origin": info["origin"], "alt": info["alt"]})
                elif c.tag == "br":
                    flush_local()
                elif c.tag in BLOCK_TAGS:
                    flush_local()
                    local.extend(emit_block(c))  # 嵌套块：就地插入
                else:
                    walk(c)

        walk(node)
        flush_local()
        return local

    def walk_top(n):
        buf = []
        for c in n.children:
            if c.tag is None:
                buf.append(c.text)
            elif c.tag in DROP_TAGS:
                continue
            elif c.tag == "img":
                flush(buf, blocks)
                info = image_info(c, base_url)
                if info and (keep_junk or not looks_junk(c, info)):
                    blocks.append({"type": "image", "src": info["src"],
                                   "origin": info["origin"], "alt": info["alt"]})
            elif c.tag == "br":
                flush(buf, blocks)
            elif c.tag in BLOCK_TAGS:
                flush(buf, blocks)
                blocks.extend(emit_block(c))
            else:
                flush(buf, blocks)
                walk_top(c)
        flush(buf, blocks)

    walk_top(container)
    # 段落边界必须保留（emit_block 已把 inline 碎片合进同一块），
    # 这里只去掉相邻完全重复的块（站点常见的重排副本）
    out = []
    for b in blocks:
        if out and out[-1] == b:
            continue
        out.append(b)
    return out


# --------------------------------------------------------------------------
# 抓取与渲染
# --------------------------------------------------------------------------
def http_get(url, timeout=30, retries=2):
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                charset = r.headers.get_content_charset()
                return decode_html(raw, charset), r.geturl()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"抓取失败 {url}: {last}")


def decode_html(raw, charset=None):
    for enc in filter(None, [charset, "utf-8", "gb18030", "latin-1"]):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.I)


def sniff_charset(raw: bytes):
    """从字节里嗅探字符集：BOM > <meta charset> > content-type"""
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    head = raw[:4096]
    m = CHARSET_RE.search(head)
    if m:
        return m.group(1).decode("ascii", "ignore")
    m = re.search(rb"""content\s*=\s*["'][^"']*charset=([a-zA-Z0-9_\-]+)""",
                  head, re.I)
    if m:
        return m.group(1).decode("ascii", "ignore")
    return None


# 认得出来的块级标签；只要出现任意一个就按 HTML 处理，
# 否则 <ul>/<li>/<table> 这种"片段另存"会被当成纯文本、标签原样漏进 guide.md
HTML_TAG_RE = re.compile(
    r"<\s*(?:html|body|head|div|p|article|section|main|ul|ol|li|table|tr|td|th|"
    r"h[1-6]|blockquote|figure|img|span|a|br|dl|dt|dd|pre|code|form)\b", re.I)


def looks_like_html(text):
    return bool(HTML_TAG_RE.search(text))


def find_chrome(explicit=None):
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.environ.get("CHROME_PATH")
    if env and os.path.exists(env):
        return env
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome",
                 "msedge", "brave-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def render_with_chrome(url, chrome, timeout=90, log=print, profile=None):
    """无头 Chrome 渲染后 dump DOM

    两个实测踩过的坑（别改回去）：
    1) `--run-all-compositor-stages-before-draw` 和 `--virtual-time-budget` 同用会挂住不出 DOM。
    2) 默认**不要**传 `--user-data-dir`。指向一次性空目录时 macOS 上会卡住不返回
       （实测 >180s 无输出）；不传时同一页面 6s 出 457KB DOM。
       需要隔离 profile 时用 --chrome-profile 指定一个**已存在**的目录。
    """
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
           "--hide-scrollbars", "--disable-extensions", "--mute-audio",
           "--virtual-time-budget=15000",
           "--dump-dom", url]
    if profile:
        cmd.insert(1, f"--user-data-dir={profile}")
    log(f"[render] {chrome}{' profile=' + profile if profile else ''}")
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"无头渲染超时（{timeout}s）。若用了 --chrome-profile，去掉它再试；"
            f"或改用 --render never 并手动提供 HTML。")
    dom = p.stdout.decode("utf-8", "replace")
    if len(dom) < 500:
        raise RuntimeError(f"无头渲染输出过短（{len(dom)}B），stderr: "
                           f"{p.stderr.decode('utf-8', 'replace')[:300]}")
    return dom


# --------------------------------------------------------------------------
# 图片下载
# --------------------------------------------------------------------------
def probe_size(path):
    """纯 Python 读图片宽高：PNG / JPEG / GIF / WebP / BMP"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return w, h, "png"
            if head[:3] == b"GIF":
                w, h = struct.unpack("<HH", head[6:10])
                return w, h, "gif"
            if head[:2] == b"BM":
                w, h = struct.unpack("<ii", head[18:26])
                return abs(w), abs(h), "bmp"
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return webp_size(f)
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        break
                    if b != b"\xff":
                        continue
                    marker = f.read(1)
                    while marker == b"\xff":
                        marker = f.read(1)
                    m = marker[0]
                    if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h, "jpeg"
                    if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                        continue
                    ln = struct.unpack(">H", f.read(2))[0]
                    f.seek(ln - 2, 1)
    except Exception:  # noqa: BLE001
        pass
    return 0, 0, ""


def webp_size(f):
    f.seek(12)
    chunk = f.read(4)
    if chunk == b"VP8X":
        f.read(4)
        w = int.from_bytes(f.read(3), "little") + 1
        h = int.from_bytes(f.read(3), "little") + 1
        return w, h, "webp"
    if chunk == b"VP8 ":
        f.read(6)
        w = int.from_bytes(f.read(2), "little") & 0x3FFF
        h = int.from_bytes(f.read(2), "little") & 0x3FFF
        return w, h, "webp"
    if chunk == b"VP8L":
        f.read(5)
        b = f.read(4)
        bits = int.from_bytes(b, "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, "webp"
    return 0, 0, "webp"


EXT_BY_FMT = {"png": ".png", "jpeg": ".jpg", "gif": ".gif",
              "webp": ".webp", "bmp": ".bmp"}


def download_image(url, outdir, index, timeout=30, max_bytes=12 * 1024 * 1024,
                   referer=None):
    """下载单图，返回 dict 或 None"""
    headers = {"User-Agent": UA, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
    except Exception:  # noqa: BLE001
        return None
    if len(data) > max_bytes or len(data) < 512:
        return None

    tmp = os.path.join(outdir, f".tmp{index}")
    with open(tmp, "wb") as f:
        f.write(data)
    w, h, fmt = probe_size(tmp)
    if not fmt:
        os.remove(tmp)
        return None
    if w and h and w < 100 and h < 100:  # 图标级
        os.remove(tmp)
        return None
    digest = hashlib.md5(data).hexdigest()[:8]
    name = f"{index:02d}_{digest}{EXT_BY_FMT.get(fmt, '.img')}"
    final = os.path.join(outdir, name)
    os.replace(tmp, final)
    return {"file": name, "path": f"images/{name}", "bytes": len(data),
            "w": w, "h": h, "format": fmt, "digest": digest}


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_markdown(meta, blocks, images):
    lines = [f"# {meta['title']}", ""]
    if meta.get("source"):
        lines += [f"> 来源：{meta['source']}", ""]
    title = (meta.get("title") or "").strip()
    for n, b in enumerate(blocks):
        if b["type"] == "text":
            text = b["text"]
            # 正文首块常常就是标题本身（h1 被 guess_title 取走了），别打两遍
            if n == 0 and text.strip() == title:
                continue
            role = b.get("role", "text")
            if role == "heading":
                lines += [f"## {text}", ""]
            elif role == "list":
                lines.append(f"- {text}")
            else:
                if lines and lines[-1].startswith("- "):
                    lines.append("")
                lines += [text, ""]
        else:
            im = images.get(b.get("img_id"))
            if im:
                if lines and lines[-1].startswith("- "):
                    lines.append("")
                lines += [f"![{im.get('alt') or im['file']}]({im['path']})",
                          f"<!-- {b['img_id']} src={b['src']} -->", ""]
            else:
                # 图没下下来（--no-images / 下载失败 / 被过滤）时留个占位。
                # 直接跳过会让读 guide.md 的 AI 以为这里本来就没有图——
                # 而"哪句话旁边有图"正是第④步配图的关键线索。
                lines += [f"<!-- 图片位置：未能下载 {b.get('src', '')[:160]} -->", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="攻略文档 → 结构化 blocks + 本地图片（纯标准库）")
    ap.add_argument("source", help="URL / 本地文件 / -（stdin 读文本）")
    ap.add_argument("-o", "--out", default="guide", help="输出目录")
    ap.add_argument("--selector", default=None, help="正文容器选择器")
    ap.add_argument("--render", choices=["auto", "always", "never"], default="auto")
    ap.add_argument("--chrome", default=None)
    ap.add_argument("--chrome-profile", default=None,
                    help="复用指定 Chrome profile 目录（默认不传，见 render_with_chrome 注释）")
    ap.add_argument("--min-chars", type=int, default=400)
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--max-images", type=int, default=60)
    ap.add_argument("--max-bytes", type=int, default=12 * 1024 * 1024)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--title", default=None)
    ap.add_argument("--keep-junk-images", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    log_lines = []

    def log(msg):
        log_lines.append(msg)
        if not args.quiet:
            print(msg, file=sys.stderr)

    src = args.source
    raw_html = None
    base_url = ""
    is_url = bool(re.match(r"^https?://", src))
    source_label = src
    mangled = 0

    # ---- 取字节 ----
    if src == "-":
        raw_html = sys.stdin.read()
        source_label = "<stdin>"
        log("[input] stdin 文本")
    elif is_url:
        base_url = src
        try:
            raw_html, final_url = http_get(src, timeout=args.timeout)
        except Exception as e:  # noqa: BLE001  网络任何失败都要给 JSON，不能吐 traceback
            print(json.dumps({"ok": False, "error": f"抓取失败: {e}",
                              "hint": "检查 URL / 网络；或浏览器另存为 HTML 后用本地文件模式",
                              "source": src}, ensure_ascii=False))
            return 2
        base_url = final_url
        log(f"[input] HTTP {len(raw_html)}B  {final_url}")
    else:
        if not os.path.exists(src):
            print(json.dumps({"ok": False, "error": f"文件不存在: {src}"},
                             ensure_ascii=False))
            return 2
        # 本地文件必须走 decode_html：国内站点大量是 GBK/GB18030，
        # 硬编 utf-8 + errors="replace" 会静默产出满屏乱码（还照样过字符闸）。
        with open(src, "rb") as fh:
            raw_bytes = fh.read()
        raw_html = decode_html(raw_bytes, sniff_charset(raw_bytes))
        mangled = raw_html.count("\ufffd")
        base_url = "file://" + os.path.abspath(src)
        log(f"[input] 本地文件 {len(raw_html)}B"
            + (f"（替换字符 {mangled} 处，编码可能没猜对）" if mangled else ""))

    is_html = looks_like_html(raw_html)
    rendered = False
    if not is_html:
        # 纯文本/Markdown 直接成块
        blocks = []
        user_picked = False
        for para in re.split(r"\n\s*\n", raw_html):
            for line in para.splitlines():
                line = norm_text(line)
                if not line:
                    continue
                m = re.match(r"^(#{1,6})\s*(.+)$", line)
                if m:
                    blocks.append({"type": "text", "text": m.group(2),
                                   "role": "heading"})
                    continue
                # 无序列表：- / * / +；有序列表：1. / 1、
                m = re.match(r"^(?:[-*+]\s+|\d+[.、)]\s*)(.+)$", line)
                if m:
                    blocks.append({"type": "text", "text": m.group(1).strip(),
                                   "role": "list"})
                    continue
                blocks.append({"type": "text", "text": line, "role": "text"})
        title = args.title or (blocks[0]["text"][:80] if blocks else "未命名攻略")
        container_desc = "纯文本直读"
    else:
        # ---- 无头渲染判定：静态 HTML 正文太短就上浏览器 ----
        parser = DomParser()
        parser.feed(raw_html)
        root = parser.root
        rough = own_text_len(root)
        if args.render == "always" or (args.render == "auto" and is_url
                                       and rough < args.min_chars):
            chrome = find_chrome(args.chrome)
            if chrome:
                try:
                    dom = render_with_chrome(src, chrome,
                                             timeout=max(60, args.timeout * 3),
                                             log=log, profile=args.chrome_profile)
                    if own_text_len_after(dom) > rough:
                        raw_html = dom
                        rendered = True
                        log(f"[render] 渲染后 {len(dom)}B（渲染前正文 {rough} 字符）")
                    else:
                        log("[render] 渲染后正文没有变多，仍用静态 HTML")
                except RuntimeError as e:
                    log(f"[render] 失败，回退静态 HTML：{e}")
            else:
                log("[render] 未找到 Chrome，跳过渲染（可用 --chrome 指定）")

        parser = DomParser()
        parser.feed(raw_html)
        root = parser.root
        container, container_desc, user_picked = pick_container(root, args.selector)
        blocks = extract_blocks(container, base_url,
                                keep_junk=args.keep_junk_images)
        title = args.title or guess_title(root) or "未命名攻略"

    # ---- 去重版式噪声 ----
    blocks = denoise(blocks)
    blocks = promote_headings(blocks)

    # ---- 兜底闸：抓不到正文就直接失败，绝不悄悄产出空稿 ----
    # ① 空结果永远不合法，跟输入模式无关：空文件、只有一个字的粘贴、
    #    --selector 命中一个空容器，产出的 guide.md 只有标题——
    #    而它是第③步唯一的输入，空稿比报错危险得多。
    # ② 80 字下限只对"自己选容器"的 HTML 生效：用户显式指定的 --selector 命中了、
    #    或输入本来就是纯文本 / Markdown / stdin，短就是真的短，不该判失败。
    extracted_chars = sum(len(b["text"]) for b in blocks if b["type"] == "text")
    too_short = (is_html and not user_picked and extracted_chars < 80)
    if extracted_chars == 0 or too_short:
        if extracted_chars == 0:
            reason = ("--selector 命中的容器里没有文字" if user_picked
                      else "没抽到任何正文文字")
        else:
            reason = f"正文只抽到 {extracted_chars} 个字符"
        hint = ("试试：① --selector <正文容器的 CSS，如 .c-feed-article__content>；"
                "② --render always；③ 浏览器打开页面「另存为」HTML 后用本地文件模式；"
                "④ 直接把正文粘贴成 txt 用 - 或文件模式。")
        err = {"ok": False,
               "error": f"{reason}，判定为抓取失败",
               "detail": {"container": container_desc,
                          "rendered": rendered,
                          "blocks": len(blocks)},
               "hint": hint}
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "fetch.log"), "w", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
        print(json.dumps(err, ensure_ascii=False))
        return 3

    # ---- 图片下载 ----
    os.makedirs(args.out, exist_ok=True)
    imgdir = os.path.join(args.out, "images")
    os.makedirs(imgdir, exist_ok=True)

    images = {}
    seen_url, seen_digest = {}, {}
    counter = 0
    warnings = list(selectors_warned)
    if not args.no_images:
        for b in blocks:
            if b["type"] != "image":
                continue
            url = b["src"]
            if url in seen_url:
                b["img_id"] = seen_url[url]
                continue
            if counter >= args.max_images:
                warnings.append(f"图片数超过 --max-images {args.max_images}，已截断")
                b["img_id"] = None
                continue
            counter += 1
            got = download_image(url, imgdir, counter, timeout=args.timeout,
                                 max_bytes=args.max_bytes, referer=base_url or None)
            if not got:
                warnings.append(f"下载失败/被过滤：{url[:110]}")
                b["img_id"] = None
                continue
            # 同一张图换个 URL（不同尺寸参数/CDN 域名）会下成两份字节完全相同的文件。
            # 按内容摘要去重，复用已有 id 并删掉这份多余文件。
            # 注意：不能拿 filename 比对——文件名里带了 counter，永远不相等。
            dg = got["digest"]
            if dg in seen_digest:
                os.remove(os.path.join(imgdir, got["file"]))
                b["img_id"] = seen_digest[dg]
                log(f"[image] 与 {seen_digest[dg]} 内容相同，复用：{url[:80]}")
                continue
            iid = f"img{counter:02d}"
            got.update({"id": iid, "src": url, "origin": b.get("origin", ""),
                        "alt": b.get("alt", "")})
            images[iid] = got
            seen_url[url] = iid
            seen_digest[dg] = iid
            b["img_id"] = iid
            log(f"[image] {iid} {got['w']}x{got['h']} {got['bytes']//1024}KB "
                f"-> {got['path']}")

    # ---- 摘要 ----
    text_blocks = [b for b in blocks if b["type"] == "text"]
    img_blocks = [b for b in blocks if b["type"] == "image"]
    meta = {
        "title": title,
        "source": src if is_url else os.path.abspath(src),
        "fetchedAt": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "container": container_desc,
        "rendered": rendered if is_html else False,
        "textChars": sum(len(b["text"]) for b in text_blocks),
        "blockCount": len(blocks),
        "textBlockCount": len(text_blocks),
        "imageCount": len(images),
        "imageBlockCount": len(img_blocks),
    }

    guide = {"meta": meta, "blocks": blocks, "images": images}
    gpath = os.path.join(args.out, "guide.json")
    with open(gpath, "w", encoding="utf-8") as f:
        json.dump(guide, f, ensure_ascii=False, indent=1)

    mpath = os.path.join(args.out, "guide.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write(build_markdown(meta, blocks, images))

    with open(os.path.join(args.out, "fetch.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    summary = {
        "ok": True,
        "out": os.path.abspath(args.out),
        "guideJson": os.path.abspath(gpath),
        "guideMd": os.path.abspath(mpath),
        "imagesDir": os.path.abspath(imgdir),
        "meta": meta,
        "images": [{"id": k, "path": v["path"], "w": v["w"], "h": v["h"],
                    "bytes": v["bytes"]} for k, v in images.items()],
        "warnings": warnings,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def own_text_len_after(html_text):
    p = DomParser()
    p.feed(html_text)
    return own_text_len(p.root)


def guess_title(root):
    """标题优先级：h1 > og:title > <title>

    很多 SPA 的 <title> 是站点通用标题（如「大神_游戏热爱者兴趣圈」），
    正文 h1 才是真标题，所以 h1 排第一。
    """
    for n in iter_elements(root):
        if n.tag == "h1":
            t = norm_text("".join(c.text or "" for c in n.children))
            if len(t) >= 2:
                return t
    for n in iter_elements(root):
        if n.tag == "meta" and n.get("property") in ("og:title", "twitter:title"):
            t = norm_text(n.get("content"))
            if t:
                return re.sub(r"[_\s]*[|｜]\s*(大神|网易|NetEase)\s*$", "", t).strip()
    for n in iter_elements(root):
        if n.tag == "title" and n.children:
            t = norm_text("".join(c.text or "" for c in n.children))
            if t:
                return re.split(r"[_|｜\-–—]", t)[0].strip() or t
    return None


def promote_headings(blocks):
    """中文攻略常把「一、xxx」「二、xxx」写成普通段落而不是 <h2>。

    guide.md 是 AI 读正文的入口，小节标题认不出来就没法按节拟稿，
    所以这里按模式把它们提升为 heading。
    限定：短（≤30 字）、无句末标点、以中文序号开头。
    """
    head_re = re.compile(
        r"^(?:[一二三四五六七八九十百]+[、.．)）]|第[一二三四五六七八九十百\d]+[节部分章][：:、]?)"
        r"\s*\S")
    out = []
    for b in blocks:
        if (b["type"] == "text" and b.get("role") == "text"
                and len(b["text"]) <= 30
                and not b["text"].endswith(("。", "！", "？", "，", "；", "："))
                and head_re.match(b["text"])):
            b = {**b, "role": "heading"}
        out.append(b)
    return out


def denoise(blocks):
    """去版式噪声：登录弹窗、AI 声明、超短碎块、页脚"""
    junk_exact = {
        "登录享受更多精彩内容", "立即登录", "关注", "分享", "评论", "点赞",
        "内容由AI生成", "（内容由AI生成）", "展开", "收起", "查看更多",
        "加载中", "热门推荐", "相关推荐", "猜你喜欢", "广告",
    }
    out = []
    for b in blocks:
        if b["type"] == "text":
            t = b["text"]
            if t in junk_exact:
                continue
            if len(t) < 2:
                continue
            out.append(b)
        else:
            out.append(b)
    # 去掉与首块重复的标题块
    if len(out) > 1 and out[0]["type"] == "text":
        first = out[0]["text"]
        out = [out[0]] + [b for b in out[1:]
                          if not (b["type"] == "text" and b["text"] == first)]
    return out


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RecursionError:
        print(json.dumps(
            {"ok": False,
             "error": "HTML 嵌套过深（或大量未闭合标签），解析栈溢出",
             "hint": "用 --selector 缩小到正文容器；或先用工具清理 HTML 再喂本地文件模式"},
            ensure_ascii=False))
        sys.exit(2)
