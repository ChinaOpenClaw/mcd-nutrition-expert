# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · 后端 API 服务
================================================
FastAPI 服务，为前端可视化应用提供麦当劳 MCP 的代理与业务编排。

路由分组
--------
/api/health    体检（检查 MCP Token 与数据库连通性）
/api/target    营养目标计算
/api/matrix    SP 矩阵（全局四象限图 + 单品定位）
/api/recommend 套餐推荐（SP 贴合度 + 多目标求解）
/api/stores    门店列表
/api/menu      门店菜单（按分类展开，含营养标注）
/api/coupons   优惠券（可领列表 / 一键领券 / 门店可用）
/api/cart      购物车计价（calculate-price 代理，金额单位分→元）
/api/order     创建订单
/api/records   健康记录（写入 / 查询 / 趋势 / 摘要）

金额单位约定
------------
麦当劳 MCP 返回的价格单位是**分**，本服务对外统一转为**元**（浮点），
前端直接显示，避免重复换算出错。
"""
from __future__ import annotations

import math
import os
import re
import sys
from datetime import date
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from combo_solver import (FoodItem, parse_nutrition_payload, solve,  # noqa: E402
                          role_of, ROLE_MAIN, ROLE_SIDE, ROLE_DRINK, ROLE_DESSERT)
from health_store import HealthStore
from mcp_client import McpError, McdMcpClient
from nutrition_engine import BodyProfile, compute_target, strategy_tips
from sp_matrix import compute_sp, recommend_by_sp, sp_summary

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(APP_DIR)
WEB_DIR = os.path.join(SKILL_DIR, "web")

app = FastAPI(title="麦当劳营养膳食专家", version="1.0.0",
              description="基于麦当劳中国 MCP Server 的营养膳食决策与点餐服务")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 全局单例 ----------

_client: Optional[McdMcpClient] = None
_client_err: Optional[str] = None
_store: Optional[HealthStore] = None
_food_cache: Optional[List[FoodItem]] = None
# 套餐明细缓存：(store_code, meal_code, order_type, be_type, be_code) → 明细
_detail_cache: Dict[Any, Dict[str, Any]] = {}


def get_client() -> McdMcpClient:
    global _client, _client_err
    if _client is None:
        try:
            _client = McdMcpClient()
            _client_err = None
        except McpError as e:
            _client_err = str(e)
            raise HTTPException(status_code=503, detail=_client_err)
    return _client


def get_store() -> HealthStore:
    global _store
    if _store is None:
        _store = HealthStore()
    return _store


def get_foods() -> List[FoodItem]:
    """营养数据（带内存缓存，官方数据变动不频繁）"""
    global _food_cache
    if _food_cache is None:
        c = get_client()
        raw = c.call_raw("list-nutrition-foods", {})
        _food_cache = parse_nutrition_payload(raw)
        if not _food_cache:
            raise HTTPException(status_code=502, detail="营养数据解析失败")
    return _food_cache


# ============================================================
# 请求模型
# ============================================================

class ProfileIn(BaseModel):
    gender: str = Field(..., description="male / female")
    age: int
    height_cm: float
    weight_kg: float
    activity: str = "light"
    goal: str = "maintain"
    body_fat_pct: Optional[float] = None


class CartIn(BaseModel):
    store_code: str
    items: List[Dict[str, Any]] = Field(default_factory=list)
    order_type: int = 1
    be_type: int = 1
    be_code: Optional[str] = None
    need_tableware: bool = False


def _normalize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把前端传来的商品项规范化成 MCP 契约要求的字段名。

    ⚠️ `calculate-price` / `create-order` 的契约是
       `items[].productCode` + `items[].quantity`（不是 `code` / `count`）。
       传错字段名时接口不报错，只是静默返回全 0 价格，
       非常难排查。这里统一做一次兼容转换。
    """
    out: List[Dict[str, Any]] = []
    for it in items or []:
        ni = dict(it)
        if "productCode" not in ni:
            for alias in ("code", "product_code", "productId", "id"):
                if ni.get(alias):
                    ni["productCode"] = str(ni[alias])
                    break
        if "quantity" not in ni:
            for alias in ("count", "num", "qty"):
                if ni.get(alias) is not None:
                    ni["quantity"] = int(ni[alias])
                    break
        ni.setdefault("quantity", 1)
        # 清理掉接口不认识的别名，避免 schema 校验失败
        for alias in ("code", "count", "num", "qty", "name", "price_yuan",
                      "sp", "tags", "image", "is_combo", "components",
                      "estimated", "currentPrice", "originalPrice",
                      "price", "originalPrice_yuan", "price_yuan"):
            ni.pop(alias, None)
        out.append(ni)
    return out


class OrderIn(BaseModel):
    store_code: str
    items: List[Dict[str, Any]] = Field(default_factory=list)
    take_way_code: str = "eat-in"
    order_type: int = 1
    be_type: int = 1
    be_code: Optional[str] = None
    need_tableware: bool = False
    remark: Optional[str] = None


class RecordIn(BaseModel):
    log_date: Optional[str] = None
    meal_type: str = "午餐"
    items: List[Dict[str, Any]] = Field(default_factory=list)
    quadrant: Optional[str] = None
    store_name: Optional[str] = None
    order_no: Optional[str] = None


class WeightIn(BaseModel):
    log_date: Optional[str] = None
    weight_kg: float


# ============================================================
# 基础
# ============================================================

@app.get("/api/health")
def health():
    ok = True
    detail = {}
    try:
        c = get_client()
        tools = c.list_tools()
        detail["mcp_tools"] = len(tools)
    except Exception as e:
        ok = False
        detail["mcp_error"] = str(e)
    try:
        get_foods()
        detail["nutrition_items"] = len(_food_cache or [])
    except Exception as e:
        detail["nutrition_error"] = str(e)
    try:
        s = get_store()
        detail["db"] = s.db_path
    except Exception as e:
        detail["db_error"] = str(e)
    return {"ok": ok, "detail": detail}


@app.get("/")
def index():
    path = os.path.join(WEB_DIR, "index.html")
    if os.path.isfile(path):
        return FileResponse(path)
    return JSONResponse({"message": "web UI 未安装", "api": "/docs"})


# ============================================================
# 营养目标
# ============================================================

@app.post("/api/target")
def api_target(p: ProfileIn):
    profile = BodyProfile(p.gender, p.age, p.height_cm, p.weight_kg,
                          p.activity, p.goal, p.body_fat_pct)
    try:
        t = compute_target(profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    get_store().save_profile(p.model_dump())
    get_store().ensure_day(date.today().isoformat(), {
        "target_kcal": t.target_kcal,
        "target_protein": t.protein_g,
        "target_carb": t.carb_g,
        "target_fat": t.fat_g,
        "target_sodium": t.sodium_limit_mg,
    }, weight_kg=p.weight_kg)
    return {
        "target": t.to_dict(),
        "tips": strategy_tips(p.goal),
        "activity_levels": _activity_levels(),
        "goals": _goals(),
    }


def _activity_levels():
    from nutrition_engine import ACTIVITY_LEVELS
    return {k: {"label": v["label"], "desc": v["desc"], "factor": v["factor"]}
            for k, v in ACTIVITY_LEVELS.items()}


def _goals():
    from nutrition_engine import GOAL_MODES
    return {k: {"label": v["label"], "desc": v["desc"]}
            for k, v in GOAL_MODES.items()}


# ============================================================
# SP 矩阵
# ============================================================

@app.get("/api/matrix")
def api_matrix(profile: Optional[str] = None):
    """
    返回 SP 矩阵全局数据。

    profile 参数格式：gender,age,height,weight,activity,goal[,bodyfat]
    传入后会计算用户目标点并给出每个餐品的贴合度。
    """
    target = None
    if profile:
        try:
            parts = [x.strip() for x in profile.split(",")]
            bf = float(parts[6]) if len(parts) > 6 and parts[6] else None
            p = BodyProfile(parts[0], int(parts[1]), float(parts[2]),
                            float(parts[3]), parts[4], parts[5], bf)
            target = compute_target(p)
        except Exception as e:
            raise HTTPException(status_code=400, detail="profile 解析失败：%s" % e)
    foods = get_foods()
    m = compute_sp(foods, target)
    m["summary"] = sp_summary(m)
    if target:
        m["recommend"] = recommend_by_sp(foods, target, top_n=10)
    return m


# ============================================================
# 套餐推荐
# ============================================================

@app.post("/api/recommend")
def api_recommend(p: ProfileIn, top_n: int = 3):
    profile = BodyProfile(p.gender, p.age, p.height_cm, p.weight_kg,
                          p.activity, p.goal, p.body_fat_pct)
    try:
        t = compute_target(profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    foods = get_foods()
    combos = solve(foods, t, p.goal, top_n=top_n)
    singles = recommend_by_sp(foods, t, top_n=10)
    return {
        "target": t.to_dict(),
        "tips": strategy_tips(p.goal),
        "combos": [c.to_dict() for c in combos],
        "singles": singles,
        "summary": sp_summary(compute_sp(foods, t)),
    }


# ============================================================
# 门店 / 菜单
# ============================================================

@app.get("/api/stores")
def api_stores(be_type: int = 1, search_type: int = 1,
               city: Optional[str] = None, keyword: Optional[str] = None):
    c = get_client()
    try:
        stores = c.nearby_stores(be_type=be_type, search_type=search_type,
                                 city=city, keyword=keyword)
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"stores": stores}


@app.get("/api/menu")
def api_menu(store_code: str, order_type: int = 1, be_type: int = 1,
             be_code: Optional[str] = None, profile: Optional[str] = None):
    """
    门店菜单。按分类展开，并给每个餐品标注 SP 象限与营养摘要。
    """
    c = get_client()
    try:
        menu = c.store_menu(store_code, order_type, be_type, be_code)
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e))

    foods = get_foods()
    target = None
    if profile:
        try:
            parts = [x.strip() for x in profile.split(",")]
            bf = float(parts[6]) if len(parts) > 6 and parts[6] else None
            target = compute_target(BodyProfile(parts[0], int(parts[1]), float(parts[2]),
                                              float(parts[3]), parts[4], parts[5], bf))
        except Exception:
            target = None
    # 未建档时用「通用参考档案」计算目标点，
    # 否则 fit（贴合度）恒为 0，菜单上所有餐品都看不到参考价值。
    ref_target = target or compute_target(
        BodyProfile("male", 25, 175, 70, "light", "maintain"))
    matrix = compute_sp(foods, ref_target)
    sp_by_name = {p["name"]: p for p in matrix["points"]}
    food_by_name = {f.name: f for f in foods}

    # 套餐类名称特征。菜单里约 56% 的条目是「三件套 / 四件套 / 分享餐」，
    # 它们必须走 query-meal-detail 聚合内含单品，
    # 绝不能被 find_sp 的「包含匹配」提前命中某个单品（否则三件套会显示成
    # 单个汉堡的营养数据，且 is_combo 永远为 false）。
    _COMBO_NAME_RE = re.compile(
        r"(件套|套餐|分享餐|组合装|超值|双人|多人|全餐|桶餐)")

    def is_combo_name(name: str) -> bool:
        return bool(_COMBO_NAME_RE.search(name or ""))

    # 菜单里的餐品名与营养表名不一定完全一致，用包含匹配兜底
    def find_sp(name: str):
        if name in sp_by_name:
            return sp_by_name[name]
        for n, p in sp_by_name.items():
            if n and (n in name or name in n):
                return p
        return None

    def find_food(name: str):
        """返回营养表中最匹配的 FoodItem。

        匹配策略（依次尝试）：
          1. 全名精确命中
          2. 去掉括号补充说明与套餐后缀后命中
          3. 去掉「龙焰/新/韩式/限定」等前缀修饰后命中
          4. 双向包含匹配（取最长命中，避免短名误配）
          5. 按**品类词**回退（如「龙焰鸡腿堡」→「鸡腿堡」类单品的中位代表）
             —— 新品在营养表里查不到，但同品类老品能代表它的营养画像
        """
        if name in food_by_name:
            return food_by_name[name]
        base = re.sub(r"[（(].*?[)）]", "", name).strip()
        base = re.sub(r"(三件套|四件套|五件套|二件套|套餐|分享餐|组合装)$", "", base).strip()
        for cand in (base, base.replace(" ", "")):
            if cand in food_by_name:
                return food_by_name[cand]
        # 去掉前缀修饰词（新品名如「龙焰鸡腿堡」→「鸡腿堡」）
        stripped = re.sub(r"^(龙焰|新品|新|韩式|限定|经典|超值|人气)+", "", base)
        stripped = re.sub(r"(芝士棒|双拼|分享)$", "", stripped)
        for cand in (stripped, stripped.replace(" ", "")):
            if cand and cand in food_by_name:
                return food_by_name[cand]
        # 双向包含：取最长的命中项。
        # ⚠️ 必须排除「无糖」修饰 ——「可乐中杯」会因包含关系误配到
        #    「无糖可口可乐中杯」（0 kcal），把含糖可乐的热量算成 0。
        best = None
        best_len = 0
        for n, f in food_by_name.items():
            if not n:
                continue
            if "无糖" in n and "无糖" not in base:
                continue
            if n in base or base in n:
                if len(n) > best_len:
                    best, best_len = f, len(n)
        if best is not None:
            return best
        # 品类词回退：新品（如「龙焰鸡腿堡」）在营养表里查不到，
        # 用同品类的代表单品（按角色分组的中位值）近似其营养画像。
        return _fallback_by_category(base)

    # 新品回退用的品类关键词表：新品名 → 营养表里的同品类关键词
    _CATEGORY_HINTS = [
        ("鸡腿堡", "鸡腿堡"), ("牛堡", "牛堡"), ("鳕鱼堡", "鳕鱼堡"),
        ("鱼排堡", "鱼排堡"), ("汉堡", "汉堡"), ("麦满分", "麦满分"),
        ("薯条", "薯条"), ("薯饼", "薯饼"), ("鸡块", "鸡块"),
        ("鸡翅", "鸡翅"), ("脆汁鸡", "脆汁鸡"), ("玉米", "玉米杯"),
        ("可乐", "可乐"), ("雪碧", "雪碧"), ("咖啡", "美式"),
        ("奶铁", "奶铁"), ("牛奶", "牛奶"), ("红茶", "红茶"),
        ("新地", "新地"), ("麦旋风", "麦旋风"), ("派", "派"),
        ("圆筒", "圆筒"), ("冰淇淋", "圆筒"), ("炸鸡", "脆汁鸡"),
        ("V翅", "鸡翅"), ("双全盒", "鸡块"),
    ]

    def _fallback_by_category(base: str):
        """新品回退：按品类关键词找到营养表里的同品类代表单品。

        麦当劳菜单常出现营养表未收录的新品（「龙焰鸡腿堡」、
        「韩式甜辣酱鸡块8块」），这些餐品的营养画像与同品类老品接近。
        优先用关键词精确命中，取该品类下热量最接近中位的单品，
        以避免用「汉堡类中位」把 485 kcal 的鸡腿堡算成 300 kcal。
        """
        for hint, kw in _CATEGORY_HINTS:
            if hint not in base:
                continue
            bucket = [f for f in foods if kw in f.name]
            if not bucket:
                continue
            bucket.sort(key=lambda f: f.energy_kcal or 0)
            return bucket[len(bucket) // 2]
        r = role_of(base)
        if r == "other":
            return None
        bucket = [f for f in foods if role_of(f.name) == r]
        if not bucket:
            return None
        bucket.sort(key=lambda f: f.energy_kcal or 0)
        return bucket[len(bucket) // 2]

    def aggregate_sp(component_names: List[str]):
        """把套餐内含的多个单品聚合成一组 SP 指标。

        麦当劳菜单里 56% 的条目是「三件套 / 四件套 / 分享餐」这类套餐，
        营养表里只有单品，直接按名字匹配命中率仅 35%。
        正确做法是用 query-meal-detail 拿到套餐内含的每个单品，
        再按热量加权聚合出这个套餐的（营养密度 X、钠负担 Y）。
        """
        comps = []
        display_names = []
        estimated = []
        for cn in component_names:
            f = find_food(cn)
            if f is not None:
                comps.append(f)
                display_names.append(cn)
                if cn not in food_by_name:
                    estimated.append(cn)
        if not comps:
            return None
        kcal = sum(float(f.energy_kcal or 0) for f in comps)
        if kcal <= 0:
            return None
        protein = sum(float(f.protein or 0) for f in comps)
        fat = sum(float(f.fat or 0) for f in comps)
        carb = sum(float(f.carbohydrate or 0) for f in comps)
        sodium = sum(float(f.sodium or 0) for f in comps)
        calcium = sum(float(f.calcium or 0) for f in comps)
        x = (protein + carb) / (kcal / 100.0)
        y = sodium / (kcal / 100.0)
        sp = {"x": round(x, 3), "y": round(y, 3), "kcal": round(kcal),
              "protein": round(protein, 1), "fat": round(fat, 1),
              "carb": round(carb, 1), "sodium": round(sodium),
              "calcium": round(calcium, 1)}
        # 象限与贴合度沿用 SP 矩阵的阈值与目标点
        th = matrix.get("thresholds", {}) or {}
        x_th = th.get("x", 0) or 0
        y_th = th.get("y", 0) or 0
        if x >= x_th and y < y_th:
            sp["quadrant"] = "sweet_point"
        elif x < x_th and y >= y_th:
            sp["quadrant"] = "treat"
        elif x >= x_th and y >= y_th:
            sp["quadrant"] = "double_edge"
        else:
            sp["quadrant"] = "filler"
        tp = matrix.get("target_point")
        if tp:
            max_x = max((p["x"] for p in matrix["points"]), default=1.0) or 1.0
            max_y = max((p["y"] for p in matrix["points"]), default=1.0) or 1.0
            dx = (sp["x"] - tp["x"]) / max(max_x, 1e-6)
            dy = (sp["y"] - tp["y"]) / max(max_y, 1e-6)
            sp["fit"] = max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy) / 1.4)
        else:
            sp["fit"] = 0.0
        sp["components"] = display_names
        # 标记哪些组件是「新品近似值」（营养表无精确收录）
        sp["estimated_components"] = estimated
        return sp

    meals = menu.get("meals", {}) or {}
    enriched: Dict[str, Any] = {}
    for code, m in meals.items():
        e = dict(m)
        name = m.get("name", "")
        sp = None
        if is_combo_name(name):
            # 套餐：强制走明细聚合（不走 find_sp，避免被包含匹配误判成单品）
            pass
        else:
            sp = find_sp(name)
            if not sp:
                # 单品：新品在营养表里查不到 → 按品类关键词回退近似
                f = find_food(name)
                if f is not None:
                    sp = aggregate_sp([name])
                    if sp:
                        e["estimated"] = True
        if not sp:
            # 套餐类条目：拉明细聚合内含单品
            # 明细按 (门店, 套餐编码) 缓存 —— 一次菜单请求要拉 30+ 个套餐，
            # 不缓存的话每次刷新页面都要重复打 MCP，延迟高达 30s。
            dkey = (store_code, code, order_type, be_type, be_code or "")
            if dkey in _detail_cache:
                detail = _detail_cache[dkey]
            else:
                try:
                    detail = c.meal_detail(store_code, code, order_type, be_type, be_code)
                except McpError:
                    detail = {}
                _detail_cache[dkey] = detail
            comp_names: List[str] = []
            seen_food: set = set()
            for rnd in (detail.get("rounds") or []):
                # 优先取该轮「已选中」的 choice（isDefault=1 / selectedQuantity>0），
                # 取不到再退回轮次名本身。
                choices = rnd.get("choices") or []
                picked = [ch for ch in choices
                          if ch.get("isDefault") == 1
                          or (ch.get("selectedQuantity") or 0) > 0]
                use = picked or choices[:1]
                for ch in use:
                    cn = ch.get("name")
                    if not cn:
                        continue
                    f = find_food(cn)
                    if f is None:
                        continue
                    # 同一营养单品只计一次（套餐里可能出现同名的多轮次描述）
                    if f.name in seen_food:
                        continue
                    seen_food.add(f.name)
                    # 保留菜单原名（新品回退时与营养表名不同，展示要用原名）
                    comp_names.append(cn)
            sp = aggregate_sp(comp_names)
            if sp:
                e["is_combo"] = True
                e["components"] = sp.get("components", [])
                if sp.get("estimated_components"):
                    e["estimated"] = True
        if sp:
            e["sp"] = {
                "quadrant": sp["quadrant"],
                "x": sp["x"], "y": sp["y"], "fit": sp.get("fit", 0),
                "kcal": sp["kcal"], "protein": sp["protein"],
                "fat": sp["fat"], "carb": sp["carb"], "sodium": sp["sodium"],
            }
        e["price_yuan"] = _to_yuan(m.get("currentPrice"))
        e["original_price_yuan"] = _to_yuan(m.get("originalPrice"))
        enriched[code] = e

    cats = []
    for c_ in menu.get("categories", []) or []:
        items = []
        for ref in c_.get("meals", []) or []:
            code = ref.get("code")
            if code in enriched:
                item = dict(enriched[code])
                item["tags"] = ref.get("tags", [])
                item["code"] = code
                items.append(item)
        if items:
            cats.append({"name": c_.get("name"), "items": items})

    return {"store_code": store_code, "categories": cats,
            "total": len(enriched), "thresholds": matrix.get("thresholds")}


# ============================================================
# 优惠券
# ============================================================

@app.get("/api/coupons")
def api_coupons(store_code: Optional[str] = None, order_type: int = 1,
                be_type: int = 1, be_code: Optional[str] = None):
    c = get_client()
    out: Dict[str, Any] = {}
    try:
        out["available"] = c.available_coupons()
    except McpError as e:
        out["available_error"] = str(e)
    try:
        out["mine"] = c.my_coupons()
    except McpError as e:
        out["mine_error"] = str(e)
    if store_code:
        try:
            out["store_usable"] = c.store_coupons(store_code, order_type, be_type, be_code)
        except McpError as e:
            out["store_usable_error"] = str(e)
    return out


@app.post("/api/coupons/claim")
def api_claim_coupons():
    c = get_client()
    try:
        return {"ok": True, "result": c.auto_bind_coupons()}
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ============================================================
# 购物车 / 订单
# ============================================================

@app.post("/api/cart")
def api_cart(p: CartIn):
    c = get_client()
    try:
        r = c.calculate_price(p.store_code, _normalize_items(p.items), p.order_type,
                              p.be_type, p.be_code, p.need_tableware)
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _money_to_yuan(r)


@app.post("/api/order")
def api_order(p: OrderIn):
    c = get_client()
    try:
        r = c.create_order(p.store_code, _normalize_items(p.items), p.take_way_code,
                           p.order_type, p.be_type, p.be_code, p.need_tableware,
                           p.remark)
    except McpError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return _money_to_yuan(r)


def _to_yuan(v: Any) -> Optional[float]:
    """把接口返回的价格统一换算为「元」。

    ⚠️ 麦当劳 MCP 的两个接口价格口径不同：
      - `query-store-menu` 的 currentPrice 已经是**元**的字符串（"29.9"）
      - `calculate-price` / 下单接口返回的是**分**的整数（2600）
    早期对菜单也除以 100，导致 29.9 元被显示成 0.3 元。
    这里的判据：数值型且 ≥ 100 视为分；字符串带小数点视为已是元。
    """
    if v is None:
        return None
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return None
        # "29.9" 已经是元；"2990" 视为分
        return round(f / 100, 2) if "." not in v and f >= 100 else round(f, 2)
    if isinstance(v, (int, float)):
        f = float(v)
        return round(f / 100, 2) if f >= 100 else round(f, 2)
    return None


def _money_to_yuan(r: Dict[str, Any]) -> Dict[str, Any]:
    """把 MCP 返回的「分」统一转成「元」"""
    if not isinstance(r, dict):
        return {"raw": r}
    out = dict(r)
    for k in ("price", "originalPrice", "discount", "productPrice",
              "productOriginalPrice"):
        if isinstance(out.get(k), (int, float)):
            out[k + "_yuan"] = round(out[k] / 100, 2)
    pl = out.get("productList")
    if isinstance(pl, list):
        new_pl = []
        for p in pl:
            np_ = dict(p)
            for k in ("subtotal", "originalSubtotal"):
                if isinstance(np_.get(k), (int, float)):
                    np_[k + "_yuan"] = round(np_[k] / 100, 2)
            new_pl.append(np_)
        out["productList"] = new_pl
    return out


# ============================================================
# 健康记录
# ============================================================

@app.post("/api/records")
def api_add_record(r: RecordIn):
    s = get_store()
    prof = s.get_profile()
    if prof:
        # 用档案重新算一遍目标，写进当日 daily_log 的目标列，
        # 否则趋势图上的「目标线」会一直是空的（记录先于建档写入时尤其明显）
        try:
            t = compute_target(BodyProfile(
                prof.get("gender", "male"), int(prof.get("age", 25)),
                float(prof.get("height_cm", 175)), float(prof.get("weight_kg", 70)),
                prof.get("activity", "light"), prof.get("goal", "maintain"),
                prof.get("body_fat_pct")))
        except Exception:
            t = None
        s.ensure_day(r.log_date or date.today().isoformat(), ({
            "target_kcal": t.target_kcal,
            "target_protein": t.protein_g,
            "target_carb": t.carb_g,
            "target_fat": t.fat_g,
            "target_sodium": t.sodium_limit_mg,
        } if t else None))
    res = s.add_meal(r.model_dump())
    return {"ok": True, "result": res,
            "summary": s.summary(30), "today": s.day_detail(res["log_date"])}


@app.post("/api/records/weight")
def api_set_weight(w: WeightIn):
    s = get_store()
    s.set_weight(w.log_date or date.today().isoformat(), w.weight_kg)
    return {"ok": True, "summary": s.summary(30)}


@app.get("/api/records")
def api_records(days: int = 30, date_str: Optional[str] = None):
    s = get_store()
    if date_str:
        return {"day": s.day_detail(date_str), "summary": s.summary(days)}
    return {"trend": s.trend(days), "summary": s.summary(days),
            "recent": s.recent_meals(20), "profile": s.get_profile()}


@app.delete("/api/records")
def api_delete_record(date_str: str):
    s = get_store()
    s.delete_day(date_str)
    return {"ok": True, "summary": s.summary(30)}


@app.get("/api/account")
def api_account():
    c = get_client()
    try:
        return {"account": c.my_account()}
    except McpError as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8848"))
    print("麦当劳营养膳食专家 API 启动：http://127.0.0.1:%d" % port)
    print("Web UI: %s" % os.path.join(WEB_DIR, "index.html"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
