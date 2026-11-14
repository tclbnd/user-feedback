#!/usr/bin/env python3
"""Deterministic labelled corpus: 10 layouts x 4 change classes = 40 pages.

Each *variant* is a different real-world page layout, so the evaluation is not
testing one skeleton twelve times:

  v1  main + nav + footer               (plain, semantic)
  v2  article + aside + header          (semantic, chrome heavy)
  v3  div#content / div#nav             (classic pre-semantic markup)
  v4  div#article-body, no <main>       (id-token located)
  v5  nested layout wrappers            (container-wrapped)
  v6  Chinese ids                       (localized markup)
  v7  no semantic hints at all          (DOM / text-mass fallback)
  v8  content via role="main"           (ARIA located)
  v9  two columns with a real side column (distractor mass)
  v10 section > article > div           (deep nesting)

Per variant, four labelled changes:

  template  markup/chrome edits only, prose untouched
  content   prose edits only, markup byte-identical
  mixed     both, applied together
  none      cosmetic jitter only (whitespace, attribute order, re-chunking)
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Callable, Dict, List, Tuple

NBSP = chr(0x00A0)
IDEO_SPACE = chr(0x3000)
TAB = chr(0x09)
NL = chr(0x0A)

# "zheng wen" (body text) and "dao hang" (navigation), built numerically.
ZH_BODY = chr(0x6B63) + chr(0x6587)
ZH_BODY_BLOCK = chr(0x6B63) + chr(0x6587) + chr(0x533A) + chr(0x5757)
ZH_NAV = chr(0x5BFC) + chr(0x822A)
ZH_SIDE = chr(0x76F8) + chr(0x5173) + chr(0x680F)

PROSE = [
    "城市轨道交通的早高峰客流在过去五年持续上升。调度中心将发车间隔从三分钟压缩到两分半，站台拥堵明显缓解。",
    "本轮设备检修覆盖十二座换乘站，重点更换信号机与道岔转辙设备。检修窗口安排在夜间收车后的四小时。",
    "运营方上线了实时的到站预测，误差中位数为四十秒。乘客可以在小程序里查看下一班车的拥挤度。",
    "年度安全演练共组织二十六次，参与人员包括站务、乘务与维保三支队伍。演练记录已归档备查。",
    "票价调整方案进入听证阶段，方案对不同里程段的计费规则做了细化。听证会全程公开。",
    "新建线路的土建进度达到总量的七成，盾构区间已有九段贯通。铺轨作业计划在下季度启动。",
    "客流分析显示周五晚间的跨江出行需求最高，运力配置将向该时段倾斜。",
    "无障碍设施改造完成首批试点，十二个站点新增了电梯语音提示与盲道引导。",
    "票务系统完成升级，二维码过闸的平均耗时从一点二秒降低到零点八秒。",
    "运维团队引入预测性维护模型，关键部件的故障预警提前量提升到七十二小时。",
]
AUTHOR = "市轨道交通运营指挥中心"
TITLE = "运营通报"


def split_sentences(text: str) -> List[str]:
    out, current = [], []
    for char in text:
        current.append(char)
        if char in "。！？":
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return [s for s in out if s]


def prose_blocks(index: int) -> List[Dict[str, str]]:
    sentences = split_sentences(PROSE[index])
    return [
        {"id": "title", "text": TITLE + "（第 %d 期）" % (index + 1)},
        {"id": "meta", "text": "发布单位：" + AUTHOR + " 发布日期：2024-05-%02d" % (index + 3)},
        {"id": "p1", "text": sentences[0]},
        {"id": "p2", "text": sentences[1] if len(sentences) > 1 else PROSE[index]},
        {"id": "p3", "text": sentences[2] if len(sentences) > 2 else "补充说明：数据来源为月度统计。"},
    ]


def chrome_blocks(side: bool = False) -> List[Dict[str, str]]:
    blocks = [
        {"id": "nav", "text": "首页 | 线路 | 票务 | 公告"},
        {"id": "footer", "text": "版权所有 " + AUTHOR},
    ]
    if side:
        blocks.append({"id": "sidebar",
                       "text": "热门公告：票务系统升级说明 换乘站检修计划 年度安全演练"})
    return blocks


# ---------------------------------------------------------------------------
# layouts: each returns (template, blocks) for one prose index
# ---------------------------------------------------------------------------


def layout_1(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """main + nav + footer, fully semantic."""
    tpl = (
        "<!DOCTYPE html><html lang=\"zh\"><head><title>{{title}}</title></head>"
        "<body><nav id=\"nav\">{{nav}}</nav>"
        "<main id=\"main\"><h1>{{title}}</h1><p id=\"meta\">{{meta}}</p>"
        "{{p1}}{{p2}}{{p3}}</main>"
        "<footer id=\"footer\">{{footer}}</footer></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks()


def layout_2(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """article + aside + header: chrome heavy, semantic."""
    tpl = (
        "<html><body><header id=\"nav\"><ul><li>{{nav}}</li></ul></header>"
        "<div class=\"wrap\"><article id=\"story\"><h1>{{title}}</h1>"
        "<p>{{meta}}</p>{{p1}}{{p2}}{{p3}}</article>"
        "<aside id=\"sidebar\">{{sidebar}}</aside></div>"
        "<footer id=\"footer\">{{footer}}</footer></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks(side=True)


def layout_3(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """classic pre-semantic divs with class words."""
    tpl = (
        "<html><body><div id=\"nav\" class=\"top-nav\">{{nav}}</div>"
        "<div id=\"content\" class=\"article-body\">"
        "<h1>{{title}}</h1><div class=\"byline\">{{meta}}</div>"
        "{{p1}}{{p2}}{{p3}}</div>"
        "<div id=\"footer\" class=\"site-footer\">{{footer}}</div></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks()


def layout_4(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """id-token located content, no <main> and no <article>."""
    tpl = (
        "<html><body><nav id=\"nav\">{{nav}}</nav><div class=\"layout\">"
        "<div id=\"article-body\"><span id=\"title-block\">{{title}}</span>"
        "<span id=\"meta-block\">{{meta}}</span>"
        "{{p1}}{{p2}}{{p3}}</div></div>"
        "<div id=\"footer\">{{footer}}</div></body></html>"
    )
    blocks = prose_blocks(index)
    blocks[0] = dict(blocks[0], id="title-block")
    blocks[1] = dict(blocks[1], id="meta-block")
    return tpl, blocks + chrome_blocks()


def layout_5(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """nested container wrapping: several divs before the content."""
    tpl = (
        "<html><body><div id=\"nav\" class=\"nav\">{{nav}}</div>"
        "<div class=\"shell\"><div class=\"grid\"><div class=\"col-main\">"
        "<main id=\"main\"><div class=\"prose\"><h1>{{title}}</h1>"
        "<p>{{meta}}</p>{{p1}}{{p2}}{{p3}}</div></main></div>"
        "<div class=\"col-side\">{{sidebar}}</div></div></div>"
        "<footer id=\"footer\">{{footer}}</footer></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks(side=True)


def layout_6(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """localized Chinese ids for content and navigation."""
    tpl = (
        "<html><body><div id=\"" + ZH_NAV + "\">{{nav}}</div>"
        "<div id=\"" + ZH_BODY + "\" class=\"" + ZH_BODY_BLOCK + "\">"
        "<h1>{{title}}</h1><div>{{meta}}</div>{{p1}}{{p2}}{{p3}}</div>"
        "<div id=\"footer\">{{footer}}</div></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks()


def layout_7(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """no semantic hints at all: forces the structural fallback.

    Containers are ``div#a1``/``div#a2``/``div#a3`` and the prose block ids are
    opaque, so neither id tokens, classes, roles nor element names identify the
    content region.  Only container text mass can find it.
    """
    tpl = (
        "<html><body><div id=\"a1\">{{nav}}</div>"
        "<div id=\"a2\"><span>{{t1}}</span><span>{{t2}}</span>"
        "{{p1}}{{p2}}{{p3}}</div>"
        "<div id=\"a3\">{{footer}}</div></body></html>"
    )
    blocks = prose_blocks(index)
    blocks[0] = dict(blocks[0], id="t1")
    blocks[1] = dict(blocks[1], id="t2")
    return tpl, blocks + chrome_blocks()


def layout_8(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """content located through role="main" on a generic element."""
    tpl = (
        "<html><body><div id=\"nav\" role=\"navigation\">{{nav}}</div>"
        "<div class=\"panel\" role=\"main\"><h1>{{title}}</h1>"
        "<p role=\"note\">{{meta}}</p>{{p1}}{{p2}}{{p3}}</div>"
        "<div id=\"footer\" role=\"contentinfo\">{{footer}}</div></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks()


def layout_9(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """two columns, both with real text mass; the side column distracts."""
    tpl = (
        "<html><body><div id=\"nav\">{{nav}}</div><div class=\"columns\">"
        "<div id=\"main-column\" class=\"main\"><h1>{{title}}</h1>"
        "<p>{{meta}}</p>{{p1}}{{p2}}{{p3}}</div>"
        "<div class=\"side-column\">{{sidebar}}</div></div>"
        "<div id=\"footer\">{{footer}}</div></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks(side=True)


def layout_10(index: int) -> Tuple[str, List[Dict[str, str]]]:
    """deep nesting: section > article > div for the content region."""
    tpl = (
        "<html><body><div id=\"nav\">{{nav}}</div>"
        "<section class=\"page\"><article id=\"post\"><div class=\"inner\">"
        "<h1>{{title}}</h1><span>{{meta}}</span>{{p1}}{{p2}}{{p3}}</div>"
        "</article></section>"
        "<div id=\"footer\">{{footer}}</div></body></html>"
    )
    return tpl, prose_blocks(index) + chrome_blocks()


LAYOUTS: List[Callable[[int], Tuple[str, List[Dict[str, str]]]]] = [
    layout_1, layout_2, layout_3, layout_4, layout_5,
    layout_6, layout_7, layout_8, layout_9, layout_10,
]


def build_page(index: int) -> Dict[str, Any]:
    """Base snapshot of variant ``index`` (0-based)."""
    tpl, blocks = LAYOUTS[index](index)
    return {"tpl": tpl, "blocks": [dict(block) for block in blocks]}


def get_text(page: Dict[str, Any], block_id_value: str) -> str:
    for block in page["blocks"]:
        if block["id"] == block_id_value:
            return block["text"]
    raise KeyError(block_id_value)


def set_text(page: Dict[str, Any], block_id_value: str, text: str) -> Dict[str, Any]:
    page = copy.deepcopy(page)
    for block in page["blocks"]:
        if block["id"] == block_id_value:
            block["text"] = text
            return page
    raise KeyError(block_id_value)


# ---------------------------------------------------------------------------
# template-layer edits (prose untouched)
# ---------------------------------------------------------------------------


def template_edit(page: Dict[str, Any], which: int) -> Dict[str, Any]:
    """One of ten template-only edits, applied generically to any layout."""
    page = copy.deepcopy(page)
    tpl = page["tpl"]
    if which == 1:
        # Element rename in the skeleton, same ids and same text.
        for old, new in (
                ("<nav id=\"nav\">", "<header id=\"nav\">"),
                ("<div id=\"nav\" class=\"top-nav\">", "<header id=\"nav\" class=\"top-nav\">"),
                ("<div id=\"nav\" role=\"navigation\">",
                 "<header id=\"nav\" role=\"navigation\">"),
                ("<div id=\"nav\">", "<header id=\"nav\">"),
                ("<div id=\"" + ZH_NAV + "\">", "<header id=\"" + ZH_NAV + "\">"),
                ("<div id=\"a1\">", "<header id=\"a1\">")):
            if old in tpl:
                tpl = tpl.replace(old, new, 1)
                break
    elif which == 2:
        # Wrapper element inserted around the whole body.
        tpl = tpl.replace("<body>", "<body><div class=\"frame\">", 1)
        tpl = tpl.replace("</body>", "</div></body>", 1)
    elif which == 3:
        # Chrome text edited: the footer block, never prose.
        for block in page["blocks"]:
            if block["id"] in ("footer", "f1"):
                block["text"] = block["text"] + "（2025 年修订）"
                break
    elif which == 4:
        # Layout attribute added to the body.
        tpl = tpl.replace("<body>", "<body data-layout=\"wide\">", 1)
    elif which == 5:
        # New chrome block appended outside the content region.
        page["blocks"].append({"id": "promo", "text": "订阅号：运营速递"})
        tpl = tpl.replace("</body>", "<aside id=\"promo\">{{promo}}</aside></body>", 1)
    elif which == 6:
        # Head metadata plus a static, non-block caption.
        tpl = tpl.replace("</head>",
                          "<meta name=\"generator\" content=\"cms-2\"></head>", 1)
        tpl = tpl.replace("</body>", "<span>（内部资料）</span></body>", 1)
    elif which == 7:
        # Chrome block order changed: the nav block moves after the content.
        nav_block = None
        for block in page["blocks"]:
            if block["id"] in ("nav", "n1"):
                nav_block = block
                break
        if nav_block is not None:
            page["blocks"] = [b for b in page["blocks"] if b["id"] != nav_block["id"]]
            page["blocks"].append(nav_block)
            for candidate in (
                    "<nav id=\"nav\">{{nav}}</nav>",
                    "<header id=\"nav\"><ul><li>{{nav}}</li></ul></header>",
                    "<div id=\"nav\" class=\"top-nav\">{{nav}}</div>",
                    "<div id=\"nav\" class=\"nav\">{{nav}}</div>",
                    "<div id=\"" + ZH_NAV + "\">{{nav}}</div>",
                    "<div id=\"a1\">{{nav}}</div>",
                    "<div id=\"nav\" role=\"navigation\">{{nav}}</div>",
                    "<div id=\"nav\">{{nav}}</div>"):
                if candidate in tpl:
                    tpl = tpl.replace(candidate, "", 1)
                    tpl = tpl.replace("</body>", candidate + "</body>", 1)
                    break
    elif which == 8:
        # Content container element swapped, same id and children.
        for old, new in (
                ("<div id=\"content\"", "<section id=\"content\""),
                ("<div id=\"article-body\"", "<section id=\"article-body\""),
                ("<main id=\"main\">", "<section id=\"main\">"),
                ("<div id=\"main\"", "<section id=\"main\""),
                ("<div id=\"a2\"", "<section id=\"a2\"")):
            if old in tpl:
                tpl = tpl.replace(old, new, 1)
                break
        if "</main>" in tpl:
            tpl = tpl.replace("</main>", "</section>", 1)
        elif "</div>" in tpl:
            tpl = tpl.replace("</div>", "</section>", 1)
    elif which == 9:
        # Extra class tokens on template elements.
        tpl = tpl.replace("<body>", "<body class=\"theme-2025\">", 1)
        for old, new in (("<footer id=\"footer\">", "<footer id=\"footer\" class=\"footer-compact\">"),
                         ("<div id=\"footer\">", "<div id=\"footer\" class=\"footer-compact\">")):
            if old in tpl:
                tpl = tpl.replace(old, new, 1)
                break
    elif which == 10:
        # CSS hook attribute on the title element.
        tpl = tpl.replace("<h1>", "<h1 data-anchor=\"top\">", 1)
    else:
        raise ValueError(which)
    if tpl == page["tpl"]:
        # The targeted markup was not present in this layout (e.g. a layout
        # that has no <nav>).  Fall back to a hook that always applies, so the
        # fixture really exercises a template change.
        tpl = page["tpl"].replace("<body>", "<body data-variant=\"%d\">" % which, 1)
        if tpl == page["tpl"]:
            tpl = page["tpl"].replace("</body>", "<span class=\"hook\"></span></body>", 1)
    page["tpl"] = tpl
    return page


# ---------------------------------------------------------------------------
# content-layer edits (markup untouched)
# ---------------------------------------------------------------------------


def title_id(page: Dict[str, Any]) -> str:
    for block in page["blocks"]:
        if block["id"] in ("title", "title-block", "t1"):
            return block["id"]
    raise KeyError("title")


def content_edit(page: Dict[str, Any], which: int) -> Dict[str, Any]:
    """Apply one prose edit; every edit is guaranteed to change the prose."""
    page = copy.deepcopy(page)
    if which == 1:
        # Replace the first full stop with a semicolon plus a new clause,
        # which is version independent (this prose need not contain a fixed
        # phrase for the edit to bite).
        text = get_text(page, "p1")
        marker = "。"
        if marker in text:
            edited = text.replace(marker, "；另据监测，客流波动处于正常区间。", 1)
        else:
            edited = text + "另据监测，客流波动处于正常区间。"
        return set_text(page, "p1", edited)
    elif which == 2:
        return set_text(page, "p2", get_text(page, "p2") + "新增的换乘通道将在下月启用。")
    elif which == 3:
        text = get_text(page, "p3")
        edited = text.replace("数据来源", "统计口径", 1)
        if edited == text:
            edited = text + "口径已按新规调整。"
        return set_text(page, "p3", edited)
    elif which == 4:
        # New prose block inserted after the last prose paragraph.
        position = max(i for i, b in enumerate(page["blocks"]) if b["id"].startswith("p"))
        page["blocks"].insert(position + 1,
                              {"id": "p4", "text": "后续还将补充夜间巡检频次，保障设备状态稳定。"})
        return page
    elif which == 5:
        page["blocks"] = [b for b in page["blocks"] if b["id"] != "p2"]
        return page
    elif which == 6:
        # Two prose blocks swapped: placement moves, prose does not.
        p1_text, p2_text = get_text(page, "p1"), get_text(page, "p2")
        if p1_text == p2_text:
            p2_text = p2_text + "续报。"
        return set_text(set_text(page, "p1", p2_text), "p2", p1_text)
    elif which == 7:
        tid = title_id(page)
        return set_text(page, tid, get_text(page, tid) + "·增刊")
    elif which == 8:
        text = get_text(page, "p1")
        return set_text(page, "p1",
                        text.replace("。", "！", 1) if "。" in text else text + "！")
    elif which == 9:
        return set_text(page, "p3", "整段替换：本期通报以运力保障为主线，各项指标均达到预期。")
    elif which == 10:
        # Prose edit that also splits a paragraph in two.
        for position, block in enumerate(page["blocks"]):
            if block["id"] == "p2":
                sentences = split_sentences(block["text"])
                if len(sentences) > 1:
                    page["blocks"][position] = dict(block, text=sentences[0])
                    page["blocks"].insert(position + 1,
                                          {"id": "p2b", "text": sentences[1]})
                else:
                    page["blocks"][position] = dict(
                        block, text=block["text"] + "补充数据见附表。")
                break
        return page
    else:
        raise ValueError(which)


# ---------------------------------------------------------------------------
# cosmetic edits (no fingerprint may move)
# ---------------------------------------------------------------------------


def cosmetic_edit(page: Dict[str, Any], which: int) -> Dict[str, Any]:
    page = copy.deepcopy(page)
    if which == 1:
        page["blocks"] = [dict(b, text="  " + b["text"].replace("，", "，" + TAB + NL + " ")
                               + "  " + NBSP + "  ") for b in page["blocks"]]
        page["tpl"] = page["tpl"].replace("><", ">" + NL + "  <")
        return page
    elif which == 2:
        # Attribute order and attribute spacing shuffled in the skeleton.
        for old, new in (("<body>", "<body   >"), ("<h1>", "<h1  >"),
                         ("</h1><p id=\"meta\">", "</h1>\n<p   id=\"meta\">")):
            page["tpl"] = page["tpl"].replace(old, new)
        return page
    elif which == 3:
        # Prose merged into one block; joined prose is identical.  The merged
        # placeholder replaces the first paragraph placeholder in place, so the
        # prose never leaves the content container.
        prose_ids = [b["id"] for b in page["blocks"] if b["id"].startswith("p")]
        merged = "".join(get_text(page, pid) for pid in prose_ids)
        page["blocks"] = [b for b in page["blocks"] if b["id"] not in prose_ids]
        page["blocks"].append({"id": "body", "text": merged})
        for pid in prose_ids:
            if "{{%s}}" % pid in page["tpl"]:
                page["tpl"] = page["tpl"].replace("{{%s}}" % pid, "{{body}}", 1)
                break
        for pid in prose_ids:
            page["tpl"] = page["tpl"].replace("{{%s}}" % pid, "", 1)
        return page
    elif which == 4:
        # Prose split into sentence-level blocks; joined prose is identical.
        prose_ids = [b["id"] for b in page["blocks"] if b["id"].startswith("p")]
        merged = "".join(get_text(page, pid) for pid in prose_ids)
        chunks = split_sentences(merged)
        new_ids = ["body%d" % i for i in range(len(chunks))]
        page["blocks"] = [b for b in page["blocks"] if b["id"] not in prose_ids]
        page["blocks"] += [{"id": new_ids[i], "text": chunks[i]}
                           for i in range(len(chunks))]
        joined = "".join("{{%s}}" % i for i in new_ids)
        for pid in prose_ids:
            if "{{%s}}" % pid in page["tpl"]:
                page["tpl"] = page["tpl"].replace("{{%s}}" % pid, joined, 1)
                break
        for pid in prose_ids:
            page["tpl"] = page["tpl"].replace("{{%s}}" % pid, "", 1)
        return page
    elif which == 5:
        page["blocks"] = [dict(b, text=b["text"].replace(" ", IDEO_SPACE + TAB))
                          for b in page["blocks"]]
        page["tpl"] = page["tpl"].replace("><", ">\r\n<")
        return page
    elif which == 6:
        # Class token whitespace: same canonical class list.
        tpl = page["tpl"]
        tpl = tpl.replace('class="article-body"', 'class="  article-body  "')
        tpl = tpl.replace('class="top-nav"', 'class="top-nav   "')
        tpl = tpl.replace('class="site-footer"', 'class=" site-footer"')
        tpl = tpl.replace('class="nav"', 'class="nav  "')
        tpl = tpl.replace('<html lang="zh">', '<html  lang="  zh  ">')
        if 'class="page"' in tpl:
            tpl = tpl.replace('class="page"', 'class="  page  "')
        page["tpl"] = tpl
        return page
    elif which == 7:
        page["tpl"] = page["tpl"].replace("><", ">" + NL + "    <")
        page["blocks"] = [dict(b, text=NL + b["text"] + NL) for b in page["blocks"]]
        return page
    elif which == 8:
        page["blocks"] = list(reversed(page["blocks"]))
        return page
    elif which == 9:
        page["blocks"] = [dict(b, text=b["text"].replace("，", "，" + NL + "  "))
                          for b in page["blocks"]]
        page["tpl"] = page["tpl"].replace("{{p1}}", "{{p1}} " + IDEO_SPACE, 1)
        return page
    elif which == 10:
        # Full re-serialization: formatting, attribute order, class order.
        # Only attributes that already exist are re-ordered/re-spaced; adding
        # a new class would be a real template change.
        page["tpl"] = page["tpl"].replace(
            "<nav id=\"nav\">", "<nav   id=\"nav\"  >")
        page["tpl"] = page["tpl"].replace(
            "<div id=\"nav\" class=\"top-nav\">", "<div class=\"top-nav\" id=\"nav\">")
        page["tpl"] = page["tpl"].replace(
            "<div id=\"nav\" class=\"nav\">", "<div class=\"nav\"   id=\"nav\">")
        page["tpl"] = page["tpl"].replace("<body>", "<body >")
        page["tpl"] = page["tpl"].replace("<h1>", "<h1 >")
        page["tpl"] = page["tpl"].replace(
            "<div id=\"content\" class=\"article-body\">",
            "<div class=\"article-body\"  id=\"content\">")
        page["tpl"] = page["tpl"].replace("><", ">" + NL + "<")
        page["blocks"] = [dict(b, text=" " + NBSP + b["text"] + TAB)
                          for b in page["blocks"]]
        return page
    else:
        raise ValueError(which)


# ---------------------------------------------------------------------------
# corpus assembly
# ---------------------------------------------------------------------------


LABELS = ("template", "content", "mixed", "none")


def build_corpus() -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for index in range(len(LAYOUTS)):
        variant = index + 1
        base = build_page(index)
        for label in LABELS:
            if label == "template":
                new = template_edit(base, variant)
            elif label == "content":
                new = content_edit(base, variant)
            elif label == "mixed":
                new = content_edit(template_edit(base, variant), variant)
            else:
                new = cosmetic_edit(base, variant)
            entries.append({
                "id": "%02d-%s" % (variant, label),
                "variant": variant,
                "label": label,
                "old": copy.deepcopy(base),
                "new": copy.deepcopy(new),
            })
    return entries


STABILITY_KINDS = {
    1: "whitespace-jitter",
    2: "attribute-order-and-spacing",
    3: "content-merge",
    4: "content-split",
    5: "unicode-spaces",
    6: "class-token-whitespace",
    7: "skeleton-format",
    8: "block-listing-order",
    9: "sentence-padding",
    10: "full-reserialization",
}


def build_stability_cases() -> List[Dict[str, Any]]:
    """Every jitter kind against every layout: 10 x 10 = 100 cases."""
    cases = []
    for index in range(len(LAYOUTS)):
        base = build_page(index)
        for which in range(1, 11):
            cases.append({
                "id": "stability-%02d-%02d" % (index + 1, which),
                "layout": index + 1,
                "kind": STABILITY_KINDS[which],
                "old": copy.deepcopy(base),
                "new": copy.deepcopy(cosmetic_edit(base, which)),
                "expected_class": "none",
            })
    return cases


def write_corpus(directory: str) -> Dict[str, Any]:
    os.makedirs(directory, exist_ok=True)
    pages = build_corpus()
    stability = build_stability_cases()
    with open(os.path.join(directory, "corpus.json"), "w", encoding="utf-8") as handle:
        json.dump({"pages": pages, "stability": stability}, handle,
                  ensure_ascii=False, indent=1, sort_keys=True)
    with open(os.path.join(directory, "labels.json"), "w", encoding="utf-8") as handle:
        json.dump([{"id": entry["id"], "label": entry["label"],
                    "variant": entry["variant"]} for entry in pages],
                  handle, ensure_ascii=False, indent=1, sort_keys=True)
    return {"pages": len(pages), "stability_cases": len(stability), "dir": directory}


def load_corpus(directory: str) -> Dict[str, Any]:
    with open(os.path.join(directory, "corpus.json"), encoding="utf-8") as handle:
        return json.load(handle)


def write_repo(directory: str) -> Dict[str, Any]:
    """Materialize the corpus as a snapshot repository for the scanner."""
    os.makedirs(directory, exist_ok=True)
    pages = build_corpus()
    for entry in pages:
        page_dir = os.path.join(directory, entry["id"])
        os.makedirs(page_dir, exist_ok=True)
        dump(os.path.join(page_dir, "s1-base.json"), entry["old"])
        dump(os.path.join(page_dir, "s2-changed.json"), entry["new"])
        if entry["label"] in ("content", "mixed"):
            # A template redesign landed before the prose edit.
            dump(os.path.join(page_dir, "s0-template.json"),
                 template_edit(entry["old"], entry["variant"]))
    history_dir = os.path.join(directory, "history-01")
    os.makedirs(history_dir, exist_ok=True)
    base = build_page(0)
    dump(os.path.join(history_dir, "s1-base.json"), base)
    dump(os.path.join(history_dir, "s2-template.json"), template_edit(base, 2))
    dump(os.path.join(history_dir, "s3-content.json"),
         content_edit(template_edit(base, 2), 3))
    dump(os.path.join(history_dir, "s4-jitter.json"),
         cosmetic_edit(content_edit(template_edit(base, 2), 3), 7))
    dump(os.path.join(history_dir, "s5-mixed.json"), content_edit(template_edit(base, 5), 1))
    return {"pages": len(pages) + 1, "entries": len(pages), "dir": directory}


def dump(path: str, page: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(page, handle, ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(json.dumps(write_corpus(os.path.join(here, "data", "labeled")),
                     ensure_ascii=False, indent=1))
    print(json.dumps(write_repo(os.path.join(here, "out", "repo")),
                     ensure_ascii=False, indent=1))
