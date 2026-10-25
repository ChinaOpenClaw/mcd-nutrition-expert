# 架构与算法说明

> 麦当劳营养膳食专家 · 内部技术文档
> 本文档说明系统的分层结构、数据流、核心算法与关键设计决策。

---

## 一、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  表现层  web/index.html（单文件原生前端，零第三方依赖）        │
│  ┌────────┬────────┬────────┬────────┬────────┐            │
│  │ 建档案  │ SP矩阵 │ 套餐   │ 门店   │ 健康   │            │
│  │ Profile│ Matrix │Recommend│ Order │Visual │            │
│  └────────┴────────┴────────┴────────┴────────┘            │
│         Canvas 原生绘图 · 无 ECharts / 无 CDN                │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP / JSON
┌───────────────────────────▼──────────────────────────────────┐
│  服务层  scripts/server.py（FastAPI + uvicorn）                │
│  14 个 REST 接口 · CORS 中间件 · pydantic 请求模型            │
│  职责：协议适配 / 价格归一 / 字段归一 / 业务编排              │
└──┬──────────┬──────────┬──────────┬──────────┬──────────────┘
   │          │          │          │          │
┌──▼──────┐┌──▼───────┐┌─▼────────┐┌▼─────────┐┌▼────────────┐
│nutrition││sp_matrix ││combo_    ││mcp_     ││health_store │
│_engine  ││ SP 矩阵   ││solver    ││client   ││ SQLite 持久化 │
│ TDEE/   ││ 四象限+   ││ 多目标   ││ MCP     ││ profile /    │
│ 目标计算 ││ 贴合度    ││ 约束求解 ││ 协议出口 ││ daily_log /  │
│         ││          ││          ││         ││ meal_log     │
└─────────┘└──────────┘└──────────┘└────┬────┘└──────────────┘
                                       │ Streamable HTTP
                                ┌──────▼──────────────────────┐
                                │ 麦当劳中国 MCP Server        │
                                │  https://mcp.mcd.cn  （35 工具）│
                                └─────────────────────────────┘
```

### 分层职责

| 层 | 模块 | 单一职责 | 依赖 |
|---|---|---|---|
| 表现 | `web/index.html` | 视图渲染 + Canvas 绘图 + 与 API 交互 | 仅 fetch / 原生 JS |
| 服务 | `server.py` | 协议适配、字段/单位归一、业务编排 | 下面全部模块 |
| 领域 | `nutrition_engine.py` | 档案校验、BMR/TDEE、三大营养素目标 | 标准库 |
| 领域 | `sp_matrix.py` | SP 坐标计算、阈值、象限归属、贴合度 | `nutrition_engine` |
| 领域 | `combo_solver.py` | 营养数据解析、套餐结构约束求解 | `nutrition_engine` |
| 集成 | `mcp_client.py` | MCP JSON-RPC 客户端（唯一对外出口） | `requests` |
| 持久 | `health_store.py` | SQLite 三表读写 | 标准库 `sqlite3` |

**依赖方向严格单向**：`表现 → 服务 → 领域 → 集成`。领域层不感知 HTTP，集成层不感知业务。

---

## 二、数据流

### 2.1 建档 → 目标 → 矩阵 → 套餐

```
POST /api/target
  → nutrition_engine.compute_target(profile)
      Mifflin-St Jeor → BMR
      × FAO/WHO PAL 活动系数 → TDEE
      × 目标模式系数 → target_kcal
      ACSM 三大营养素配比 + Katch-McArdle 体脂修正 → 蛋白/碳水/脂肪
      → 钠上限 = 目标热量 × 钠密度基准
  → 落库 profile + ensure_day(今日目标快照)

GET /api/matrix?profile=...
  → sp_matrix.compute_sp(foods, target)
      每个单品 → SpPoint(x=营养密度, y=钠密度)
      动态阈值 → 象限归属 → 与目标点的欧氏距离 → fit
  → 推荐单品 top10

POST /api/recommend
  → combo_solver.solve(foods, target, goal)
      单餐口径折算 → 候选池分层采样 → 结构约束过滤
      → 加权打分 → 局部搜索（加/减/换一份）→ 多样性去重
```

### 2.2 门店 → 菜单 → 点餐 → 记录

```
GET  /api/stores          → 附近门店（be_type / city / keyword）
GET  /api/menu            → 门店菜单 + 每条目 SP 象限标注
                             ├ 单品：直接匹配营养表
                             ├ 套餐：query-meal-detail 取内含单品，按热量加权聚合 SP
                             └ 新品：同品类中位单品近似，标记 estimated
POST /api/cart            → calculate-price（productCode + quantity）
POST /api/coupons/claim   → auto-bind-coupons 一键领券
POST /api/order           → create-order
POST /api/records         → 写入 meal_log，回算 daily_log 汇总 + 象限分布
GET  /api/records         → 趋势 / 摘要 / 最近记录 / 档案
```

---

## 三、核心算法

### 3.1 SP 矩阵（Sweet Point 战略定位矩阵）

原始 SP 矩阵是营销学工具：横轴「品质」、纵轴「价格」，划分四象限寻找「甜蜜点」。
本项目做了一次**语义转译**，把它映射到饮食决策：

| SP 原始轴 | 本项目映射 | 计算式 |
|---|---|---|
| 横轴（品质） | **营养密度** X | `(protein_g + carb_g) / (kcal / 100)` |
| 纵轴（价格） | **钠负担** Y | `sodium_mg / (kcal / 100)` |

用「每 100 kcal 能换来多少克营养」与「每 100 kcal 要背多少毫克钠」两个密度指标，
天然消除了「大份餐热量高所以显得更差」的量纲偏差。

**四象限语义**（方位与 `docs/screenshots/03-sp-matrix.png` 实际界面一致）：

```
                     钠负担高 (Y↑)
                        │
      ┌─────────────────┼─────────────────┐
      │                 │                 │
      │    解馋区       │    双刃区       │
      │  营养密度低      │   营养密度高     │
      │  且钠负担高      │   且钠负担高     │
      │ （高热量高钠，   │ （高蛋白但高钠， │
      │   想吃就吃）     │   增肌日可吃）   │
 ─────────────────────┼──────────────────  营养密度高 (X↑)
      │                 │                 │
      │    填充区       │    主力区 ★     │
      │  营养密度低      │   营养密度高     │
      │  且钠负担低      │   且钠负担低     │
      │ （高热量低钠的   │ （高蛋白低钠，   │
      │   饱腹陷阱）     │   日常推荐主力） │
      └─────────────────┼─────────────────┘
                        │
                     钠负担低 (Y↓)
```

- `sweet_point` 主力区：高营养密度 + 低钠负担 → 推荐主力
- `double_edge` 双刃区：高蛋白但高钠 → 控钠期慎选
- `treat` 解馋区：低营养密度 + 高钠负担 → 计划性享用，别当正餐
- `filler` 填充区：低营养密度 + 低钠负担 → 饱腹陷阱，减脂期避开

**动态阈值**（不写死）：
```
x_th = P50(xs) × 0.6 + P75(xs) × 0.4
y_th = P50(ys) × 0.6 + P75(ys) × 0.4
```
用中位数与 P75 的折中，避免极端单品拉偏分割线；同时设下限 `x_th ≥ 0.15`、`y_th ≥ 0.10`。

**贴合度 fit**：目标点 = 用户期望密度 `(target_protein + target_carb) / (kcal/100)`，
单品与目标点在**归一化后**的欧氏距离，`fit = max(0, 1 - dist/1.4)`。

### 3.2 营养目标计算

```
BMR  = Mifflin-St Jeor:
       男: 10w + 6.25h - 5a + 5
       女: 10w + 6.25h - 5a - 161
TDEE = BMR × PAL 活动系数 {sedentary 1.4 … very_active 1.9}
target_kcal = TDEE × 目标系数 {cut 0.80, recomp 0.90, maintain 1.0, bulk 1.12}
蛋白/碳水/脂肪 = ACSM 配比 × target_kcal；已知体脂率时叠加 Katch-McArdle 修正
钠上限 = target_kcal × 钠密度基准（约 3.5 mg/kcal）
```

### 3.3 套餐求解器（多目标约束求解）

**核心洞察：全天目标 ≠ 单餐目标。**
`target_kcal` 是全天 TDEE 目标，若直接当单餐目标求解，主餐热量窗口会被压到
96–193 kcal —— 一个主餐都选不出来，蛋白目标 140 g 在单餐里也永远达不到。
因此引入 **单餐份额 `meal_share = 0.34`**：

```
meal_kcal   = target_kcal   × 0.34
meal_protein= target_protein× 0.34
meal_carb   = target_carb   × 0.34
meal_fat    = target_fat    × 0.34
meal_sodium = target_sodium × 0.34
```

**求解流程：**

1. **角色分类**：每个单品按名称归入 `ROLE_MAIN / SIDE / DRINK / DESSERT`
   （约 90 条品类规则）。
2. **候选池**：主餐 / 配菜 / 饮品 / 甜点各自按热量分层，**等距分位采样**
   （早期版本用 `items[:n]` 只取最低档，导致种子池全是同质低热量单品，0 解）。
   零卡饮品（无糖可乐、纯悦）**单独保留**——早期用 `0 < kcal < 900` 一刀切，
   把它们全部剔除，导致减脂套餐被迫塞高糖饮品。
3. **结构约束**（硬过滤）：
   - 主餐恰好 1 份
   - 饮品恰好 1 份
   - 小食 ≤ 1 份、甜点 ≤ 1 份
   - 角色级热量上限：`min(比例上限, 绝对上限)` 取大者
     `SIDE ≤ max(45%, 400kcal)` / `DRINK ≤ max(25%, 250kcal)` / `DESSERT ≤ max(20%, 300kcal)`
   - 配菜质量下限：配菜合计 ≥ 6 g 蛋白 **或** ≥ 120 kcal
     （防止「玉米杯」把蛋白源挤掉）
   - 控卡期（cut/recomp）禁止甜点
   - 钠 ≤ 1.25 × 单餐钠目标
4. **加权打分**：
   `热量 26% / 蛋白 28% / 碳水 13% / 脂肪 11% / 钠 14% / 饱腹 8%`
   目标模式下对甜点与高糖饮品追加惩罚项。
5. **局部搜索**：加一份 / 减一份 / 换一份，三种邻域迭代至收敛。
6. **多样性去重**：主餐相同 **且**（份数相同 **或** 热量接近）才判为重复
   （早期用「同名重叠 ≥ min(份数)」误杀了四件套）。

**蛋白天花板校正**：按各角色可达上限估算 `_ceil`，
`protein_target_eff = min(target.protein_g, _ceil × 1.05)`，
避免不现实的蛋白目标把打分拉偏。

### 3.4 套餐 SP 指标聚合

菜单中约 56% 是「三件套 / 四件套 / 分享餐」，营养表未收录。
处理方式：

1. `query-meal-detail` 拿到套餐的 `rounds`，只取 `isDefault == 1`
   或 `selectedQuantity > 0` 的 choice（否则会把所有 rounds 的所有 choice 累加，
   实测出现过 3222 kcal 的荒谬结果）；
2. 同一单品只计一次；
3. 按热量加权聚合出套餐的蛋白/碳水/脂肪/钠；
4. 代入与单品相同的 SP 公式，得到套餐的坐标与象限；
5. 若内含单品在营养表中缺失（新品），按同品类中位单品近似并标记 `estimated`。

单品与套餐的名称匹配走**五级降级**：全名 → 去括号/套餐后缀 → 去前缀修饰词
→ 双向包含（排除「无糖」误配）→ 品类关键词回退。
实测菜单 SP 覆盖率从 **31/87（35%）提升到 86/98（88%）**。

---

## 四、关键设计决策与踩坑记录

| # | 问题 | 症状 | 根因 | 解法 |
|---|---|---|---|---|
| 1 | 计价参数名 | `calculate-price` 返回全 0 | 契约是 `productCode`/`quantity`，传了 `code`/`count` | `_normalize_items()` 统一兼容别名 |
| 2 | 价格单位 | 菜单 29.9 元显示成 0.3 元 | `query-store-menu` 的 `currentPrice` 已是元，`calculate-price` 却是分 | `_to_yuan()` 按「字符串带小数点=元，数值 ≥100=分」判别 |
| 3 | 营养表解析 | 解析出 0 条 | `list-nutrition-foods` 的 data 是**伪表格字符串**（`[160]{字段列表}:` + 每行一条），不是 JSON | `parse_nutrition_payload()` 专用解析器 |
| 4 | 套餐热量爆表 | 套餐显示 3222 kcal | 累加了所有 rounds 的所有 choices | 只取 `isDefault==1`/`selectedQuantity>0`，同单品去重 |
| 5 | 零卡饮品消失 | 减脂套餐被迫塞奶茶 | 候选池用 `0 < kcal < 900` 一刀切 | 零卡饮品单独入池 + 饱腹加分 |
| 6 | 0 解 | 求解器返回空列表 | 全天目标当单餐目标用 | `meal_share = 0.34` 折算 |
| 7 | fit 恒为 0 | 矩阵贴合度全 0 | 未传 profile 时 `target=None` | `ref_target` 兜底通用参考档案 |
| 8 | 记录字段全 0 | 蛋白/脂肪/碳水都是 0 | 前端传 `protein_g`，库期望 `protein` | `_num()` 兼容多字段名取值 |

---

## 五、扩展性

- **新增品类**：往 `combo_solver.CATEGORY_RULES` 加一条正则即可，
  `classify()` 与 `role_of()` 自动生效。
- **新增 SP 轴**：改 `sp_matrix.compute_sp()` 的 x/y 计算式，
  象限判定与贴合度公式无需改动。
- **新增营养素维度**：在 `_score_combo()` 的权重表加一项，
  在 `NutritionTarget` 加字段。
- **换数据源**：`mcp_client.py` 是唯一对外出口，
  换成任何符合 MCP 协议的 Server 只需改 `MCD_MCP_URL`。
- **换前端**：`server.py` 对外是纯 REST/JSON，与前端技术栈解耦。

---

## 六、性能

| 操作 | 耗时 | 说明 |
|---|---|---|
| `/api/health` | ~0.2 s | 首次含营养表拉取（内存缓存，后续 <50 ms） |
| `/api/matrix` | ~0.1 s | 160 个单品纯计算 |
| `/api/recommend` | ~1.5 s | 候选池采样 + 局部搜索 |
| `/api/menu` | ~2.5 s | 含 `query-meal-detail` 逐套餐聚合（可缓存） |
| `/api/cart` `/api/order` | ~1.0 s | MCP 实时调用 |

`get_foods()` 对营养表做进程内内存缓存——官方数据变动不频繁，无需每次请求拉取。

---

**相关文档**：[API.md](API.md) · [../MCP_INTEGRATION.md](../MCP_INTEGRATION.md) · [../workbuddy.md](../workbuddy.md)
