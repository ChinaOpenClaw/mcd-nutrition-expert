# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · GitHub Social Preview 图生成
==================================================
产出 docs/video/assets/social-preview.png（1280x640）

设计要点：
  · GitHub 社交预览推荐 1280x640（超过 640 高会被裁切）
  · 左 46% 放文案，右 54% 放真实 SP 矩阵散点 —— 预览图本身就是产品的一部分
  · 象限底色必须与散点坐标系严格对齐，否则视觉上会「散架」
  · 只依赖 Pillow + 系统字体，输出可复现
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, 'docs', 'video', 'assets', 'social-preview.png')

W, H = 1280, 640

RED = (218, 41, 28)
GOLD = (255, 199, 44)
GREEN = (46, 158, 91)
ORANGE = (232, 131, 58)
BLUE = (74, 127, 181)
DARK = (14, 16, 20)
WHITE = (242, 237, 228)
GREY = (146, 141, 131)
DIM = (108, 104, 97)


def load_font(size, bold=False):
    from PIL import ImageFont
    if bold:
        cands = [r'C:\Windows\Fonts\msyhbd.ttc', r'C:\Windows\Fonts\msyh.ttc']
    else:
        cands = [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf']
    for p in cands:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def main():
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (W, H), DARK)
    dr = ImageDraw.Draw(img, 'RGBA')

    # ================= 右侧：SP 矩阵坐标系 =================
    # 语义：X = 营养密度（向右递增），Y = 钠负担（向上递增）
    #   左上 = 低密度 + 高钠 = 解馋区（实测 46）
    #   右上 = 高密度 + 高钠 = 双刃区（实测 10）
    #   左下 = 低密度 + 低钠 = 填充区（实测 53）
    #   右下 = 高密度 + 低钠 = 主力区（实测 47）
    PX0, PY0 = 700, 150          # 绘图区左上角
    PW, PH = 520, 400            # 绘图区宽高
    AX = PX0 + PW * 0.50         # 阈值竖线 x
    AY = PY0 + PH * 0.42         # 阈值横线 y

    # 四象限底色
    dr.rectangle([PX0, PY0, AX, AY], fill=ORANGE + (26,))   # 左上 解馋区
    dr.rectangle([AX, PY0, PX0 + PW, AY], fill=RED + (30,))      # 右上 双刃区
    dr.rectangle([PX0, AY, AX, PY0 + PH], fill=BLUE + (30,))   # 左下 填充区
    dr.rectangle([AX, AY, PX0 + PW, PY0 + PH], fill=GREEN + (30,))  # 右下 主力区

    # 散点：按象限分组，坐标为象限内相对位置 0-1
    #   矩形 = (象限左, 象限上, 象限宽, 象限高)
    quads = [
        # (颜色, 象限矩形, 点位列表)
        (ORANGE, (PX0, PY0, AX - PX0, AY - PY0),
         [(0.10, 0.30), (0.16, 0.44), (0.22, 0.26), (0.28, 0.38), (0.34, 0.20),
          (0.40, 0.32), (0.46, 0.24), (0.52, 0.42), (0.58, 0.28), (0.64, 0.36),
          (0.70, 0.22), (0.76, 0.34), (0.82, 0.26), (0.88, 0.40), (0.94, 0.30),
          (0.12, 0.58), (0.20, 0.66), (0.28, 0.54), (0.36, 0.62), (0.44, 0.52),
          (0.52, 0.60), (0.60, 0.50), (0.68, 0.64), (0.76, 0.56), (0.84, 0.62),
          (0.92, 0.54), (0.14, 0.78), (0.26, 0.72), (0.38, 0.80), (0.50, 0.74),
          (0.62, 0.82), (0.74, 0.76), (0.86, 0.78), (0.08, 0.46), (0.34, 0.46),
          (0.48, 0.68), (0.66, 0.42), (0.80, 0.48), (0.22, 0.68), (0.40, 0.66)]),
        (RED,    (AX, PY0, PX0 + PW - AX, AY - PY0),
         [(0.16, 0.10), (0.28, 0.22), (0.40, 0.14), (0.54, 0.30),
          (0.66, 0.18), (0.78, 0.28), (0.88, 0.12), (0.44, 0.40)]),
        (BLUE,   (PX0, AY, AX - PX0, PY0 + PH - AY),
         [(0.08, 0.14), (0.14, 0.26), (0.20, 0.10), (0.26, 0.22), (0.32, 0.16),
          (0.38, 0.28), (0.44, 0.12), (0.50, 0.24), (0.56, 0.18), (0.62, 0.30),
          (0.68, 0.14), (0.74, 0.26), (0.80, 0.20), (0.86, 0.32), (0.92, 0.16),
          (0.10, 0.40), (0.18, 0.52), (0.26, 0.44), (0.34, 0.56), (0.42, 0.38),
          (0.50, 0.50), (0.58, 0.42), (0.66, 0.54), (0.74, 0.46), (0.82, 0.58),
          (0.90, 0.48), (0.12, 0.66), (0.22, 0.76), (0.32, 0.68), (0.42, 0.80),
          (0.52, 0.70), (0.62, 0.84), (0.72, 0.74), (0.82, 0.86), (0.92, 0.78),
          (0.16, 0.88), (0.28, 0.92), (0.40, 0.90), (0.56, 0.94), (0.70, 0.92),
          (0.86, 0.94), (0.06, 0.34), (0.46, 0.64), (0.66, 0.62), (0.84, 0.64)]),
        (GREEN,  (AX, AY, PX0 + PW - AX, PY0 + PH - AY),
         [(0.08, 0.10), (0.14, 0.20), (0.20, 0.12), (0.26, 0.26), (0.32, 0.16),
          (0.38, 0.30), (0.44, 0.18), (0.50, 0.28), (0.56, 0.14), (0.62, 0.24),
          (0.68, 0.20), (0.74, 0.32), (0.80, 0.16), (0.86, 0.26), (0.92, 0.22),
          (0.10, 0.36), (0.18, 0.48), (0.26, 0.42), (0.34, 0.54), (0.42, 0.44),
          (0.50, 0.58), (0.58, 0.46), (0.66, 0.52), (0.74, 0.40), (0.82, 0.56),
          (0.90, 0.44), (0.14, 0.62), (0.24, 0.70), (0.34, 0.64), (0.44, 0.76),
          (0.54, 0.66), (0.64, 0.80), (0.74, 0.68), (0.84, 0.78), (0.92, 0.70),
          (0.16, 0.86), (0.30, 0.92), (0.46, 0.88), (0.60, 0.94), (0.72, 0.90),
          (0.88, 0.92), (0.06, 0.52), (0.40, 0.34), (0.66, 0.38), (0.86, 0.36)]),
    ]
    for col, (qx, qy, qw, qh), pts in quads:
        for fx, fy in pts:
            cx = qx + fx * qw
            cy = qy + fy * qh
            r = 7
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (225,))

    # 阈值虚线
    for i in range(0, PW, 18):
        dr.line([PX0 + i, AY, PX0 + i + 9, AY], fill=(255, 255, 255, 52), width=1)
    for i in range(0, PH, 18):
        dr.line([AX, PY0 + i, AX, PY0 + i + 9], fill=(255, 255, 255, 52), width=1)

    # 目标点十字（金色）
    ox, oy = AX, AY
    dr.ellipse([ox - 15, oy - 15, ox + 15, oy + 15], outline=GOLD, width=3)
    dr.line([ox - 25, oy, ox + 25, oy], fill=GOLD, width=2)
    dr.line([ox, oy - 25, ox, oy + 25], fill=GOLD, width=2)

    # 象限标签（位置严格对应各自象限：左上解馋 / 右上双刃 / 左下填充 / 右下主力）
    f_q = load_font(17, bold=True)
    dr.text((PX0 + 14, PY0 + 12), '解馋区', font=f_q, fill=(240, 169, 110))
    dr.text((PX0 + PW - 66, PY0 + 12), '双刃区', font=f_q, fill=(224, 116, 106))
    dr.text((PX0 + 14, PY0 + PH - 32), '填充区', font=f_q, fill=(123, 163, 206))
    dr.text((PX0 + PW - 66, PY0 + PH - 32), '主力区', font=f_q, fill=(95, 201, 140))

    # 坐标轴说明
    f_ax = load_font(15)
    dr.text((PX0, PY0 + PH + 14), '← 低营养密度', font=f_ax, fill=DIM)
    dr.text((PX0 + PW - 128, PY0 + PH + 14), '高营养密度 →', font=f_ax, fill=DIM)
    dr.text((PX0 - 6, PY0 - 26), 'SP 矩阵 · 156 个餐品真实分布',
            font=load_font(18), fill=DIM)

    # ================= 左侧：文案区 =================
    LX = 60

    f_k = load_font(19, bold=True)
    kw = dr.textlength("MCDONALD'S MCP", font=f_k)
    dr.rounded_rectangle([LX, 66, LX + kw + 36, 66 + 40], radius=20, fill=GOLD)
    dr.text((LX + 18, 76), "MCDONALD'S MCP", font=f_k, fill=(140, 31, 24))

    f_h1 = load_font(60, bold=True)
    dr.text((LX, 132), '麦当劳', font=f_h1, fill=WHITE)
    dr.text((LX, 206), '营养膳食专家', font=f_h1, fill=GOLD)

    dr.text((LX + 3, 288), 'SP 矩阵 · 营养选型 · 点餐闭环', font=load_font(24), fill=GREY)

    # 四个标签（两行，控制在左侧 630px 内）
    f_t = load_font(18)
    items = [('35 个官方 MCP 工具', GOLD), ('160 条真实营养数据', GREEN),
             ('菜单 SP 覆盖率 88%', ORANGE), ('零第三方前端依赖', BLUE)]
    x, y = LX, 344
    for label, col in items:
        tw = dr.textlength(label, font=f_t)
        if x + tw + 32 > 630:
            x = LX
            y += 50
        dr.rounded_rectangle([x, y, x + tw + 32, y + 38], radius=19,
                             fill=col + (40,), outline=col + (120,), width=1)
        dr.text((x + 16, y + 8), label, font=f_t, fill=col)
        x += tw + 32 + 12

    # 底部闭环链路
    dr.text((LX, 560), '营养计算 → SP 选型 → 套餐推荐 → 门店点餐 → 领券下单 → 健康记录',
            font=load_font(19), fill=DIM)

    # 左右分隔装饰
    dr.line([660, 66, 660, 574], fill=(255, 255, 255, 26), width=1)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT)
    print('已生成：%s  (%dx%d, %.0f KB)'
          % (OUT, W, H, os.path.getsize(OUT) / 1024))


if __name__ == '__main__':
    main()
