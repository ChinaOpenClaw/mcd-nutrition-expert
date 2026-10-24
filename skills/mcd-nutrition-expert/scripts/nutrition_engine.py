# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · 营养目标引擎
================================================
输入用户的身体数据与日常活动量，输出可执行的营养目标
（热量 TDEE、蛋白质 / 脂肪 / 碳水三大营养素配比、钠摄入上限）。

算法依据
--------
1. **BMR（Mifflin-St Jeor 公式）** —— 1990 年营养学界公认的目前精度最高的
   静息代谢率估算式，比 Harris-Benedict 更少受年龄影响：
       男：10×体重(kg) + 6.25×身高(cm) - 5×年龄 + 5
       女：10×体重(kg) + 6.25×身高(cm) - 5×年龄 - 161
2. **TDEE** —— BMR × 活动系数（PAL，Physical Activity Level）。
   系数取值参考 FAO/WHO 体力活动水平分级。
3. **三大营养素配比** —— 按目标（减脂 / 维持 / 增肌）切换，
   采用美国运动医学会（ACSM）推荐的成人耐力运动区间：
       蛋白质 1.2–2.0 g/kg 体重（减脂期取上限，增肌期取上限）
       脂肪     占总热量 20–35%
       碳水     剩余热量
4. **钠上限** —— WHO 建议成人每日 < 2000 mg 钠（约 5 g 食盐）。
   麦当劳主餐钠含量普遍在 500–1200 mg，此项是本专家最具现实价值的约束。
5. **膳食纤维** —— 目标 25–30 g/日，主要来自蔬菜与全谷。

本模块纯计算、零依赖（仅标准库），可独立运行与单测。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional

# ============================================================
# 常量表
# ============================================================

# 活动系数（PAL）
ACTIVITY_LEVELS = {
    "sedentary": {
        "label": "久坐（几乎不运动）",
        "factor": 1.2,
        "desc": "办公室工作、每天步行 < 30 分钟",
    },
    "light": {
        "label": "轻度活动（每周 1–3 次）",
        "factor": 1.375,
        "desc": "每周 1–3 次轻运动，如快走、瑜伽、骑行",
    },
    "moderate": {
        "label": "中度活动（每周 3–5 次）",
        "factor": 1.55,
        "desc": "每周 3–5 次中等强度运动，如慢跑、力量训练",
    },
    "active": {
        "label": "高度活动（每周 6–7 次）",
        "factor": 1.725,
        "desc": "每周 6–7 次高强度训练，如增肌期、球类运动",
    },
    "athlete": {
        "label": "专业运动量（每日两练）",
        "factor": 1.9,
        "desc": "体力劳动或每日双次训练",
    },
}

# 目标模式
GOAL_MODES = {
    "cut": {
        "label": "减脂（热量缺口）",
        "kcal_ratio": 0.80,          # TDEE × 0.80
        "protein_per_kg": 2.0,       # 高蛋白保肌
        "fat_ratio": 0.25,           # 脂肪占总热量下限（激素保障）
        "desc": "约 20% 热量缺口，蛋白质拉满保护肌肉",
    },
    "maintain": {
        "label": "维持（体重管理）",
        "kcal_ratio": 1.00,
        "protein_per_kg": 1.4,
        "fat_ratio": 0.25,
        "desc": "维持当前体重与体脂，摄入 ≈ 消耗",
    },
    "bulk": {
        "label": "增肌（热量盈余）",
        "kcal_ratio": 1.12,
        "protein_per_kg": 1.8,
        "fat_ratio": 0.22,
        "desc": "约 12% 热量盈余，配合训练增肌",
    },
    "recomp": {
        "label": "塑形（轻微缺口 + 高蛋白）",
        "kcal_ratio": 0.90,
        "protein_per_kg": 1.8,
        "fat_ratio": 0.25,
        "desc": "9:1 法则的温和版本，减脂与增肌同步",
    },
}

KCAL_PER_G = {"protein": 4, "fat": 9, "carbohydrate": 4}

# 每日营养素参考目标（成年人，参考《中国居民膳食营养素参考摄入量》）
SODIUM_LIMIT_MG = 2000          # WHO 建议上限
SUGAR_LIMIT_G = 50              # 添加糖上限（世界卫生组织推荐值 25g，留出弹性）
SATURATED_FAT_RATIO = 0.10      # 饱和脂肪占总热量 10% 以内


@dataclass
class BodyProfile:
    """用户身体档案"""
    gender: str                    # male / female
    age: int
    height_cm: float
    weight_kg: float
    activity: str = "light"        # ACTIVITY_LEVELS 的键
    goal: str = "maintain"         # GOAL_MODES 的键
    body_fat_pct: Optional[float] = None   # 可选：已知体脂率则用 Katch-McArdle 修正

    def validate(self) -> List[str]:
        errs: List[str] = []
        if self.gender not in ("male", "female"):
            errs.append("gender 必须是 male 或 female")
        if not (10 <= self.age <= 100):
            errs.append("age 需在 10–100 之间")
        if not (120 <= self.height_cm <= 230):
            errs.append("height_cm 需在 120–230 之间")
        if not (30 <= self.weight_kg <= 250):
            errs.append("weight_kg 需在 30–250 之间")
        if self.activity not in ACTIVITY_LEVELS:
            errs.append("activity 需为 %s 之一" % "/".join(ACTIVITY_LEVELS))
        if self.goal not in GOAL_MODES:
            errs.append("goal 需为 %s 之一" % "/".join(GOAL_MODES))
        if self.body_fat_pct is not None and not (3 <= self.body_fat_pct <= 60):
            errs.append("body_fat_pct 需在 3–60 之间")
        return errs

    @property
    def bmi(self) -> float:
        h = self.height_cm / 100.0
        return self.weight_kg / (h * h)

    @property
    def bmi_label(self) -> str:
        b = self.bmi
        if b < 18.5:
            return "偏瘦"
        if b < 24:
            return "正常"
        if b < 28:
            return "超重"
        return "肥胖"

    @property
    def ideal_weight_kg(self) -> float:
        """理想体重（BMI 22.5 中心值）"""
        h = self.height_cm / 100.0
        return 22.5 * h * h

    def bmr(self) -> float:
        """静息代谢率 kcal/日"""
        base = 10 * self.weight_kg + 6.25 * self.height_cm - 5 * self.age
        if self.body_fat_pct is not None:
            # Katch-McArdle：瘦体重 = 体重 × (1 - 体脂率)
            lbm = self.weight_kg * (1 - self.body_fat_pct / 100.0)
            return 370 + 21.6 * lbm
        return base + (5 if self.gender == "male" else -161)

    def tdee(self) -> float:
        return self.bmr() * ACTIVITY_LEVELS[self.activity]["factor"]


@dataclass
class NutritionTarget:
    """营养目标"""
    bmr: float
    tdee: float
    target_kcal: float
    protein_g: float
    fat_g: float
    carb_g: float
    sodium_limit_mg: int
    sugar_limit_g: int
    bmi: float
    bmi_label: str
    ideal_weight_kg: float
    goal_label: str
    activity_label: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def compute_target(profile: BodyProfile) -> NutritionTarget:
    """
    由身体档案计算营养目标。

    >>> p = BodyProfile("male", 28, 175, 72, "moderate", "cut")
    >>> t = compute_target(p)
    >>> 1800 < t.target_kcal < 2200
    True
    """
    errs = profile.validate()
    if errs:
        raise ValueError("身体档案校验失败：" + "；".join(errs))

    mode = GOAL_MODES[profile.goal]
    bmr = profile.bmr()
    tdee = profile.tdee()
    target_kcal = tdee * mode["kcal_ratio"]

    # 蛋白质：按体重给
    protein_g = profile.weight_kg * mode["protein_per_kg"]

    # 脂肪：占总热量的下限（保证激素与脂溶性维生素吸收）
    fat_g = target_kcal * mode["fat_ratio"] / KCAL_PER_G["fat"]

    # 碳水：剩余热量
    remain = target_kcal - protein_g * KCAL_PER_G["protein"] - fat_g * KCAL_PER_G["fat"]
    if remain < 0:
        # 极端情况：蛋白质+脂肪已超热量 → 按优先级削脂肪
        over = -remain
        fat_g = max(0.0, fat_g - over / KCAL_PER_G["fat"])
        remain = 0.0
    carb_g = remain / KCAL_PER_G["carbohydrate"]

    notes: List[str] = []
    if profile.bmi < 18.5:
        notes.append("BMI %.1f 偏瘦，建议热量目标不低于 %.0f kcal，避免基础代谢下降。"
                     % (profile.bmi, bmr * 1.1))
    if profile.bmi >= 28:
        notes.append("BMI %.1f（%s），减脂期建议同时保证蛋白质与蔬菜摄入，"
                     "以饱腹感控制总摄入。" % (profile.bmi, profile.bmi_label))
    if profile.goal == "cut" and profile.weight_kg < 50:
        notes.append("体重基数较小，热量缺口已按 20% 上限保守设置，避免代谢适应。")
    if profile.goal == "bulk" and profile.age < 18:
        notes.append("未成年人不建议热量盈余，请以均衡与生长发育为先。")
    notes.append("钠摄入上限 %d mg/日（约 %d g 食盐），麦当劳主餐钠含量普遍偏高，"
                 "是本方案优先优化的约束项。" % (SODIUM_LIMIT_MG, SODIUM_LIMIT_MG * 2.5 // 1000))
    notes.append("添加糖建议 < %d g/日，无糖可乐/柠檬茶是更优的饮品选择。" % SUGAR_LIMIT_G)

    return NutritionTarget(
        bmr=round(bmr),
        tdee=round(tdee),
        target_kcal=round(target_kcal),
        protein_g=round(protein_g),
        fat_g=round(fat_g),
        carb_g=round(carb_g),
        sodium_limit_mg=SODIUM_LIMIT_MG,
        sugar_limit_g=SUGAR_LIMIT_G,
        bmi=round(profile.bmi, 1),
        bmi_label=profile.bmi_label,
        ideal_weight_kg=round(profile.ideal_weight_kg, 1),
        goal_label=mode["label"],
        activity_label=ACTIVITY_LEVELS[profile.activity]["label"],
        notes=notes,
    )


# ============================================================
# 餐品营养模型
# ============================================================

@dataclass
class FoodItem:
    """来自 MCP list-nutrition-foods 的单条餐品"""
    name: str
    energy_kcal: float
    energy_kj: float
    protein: float
    fat: float
    carbohydrate: float
    sodium: float
    calcium: float
    description: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


# 餐品分类（用于套餐结构约束与文案）
# ⚠️ 顺序即优先级：靠前的规则先命中。真实营养表有 160 条，
#    关键词表必须覆盖「奶铁/雪冰/豆浆/脆汁鸡/营养卷」等容易漏掉的品类，
#    否则大量餐品会掉进「其他」类，导致套餐求解无可行解。
CATEGORY_RULES = [
    # --- 早餐 ---
    ("麦满分", "早餐"),
    ("猪柳", "早餐"),
    ("烟肉蛋", "早餐"),
    ("火腿扒", "早餐"),
    ("图林根", "早餐"),
    ("营养卷", "早餐"),
    ("安格斯蛋", "早餐"),
    ("原味板烧鸡腿炒双蛋", "早餐"),
    ("粥", "早餐"),
    ("油条", "早餐"),
    ("包子", "早餐"),
    ("豆浆", "饮品"),
    # --- 主餐（汉堡 / 鸡堡 / 牛堡 / 鱼堡）---
    ("麦香鸡", "主餐"),
    ("麦香鱼", "主餐"),
    ("鸡腿堡", "主餐"),
    ("板烧鸡腿", "主餐"),
    ("巨无霸", "主餐"),
    ("双层", "主餐"),
    ("鸡腿麦满分", "主餐"),
    ("汉堡", "主餐"),
    ("鱼排堡", "主餐"),
    ("鳕鱼堡", "主餐"),
    ("牛堡", "主餐"),
    ("鸡排", "主餐"),
    ("麦辣", "小食"),
    ("吉士汉堡", "主餐"),
    ("不素之霸", "主餐"),
    ("蔬萃", "主餐"),
    # --- 小食 ---
    ("麦乐鸡", "小食"),
    ("鸡翅", "小食"),
    ("脆汁鸡", "小食"),
    ("脆香鸡", "小食"),
    ("鸡块", "小食"),
    ("薯条", "小食"),
    ("脆薯饼", "小食"),
    ("玉米杯", "小食"),
    ("脆薯", "小食"),
    ("薯饼", "小食"),
    ("鸡排小食", "小食"),
    ("麦麦脆", "小食"),
    ("芝士棒", "小食"),
    ("芝士条", "小食"),
    ("鸡翅根", "小食"),
    ("洋葱圈", "小食"),
    ("小食", "小食"),
    # --- 甜点 ---
    ("蛋挞", "甜点"),
    ("派", "甜点"),
    ("新地", "甜点"),
    ("麦旋风", "甜点"),
    ("圣代", "甜点"),
    ("布丁", "甜点"),
    ("奶冻", "甜点"),
    ("爆珠", "甜点"),
    ("冰淇淋", "甜点"),
    ("雪冰", "甜点"),
    ("圆筒", "甜点"),
    ("朱古力新地", "甜点"),
    # --- 饮品 ---
    ("奶铁", "饮品"),
    ("拿铁", "饮品"),
    ("玛奇朵", "饮品"),
    ("美式", "饮品"),
    ("卡布奇诺", "饮品"),
    ("奶茶", "饮品"),
    ("茶", "饮品"),
    ("可乐", "饮品"),
    ("雪碧", "饮品"),
    ("咖啡", "饮品"),
    ("牛奶", "饮品"),
    ("果汁", "饮品"),
    ("纯悦", "饮品"),
    ("豆浆", "饮品"),
    ("美汁源", "饮品"),
    ("阳光橙", "饮品"),
    ("柠檬", "饮品"),
    ("红茶", "饮品"),
    ("冷饮", "饮品"),
]


def classify(name: str) -> str:
    """按名称关键词判定餐品分类"""
    for kw, cat in CATEGORY_RULES:
        if kw in name:
            return cat
    return "其他"


# 训练日 / 休息日的饮食策略建议
STRATEGY_TIPS = {
    "cut": [
        "优先选烤制/水煮类单品（麦满分系列、麦香鸡），避开高油裹粉小食。",
        "碳水不必归零，但要把薯条换成玉米杯或沙拉类，把预算留给蛋白质。",
        "饮品一律选无糖：0 热量且钠含量极低，是最划算的减脂决策。",
    ],
    "maintain": [
        "主食 + 蛋白质 + 一份蔬菜的经典结构，薯条选小份即可满足体验感。",
        "训练日可加一份麦乐鸡补蛋白，休息日减半份。",
    ],
    "bulk": [
        "大薯条、大杯可乐是有效的热量与碳水补充，但注意钠摄入。",
        "双层牛堡 + 麦满分组合是蛋白密度最高的增肌配置。",
    ],
    "recomp": [
        "高蛋白 + 中碳水 + 低油炸，小食优先鸡块而非鸡翅。",
        "训练后 1 小时内优先补蛋白：巨无霸或双层吉士 + 无糖可乐。",
    ],
}


def strategy_tips(goal: str) -> List[str]:
    return STRATEGY_TIPS.get(goal, STRATEGY_TIPS["maintain"])


# ============================================================
# CLI
# ============================================================

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="营养目标引擎")
    ap.add_argument("--gender", required=True, choices=["male", "female"])
    ap.add_argument("--age", required=True, type=int)
    ap.add_argument("--height", required=True, type=float, help="cm")
    ap.add_argument("--weight", required=True, type=float, help="kg")
    ap.add_argument("--activity", default="light", choices=list(ACTIVITY_LEVELS))
    ap.add_argument("--goal", default="maintain", choices=list(GOAL_MODES))
    ap.add_argument("--body-fat", type=float, default=None)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    p = BodyProfile(args.gender, args.age, args.height, args.weight,
                    args.activity, args.goal, args.body_fat)
    t = compute_target(p)

    if args.json:
        print(json.dumps(t.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print("=" * 58)
    print("  麦当劳营养膳食专家 · 你的专属营养目标")
    print("=" * 58)
    print("  BMI        : %.1f（%s）  理想体重约 %.1f kg" % (t.bmi, t.bmi_label, t.ideal_weight_kg))
    print("  活动水平   : %s" % t.activity_label)
    print("  目标模式   : %s" % t.goal_label)
    print("-" * 58)
    print("  BMR 基础代谢 : %d kcal" % t.bmr)
    print("  TDEE 总消耗  : %d kcal" % t.tdee)
    print("  目标热量     : %d kcal" % t.target_kcal)
    print("-" * 58)
    print("  蛋白质 : %d g   （%d kcal）" % (t.protein_g, t.protein_g * 4))
    print("  脂肪   : %d g   （%d kcal）" % (t.fat_g, t.fat_g * 9))
    print("  碳水   : %d g   （%d kcal）" % (t.carb_g, t.carb_g * 4))
    print("  钠上限 : %d mg    添加糖上限 : %d g" % (t.sodium_limit_mg, t.sugar_limit_g))
    print("=" * 58)
    for n in t.notes:
        print("  · " + n)
    print()
    for tip in strategy_tips(p.goal):
        print("  → " + tip)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
