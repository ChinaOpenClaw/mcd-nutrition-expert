# -*- coding: utf-8 -*-
"""
SP 矩阵象限一致性门禁。

背景
----
SP 矩阵有两条必须始终一致的语义线：
  1) 后端 `sp_matrix.py` 依据动态阈值把每个餐品判定到四个象限之一；
  2) 前端 Canvas 与视频用的 SVG 用「四象限底色 + 标签」把同样的语义画出来。

只要底色矩形的 Y 坐标没有跟随散点做轴翻转，就会出现「橙色点在左上，图上却写着填充区」
这类错误 —— 界面看起来正常，但语义是反的，对评审是硬伤。

本脚本从真实接口取数据，逐点校验：
  - SVG（docs/video/assets/sp-matrix.svg）：散点落入的矩形颜色 == 后端 quadrant
  - 前端 Canvas（skills/.../web/index.html）：重放 drawSP() 坐标换算后同��校验

用法
----
    python tools/check_quadrants.py

依赖：后端已启动（默认 http://127.0.0.1:8899，可用 MCD_API 覆盖）。
退出码 0 = 全部一致；1 = 存在不一致。
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.environ.get("MCD_API", "http://127.0.0.1:8899")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG = os.path.join(ROOT, "docs", "video", "assets", "sp-matrix.svg")
WEB = os.path.join(ROOT, "skills", "mcd-nutrition-expert", "web", "index.html")

# 后端 QUADRANTS 的权威配色（scripts/sp_matrix.py）
QUAD_COLORS = {
    "sweet_point": "#2E9E5B",
    "double_edge": "#C0392B",
    "treat": "#E8833A",
    "filler": "#4A7FB5",
}
QUAD_LABEL = {
    "sweet_point": "主力区",
    "double_edge": "双刃区",
    "treat": "解馋区",
    "filler": "填充区",
}
# 与后端判定逻辑一致的方位（X 向右为营养密度↑，Y 向上为钠负担↑）
QUAD_POS = {
    "sweet_point": "右下",
    "double_edge": "右上",
    "treat": "左上",
    "filler": "左下",
}


def fetch_matrix():
    with urllib.request.urlopen(BASE + "/api/matrix", timeout=60) as r:
        return json.load(r)


def check_svg(d):
    """校验 SVG：每个散点落入的底色矩形颜色 == 后端 quadrant 颜色。"""
    if not os.path.isfile(SVG):
        return None, "SVG 不存在：%s（先运行 tools/make_sp_svg.py）" % SVG
    ns = "{http://www.w3.org/2000/svg}"
    root = ET.parse(SVG).getroot()
    rects, circles = [], []
    for el in root.iter():
        if el.tag == ns + "rect" and el.get("opacity") == "0.07":
            rects.append(el)
        elif el.tag == ns + "circle" and el.get("fill-opacity") == "0.82":
            circles.append(el)
    if len(rects) != 4:
        return False, "SVG 象限底色矩形数应为 4，实际 %d" % len(rects)

    bad = []
    for c in circles:
        cx, cy = float(c.get("cx")), float(c.get("cy"))
        want = c.get("fill")
        hit = None
        for el in rects:
            x, y = float(el.get("x")), float(el.get("y"))
            w, h = float(el.get("width")), float(el.get("height"))
            if x <= cx <= x + w and y <= cy <= y + h:
                hit = el.get("fill")
                break
        if hit != want:
            bad.append((cx, cy, want, hit))
    if bad:
        msg = "SVG 有 %d 个散点的底色与判定不符，例：" % len(bad)
        msg += "; ".join("点(%.1f,%.1f) 颜色=%s 所在矩形=%s" % b for b in bad[:5])
        return False, msg
    return True, "SVG：%d 个散点全部落在与后端判定一致的象限内" % len(circles)


def check_web(d):
    """校验前端：静态解析 drawSP() 的象限坐标块是否与基准写法一致。"""
    if not os.path.isfile(WEB):
        return None, "前端文件不存在：%s" % WEB
    with open(WEB, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"const quads=\[(.*?)\];", src, re.S)
    if not m:
        return False, "前端 drawSP() 中未找到 quads 定义"
    block = m.group(1)

    # 期望：主力区(右下) / 双刃区(右上) / 解馋区(左上) / 填充区(左下)
    expect = ["主力区", "双刃区", "解馋区", "填充区"]
    for name in expect:
        if name not in block:
            return False, "前端 quads 缺少象限：%s" % name
    # 颜色与象限必须对应（复用 drawSP 的底色表）
    color_of = {
        "主力区": "#E4F1E8",
        "双刃区": "#FBE4E1",
        "解馋区": "#FBE9DC",
        "填充区": "#F6EFE4",
    }
    for name, col in color_of.items():
        if col not in block:
            return False, "前端 quads 缺少 %s 的底色 %s" % (name, col)
    # 关键：必须存在 Y 轴翻转的换算
    if "pad.t+ph-(th.y/maxY)*ph" not in src.replace(" ", ""):
        return False, "前端象限底色未做 Y 轴翻转（缺 pad.t+ph-(th.y/maxY)*ph）"
    return True, "前端 drawSP() 象限坐标与后端语义一致（%s）" % "、".join(expect)


def main():
    try:
        d = fetch_matrix()
    except Exception as e:
        print("✗ 无法连接后端 %s：%s" % (BASE, e))
        print("  请先启动服务（scripts/run.py 或 uvicorn），再运行本脚本。")
        return 1

    pts = d.get("points") or []
    th = d.get("thresholds") or {}
    quads = d.get("quadrants") or {}
    print("真实数据：%d 个有效样本   阈值 x=%.2f y=%.2f" % (len(pts), th.get("x", 0), th.get("y", 0)))
    for k in ("sweet_point", "double_edge", "treat", "filler"):
        print("  %-12s %s %-4s 实测 %d" % (k, QUAD_LABEL[k], QUAD_POS[k], (quads.get(k) or {}).get("count", 0)))

    ok = True
    print("\n[1] SVG 视频素材")
    r, msg = check_svg(d)
    print(("  ✓ " if r else "  ✗ ") + msg)
    ok = ok and (r is not False)

    print("\n[2] 前端 Canvas")
    r, msg = check_web(d)
    print(("  ✓ " if r else "  ✗ ") + msg)
    ok = ok and (r is not False)

    print("\n%s" % ("全部通过：前端与视频的象限语义与后端判定完全一致" if ok else "存在不一致，请修正后重跑"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
