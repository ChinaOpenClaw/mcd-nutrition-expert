# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · 套餐求解器
================================================
在 160 条真实营养数据中，为用户求解**最贴合其营养目标**的套餐组合。

核心算法：多目标约束下的加权打分 + 局部搜索
------------------------------------------------
1. **套餐结构约束**（来自麦当劳官方 `query-meals` 的企业团餐搭配规则精神）：
       20 元以下       → 小食
       20–30 元        → 汉堡 + 小食
       30–50 元        → 汉堡 + 小食 + 饮品
       50 元以上       → 汉堡 + 小食 + 饮品 + 甜点
   求解器强制要求组合中至少含 1 个主餐、1 个小食或饮品，保证结构完整、
   吃法正常（不会给你推荐"三个甜点"这种荒谬组合）。

2. **多目标加权打分**，每个维度归一化到 0–1 后加权求和：
       热量贴合度   30%   —— 与目标热量的相对误差
       蛋白达标度   22%   —— 蛋白质 / 目标蛋白质
       碳水达标度   16%   —— 碳水 / 目标碳水
       脂肪达标度   12%   —— 脂肪 / 目标脂肪
       钠友好度     14%   —— 越低越好（硬约束：不超钠上限）
       饱腹性价比    6%   —— 蛋白 + 纤维感餐品带来的饱腹价值 / 热量

3. **硬约束**（违反直接淘汰）：
       · 总热量落在目标热量的 [60%, 130%] 区间
       · 钠摄入不超过目标钠上限
       · 必须含主餐

4. **局部搜索改进**：从打分最高的 Top-K 种子出发，尝试
   「加一份 / 减一份 / 换一份」三种邻域操作，取更优解。

5. **多样性输出**：返回前 N 个方案时做去重，避免给出三个几乎一样的套餐。

评分函数完全透明，每个维度的贡献都会写进 `explain`，便于在对话中
向用户解释「为什么推荐这一套」。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict, field, replace
from typing import Dict, List, Optional, Sequence

from nutrition_engine import (
    BodyProfile,
    FoodItem,
    NutritionTarget,
    classify,
    compute_target,
    strategy_tips,
)

# ============================================================
# 套餐结构
# ============================================================

@dataclass
class ComboItem:
    food: FoodItem
    category: str
    count: int = 1

    @property
    def kcal(self) -> float:
        return (self.food.energy_kcal or 0) * self.count

    @property
    def protein(self) -> float:
        return (self.food.protein or 0) * self.count

    @property
    def fat(self) -> float:
        return (self.food.fat or 0) * self.count

    @property
    def carb(self) -> float:
        return (self.food.carbohydrate or 0) * self.count

    @property
    def sodium(self) -> float:
        return (self.food.sodium or 0) * self.count

    @property
    def calcium(self) -> float:
        return (self.food.calcium or 0) * self.count

    def to_dict(self) -> Dict:
        return {
            "name": self.food.name,
            "category": self.category,
            "count": self.count,
            "kcal": round(self.kcal),
            "protein_g": round(self.protein, 1),
            "fat_g": round(self.fat, 1),
            "carb_g": round(self.carb, 1),
            "sodium_mg": round(self.sodium),
            "calcium_mg": round(self.calcium),
        }


@dataclass
class Combo:
    """一份套餐方案"""
    items: List[ComboItem]
    total_kcal: float
    total_protein: float
    total_fat: float
    total_carb: float
    total_sodium: float
    total_calcium: float
    score: float
    explain: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    # 全天口径参考（便于前端展示「本餐占全天 X%」）
    day_target_kcal: float = 0.0
    day_target_protein_g: float = 0.0
    meal_target_kcal: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "items": [i.to_dict() for i in self.items],
            "totals": {
                "kcal": round(self.total_kcal),
                "protein_g": round(self.total_protein, 1),
                "fat_g": round(self.total_fat, 1),
                "carb_g": round(self.total_carb, 1),
                "sodium_mg": round(self.total_sodium),
                "calcium_mg": round(self.total_calcium),
            },
            "score": round(self.score, 4),
            "explain": {k: round(v, 4) for k, v in self.explain.items()},
            "warnings": self.warnings,
            "tags": self.tags,
            "day_target": {
                "kcal": round(self.day_target_kcal),
                "protein_g": round(self.day_target_protein_g, 1),
                "meal_target_kcal": round(self.meal_target_kcal),
                "meal_kcal_share": round(
                    self.total_kcal / self.day_target_kcal * 100, 1
                ) if self.day_target_kcal > 0 else 0.0,
            },
        }


# ============================================================
# 结构角色模型
# ============================================================
# 单纯按「分类」做笛卡尔积会产生「两个雪冰 + 一杯奶昔」这种
# 甜品堆叠的荒谬组合。真实的一餐应该有明确的角色分工：
#     蛋白源 → 饱腹主食 → 配菜/小食 → 饮品（→ 甜点）
# 这里把每个餐品映射到角色，套餐按「角色槽位」组装，
# 保证结构上像一顿正常的饭。
#
# 每个槽位的可选数量由 TARGET_KCAL 决定：
#     小份套餐：蛋白源 + 饮品
#     常规套餐：蛋白源 + 小食 + 饮品
#     丰盛套餐：蛋白源 + 小食 + 饮品 + 甜点
ROLE_MAIN = "protein"      # 蛋白源（汉堡/鸡堡/牛堡/鱼堡/麦满分）
ROLE_SIDE = "side"         # 配菜小食（薯条/鸡块/玉米杯/薯饼）
ROLE_DRINK = "drink"       # 饮品
ROLE_DESSERT = "dessert"   # 甜点

# 角色归类关键词（按优先级匹配）
ROLE_RULES = [
    # 甜点优先判定，避免"雪冰"被当成饮品
    ("新地", ROLE_DESSERT),
    ("麦旋风", ROLE_DESSERT),
    ("派", ROLE_DESSERT),
    ("圣代", ROLE_DESSERT),
    ("布丁", ROLE_DESSERT),
    ("奶冻", ROLE_DESSERT),
    ("爆珠", ROLE_DESSERT),
    ("冰淇淋", ROLE_DESSERT),
    ("雪冰", ROLE_DESSERT),
    ("圆筒", ROLE_DESSERT),
    # 蛋白源
    ("麦满分", ROLE_MAIN),
    ("汉堡", ROLE_MAIN),
    ("鸡腿堡", ROLE_MAIN),
    ("板烧鸡腿", ROLE_MAIN),
    ("巨无霸", ROLE_MAIN),
    ("双层", ROLE_MAIN),
    ("鱼排堡", ROLE_MAIN),
    ("鳕鱼堡", ROLE_MAIN),
    ("牛堡", ROLE_MAIN),
    ("鸡排", ROLE_MAIN),
    ("吉士", ROLE_MAIN),
    ("蔬萃", ROLE_MAIN),
    ("不素之霸", ROLE_MAIN),
    ("麦香鸡", ROLE_MAIN),
    ("麦香鱼", ROLE_MAIN),
    ("营养卷", ROLE_MAIN),
    # 配菜
    ("薯条", ROLE_SIDE),
    ("麦乐鸡", ROLE_SIDE),
    ("鸡翅", ROLE_SIDE),
    ("脆汁鸡", ROLE_SIDE),
    ("脆香鸡", ROLE_SIDE),
    ("鸡块", ROLE_SIDE),
    ("脆薯", ROLE_SIDE),
    ("薯饼", ROLE_SIDE),
    ("玉米杯", ROLE_SIDE),
    ("芝士棒", ROLE_SIDE),
    ("芝士条", ROLE_SIDE),
    ("麦麦脆", ROLE_SIDE),
    # 饮品
    ("可乐", ROLE_DRINK),
    ("雪碧", ROLE_DRINK),
    ("咖啡", ROLE_DRINK),
    ("美式", ROLE_DRINK),
    ("拿铁", ROLE_DRINK),
    ("玛奇朵", ROLE_DRINK),
    ("奶茶", ROLE_DRINK),
    ("奶铁", ROLE_DRINK),
    ("牛奶", ROLE_DRINK),
    ("豆浆", ROLE_DRINK),
    ("果汁", ROLE_DRINK),
    ("纯悦", ROLE_DRINK),
    ("茶", ROLE_DRINK),
    ("柠檬", ROLE_DRINK),
    ("红茶", ROLE_DRINK),
    ("阳光橙", ROLE_DRINK),
    ("黄金橙橙", ROLE_DRINK),
    ("拉明顿", ROLE_DRINK),
    ("美汁源", ROLE_DRINK),
    # 早餐主食
    ("德式", ROLE_MAIN),
    ("香肠", ROLE_MAIN),
    ("麦满分", ROLE_MAIN),
]


def role_of(name: str) -> str:
    """判定餐品在套餐中的角色"""
    for kw, role in ROLE_RULES:
        if kw in name:
            return role
    return "other"


def role_of_dessert_present(items: Sequence["ComboItem"]) -> bool:
    """组合中是否含甜点"""
    return any(role_of(i.food.name) == ROLE_DESSERT for i in items)


# 套餐规模档位：(最大单品数, 必需槽位, 可选槽位)
STRUCTURE_TIERS = [
    (2, [ROLE_MAIN, ROLE_DRINK], []),
    (3, [ROLE_MAIN, ROLE_SIDE, ROLE_DRINK], []),
    (4, [ROLE_MAIN, ROLE_SIDE, ROLE_DRINK], [ROLE_DESSERT]),
]


def _structure_ok(items: Sequence[ComboItem]) -> bool:
    """校验组合的结构合法性：必需槽位齐全，甜点最多 1 个，蛋白源唯一"""
    roles = [role_of(i.food.name) for i in items]
    rset = set(roles)
    # 角色未识别的餐品（other）不允许出现在套餐里 ——
    # 它们多是「麦旋酷阳光橙」这类含糖饮料或未归类单品，
    # 混进来会让套餐结构失控。
    if "other" in rset:
        return False
    if roles.count(ROLE_MAIN) != 1:
        return False
    if roles.count(ROLE_DESSERT) > 1:
        return False
    if roles.count(ROLE_SIDE) > 1:
        return False
    if roles.count(ROLE_DRINK) > 1:
        return False
    # 至少要有 主餐 + 饮品
    if ROLE_MAIN not in rset or ROLE_DRINK not in rset:
        return False
    return True


# ============================================================
# 权重
# ============================================================

WEIGHTS = {
    "calorie": 0.26,
    "protein": 0.28,
    "carb": 0.13,
    "fat": 0.11,
    "sodium": 0.14,
    "satiety": 0.08,
}

# 饱腹价值：每类餐品的"每 100 kcal 饱腹贡献"
SATIETY_VALUE = {
    "主餐": 0.92,
    "小食": 0.45,
    "饮品": 0.30,
    "甜点": 0.35,
    "早餐": 0.60,
    "其他": 0.40,
}

# 角色级热量上限。
# 上限取「相对单餐热量的比例」与「单品真实规格上限」的较大值：
#   - 相对上限：防止饮品/甜点成为套餐热量主体
#     （「抹茶奶 333 kcal + 雪冰 309 kcal」曾占掉减脂套餐 36% 热量）
#   - 绝对上限：贴合麦当劳真实单品规格，否则大薯条（379 kcal）
#     这类经典配菜在低热量单餐目标下会被完全排除
ROLE_KCAL_CAP = {
    ROLE_SIDE: 0.45,
    ROLE_DRINK: 0.25,
    ROLE_DESSERT: 0.20,
}
ROLE_KCAL_ABS_CAP = {
    ROLE_SIDE: 400.0,     # 大薯条 379 / 大杯玉米杯 87
    ROLE_DRINK: 250.0,    # 热奶铁大杯 249 / 抹茶牛奶大杯 333（会被比例上限拦）
    ROLE_DESSERT: 300.0,  # 朱古力新地 282
}


# 高饱腹低热量的饮品（加分项）
ZERO_CAL_DRINKS = {"无糖可口可乐", "纯牛奶（盒装）"}


# ============================================================
# 核心求解
# ============================================================

def _norm_hit(value: float, target: float, tolerance: float = 0.15) -> float:
    """
    达标度：value 命中 target 附近得高分。
    超出 tolerance 后按比例衰减，最低 0。
    """
    if target <= 0:
        return 1.0
    ratio = value / target
    if abs(ratio - 1.0) <= tolerance:
        return 1.0
    over = (abs(ratio - 1.0) - tolerance) / max(tolerance, 0.01)
    return max(0.0, 1.0 - over * 0.8)


def _sodium_score(value: float, limit: int) -> float:
    """钠友好度：0mg 得 1.0，达到上限得 0.35，超限得分快速下降但不为负"""
    if value <= 0:
        return 1.0
    r = value / limit
    if r <= 0.5:
        return 1.0 - 0.3 * (r / 0.5)
    if r <= 1.0:
        return 0.7 - 0.35 * ((r - 0.5) / 0.5)
    return max(0.0, 0.35 - (r - 1.0) * 0.9)


def _score_combo(items: Sequence[ComboItem], target: NutritionTarget,
                 goal: str = "maintain") -> Optional[Dict]:
    """
    对一个组合打分。违反硬约束返回 None。
    返回 {'score': float, 'explain': dict}

    ⚠️ target 传入的应是「单餐目标」（由全天目标 × 单餐份额折算），
       不是全天目标。早期实现直接把全天 target_kcal（如 1771）当单餐用，
       导致主餐上限被设成 567 kcal、蛋白质目标 140 g，
       而麦当劳一餐根本达不到那个量级 —— 求解结果要么 0 解、要么荒唐。
    """
    if not items:
        return None
    cats = {i.category for i in items}
    # 同一款餐品不重复出现（避免「热浓浓抹茶牛奶大杯 ×2」这种无意义组合）
    names = [i.food.name for i in items]
    if len(set(names)) != len(names):
        return None
    # 结构合法性：主餐唯一、饮品唯一、小食唯一、甜点最多 1
    if not _structure_ok(items):
        return None

    # ---- 硬约束 ----
    if "主餐" not in cats:
        return None
    if len(items) > 5:
        return None

    kcal = sum(i.kcal for i in items)
    protein = sum(i.protein for i in items)
    fat = sum(i.fat for i in items)
    carb = sum(i.carb for i in items)
    sodium = sum(i.sodium for i in items)

    if kcal <= 0:
        return None
    # 热量区间约束：下限按件数分档（单餐口径）。
    # 一餐由 2–4 件构成，件数少时热量天然更低，
    # 固定下限会误杀合理的小份套餐。
    lo_ratio = {2: 0.45, 3: 0.62, 4: 0.75, 5: 0.85}.get(len(items), 0.60)
    lo, hi = target.target_kcal * lo_ratio, target.target_kcal * 1.25
    if not (lo <= kcal <= hi):
        return None
    # 钠硬上限：放宽到 1.25 倍（麦当劳主餐钠普遍 900–1200 mg，
    # 若按 WHO 2000 mg 严格执行会把绝大多数真实套餐全部淘汰，
    # 无法给出可用建议）。超限部分在评分中强烈扣分并生成显式警告。
    if sodium > target.sodium_limit_mg * 1.25:
        return None
    # ⚠️ 控卡期结构性约束：减脂 / 塑形模式不推荐带甜点的套餐。
    #    「麦辣鸡腿堡 + 脆汁鸡 + 草莓新地」虽然每项单看都合理，
    #    但在热量管控期，甜点带来的 200+ kcal 纯糖会直接挤掉蛋白额度，
    #    与用户「身材管控期」的诉求相悖。增肌/维持模式保留甜点。
    if goal in ("cut", "recomp") and role_of_dessert_present(items):
        return None
    # ⚠️ 单项合理性：主餐必须占本餐热量的 40%–85%。
    #    这两个数字是按「单餐口径」重新标定的：麦当劳主餐本身
    #    （麦香鱼 325 / 板烧鸡腿堡 391 / 巨无霸 513 / 安格斯 696）
    #    就占一餐 500–800 kcal 的 50%–85%。
    #    早期按全天目标设 16%–32%，主餐窗口被压到 96–193 kcal，
    #    结果一个主餐都选不出来（全模式 0 解）。
    main_kcal = sum(i.kcal for i in items if role_of(i.food.name) == ROLE_MAIN)
    main_hi = max(target.target_kcal * 0.85, 720.0)   # 安格斯 696 是麦当劳真实主餐
    main_lo = min(target.target_kcal * 0.40, 300.0)   # 吉士汉堡包 294 是最小主餐
    if main_kcal > main_hi:
        return None
    if main_kcal < main_lo:
        return None
    # ⚠️ 角色级热量上限：饮品与甜点不得成为套餐的热量主体。
    #    实测「热浓浓抹茶牛奶大杯 333 kcal」+「雪冰大杯 309 kcal」曾占掉
    #    减脂套餐 36% 的热量，结构荒唐。这里对每个非主餐角色单独设上限。
    for role, cap_ratio in ROLE_KCAL_CAP.items():
        r_kcal = sum(i.kcal for i in items if role_of(i.food.name) == role)
        limit_kcal = max(target.target_kcal * cap_ratio,
                         ROLE_KCAL_ABS_CAP.get(role, 1e9))
        if r_kcal > limit_kcal:
            return None

    # ⚠️ 配菜质量下限：玉米杯（53 kcal / 2 g 蛋白）这类「纯碳水配菜」
    #    在评分上因低热量低钠而总能胜出，把真正的蛋白源（脆汁鸡 19–22 g）
    #    挤出套餐。减脂场景下配菜必须带蛋白，否则「高蛋白套餐」名不副实。
    #    门槛：配菜至少提供 6 g 蛋白，或至少 120 kcal（份量型主食）。
    side_items = [i for i in items if role_of(i.food.name) == ROLE_SIDE]
    if side_items:
        side_p = sum(i.protein for i in side_items)
        side_k = sum(i.kcal for i in side_items)
        if side_p < 6 and side_k < 120:
            return None

    # ---- 各维度归一化 ----
    s_cal = _norm_hit(kcal, target.target_kcal, 0.15)
    s_pro = _norm_hit(protein, target.protein_g, 0.15)
    s_car = _norm_hit(carb, target.carb_g, 0.20)
    s_fat = _norm_hit(fat, target.fat_g, 0.25)
    s_sod = _sodium_score(sodium, target.sodium_limit_mg)

    # 饱腹性价比：饱腹价值 / 热量密度（每 100kcal 贡献）
    # 零卡饮品（无糖可乐/纯悦）不贡献热量，但对减脂套餐的饱腹与水分补充
    # 有实际价值，因此给一个固定饱腹加分而不是 0。
    satiety = sum(SATIETY_VALUE.get(i.category, 0.4) * i.kcal for i in items)
    zero_cal_count = sum(
        1 for i in items
        if role_of(i.food.name) == ROLE_DRINK and i.kcal == 0
    )
    satiety += zero_cal_count * 60.0
    s_sat = min(1.0, (satiety / max(kcal, 1.0)) / 0.85)

    explain = {
        "calorie": s_cal,
        "protein": s_pro,
        "carb": s_car,
        "fat": s_fat,
        "sodium": s_sod,
        "satiety": s_sat,
    }
    score = sum(explain[k] * WEIGHTS[k] for k in WEIGHTS)

    # 零卡饮品额外加分（减脂期特别有价值）
    # 名称是「无糖可口可乐中杯 / 大杯 / 小杯」，需前缀匹配而非全等
    names = {i.food.name for i in items}
    if any(n.startswith(("无糖可口可乐", "纯悦")) or n in ZERO_CAL_DRINKS
           for n in names):
        score += 0.04
        explain["zero_cal_drink_bonus"] = 1.0

    # ⚠️ 目标模式修正项：
    #    同一套菜在「增肌」和「减脂」下的合理性完全不同。
    #    减脂/塑形期，333 kcal 的抹茶奶 + 245 kcal 草莓新地这类
    #    「高糖高卡配角」虽然不违反硬约束，但会把总热量顶到目标之上、
    #    挤掉真正的蛋白来源。目标模式修正项直接对这类配角扣分。
    if goal in ("cut", "recomp"):
        # 甜点：减脂期强烈不建议
        dessert_kcal = sum(i.kcal for i in items if role_of(i.food.name) == ROLE_DESSERT)
        if dessert_kcal > 0:
            pen = min(0.12, dessert_kcal / max(target.target_kcal, 1) * 0.9)
            score -= pen
            explain["dessert_penalty"] = -round(pen, 3)
        # 饮品：超过 150 kcal 的饮品（多为含糖奶饮）在减脂期扣分
        drink_kcal = sum(i.kcal for i in items if role_of(i.food.name) == ROLE_DRINK)
        if drink_kcal > 150:
            pen = min(0.10, (drink_kcal - 150) / max(target.target_kcal, 1) * 0.5)
            score -= pen
            explain["sweet_drink_penalty"] = -round(pen, 3)

    return {"score": score, "explain": explain,
            "kcal": kcal, "protein": protein, "fat": fat,
            "carb": carb, "sodium": sodium}


def _build_combo(items: List[ComboItem], scored: Dict, target: NutritionTarget,
                 goal: str, orig_protein_g: Optional[float] = None,
                 day_target_kcal: float = 0.0,
                 day_target_protein: float = 0.0,
                 meal_target_kcal: float = 0.0) -> Combo:
    """把打分结果组装成 Combo，并生成可读的解释与标签"""
    kcal = scored["kcal"]
    protein = scored["protein"]
    fat = scored["fat"]
    carb = scored["carb"]
    sodium = scored["sodium"]
    calcium = sum(i.calcium for i in items)

    warnings: List[str] = []
    # 钠提醒（未超但偏高）
    if sodium > target.sodium_limit_mg:
        warnings.append("⚠️ 钠摄入 %d mg，**已超出 %d mg 上限**（超出 %d mg）。"
                        "这是本方案最需要你知晓的取舍点：建议把含糖饮料换成无糖、"
                        "并避免同一天再叠加其他高钠食物。"
                        % (round(sodium), target.sodium_limit_mg,
                           round(sodium - target.sodium_limit_mg)))
    elif sodium > target.sodium_limit_mg * 0.75:
        warnings.append("钠摄入 %d mg，已达上限的 %d%%，建议搭配无糖饮品并注意当日其他饮食的钠来源。"
                        % (round(sodium), round(sodium / target.sodium_limit_mg * 100)))
    # 热量提醒
    if kcal > target.target_kcal * 1.15:
        warnings.append("热量比目标高 %d kcal，训练日可接受，休息日建议减一份小食。"
                        % round(kcal - target.target_kcal))
    elif kcal < target.target_kcal * 0.75:
        warnings.append("热量比目标低 %d kcal，长期偏低会降低基础代谢，建议加一份小食或饮品。"
                        % round(target.target_kcal - kcal))
    # 蛋白提醒
    if orig_protein_g and orig_protein_g > target.protein_g * 1.15:
        warnings.append("本方案蛋白质 %d g；按你的减脂目标，单餐理论需要 %d g，"
                        "但麦当劳单餐最多只能提供约 %d g 蛋白——"
                        "缺口建议用鸡胸/蛋白棒/无糖酸奶补足，别靠加餐麦当劳来凑。"
                        % (round(protein), round(orig_protein_g), round(target.protein_g)))
    elif protein < target.protein_g * 0.8:
        warnings.append("蛋白质 %d g，略低于本餐目标 %d g，训练后可额外补一份麦乐鸡或蛋白类单品。"
                        % (round(protein), round(target.protein_g)))

    tags: List[str] = []
    cats = {i.category for i in items}
    if "饮品" in cats:
        drink = next((i.food.name for i in items if i.category == "饮品"), "")
        if "无糖" in drink:
            tags.append("零卡饮品")
        elif "可乐" in drink or "雪碧" in drink:
            tags.append("含糖饮料")
    if "甜点" in cats:
        tags.append("含甜点")
    if "小食" in cats:
        tags.append("含小食")
    if protein >= target.protein_g:
        tags.append("蛋白达标")
    if sodium <= target.sodium_limit_mg * 0.6:
        tags.append("低钠友好")
    if kcal <= target.target_kcal * 0.95:
        tags.append("热量留余量")
    else:
        tags.append("热量充足")
    if goal == "cut" and kcal <= target.target_kcal:
        tags.append("适合控卡期")

    return Combo(
        items=items,
        total_kcal=kcal,
        total_protein=protein,
        total_fat=fat,
        total_carb=carb,
        total_sodium=sodium,
        total_calcium=calcium,
        score=scored["score"],
        explain=scored["explain"],
        warnings=warnings,
        tags=tags,
        day_target_kcal=day_target_kcal,
        day_target_protein_g=day_target_protein,
        meal_target_kcal=meal_target_kcal,
    )


def _combo_key(items: Sequence[ComboItem]) -> str:
    return "|".join(sorted("%s x%d" % (i.food.name, i.count) for i in items))


def solve(foods: List[FoodItem], target: NutritionTarget, goal: str = "maintain",
          top_n: int = 3, seed_limit: int = 260,
          meal_share: float = 0.34) -> List[Combo]:
    """
    主求解入口。

    foods       —— 来自 MCP list-nutrition-foods 的餐品列表
    target      —— 由 nutrition_engine.compute_target 得到的**全天**营养目标
    goal        —— 目标模式，用于标签与策略
    top_n       —— 返回方案数
    meal_share  —— 一餐占全天目标的比例。默认 0.34（约早/中/晚三餐中的一餐）。
                   全天目标 1771 kcal → 单餐目标 602 kcal，与麦当劳实际一餐
                   （主餐 + 小食 + 饮品 ≈ 500–800 kcal）量级吻合。
    """
    # ⚠️ 全天目标 → 单餐目标折算。
    #    早期实现直接把全天 target_kcal 当单餐目标用：减脂全天 1771 kcal，
    #    单餐却按 1771 kcal 求解，于是主餐上限被设成 566 kcal（安格斯 696 被淘汰、
    #    勉强放行 485）、蛋白目标 140 g（麦当劳一餐最多 55 g，永远不达标），
    #    最终结果既不合理又 0 解。折算到单餐后所有比例才恢复正常。
    day_target = target
    if meal_share <= 0 or meal_share >= 1:
        meal_share = 0.34
    meal_kcal = target.target_kcal * meal_share
    meal_protein = target.protein_g * meal_share
    meal_fat = target.fat_g * meal_share
    meal_carb = target.carb_g * meal_share
    # 钠不分摊：WHO 上限 2000 mg 是全天口径，而麦当劳单餐钠普遍 900–1500 mg，
    # 分摊到单餐会让几乎所有套餐都判超标，直接失去指导意义。
    meal_sodium = target.sodium_limit_mg

    # 构造单餐口径的目标对象，后续求解全部基于它
    meal_target = replace(
        target,
        target_kcal=meal_kcal,
        protein_g=meal_protein,
        fat_g=meal_fat,
        carb_g=meal_carb,
        sodium_limit_mg=meal_sodium,
    )
    day_target_kcal = target.target_kcal
    day_target_protein = target.protein_g
    # 求解内部统一使用 meal_target
    target = meal_target

    # 按分类建索引，过滤掉明显不适合的条目（零能量且无营养价值的条目）
    # 真实接口中部分字段可能为 null，统一兜底为 0
    def _kcal(f: FoodItem) -> float:
        return float(f.energy_kcal or 0)

    # ⚠️ 候选池必须保留「零卡饮品」（无糖可口可乐 0 kcal / 纯悦 0 kcal）。
    #    早期用 `0 < kcal < 900` 一刀切，把零卡饮品全部过滤掉，
    #    导致饮品候选只剩 100–333 kcal 的含糖奶饮/可乐，
    #    减脂套餐被迫塞高热量饮品。这正是减脂场景最该推荐的选项。
    pool = [f for f in foods if 0 < _kcal(f) < 900]
    zero_cal = [f for f in foods
                if _kcal(f) == 0 and classify(f.name) == "饮品"]
    pool = pool + zero_cal
    if not pool:
        pool = [f for f in foods if _kcal(f) > 0]

    # ---- 按「角色」分桶（而不是按大类分桶）----
    by_role: Dict[str, List[FoodItem]] = {
        ROLE_MAIN: [], ROLE_SIDE: [], ROLE_DRINK: [], ROLE_DESSERT: [],
    }
    for f in pool:
        r = role_of(f.name)
        if r in by_role:
            by_role[r].append(f)
    # 兜底：若某角色为空，用其他分类补
    if not by_role[ROLE_MAIN]:
        by_role[ROLE_MAIN] = [f for f in pool if classify(f.name) == "主餐"] or pool[:15]
    if not by_role[ROLE_SIDE]:
        by_role[ROLE_SIDE] = [f for f in pool if classify(f.name) in ("小食", "早餐")] or pool[:12]
    if not by_role[ROLE_DRINK]:
        by_role[ROLE_DRINK] = [f for f in pool if classify(f.name) == "饮品"] or pool[:12]
    if not by_role[ROLE_DESSERT]:
        by_role[ROLE_DESSERT] = [f for f in pool if classify(f.name) == "甜点"]

    def sort_by(key, reverse=False):
        return sorted(pool, key=key, reverse=reverse)

    def by_tier(items: List[FoodItem], n: int) -> List[FoodItem]:
        """按热量分 4 档，每档分位采样若干，保证热量谱覆盖且不偏向最低档。

        ⚠️ 早期实现每档取 `b[:per]`（排序后最靠前的 per 个），
        结果每个角色的候选全是该档最低热量值（薯条取到最小杯、
        甜点只取到 93 kcal 的圆筒），组合总热量永远够不到下限，
        而结构规则又不允许同角色加第二份 → 求解器直接 0 解。
        改为在档内做等距分位采样，才能同时拿到低/中/高热量候选。
        """
        if not items:
            return []
        buckets: List[List[FoodItem]] = [[], [], [], []]
        for f in items:
            k = f.energy_kcal or 0
            idx = 0 if k < 300 else (1 if k < 450 else (2 if k < 600 else 3))
            buckets[idx].append(f)
        per = max(1, n // 4)
        out: List[FoodItem] = []
        for b in buckets:
            if not b:
                continue
            b.sort(key=lambda f: f.energy_kcal or 0)
            if len(b) <= per:
                out.extend(b)
                continue
            # 等距取 per 个：覆盖该档低/中/高热量
            for j in range(per):
                pos = int(round(j * (len(b) - 1) / max(per - 1, 1)))
                item = b[pos]
                if item not in out:
                    out.append(item)
        return out

    limit = max(5, seed_limit // 45)

    # ⚠️ 蛋白天花板校正：即使按单餐口径（如减脂 140 g × 0.34 = 48 g），
    #    麦当劳菜单能提供的蛋白也受限于每个角色的上限。
    #    若目标仍高于可达上限，所有方案蛋白得分都是 0，评分失去区分度，
    #    求解器会退化为「只追热量」。这里按各角色蛋白上限估算可达天花板，
    #    把评分用的蛋白目标收敛到可达区间，并在方案里给出诚实的提示。
    def _role_protein_ceiling(role: str, kcal_cap: float) -> float:
        items = by_role.get(role, [])
        best = 0.0
        for f in items:
            k = float(f.energy_kcal or 0)
            p = float(f.protein or 0)
            if k <= kcal_cap and p > best:
                best = p
        return best

    _ceil = (
        _role_protein_ceiling(ROLE_MAIN, target.target_kcal * 0.85)
        + _role_protein_ceiling(ROLE_SIDE, target.target_kcal * ROLE_KCAL_CAP[ROLE_SIDE])
        + _role_protein_ceiling(ROLE_DRINK, target.target_kcal * ROLE_KCAL_CAP[ROLE_DRINK])
        + _role_protein_ceiling(ROLE_DESSERT, target.target_kcal * ROLE_KCAL_CAP[ROLE_DESSERT])
    )
    protein_target_eff = min(float(target.protein_g), _ceil * 1.05)
    protein_gap = float(target.protein_g) - protein_target_eff
    orig_protein_g = float(target.protein_g)
    if abs(protein_target_eff - float(target.protein_g)) > 1e-6:
        target = replace(target, protein_g=protein_target_eff)

    # 各角色候选：主餐按热量分层 + 高蛋白补充；其余按热量分层
    main_cands = by_tier(by_role[ROLE_MAIN], limit)
    _hp = sort_by(lambda f: (f.protein or 0) / max(f.energy_kcal or 1, 1), reverse=True)
    for f in _hp:
        if role_of(f.name) == ROLE_MAIN and f.name not in {m.name for m in main_cands}:
            if len(main_cands) < limit * 2:
                main_cands.append(f)

    # ⚠️ 关键修正：主餐候选必须围绕「单餐热量」选取。
    #    固定分层采样会均匀覆盖全热量谱，导致高热量主餐（安格斯 707 kcal）
    #    在减脂目标下也反复入选，组合热量顶到上限、蛋白却远低于目标。
    #    这里按单餐热量的 40%–85% 划定主餐热量带（与 _score_combo 的硬约束一致），
    #    并把候选直接裁剪到该带内，保证不会生成「注定被硬约束淘汰」的种子。
    ideal_main_kcal = target.target_kcal * 0.55
    main_lo, main_hi = min(target.target_kcal * 0.40, 300.0), max(target.target_kcal * 0.85, 720.0)
    _band = [f for f in main_cands
             if main_lo <= (f.energy_kcal or 0) <= main_hi]
    _fallback = [f for f in main_cands if f not in _band]
    main_cands.sort(key=lambda f: abs((f.energy_kcal or 0) - ideal_main_kcal))
    main_cands = main_cands[:limit] + [f for f in _fallback if f.name not in {m.name for m in main_cands}]
    # 硬约束下仍可用的主餐优先（其余作为兜底，排在后面）
    main_cands = _band + [f for f in main_cands if f not in _band]

    side_cands = by_tier(by_role[ROLE_SIDE], limit)
    drink_cands = by_tier(by_role[ROLE_DRINK], limit)
    dessert_cands = by_tier(by_role[ROLE_DESSERT], max(3, limit // 2))

    # ---- 按结构槽位组装种子 ----
    seeds: List[List[ComboItem]] = []

    def mk(m, s=None, d=None, de=None):
        items = [ComboItem(m, classify(m.name))]
        if s is not None:
            items.append(ComboItem(s, classify(s.name)))
        if d is not None:
            items.append(ComboItem(d, classify(d.name)))
        if de is not None:
            items.append(ComboItem(de, classify(de.name)))
        return items

    for m in main_cands:
        # 2 件套：主餐 + 饮品
        for d in drink_cands:
            seeds.append(mk(m, d=d))
        # 3 件套：主餐 + 小食 + 饮品
        for s in side_cands:
            for d in drink_cands:
                seeds.append(mk(m, s=s, d=d))
        # 4 件套：主餐 + 小食 + 饮品 + 甜点
        if dessert_cands:
            for s in side_cands[:3]:
                for d in drink_cands[:3]:
                    for de in dessert_cands[:2]:
                        seeds.append(mk(m, s=s, d=d, de=de))

    # 3 件套的另一个方向：主餐 + 甜点 + 饮品（无小食的轻食型）
    for m in main_cands[:limit]:
        for de in dessert_cands[:2]:
            for d in drink_cands[:3]:
                seeds.append(mk(m, d=d, de=de))

    # 1) 种子打分
    scored_seeds: List[Tuple[float, List[ComboItem], Dict]] = []
    seen = set()
    for items in seeds:
        sc = _score_combo(items, target, goal)
        if sc is None:
            continue
        key = _combo_key(items)
        if key in seen:
            continue
        seen.add(key)
        scored_seeds.append((sc["score"], items, sc))
    if not scored_seeds:
        return []

    scored_seeds.sort(key=lambda x: -x[0])

    # 2) 局部搜索改进（加一份 / 减一份 / 换一份）
    all_foods = pool
    best = scored_seeds[:40]
    improved = { _combo_key(i) for _, i, _ in best }

    for _, items, sc in list(best):
        base_items = list(items)
        # 邻域 1：加一份（只加小食/饮品/甜点，不加主餐）
        for f in all_foods:
            if classify(f.name) in ("小食", "饮品", "甜点"):
                cand = base_items + [ComboItem(f, classify(f.name))]
                k = _combo_key(cand)
                if k in improved:
                    continue
                c = _score_combo(cand, target, goal)
                if c:
                    improved.add(k)
                    best.append((c["score"], cand, c))
        # 邻域 2：减一份
        if len(base_items) > 1:
            for idx in range(len(base_items)):
                cand = base_items[:idx] + base_items[idx + 1:]
                k = _combo_key(cand)
                if k in improved:
                    continue
                c = _score_combo(cand, target, goal)
                if c:
                    improved.add(k)
                    best.append((c["score"], cand, c))
        # 邻域 3：换一份（保持结构）
        for idx in range(len(base_items)):
            for f in all_foods:
                if classify(f.name) != base_items[idx].category:
                    continue
                cand = list(base_items)
                cand[idx] = ComboItem(f, classify(f.name))
                k = _combo_key(cand)
                if k in improved:
                    continue
                c = _score_combo(cand, target, goal)
                if c:
                    improved.add(k)
                    best.append((c["score"], cand, c))
                    if len(best) > 600:
                        break

    best.sort(key=lambda x: -x[0])

    # 3) 多样性去重
    #    ⚠️ 早期规则：两份方案同名重叠数 >= min(份数) 即判重复。
    #    这会把「3 件套（主+饮+甜）」和「4 件套（主+饮+甜+小食）」
    #    判成同一个 —— 重叠 2/3 < 3 才幸免，但顺序一变就会误杀，
    #    结果 maintain 模式永远只剩没有小食的 3 件套，蛋白仅 28 g。
    #    正确规则：主餐相同 且（份数相同 或 热量接近）才算重复。
    results: List[Combo] = []
    for score, items, sc in best:
        kc = sc["kcal"]
        c_main = next((i.food.name for i in items
                       if role_of(i.food.name) == ROLE_MAIN), "")
        dup = False
        for r in results:
            r_main = next((i.food.name for i in r.items
                           if role_of(i.food.name) == ROLE_MAIN), "")
            if r_main != c_main:
                continue
            same_len = len(r.items) == len(items)
            kcal_close = abs(r.total_kcal - kc) / max(kc, 1) < 0.08
            if same_len or kcal_close:
                dup = True
                break
        if dup:
            continue
        results.append(_build_combo(
            items, sc, target, goal, orig_protein_g=orig_protein_g,
            day_target_kcal=day_target_kcal,
            day_target_protein=day_target_protein,
            meal_target_kcal=meal_kcal,
        ))
        if len(results) >= top_n:
            break

    return results


# ============================================================
# 解析 MCP 返回
# ============================================================

def parse_nutrition_payload(text: str) -> List[FoodItem]:
    """
    解析 list-nutrition-foods 工具返回的伪表格文本。

    该接口把数据塞在 content[0].text 里，形如：
        # API Response Information
        ...
        ## Original Response
        {"success":true,...,"data":"[160]{f1,f2,...}:\\n  名称,描述,1,2,...\\n  ..."}
    我们从中抽出 data 字段并解析成 FoodItem 列表。
    """
    obj = None
    m = re.search(r"## Original Response\s*\n(.*)", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
        except Exception:
            obj = None
    if obj is None:
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
    if obj is None:
        return []
    if "result" in obj and "content" in obj.get("result", {}):
        try:
            return parse_nutrition_payload(obj["result"]["content"][0]["text"])
        except Exception:
            return []
    data = obj.get("data")
    if data is None:
        return []
    if isinstance(data, str):
        # 伪表格： [160]{fields}:\n  row\n  row ...
        if ":" in data:
            body = data.split(":", 1)[1]
        else:
            body = data
        items: List[FoodItem] = []
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 9:
                continue
            name, desc = parts[0], parts[1]
            try:
                vals = [float(x) if x not in ("null", "", "None") else 0.0 for x in parts[2:9]]
            except ValueError:
                continue
            items.append(FoodItem(
                name=name,
                description=None if desc in ("null", "", "None") else desc,
                energy_kj=vals[0], energy_kcal=vals[1], protein=vals[2],
                fat=vals[3], carbohydrate=vals[4], sodium=vals[5], calcium=vals[6],
            ))
        return items
    if isinstance(data, list):
        out = []
        for d in data:
            out.append(FoodItem(
                name=d.get("productName", ""),
                description=d.get("nutritionDescription"),
                energy_kj=d.get("energyKj", 0), energy_kcal=d.get("energyKcal", 0),
                protein=d.get("protein", 0), fat=d.get("fat", 0),
                carbohydrate=d.get("carbohydrate", 0), sodium=d.get("sodium", 0),
                calcium=d.get("calcium", 0),
            ))
        return out
    return []


# ============================================================
# CLI 自测
# ============================================================

def _selftest() -> int:
    """离线自测：用一小段内嵌数据验证求解器行为"""
    demo = [
        FoodItem("巨无霸", 513, 2146, 27, 26, 42, 961, 171),
        FoodItem("麦香鸡", 369, 1546, 15, 17, 39, 731, 73),
        FoodItem("板烧鸡腿堡", 391, 1638, 23, 17, 35, 1041, 93),
        FoodItem("中薯条", 289, 1210, 4, 12, 38, 165, 18),
        FoodItem("麦乐鸡5块", 213, 890, 12, 12, 13, 422, 9),
        FoodItem("无糖可口可乐中杯", 0, 0, 0, 0, 1, 35, 0),
        FoodItem("可乐中杯", 147, 616, 0, 0, 36, 0, 10),
        FoodItem("朱古力新地", 282, 1178, 5, 9, 44, 126, 182),
    ]
    p = BodyProfile("male", 28, 175, 72, "moderate", "cut")
    t = compute_target(p)
    print("目标：%d kcal / 蛋白 %d g / 碳水 %d g / 脂肪 %d g / 钠上限 %d mg"
          % (t.target_kcal, t.protein_g, t.carb_g, t.fat_g, t.sodium_limit_mg))
    res = solve(demo, t, p.goal, top_n=3)
    if not res:
        print("!! 未求解出方案")
        return 1
    for i, c in enumerate(res, 1):
        print("\n方案 %d  score=%.3f  %d kcal / 蛋白 %.0f g / 钠 %d mg"
              % (i, c.score, c.total_kcal, c.total_protein, c.total_sodium))
        for it in c.items:
            print("   + %-16s (%s)  %d kcal" % (it.food.name, it.category, it.kcal))
        print("   tags:", " / ".join(c.tags))
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
