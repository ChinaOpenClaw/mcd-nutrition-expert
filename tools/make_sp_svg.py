# -*- coding: utf-8 -*-
"""
生成讲解视频用的 SP 矩阵示意图（SVG）。

不手绘：直接调用后端 /api/matrix 拿真实的 160 个单品坐标，
用真实数据点渲染散点图 —— 视频里展示的数字与产品完全一致。

输出：docs/video/assets/sp-matrix.svg
"""
import json
import os
import urllib.request

BASE = os.environ.get("MCD_API", "http://127.0.0.1:8899")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "video", "assets")
os.makedirs(OUT, exist_ok=True)

W, H = 1080, 720
PAD_L, PAD_R, PAD_T, PAD_B = 92, 46, 52, 74
PW = W - PAD_L - PAD_R
PH = H - PAD_T - PAD_B

COLORS = {
    "sweet_point": "#2E9E5B",
    "double_edge": "#C0392B",
    "treat": "#E8833A",
    "filler": "#4A7FB5",
}
CN = {
    "sweet_point": "主力区",
    "double_edge": "双刃区",
    "treat": "解馋区",
    "filler": "填充区",
}


def fetch():
    with urllib.request.urlopen(BASE + "/api/matrix", timeout=60) as r:
        return json.load(r)


def main():
    d = fetch()
    pts = d["points"]
    th = d["thresholds"]
    tp = d.get("target_point") or {}
    quads = d["quadrants"]

    xs = [p["x"] for p in pts]
    ys = [p["y"] for p in pts]
    max_x = max(xs) * 1.06
    max_y = max(ys) * 1.06
    x_th, y_th = th["x"], th["y"]

    def X(v):
        return PAD_L + v / max_x * PW

    def Y(v):
        return PAD_T + PH - v / max_y * PH

    out = []
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
               'viewBox="0 0 %d %d">' % (W, H, W, H))
    out.append('<rect width="%d" height="%d" fill="#0f1116"/>' % (W, H))

    # 象限底色（与后端 sp_matrix.py 判定一致，且遵循 Y 轴翻转：y 越大越靠上）
    #   主力区 sweet_point : x>=x_th 且 y<y_th  → 右下
    #   双刃区 double_edge: x>=x_th 且 y>=y_th → 右上
    #   解馋区 treat      : x< x_th 且 y>=y_th → 左上
    #   填充区 filler     : x< x_th 且 y< y_th  → 左下
    def qrect(x0, x1, y0, y1, color):
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity="0.07"/>'
                   % (X(x0), Y(y1), X(x1) - X(x0), Y(y0) - Y(y1), color))

    qrect(x_th, max_x, 0, y_th, COLORS["sweet_point"])
    qrect(x_th, max_x, y_th, max_y, COLORS["double_edge"])
    qrect(0, x_th, y_th, max_y, COLORS["treat"])
    qrect(0, x_th, 0, y_th, COLORS["filler"])

    # 网格
    for i in range(6):
        gx = PAD_L + PW * i / 5
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#ffffff" '
                   'stroke-opacity="0.07"/>' % (gx, PAD_T, gx, PAD_T + PH))
        gy = PAD_T + PH * i / 5
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ffffff" '
                   'stroke-opacity="0.07"/>' % (PAD_L, gy, PAD_L + PW, gy))

    # 象限分割线
    out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#ffc72c" '
               'stroke-width="2" stroke-dasharray="7 6" opacity="0.85"/>'
               % (X(x_th), PAD_T, X(x_th), PAD_T + PH))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#ffc72c" '
               'stroke-width="2" stroke-dasharray="7 6" opacity="0.85"/>'
               % (PAD_L, Y(y_th), PAD_L + PW, Y(y_th)))

    # 数据点
    for p in pts:
        c = COLORS.get(p["quadrant"], "#888")
        out.append('<circle cx="%.1f" cy="%.1f" r="6.5" fill="%s" fill-opacity="0.82"/>'
                   % (X(p["x"]), Y(p["y"]), c))

    # 目标点十字
    if tp:
        tx, ty = X(tp["x"]), Y(tp["y"])
        out.append('<circle cx="%.1f" cy="%.1f" r="17" fill="none" stroke="#ffc72c" '
                   'stroke-width="2.5"/>' % (tx, ty))
        out.append('<circle cx="%.1f" cy="%.1f" r="6" fill="#ffc72c"/>' % (tx, ty))
        out.append('<text x="%.1f" y="%.1f" fill="#ffc72c" font-size="19" '
                   'font-weight="700" font-family="PingFang SC,Microsoft YaHei,sans-serif">'
                   '你的目标点</text>' % (tx + 24, ty + 6))

    # 坐标轴
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#6b6560" stroke-width="1.6"/>'
               % (PAD_L, PAD_T + PH, PAD_L + PW, PAD_T + PH))
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#6b6560" stroke-width="1.6"/>'
               % (PAD_L, PAD_T, PAD_L, PAD_T + PH))
    for i in range(6):
        gx = PAD_L + PW * i / 5
        out.append('<text x="%.1f" y="%d" fill="#8b8378" font-size="15" text-anchor="middle" '
                   'font-family="sans-serif">%.0f</text>' % (gx, PAD_T + PH + 26, max_x * i / 5))
        gy = PAD_T + PH * i / 5
        out.append('<text x="%d" y="%.1f" fill="#8b8378" font-size="15" text-anchor="end" '
                   'font-family="sans-serif">%.0f</text>' % (PAD_L - 12, gy + 5, max_y * (1 - i / 5)))

    out.append('<text x="%.1f" y="%d" fill="#c9c1b6" font-size="19" text-anchor="middle" '
               'font-family="PingFang SC,Microsoft YaHei,sans-serif">'
               '营养密度 X （蛋白质 + 碳水）/ 热量 × 100</text>' % (PAD_L + PW / 2, H - 26))
    out.append('<text x="26" y="%.1f" fill="#c9c1b6" font-size="19" text-anchor="middle" '
               'transform="rotate(-90 26 %.1f)" '
               'font-family="PingFang SC,Microsoft YaHei,sans-serif">'
               '钠负担 Y （钠）/ 热量 × 100</text>' % (PAD_T + PH / 2, PAD_T + PH / 2))

    # 象限标签（位置严格落在各自象限内）
    labels = [
        ("主力区", X(x_th) + 16, Y(y_th) + 30, "#5FC98C"),     # 右下
        ("双刃区", X(x_th) + 16, PAD_T + 34, "#E0746A"),      # 右上
        ("解馋区", PAD_L + 16, PAD_T + 34, "#F0A96E"),        # 左上
        ("填充区", PAD_L + 16, PAD_T + PH - 16, "#7BA3CE"),    # 左下
    ]
    for name, lx, ly, col in labels:
        cnt = quads.get({"主力区": "sweet_point", "双刃区": "double_edge",
                         "解馋区": "treat", "填充区": "filler"}[name], {}).get("count", 0)
        out.append('<text x="%.1f" y="%.1f" fill="%s" font-size="21" font-weight="700" '
                   'font-family="PingFang SC,Microsoft YaHei,sans-serif">%s · %d</text>'
                   % (lx, ly, col, name, cnt))

    out.append('</svg>')

    p = os.path.join(OUT, "sp-matrix.svg")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("已生成：%s" % p)
    print("点数 %d  阈值 x=%.2f y=%.2f" % (len(pts), x_th, y_th))
    for k in ("sweet_point", "double_edge", "treat", "filler"):
        print("  %-12s %s %d" % (k, CN[k], quads[k]["count"]))


if __name__ == "__main__":
    main()
