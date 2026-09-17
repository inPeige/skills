#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_output.py — 口播稿与素材计划的机器闸

把能机器判定的错误全拦下来，人只需要看"好不好"。

用法:
  python3 check_output.py --dir <产出目录> [选项]

  --dir DIR          含 script.json / assets.plan.json / guide.json 的目录
  --chars-per-sec N  口播语速（默认 5.0 字/秒，中文口播常用 4.5–5.5）
  --min-sec N        总时长下限（默认 20）
  --max-sec N        总时长上限（默认 300）
  --strict           警告也算失败
  -q/--quiet         只输出 JSON

退出码: 0 全过 / 1 有 error（或 --strict 下有 warning）/ 2 用法或文件问题

检查项:
  E  结构性错误（缺文件、字段缺失、索引越界、图片文件不存在、补拍项标错）
  W  质量问题（句子过长过短、某段没配图、素材闲置、补拍项缺 how、§8 叙事三件套）

补拍项统一用 `"kind": "need"` 的素材条目表示（没有 file），这样每段都有素材可引用，
缺口本身也是产出的一部分。

错误码一览:
  E001 script.json 缺失或 JSON 损坏     E009 assets.plan.json 缺 assets 数组
  E002 assets.plan.json 缺失或损坏      E010 素材 id 重复
  E003 script.json 缺 sentences         E011 素材文件在磁盘上不存在
  E004 sentences 有空值或非字符串       E012 素材指向不存在的段
  E005 script.json 缺 segments          E013 某段没配任何素材
  E006 某段没有 sentences 索引          E014 段引用了不存在的素材
  E007 句子索引越界                     E015 非 need 素材 id 不在 guide.json 里
  E008 有句子没被任何段覆盖             E016 素材既没有 file 也不是 kind:"need"
  E017 口播文稿.md 与 script.json 句数不一致
  E018 口播文稿.md 与 script.json 有句子文字不一致
  E019 口播文稿.md 正文的句子编号不连续
  E020 口播文稿.md 找不到编号行，无法核对两版

  W101 没有 guide.json，跳过原图对账    W209 段的 role 不在允许集合内
  W201 估算时长低于下限                 W210 素材缺 what（画面内容说明）
  W202 估算时长高于上限                 W211 可用素材没被任何段用上
  W203 （已废弃：数字形态不检查，见 voiceover-spec §2）
  W204 句子含标点（要求零标点）         W213 标了 kind:need 却又给了 file
  W205 句子超过 42 字                   W214 补拍项缺 how（怎么拿到）
  W206 句子少于 4 字，太碎              W215 补拍项 priority 不是 P0/P1/P2
  W207 整句全英文                       W216 unused 条目缺 why
  W208 句子被多个段重复覆盖             W217 段的句子索引没有按序递增
  W213 need 项又给了 file               W218 meta.estimatedSec 与实测差 >10%
  W214 补拍项缺 how                     W219 kind / usage 不在封闭词表里
  W215 priority 不是 P0/P1/P2           W220 file 不是图片扩展名
  W221 文字卡没写 onScreenText

  —— 互动与叙事（voiceover-spec.md §8，2026-09 牧场系列复盘新增）——
  W222 没有价值兑现段（role:"like"）    W226 meta.axis 缺失或非法（长稿）
  W223 没有第一人称背书句               W227 body 段缺 suspense（段首悬念）
  W224 长稿没有观点收束段（outro）      W228 缺/错 highlight，或看点单调
  W225 body 段缺 logic（因果方向）      W229 长稿没标任何热点元素（meme）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# 句子契约是「零标点」：下游配音按文本逐字锚定、字幕直出都要求零标点。
# 带标点的人读版写在 口播文稿.md 里，不进 sentences。
# 小数点不算标点：`17.8` / `2.5` 里的点要保留（数字就是数字，见 voiceover-spec §2），
# 所以用 (?<!\d)\.(?!\d) 只匹配不在两个数字之间的点。
PUNCT_RE = re.compile(
    r"""[，。！？；：、,!?;:…—～~·"'“”‘’（）()\[\]{}<>《》【】「」『』]"""
    r"""|(?<!\d)\.(?!\d)""")

# like = 价值兑现/点赞钩子段；outro = 观点收束段（voiceover-spec §8.1 / §8.3）
ROLE_SET = {"hook", "intro", "body", "tip", "like", "outro", "cta"}
# text = 纯文字/动效镜（没有图片文件），对应 talkcraft SHOTBOOK 的 `素材：**文**`
KIND_SET = {"screenshot", "table", "ui", "need", "text"}
USAGE_SET = {"full", "crop", "zoom", "blur-bg", "reference-only"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# §8 叙事字段的取值词表
LOGIC_SET = {"cause→effect", "effect→cause"}
HIGHLIGHT_SET = {"reversal", "surprise", "funny", "payoff", "data"}
AXIS_SET = {"time", "space", "event"}

# §8 的阈值：短稿不该被长稿规则误伤，所以只在规模以上才查
INTERACTION_MIN_SENTS = 20   # ≥20 句才要求点赞钩子与第一人称背书
LONG_FORM_MIN_SEGS = 8       # ≥8 段算长稿：要求 outro / 主轴 / 热点元素
NARRATIVE_MIN_BODY = 5       # ≥5 个 body/tip 段才要求 logic/suspense/highlight
NARRATIVE_MAX_MISSING = 0.5  # 叙事字段缺失率上限，超过就提醒"还没设计过"


def load_json(path, want=dict):
    """读 JSON 并校验顶层类型。

    顶层类型不对（null / [] / "oops"）比 JSON 语法错更常见，
    必须在入口拦住——否则后面 `d.get(...)` 会抛 AttributeError，
    用户看到的是 traceback 而不是机器闸报告，退出码语义也失效。
    """
    if not os.path.exists(path):
        return None, f"文件不存在: {path}"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"{os.path.basename(path)} JSON 解析失败: {e}"
    if want is not None and not isinstance(data, want):
        return None, (f"{os.path.basename(path)} 顶层必须是 JSON "
                      f"{'对象' if want is dict else want.__name__}，"
                      f"实际是 {type(data).__name__}")
    return data, None


def parse_md_sentences(md_path):
    """从 口播文稿.md 抽出带标点的句子（正文区 `N. 句子` 行）。

    返回 (sentences, error_msg)。找不到这个结构时返回 (None, None)——
    md 的版式是给人看的，不该被机器绑死。
    """
    try:
        md = open(md_path, encoding="utf-8").read()
    except OSError as e:
        return None, f"读不到 {md_path}: {e}"
    if "## 正文" not in md:
        return None, None
    body = md.split("## 正文", 1)[1]
    body = re.split(r"\n## ", body, maxsplit=1)[0]

    pairs = []
    for ln in body.splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.+?)\s*$", ln)
        if m:
            pairs.append((int(m.group(1)), m.group(2)))
    if not pairs:
        return None, None

    nums = [n for n, _ in pairs]
    if nums != list(range(1, len(nums) + 1)):
        return None, (f"正文的句子编号不连续（期望 1..{len(nums)}）："
                      f"{nums[:15]}{'…' if len(nums) > 15 else ''}")
    return [t for _, t in pairs], None


def check_markdown_matches(md_path, sents):
    """人读版 口播文稿.md 与 script.json 必须逐字一致（只允许标点差异）。

    找不到 md 的编号结构时返回 E020 —— 不能静默通过，
    否则"没检查"会被当成"检查过了"。同理，返回值区分"跳过"和"通过"。
    """
    md_sents, err = parse_md_sentences(md_path)
    if err:
        return [{"code": "E019", "msg": f"口播文稿.md {err}"}]
    if md_sents is None:
        return [{"code": "E020",
                 "msg": "口播文稿.md 里找不到「## 正文」下的 `N. 句子` 编号行，"
                        "无法核对两版是否一致（格式见 voiceover-spec.md §6）"}]
    if len(md_sents) != len(sents):
        return [{"code": "E017",
                 "msg": f"口播文稿.md 正文有 {len(md_sents)} 句，"
                        f"script.json 有 {len(sents)} 句——两版必须逐字对应"}]

    out = []
    for i, (a_raw, b_raw) in enumerate(zip(sents, md_sents), start=1):
        a = PUNCT_RE.sub("", a_raw).strip()
        b = PUNCT_RE.sub("", b_raw).strip()
        if a != b:
            out.append({"code": "E018",
                        "msg": f"第 {i} 句两版文字不一致（差别不能只有标点之外的任何字符）\n"
                               f"        script.json : {a}\n"
                               f"        口播文稿.md : {b}"})
    return out


def _narrative_stats(segs, field, valid=None):
    """统计段级叙事字段（`logic`/`suspense`/`highlight`）的缺失数与非法值数。

    返回 (missing, bad)。字段全部可选，所以"没写"和"写错了"要分开报——
    没写是"还没设计过"，写错了是词表用错，处理方式不一样。
    """
    miss = bad = 0
    for s in segs:
        v = s.get(field)
        if not (isinstance(v, str) and v.strip()):
            miss += 1
        elif valid is not None and v not in valid:
            bad += 1
    return miss, bad


def check(dirpath, cps=5.0, min_sec=20.0, max_sec=300.0):
    errors, warnings, info = [], [], {}

    script, err = load_json(os.path.join(dirpath, "script.json"))
    if err:
        errors.append({"code": "E001", "msg": err})
        return errors, warnings, info
    plan, err = load_json(os.path.join(dirpath, "assets.plan.json"))
    if err:
        errors.append({"code": "E002", "msg": err})
    guide, gerr = load_json(os.path.join(dirpath, "guide.json"))
    if gerr:
        # fetch_guide.py 的默认输出布局是 <dir>/guide/guide.json
        guide, gerr = load_json(os.path.join(dirpath, "guide", "guide.json"))
    if gerr:
        warnings.append({"code": "W101", "msg": f"没有 guide.json（{gerr}）；跳过原图对账"})

    # ---------- script.json ----------
    sents = script.get("sentences")
    if not isinstance(sents, list) or not sents:
        errors.append({"code": "E003", "msg": "script.json 缺少非空 sentences 数组"})
        return errors, warnings, info
    if not all(isinstance(s, str) and s.strip() for s in sents):
        errors.append({"code": "E004", "msg": "sentences 里有空句或非字符串"})

    total_chars = sum(len(PUNCT_RE.sub("", s).strip())
                      for s in sents if isinstance(s, str))
    est_sec = total_chars / cps if cps else 0
    info["sentences"] = len(sents)
    info["chars"] = total_chars
    info["estimatedSec"] = round(est_sec, 1)
    info["charsPerSec"] = cps

    if est_sec < min_sec:
        warnings.append({"code": "W201",
                         "msg": f"估算时长 {est_sec:.1f}s < 下限 {min_sec}s，"
                                f"内容可能太少（{total_chars} 字 / {len(sents)} 句）"})
    if est_sec > max_sec:
        warnings.append({"code": "W202",
                         "msg": f"估算时长 {est_sec:.1f}s > 上限 {max_sec}s，"
                                f"建议拆成上下集（{total_chars} 字）"})

    for i, s in enumerate(sents):
        if not isinstance(s, str):
            continue  # E004 已经报过；继续跑正则只会抛 TypeError
        if PUNCT_RE.search(s):
            warnings.append({"code": "W204", "sentence": i,
                             "msg": f"第 {i} 句含标点，口播稿 sentences 要求零标点"
                                    f"（配音逐字锚定 + 字幕直出）：「{s[:40]}」→ "
                                    f"标点只写在 口播文稿.md 里"})
        if len(PUNCT_RE.sub("", s)) > 42:
            warnings.append({"code": "W205", "sentence": i,
                             "msg": f"第 {i} 句 {len(s)} 字偏长（>42），"
                                    f"一口气念不完，建议断句：「{s[:30]}…」"})
        if len(s) < 4:
            warnings.append({"code": "W206", "sentence": i,
                             "msg": f"第 {i} 句只有 {len(s)} 字，太碎："
                                    f"「{s}」"})
        if re.search(r"[a-zA-Z]{3,}", s) and not re.search(r"[\u4e00-\u9fff]", s):
            warnings.append({"code": "W207", "sentence": i,
                             "msg": f"第 {i} 句全是英文，确认配音能读："
                                    f"「{s[:40]}」"})

    segments = script.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append({"code": "E005", "msg": "script.json 缺少 segments 数组"})
    else:
        covered = {}
        for seg in segments:
            sid = seg.get("id", "?")
            idxs = seg.get("sentences")
            if not isinstance(idxs, list) or not idxs:
                errors.append({"code": "E006", "msg": f"段 {sid} 没有 sentences 索引"})
                continue
            for i in idxs:
                # bool 是 int 的子类，JSON 里的 true/false 会悄悄混进来当索引
                if isinstance(i, bool) or not isinstance(i, int) \
                        or not (0 <= i < len(sents)):
                    errors.append({"code": "E007",
                                   "msg": f"段 {sid} 的句子索引非法或越界: {i!r}"})
                    continue
                if i in covered:
                    warnings.append({"code": "W208",
                                     "msg": f"句子 {i} 被段 {covered[i]} 和 {sid} 重复覆盖"})
                covered[i] = sid
            if seg.get("role") and seg["role"] not in ROLE_SET:
                warnings.append({"code": "W209",
                                 "msg": f"段 {sid} 的 role「{seg['role']}」不在 "
                                        f"{sorted(ROLE_SET)} 里"})
        missing = [i for i in range(len(sents)) if i not in covered]
        if missing:
            errors.append({"code": "E008",
                           "msg": f"有 {len(missing)} 句没被任何段覆盖: {missing[:12]}"
                                  f"{'…' if len(missing) > 12 else ''}"})
        # 段是按时序播的：句子索引必须整体递增，否则成片顺序是乱的
        prev_max = -1
        for seg in segments:
            idxs = seg.get("sentences") or []
            if not idxs or not all(isinstance(i, int) and not isinstance(i, bool)
                                   for i in idxs):
                continue
            if min(idxs) <= prev_max:
                warnings.append({"code": "W217",
                                 "msg": f"段 {seg.get('id')} 的句子索引"
                                        f"（{min(idxs)}–{max(idxs)}）没有接在上一段"
                                        f"（止于 {prev_max}）之后，成片顺序会乱"})
            prev_max = max(prev_max, max(idxs))
        info["segments"] = len(segments)

    # ---------- meta 自洽 ----------
    meta = script.get("meta") or {}
    if isinstance(meta, dict) and est_sec and meta.get("estimatedSec") is not None:
        try:
            declared = float(meta["estimatedSec"])
        except (TypeError, ValueError):
            warnings.append({"code": "W218",
                             "msg": f"meta.estimatedSec 不是数字："
                                    f"{meta['estimatedSec']!r}"})
        else:
            if declared > 0 and abs(declared - est_sec) / est_sec > 0.10:
                warnings.append({"code": "W218",
                                 "msg": f"meta.estimatedSec={declared} 与实测估算 "
                                        f"{est_sec:.1f}s 差超过 10%（按 "
                                        f"{cps} 字/秒、{total_chars} 字算）——"
                                        f"改了稿子记得同步这个数"})

    # ---------- 互动与叙事（voiceover-spec §8） ----------
    # 这些字段全部可选，缺了只报 W（--strict 才算失败）：
    # 它们提醒的是"这一段还没设计过"，不是格式错误。
    if isinstance(segments, list) and segments:
        roles = [s.get("role") for s in segments]
        narr_segs = [s for s in segments if s.get("role") in ("body", "tip")]
        n_seg, n_narr = len(segments), len(narr_segs)

        # W222 价值兑现：硬数据交付后要挂点赞引导，不能只在结尾 cta 伸手
        if len(sents) >= INTERACTION_MIN_SENTS and "like" not in roles:
            warnings.append({"code": "W222",
                             "msg": f"全文 {len(sents)} 句却没有 role:\"like\" 段——"
                                    f"每个硬数据交付点之后应跟一次价值兑现+点赞引导"
                                    f"（§8.1）"})
        # W223 第一人称背书：攻略结论落到真人验证上才有温度
        if len(sents) >= INTERACTION_MIN_SENTS and not any(
                isinstance(s, str) and "我" in s for s in sents):
            warnings.append({"code": "W223",
                             "msg": "正文没有一句含「我」——至少一处第一人称实操背书"
                                    "（时长/结果/情绪）。注意句里的数字必须是真人验过的，"
                                    "没验过就换成可核验的客观表述（§8.2）"})
        # W224 观点收束：长稿不能讲完就散场
        if n_seg >= LONG_FORM_MIN_SEGS and "outro" not in roles:
            warnings.append({"code": "W224",
                             "msg": f"{n_seg} 段的长稿没有 role:\"outro\"——"
                                    f"结尾要收束到「我怎么看 + 你能拿走什么」（§8.3）"})

        if n_narr >= NARRATIVE_MIN_BODY:
            for field, valid, code, hint in (
                ("logic", LOGIC_SET, "W225", "先因后果还是先果后因（§8.4）"),
                ("suspense", None, "W227",
                 "段首藏住了时间/地点/人物/事件里的哪一项（§8.6）"),
                ("highlight", HIGHLIGHT_SET, "W228", "这段的看点类型（§8.7）"),
            ):
                miss, bad = _narrative_stats(narr_segs, field, valid)
                if miss / n_narr > NARRATIVE_MAX_MISSING:
                    warnings.append({"code": code,
                                     "msg": f"{n_narr} 个 body/tip 段里有 {miss} 段"
                                            f"没写 {field}：{hint}"})
                if bad:
                    warnings.append({"code": code,
                                     "msg": f"有 {bad} 段的 {field} 取值不对"
                                            + (f"，应取 {sorted(valid)}" if valid
                                               else "") + f"：{hint}"})
                if field == "highlight":
                    seq = [s.get("highlight") for s in narr_segs]
                    run = 1
                    for i in range(1, len(seq)):
                        if seq[i] and seq[i] == seq[i - 1]:
                            run += 1
                            if run == 3:
                                warnings.append(
                                    {"code": "W228",
                                     "msg": f"连续 3 段 highlight 都是「{seq[i]}」，"
                                            f"看点单调，相邻段要换类型（§8.7）"})
                        else:
                            run = 1
                    kinds = {v for v in seq if v}
                    if kinds and len(kinds) < 3:
                        warnings.append(
                            {"code": "W228",
                             "msg": f"全片只用了 {len(kinds)} 种看点类型"
                                    f"（{sorted(kinds)}），一集至少覆盖 3 种（§8.7）"})
                    if sum(1 for v in seq if v == "data") > len(seq) / 2:
                        warnings.append(
                            {"code": "W228",
                             "msg": "超过一半的段是 highlight:\"data\"，"
                                    "全是数据冲击等于没有冲击（§8.7）"})
            info["narrative"] = f"{n_narr - _narrative_stats(narr_segs, 'highlight')[0]}/{n_narr}"

        # W226 叙事主轴：长稿要在 meta.axis 声明按什么维度推进
        axis = (script.get("meta") or {}).get("axis")
        if n_seg >= LONG_FORM_MIN_SEGS and axis not in AXIS_SET:
            warnings.append({"code": "W226",
                             "msg": f"{n_seg} 段的长稿 meta.axis="
                                    f"{axis!r}，应为 {sorted(AXIS_SET)} 之一"
                                    f"（时间/空间/事件走向），并在 intro 里说破（§8.5）"})
        # W229 热点元素：长稿至少标几处可以上当期热点的地方
        if n_seg >= LONG_FORM_MIN_SEGS and not any(
                s.get("meme") for s in segments):
            warnings.append({"code": "W229",
                             "msg": f"{n_seg} 段的长稿没有任何段标 meme——"
                                    f"在悬念兑现处和看点处留 2–4 处热点位"
                                    f"（BGM/特效/表情动作），别写死热点名（§8.8）"})

    # ---------- assets.plan.json ----------
    # 注意用 `is not None` 而不是真值判断：`{}` / `[]` 是假值，
    # 用 `if plan:` 会让整段素材闸静默失效（连"缺 assets 数组"都不报）。
    if plan is not None:
        assets = plan.get("assets")
        if not isinstance(assets, list):
            errors.append({"code": "E009",
                           "msg": "assets.plan.json 缺少 assets 数组"})
            assets = []
        assets = [a for a in assets if isinstance(a, dict)]
        seen_ids = set()
        for a in assets:
            aid = a.get("id", "?")
            if aid in seen_ids:
                errors.append({"code": "E010", "msg": f"素材 id 重复: {aid}"})
            seen_ids.add(aid)
            is_need = a.get("kind") == "need"
            is_text = a.get("kind") == "text"
            f = a.get("file")
            if f:
                p = f if os.path.isabs(f) else os.path.join(dirpath, f)
                if not os.path.exists(p):
                    errors.append({"code": "E011",
                                   "msg": f"素材 {aid} 的文件不存在: {f}"})
            elif not (is_need or is_text):
                errors.append({"code": "E016",
                               "msg": f"素材 {aid} 既没有 file，也不是 kind:\"need\""
                                      f"（补拍占位）或 kind:\"text\"（纯文字/动效镜）"})
            if is_need and f:
                warnings.append({"code": "W213",
                                 "msg": f"素材 {aid} 标了 kind:need 却又给了 file，"
                                        f"确认是补拍占位还是已有素材"})
            for seg_id in a.get("segments", []) or []:
                if segments and seg_id not in {s.get("id") for s in segments}:
                    errors.append({"code": "E012",
                                   "msg": f"素材 {aid} 指向不存在的段: {seg_id}"})
            if not a.get("what"):
                warnings.append({"code": "W210",
                                 "msg": f"素材 {aid} 没有 what（画面内容说明），"
                                        f"分镜时无法判断能不能用"})
            if is_text and not (a.get("onScreenText") or []):
                warnings.append({"code": "W221",
                                 "msg": f"文字卡 {aid} 没写 onScreenText"
                                        f"（卡上要出现的字），分镜时要重新猜一遍"})
            # 文档里 kind / usage 是封闭词表，就该真的校验
            kind = a.get("kind")
            if kind is not None and kind not in KIND_SET:
                warnings.append({"code": "W219",
                                 "msg": f"素材 {aid} 的 kind「{kind}」不在 "
                                        f"{sorted(KIND_SET)} 里"})
            usage = a.get("usage")
            if usage is not None and usage not in USAGE_SET:
                warnings.append({"code": "W219",
                                 "msg": f"素材 {aid} 的 usage「{usage}」不在 "
                                        f"{sorted(USAGE_SET)} 里"})
            # file 得真的是图片，别把 txt 塞进来充数满足"每段配图"
            if f:
                ext = os.path.splitext(f)[1].lower()
                if ext and ext not in IMG_EXT:
                    warnings.append({"code": "W220",
                                     "msg": f"素材 {aid} 的 file 不是图片扩展名："
                                            f"{f}"})

        info["assets"] = len(assets)

        # 每段至少一张图
        seg_assets = {}
        for seg in (segments or []):
            seg_assets[seg.get("id")] = list(seg.get("assets") or [])
        for seg in (segments or []):
            sid = seg.get("id")
            declared = seg_assets.get(sid, [])
            from_plan = [a.get("id") for a in assets
                         if sid in (a.get("segments") or [])]
            if not declared and not from_plan:
                errors.append({"code": "E013",
                               "msg": f"段 {sid} 没配任何素材（也未被任何素材引用）"})
            for aid in declared:
                if aid not in seen_ids:
                    errors.append({"code": "E014",
                                   "msg": f"段 {sid} 引用了不存在的素材: {aid}"})

        # 素材闲置：显式写进 unused 的算已交代，不再提醒
        declared_unused = {u.get("id") for u in (plan.get("unused") or [])}
        used = set()
        for seg in (segments or []):
            used.update(seg.get("assets") or [])
        for a in assets:
            used.update(a.get("segments") or [])
        idle = [a.get("id") for a in assets
                if a.get("id") not in used and a.get("usable", True)
                and a.get("id") not in declared_unused]
        if idle:
            warnings.append({"code": "W211",
                             "msg": f"有 {len(idle)} 个可用素材没被任何段用上: {idle}"
                                    f"（不需要就写进 unused 说明原因）"})
        for u in (plan.get("unused") or []):
            if not u.get("why"):
                warnings.append({"code": "W216",
                                 "msg": f"unused 里的 {u.get('id')} 没写 why，"
                                        f"等于没说清为什么不用"})

        gaps = [a for a in assets if a.get("kind") == "need"]
        info["needs"] = len(gaps)
        for g in gaps:
            if not g.get("how"):
                warnings.append({"code": "W214",
                                 "msg": f"补拍项 {g.get('id')} 没写 how"
                                        f"（怎么拿到这张图），执行时无从下手"})
            if g.get("priority") not in ("P0", "P1", "P2", None):
                warnings.append({"code": "W215",
                                 "msg": f"补拍项 {g.get('id')} 的 priority "
                                        f"「{g.get('priority')}」不是 P0/P1/P2"})
        info["p0Needs"] = sum(1 for g in gaps if g.get("priority") == "P0")

    # ---------- 与人读版对账：两版文字必须逐字一致，差别只能有标点 ----------
    md_path = os.path.join(dirpath, "口播文稿.md")
    if os.path.exists(md_path):
        mism = check_markdown_matches(md_path, sents)
        if mism:
            errors.extend(mism)
        else:
            info["markdownSynced"] = True

    # ---------- 与 guide.json 对账 ----------
    if guide is not None:
        gimgs = guide.get("images") or {}
        gids = set(gimgs.keys())
        if plan is not None:
            all_assets = [a for a in (plan.get("assets") or [])
                          if isinstance(a, dict)]
            declared_unused = {u.get("id")
                               for u in (plan.get("unused") or [])
                               if isinstance(u, dict)}
            # E015 只管"声称来自原攻略"的素材。判定依据是文件落在 guide/images/ 下，
            # 或用 fromGuide 显式声明——这样用户自己做的封面/标题卡不会被误杀，
            # 把 need 项补拍完成后（改成普通素材 + 真实文件）也能顺利通过。
            unknown = sorted(
                a.get("id") for a in all_assets
                if is_guide_derived(a) and a.get("id") not in gids)
            if unknown:
                errors.append({"code": "E015",
                               "msg": f"这些素材声称来自原攻略（file 在 "
                                      f"guide/images/ 下或标了 fromGuide），"
                                      f"但 id 不在 guide.json 里: {unknown}"})
            # 每张原攻略的图，要么以素材身份出现，要么进 unused 交代
            covered = {a.get("id") for a in all_assets} | declared_unused
            missing_in_plan = sorted(gids - covered)
            if missing_in_plan:
                warnings.append({"code": "W212",
                                 "msg": f"原攻略有 {len(missing_in_plan)} 张图既没进 "
                                        f"assets 也没进 unused: {missing_in_plan}"})
        info["guideImages"] = len(gids)

    return errors, warnings, info


def is_guide_derived(asset):
    """这个素材是否声称「来自原攻略」（据此决定要不要和 guide.json 对账）"""
    if asset.get("fromGuide") is not None:
        return bool(asset["fromGuide"])
    f = (asset.get("file") or "").replace("\\", "/")
    return f.startswith("guide/images/")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="口播稿与素材计划的机器闸"
        "（核对 script.json / assets.plan.json / 口播文稿.md / guide.json）")
    ap.add_argument("--dir", default=".", help="产出目录")
    ap.add_argument("--chars-per-sec", type=float, default=5.0)
    ap.add_argument("--min-sec", type=float, default=20.0)
    ap.add_argument("--max-sec", type=float, default=300.0)
    ap.add_argument("--strict", action="store_true", help="warning 也算失败")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.dir):
        print(json.dumps({"ok": False, "error": f"目录不存在: {args.dir}"},
                         ensure_ascii=False))
        return 2

    errors, warnings, info = check(args.dir, args.chars_per_sec,
                                   args.min_sec, args.max_sec)
    ok = not errors and (not args.strict or not warnings)
    report = {"ok": ok, "errors": errors, "warnings": warnings, "info": info}

    if not args.quiet:
        i = info
        print(f"句数 {i.get('sentences', '?')} · 字数 {i.get('chars', '?')} · "
              f"估算 {i.get('estimatedSec', '?')}s · 段 {i.get('segments', '?')} · "
              f"素材 {i.get('assets', '?')}（原图 {i.get('guideImages', '?')}）· "
              f"待补拍 {i.get('needs', 0)}（P0 {i.get('p0Needs', 0)}）· "
              f"看点标注 {i.get('narrative', '-')}")
        if errors:
            print(f"\n❌ {len(errors)} 个错误：")
            for e in errors:
                print(f"  [{e['code']}] {e['msg']}")
        if warnings:
            print(f"\n⚠️  {len(warnings)} 个警告：")
            for w in warnings:
                print(f"  [{w['code']}] {w['msg']}")
        if ok:
            print("\n✅ 机器闸全过")
        print()

    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
