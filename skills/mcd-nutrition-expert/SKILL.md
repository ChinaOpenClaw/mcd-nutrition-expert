---
name: mcd-nutrition-expert
description: 麦当劳营养膳食专家。基于 SP 矩阵理论为身材管控期用户提供麦当劳个人饮食搭配可视化选型：计算每日消耗与营养目标（BMR/TDEE/四目标模式），用 SP 四象限矩阵定位 160 个官方餐品的营养密度与钠负担，多目标约束求解出结构合理的麦麦套餐，并支持点击门店进店点餐、领券下单与健康可视化记录。触发词：麦当劳、麦麦套餐、控卡期吃什么、减脂餐、增肌餐、麦当劳点餐、麦当劳优惠券、营养专家、SP 矩阵、饮食搭配、热量计算。
---

# 麦当劳营养膳食专家

> 身材管控期也想吃麦当劳？这个专家会帮你根据每日消耗搭配麦麦套餐。

## 核心能力

| 能力 | 说明 |
|---|---|
| **营养目标计算** | Mifflin-St Jeor / Katch-McArdle 求 BMR，FAO/WHO 活动系数求 TDEE，四种目标模式（减脂/维持/增肌/塑形） |
| **SP 矩阵选型** | 营养密度（X）× 钠负担（Y）四象限定位 + 贴合度评分，160 个官方餐品全覆盖 |
| **套餐求解** | 结构角色模型 + 多目标加权打分 + 硬约束 + 局部搜索，输出可执行的成套搭配 |
| **门店点餐** | 附近门店 → 进店 → 分类菜单（每个条目带 SP 标签）→ 购物车 → 计价 → 下单 |
| **领券购买** | 可领取 / 我的卡包 / 本店可用三段式，一键领券后可直接用于下单 |
| **健康可视化** | SQLite 三表分离存储每日汇总与餐次明细，输出体重趋势、热量收支、象限分布 |

## 环境准备

```bash
cd scripts
pip install fastapi uvicorn
```

`.env` 配置（Token 只从环境变量读取，**不要写入代码或提交到仓库**）：

```ini
MCD_MCP_URL=https://mcp.mcd.cn
MCD_MCP_TOKEN=<your_token>
```

## 工作流

### 1. 建档 → 目标

收集用户信息并计算：

| 字段 | 说明 | 缺省处理 |
|---|---|---|
| gender | male / female | 必填 |
| age | 10–100 | 必填 |
| height_cm | 120–230 | 必填 |
| weight_kg | 30–250 | 必填 |
| activity | sedentary / light / moderate / active / athlete | 默认 light |
| goal | cut / maintain / bulk / recomp | 默认 maintain |
| body_fat_pct | 3–60，选填 | 填了则用 Katch-McArdle 修正 BMR |

```bash
python scripts/nutrition_engine.py --demo
```

输出：BMR / TDEE / 全天热量目标 / 蛋白·碳水·脂肪目标 / 钠上限 / BMI / 策略建议。

**关键概念：全天目标 ≠ 单餐目标。** 求解器内部按 `meal_share = 0.34` 把全天目标折算成一餐目标（如全天 1771 kcal → 单餐 602 kcal），所有比例约束都基于单餐口径。

### 2. SP 矩阵选型

```bash
python scripts/sp_matrix.py
```

- X 轴 = (蛋白质g + 碳水g) / (热量/100) —— 营养密度
- Y 轴 = 钠mg / (热量/100) —— 钠负担
- 四象限：**主力区**（高营养低钠）/ **解馋区**（低营养低钠）/ **双刃区**（高营养高钠）/ **填充区**（低营养高钠）
- `fit` = 相对用户目标点的归一化欧氏距离评分
- 分界线为动态阈值：P50×0.6 + P75×0.4

### 3. 套餐推荐

```bash
python scripts/combo_solver.py --selftest    # 离线自检
```

或调用后端：

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8899

curl -X POST http://127.0.0.1:8899/api/recommend -H "Content-Type: application/json" \
  -d '{"gender":"male","age":25,"height_cm":175,"weight_kg":70,
       "activity":"light","goal":"cut","body_fat_pct":18}'
```

**结构角色模型**（保证套餐像一顿正常的饭，而非「两个雪冰 + 一杯奶昔」）：

```
主餐（唯一） → 小食（≤1） → 饮品（唯一） → 甜点（≤1，减脂/塑形模式禁止）
```

**硬约束**：主餐占本餐 40%–85%；角色级热量上限（比例 ∪ 真实规格绝对上限取大者）；配菜须带蛋白（≥6g）或足量主食（≥120 kcal）；钠 ≤ 全日上限 × 1.25。

**权重**：热量 26% / 蛋白 28% / 碳水 13% / 脂肪 11% / 钠 14% / 饱腹 8%。

**输出示例**（减脂，全天 1771 kcal）：

```
方案 1  637 kcal（占全天 36%）· 蛋白 44g · 钠 1878mg
   麦辣鸡腿汉堡 + 无糖可口可乐中杯 + 蜜汁BBQ风味薄皮脆汁鸡-带骨里脊
   ⚠️ 钠摄入 1878 mg，已达上限的 94%
```

**必须向用户说明的取舍点**：
- 钠普遍偏高（麦当劳主餐 900–1200 mg），超限要显式提示
- 蛋白质缺口：单餐最多 ~55 g，达不到理论目标时说明用鸡胸/蛋白棒/无糖酸奶补足，**不要建议靠加餐麦当劳凑**

### 4. 门店点餐

```bash
curl "http://127.0.0.1:8899/api/stores"
curl "http://127.0.0.1:8899/api/menu?store_code=3230034"
```

- 门店可能处于「暂停营业」，需核验后再推荐
- 菜单 87 个条目中 56% 是套餐类（"三件套/四件套/分享餐"），SP 指标由 `query-meal-detail` 的内含单品**按热量加权聚合**得出
- 新品未收录营养表时按品类关键词近似估算，**必须标注「新品估算」**

### 5. 领券与下单

```bash
curl "http://127.0.0.1:8899/api/coupons?store_code=3230034"
curl -X POST "http://127.0.0.1:8899/api/coupons/claim"
curl -X POST "http://127.0.0.1:8899/api/cart" -H "Content-Type: application/json" \
  -d '{"store_code":"3230034","items":[{"code":"521741","quantity":1}]}'
```

⚠️ **两个价格口径**：`query-meals` 的 `currentPrice` 已是元（"29.9"），`calculate-price` 返回分（2690）。后端已统一归一化。

⚠️ **下单字段**：MCP 契约是 `items[].productCode` + `items[].quantity`。后端 `_normalize_items()` 已做兼容，前端用 `code` 即可。

### 6. 健康记录

```bash
curl -X POST "http://127.0.0.1:8899/api/records" -H "Content-Type: application/json" \
  -d '{"log_date":"2026-10-25","meal_type":"午餐","quadrant":"treat",
       "items":[{"name":"脆薯饼","kcal":146,"protein_g":1.0,"fat_g":4.0,
                 "carb_g":22.0,"sodium_mg":311}]}'
```

字段名兼容 `protein` / `protein_g`、`sodium` / `sodium_mg` 两套写法。

## Web 应用

```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8899
```

打开 <http://127.0.0.1:8899>，五个视图：

1. **建档** —— 计算营养目标
2. **SP 矩阵选型** —— 原生 Canvas 散点矩阵
3. **套餐推荐** —— 多目标求解结果 + 维度贴合度表
4. **门店点餐** —— 门店 → 菜单 → 购物车 → 计价 → 下单
5. **健康可视化** —— 体重折线 / 热量收支 / 象限分布

## 边界与合规

- 所有数据来自麦当劳官方 MCP，**无模拟数据**
- 输出仅供参考，不构成医疗、营养或其他专业建议
- 餐品信息、价格、供应状态以官方渠道实时结果为准
- Token 只读环境变量；`.env` 与 `data/*.db` 必须 gitignore
- 健康数据仅存本地 SQLite，不上传
