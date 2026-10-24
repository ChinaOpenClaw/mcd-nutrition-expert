# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · SP 矩阵选型引擎
================================================
用 **SP 矩阵**（Sweet Point 战略定位矩阵）把 160 种麦当劳餐品放进一张二维图，
让"该吃什么"从模糊的直觉变成可解释的坐标定位。

矩阵定义
--------
经典 SP 矩阵（业务增长矩阵）以「相对份额 × 相对利润率」划分四象限，
每个象限对应一种战略动作。本项目把它转译为**个人饮食选型**语境：

    横轴 X = 营养密度（Nutrient Density）
             = (蛋白质 + 碳水) g / 100 kcal
             越右 → 单位热量的营养素越足，饱腹感与代谢收益越高

    纵轴 Y = 钠负担系数（Sodium Burden）
             = 钠 mg / 100 kcal
             越上 → 单位热量的钠负荷越重，心血管与水肿负担越大

四个象限的饮食策略
------------------
    ┌─────────────────────────┬─────────────────────────┐
    │  高钠 · 低营养密度        │  低钠 · 高营养密度        │
    │  【解馋区】              │  【主力区 / Sweet Point】 │
    │  薯条、麦旋风、加料       │  麦满分、麦香鸡、无糖饮   │
    │  → 偶尔放纵，不作正餐     │  → 日常主食首选           │
    ├─────────────────────────┼─────────────────────────┤
    │  高钠 · 高营养密度        │  低钠 · 低营养密度        │
    │  【双刃区】              │  【填充区】              │
    │  巨无霸、板烧鸡腿堡       │  可乐、玉米杯            │
    │  → 蛋白够但控钠          │  → 低热量填充，饮品位     │
    └─────────────────────────┴─────────────────────────┘

选型算法
--------
1. 计算每个餐品的 X / Y 坐标。
2. 用**用户自己的营养目标**确定"理想区"中心点：
   目标点 = (目标蛋白密度, 目标钠密度)。
3. 分象限 + 计算与目标点的**归一化欧氏距离**，
   得到每个餐品的「贴合度」得分。
4. 结合求解器给出的套餐级最优解，给出**单品推荐 + 套餐推荐**双层结果。

之所以要"单品 SP 矩阵"与"套餐求解"两层：
   · 单品矩阵回答「这一餐里我该选哪几样」
   · 套餐求解回答「加起来总共会怎样」
两者结合才是完整的饮食决策。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from nutrition_engine import FoodItem, NutritionTarget, classify

# ============================================================
# 象限定义
# ============================================================

QUADRANTS = {
    "sweet_point": {
        "label": "主力区",
        "en": "Sweet Point",
        "color": "#2E9E5B",
        "desc": "低钠高营养密度，日常正餐首选",
        "advice": "放心吃，这是最符合营养目标的区域",
    },
    "treat": {
        "label": "解馋区",
        "en": "Treat",
        "color": "#E8833A",
        "desc": "高钠低营养密度，偶尔放纵用",
        "advice": "可以吃，但建议作为加餐而非正餐，并搭配无糖饮品",
    },
    "double_edge": {
        "label": "双刃区",
        "en": "Double Edge",
        "color": "#C0392B",
        "desc": "营养密度高但钠负荷重，控钠期需留意",
        "advice": "蛋白质优秀，注意当日总钠摄入，避免叠加其他高钠食物",
    },
    "filler": {
        "label": "填充区",
        "en": "Filler",
        "color": "#4A7FB5",
        "desc": "低钠但营养密度低，适合作为搭配位",
        "advice": "热量低但营养少，适合用来补体积或作为饮品位",
    },
}

# 象限基准线（会被数据统计动态微调）
DEFAULT_X_THRESHOLD = 0.55     # 营养密度 g/100kcal
DEFAULT_Y_THRESHOLD = 0.90     # 钠密度 mg/100kcal


@dataclass
class SpPoint:
    """SP 矩阵上的一个点"""
    name: str
    category: str
    x: float                 # 营养密度 (g/100kcal)
    y: float                 # 钠密度 (mg/100kcal)
    kcal: float
    protein: float
    fat: float
    carb: float
    sodium: float
    quadrant: str
    fit: float = 0.0         # 与用户目标点的贴合度 0–1

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["x"] = round(self.x, 3)
        d["y"] = round(self.y, 3)
        d["fit"] = round(self.fit, 3)
        d["kcal"] = round(self.kcal)
        return d


def _safe(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_sp(foods: List[FoodItem],
               target: Optional[NutritionTarget] = None) -> Dict:
    """
    构建 SP 矩阵。

    返回 {
      'points': [SpPoint...],
      'axes': {'x': {...}, 'y': {...}},
      'quadrants': {key: {..., 'count': n}},
      'thresholds': {'x':..., 'y':...},
      'target_point': {'x':..., 'y':...} | None,
    }
    """
    pts: List[SpPoint] = []
    for f in foods:
        kcal = _safe(f.energy_kcal)
        if kcal <= 0:
            continue
        protein = _safe(f.protein)
        carb = _safe(f.carbohydrate)
        fat = _safe(f.fat)
        sodium = _safe(f.sodium)
        x = (protein + carb) / (kcal / 100.0)      # g / 100kcal
        y = sodium / (kcal / 100.0)               # mg / 100kcal
        pts.append(SpPoint(
            name=f.name, category=classify(f.name),
            x=x, y=y, kcal=kcal, protein=protein, fat=fat,
            carb=carb, sodium=sodium, quadrant="",
        ))

    if not pts:
        return {"points": [], "axes": {}, "quadrants": {}, "thresholds": {},
                "target_point": None}

    # ---- 动态阈值：取中位数与 P75 的折中 ----
    xs = sorted(p.x for p in pts)
    ys = sorted(p.y for p in pts)

    def _pct(arr, q):
        i = int(len(arr) * q)
        return arr[min(i, len(arr) - 1)]

    x_th = _pct(xs, 0.50) * 0.6 + _pct(xs, 0.75) * 0.4
    y_th = _pct(ys, 0.50) * 0.6 + _pct(ys, 0.75) * 0.4
    x_th = max(x_th, 0.15)
    y_th = max(y_th, 0.10)

    # ---- 目标点 ----
    target_point = None
    if target is not None:
        t_kcal = max(target.target_kcal, 1)
        # 用户期望的营养密度：目标蛋白+碳水（g）/ 目标热量 × 100
        t_x = (target.protein_g + target.carb_g) / (t_kcal / 100.0)
        # 用户期望的钠密度：把钠上限摊到目标热量上
        t_y = target.sodium_limit_mg / (t_kcal / 100.0)
        target_point = {"x": t_x, "y": t_y}

    # ---- 象限归属 + 贴合度 ----
    max_x = max(p.x for p in pts) or 1.0
    max_y = max(p.y for p in pts) or 1.0
    for p in pts:
        if p.x >= x_th and p.y < y_th:
            p.quadrant = "sweet_point"
        elif p.x < x_th and p.y >= y_th:
            p.quadrant = "treat"
        elif p.x >= x_th and p.y >= y_th:
            p.quadrant = "double_edge"
        else:
            p.quadrant = "filler"

        if target_point:
            dx = (p.x - target_point["x"]) / max(max_x, 1e-6)
            dy = (p.y - target_point["y"]) / max(max_y, 1e-6)
            dist = math.sqrt(dx * dx + dy * dy)
            p.fit = max(0.0, 1.0 - dist / 1.4)
        else:
            p.fit = 0.0

    # ---- 象限统计 ----
    quad_stats: Dict[str, Dict] = {}
    for key, meta in QUADRANTS.items():
        members = [p for p in pts if p.quadrant == key]
        quad_stats[key] = dict(meta)
        quad_stats[key]["count"] = len(members)
        if members:
            quad_stats[key]["avg_kcal"] = round(sum(m.kcal for m in members) / len(members))
            quad_stats[key]["avg_sodium"] = round(sum(m.sodium for m in members) / len(members))
            quad_stats[key]["items"] = sorted(
                (m.name for m in members),
                key=lambda n: -max([m.fit for m in members if m.name == n] or [0])
            )[:12]

    # 食物在 X/Y 上的分布直方图（供前端画背景网格）
    hist_x = [0] * 10
    hist_y = [0] * 10
    for p in pts:
        hist_x[min(9, int(p.x / max_x * 10))] += 1
        hist_y[min(9, int(p.y / max_y * 10))] += 1

    return {
        "points": [p.to_dict() for p in pts],
        "axes": {
            "x": {"label": "营养密度", "unit": "g/100kcal",
                  "desc": "（蛋白质+碳水）/ 热量，越右越饱腹越高效",
                  "max": round(max_x, 2), "threshold": round(x_th, 2)},
            "y": {"label": "钠负担", "unit": "mg/100kcal",
                  "desc": "钠 / 热量，越上钠负荷越重",
                  "max": round(max_y, 2), "threshold": round(y_th, 2)},
        },
        "quadrants": quad_stats,
        "thresholds": {"x": round(x_th, 3), "y": round(y_th, 3)},
        "target_point": ({"x": round(target_point["x"], 3),
                          "y": round(target_point["y"], 3)} if target_point else None),
        "histogram": {"x": hist_x, "y": hist_y},
        "total": len(pts),
    }


def recommend_by_sp(foods: List[FoodItem], target: NutritionTarget,
                    top_n: int = 8) -> List[Dict]:
    """
    基于 SP 矩阵给单品推荐。

    策略：先按贴合度排序，再做**象限多样性约束**——
    主力区最多 4 个（避免全是同质单品），双刃区最多 2 个，
    保证推荐结果覆盖不同场景。
    """
    matrix = compute_sp(foods, target)
    pts = matrix["points"]
    if not pts:
        return []

    cap = {"sweet_point": 4, "double_edge": 2, "filler": 2, "treat": 2}
    used = {k: 0 for k in cap}
    out: List[Dict] = []
    for p in sorted(pts, key=lambda d: -d["fit"]):
        q = p["quadrant"]
        if used.get(q, 0) >= cap.get(q, 2):
            continue
        used[q] = used.get(q, 0) + 1
        item = dict(p)
        item["quadrant_label"] = QUADRANTS[q]["label"]
        item["quadrant_color"] = QUADRANTS[q]["color"]
        item["quadrant_advice"] = QUADRANTS[q]["advice"]
        out.append(item)
        if len(out) >= top_n:
            break
    return out


def sp_summary(matrix: Dict) -> str:
    """把 SP 矩阵结果转成一段可读的中文描述（用于对话与报告）"""
    if not matrix or not matrix.get("points"):
        return "暂无 SP 矩阵数据。"
    lines = []
    q = matrix["quadrants"]
    total = matrix["total"]
    lines.append("SP 矩阵共定位 %d 种餐品，四象限分布：" % total)
    for key in ("sweet_point", "double_edge", "treat", "filler"):
        meta = q.get(key, {})
        lines.append("  · %s（%s）%d 种 —— %s"
                     % (meta.get("label"), meta.get("en"), meta.get("count", 0),
                        meta.get("desc")))
    return "\n".join(lines)
