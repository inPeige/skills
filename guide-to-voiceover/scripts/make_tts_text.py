#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_tts_text.py — 生成喂给 TTS 的配音文本 `tts.txt`

## 为什么需要这个文件

`video-talkcraft` 的 ② 阶段**要音频，但不会合成音频**：
「配音是输入，不是本 skill 的产物…skill 不含合成技术」。
音频通常由 `xfyun-long-tts` 合成，而 **TTS 要喂的是带标点的文本**：

- 标点决定语气和停顿。喂零标点的稿子，TTS 会念成一口气的长串，平得没法听。
- 但 `script.json` 的 `sentences` 又必须是**零标点**——
  talkcraft 按文本逐字锚定做时间戳、字幕整句直出都要求零标点。

所以一份稿子要出**两个文本**，文字逐字相同、只差标点：

| 文件 | 标点 | 给谁 |
|---|---|---|
| `tts.txt` | **带标点**，一句一行 | `xfyun-long-tts` 合成音频 |
| `script.json` | 零标点 | `video-talkcraft` 做字级时间戳 |

这个脚本从 `口播文稿.md` 的正文区抽出带标点的句子写成 `tts.txt`，
并当场和 `script.json` 核对两版是否逐字一致（不一致就报错，不产出）。

用法:
  python3 make_tts_text.py --dir <产出目录> [-o tts.txt]
                          [--chars-per-sec 5.0] [--voice x4_mingge]
                          [--max-chars 90000] [-q]

stdout 一行 JSON：字数、估算时长、可直接复制的 xfyun 合成命令。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_output import PUNCT_RE, load_json, parse_md_sentences  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成 TTS 配音文本 tts.txt")
    ap.add_argument("--dir", default=".", help="产出目录（含 script.json / 口播文稿.md）")
    ap.add_argument("-o", "--out", default=None, help="输出路径（默认 <dir>/tts.txt）")
    ap.add_argument("--chars-per-sec", type=float, default=5.0)
    ap.add_argument("--voice", default="x4_mingge", help="发音人，仅用于提示命令")
    ap.add_argument("--max-chars", type=int, default=90000,
                    help="xfyun 单任务上限，超出会提示切段")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    script, err = load_json(os.path.join(args.dir, "script.json"))
    if err:
        print(json.dumps({"ok": False, "error": err}, ensure_ascii=False))
        return 2
    sents = script.get("sentences") or []
    if not sents:
        print(json.dumps({"ok": False,
                          "error": "script.json 没有 sentences"}, ensure_ascii=False))
        return 2

    md_path = os.path.join(args.dir, "口播文稿.md")
    if not os.path.exists(md_path):
        print(json.dumps({"ok": False,
                          "error": f"找不到 {md_path}（带标点的句子在它里面）"},
                         ensure_ascii=False))
        return 2

    md_sents, perr = parse_md_sentences(md_path)
    if perr:
        print(json.dumps({"ok": False, "error": f"口播文稿.md {perr}"},
                         ensure_ascii=False))
        return 1
    if md_sents is None:
        print(json.dumps({"ok": False,
                          "error": "口播文稿.md 里找不到「## 正文」下的 `N. 句子` 编号行，"
                                   "无法取出带标点的配音文本（格式见 voiceover-spec.md §6）"},
                         ensure_ascii=False))
        return 1

    # 两版必须逐字一致，只差标点——否则配音和字幕对不上
    mismatches = []
    if len(md_sents) != len(sents):
        mismatches.append(f"句数不同：口播文稿.md {len(md_sents)} 句 vs "
                          f"script.json {len(sents)} 句")
    else:
        for i, (a_raw, b_raw) in enumerate(zip(sents, md_sents), start=1):
            a = PUNCT_RE.sub("", a_raw).strip()
            b = PUNCT_RE.sub("", b_raw).strip()
            if a != b:
                mismatches.append(f"第 {i} 句：script.json「{a}」vs 口播文稿.md「{b}」")
    if mismatches:
        print(json.dumps({"ok": False,
                          "error": "两版文字不一致，先改稿再配音",
                          "mismatches": mismatches[:10],
                          "mismatchCount": len(mismatches)}, ensure_ascii=False))
        return 1

    # 配音文本：一句一行。行尾换行让多数 TTS 在这里落一个停顿，
    # 也让"哪句念歪了"在听感上对得上稿子。
    text = "\n".join(md_sents) + "\n"
    out_path = args.out or os.path.join(args.dir, "tts.txt")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)

    plain_chars = sum(len(PUNCT_RE.sub("", s)) for s in md_sents)
    est = plain_chars / args.chars_per_sec if args.chars_per_sec else 0
    abs_out = os.path.abspath(out_path)
    cmd = (f"python3 \"<xfyun-long-tts skill 目录>/scripts/xfyun_tts.py\" synth "
           f"-f {abs_out} -v {args.voice} -o audio/voice.mp3")

    summary = {
        "ok": True,
        "ttsText": abs_out,
        "sentences": len(md_sents),
        "chars": plain_chars,
        "charsWithPunct": len(text),
        "estimatedSec": round(est, 1),
        "charsPerSec": args.chars_per_sec,
        "xfyunCommand": cmd,
        "nextSteps": [
            "① 合成配音：" + cmd,
            "② 直接喂给 video-talkcraft ②（mp3 即可，它内部重采样；"
            "TTS 配音可跳过 ②-0 预剪）：",
            "   python3 scripts/timestamps_cpu.py audio/voice.mp3 script.json audio/timestamps.json",
        ],
    }
    if len(text) > args.max_chars:
        summary["warnings"] = [
            f"配音文本 {len(text)} 字符超过单任务上限 {args.max_chars}，"
            f"xfyun 脚本会自动切段（加 --concat 可拼回一个文件）"]

    if not args.quiet:
        print(f"配音文本 → {abs_out}")
        print(f"  {len(md_sents)} 句 · {plain_chars} 字（含标点 {len(text)}）"
              f" · 估算 {est:.1f}s @ {args.chars_per_sec} 字/秒")
        print(f"  合成：{cmd}")
        print("\n  注意：tts.txt 与 script.json 文字逐字一致、只差标点。"
              "别拿 script.json 去合成（零标点会念成平串），"
              "也别拿 tts.txt 去做时间戳。")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
