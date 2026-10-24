# 接口清单

> 麦当劳营养膳食专家 · REST API
> Base URL：`http://127.0.0.1:8848`（`PORT` 环境变量可改）
> 交互式文档：`http://127.0.0.1:8848/docs`（FastAPI 自动生成）

**通用约定**
- 请求与响应均为 `application/json`（`/api/matrix`、`/api/stores`、`/api/menu`、`/api/coupons`、`/api/records`、`/api/account` 为 GET，其余为 POST）
- **金额一律为「元」（浮点）**，已在服务端完成「分 → 元」归一，前端不需要再换算
- MCP 错误统一返回 HTTP `502`，`detail` 为可读错误描述
- 认证错误返回 HTTP `503`（Token 缺失或无效）

---

## 一、体检

### `GET /api/health`

检查 MCP 连接、营养数据、数据库连通性。

**响应**
```json
{
  "ok": true,
  "detail": {
    "mcp_tools": 35,
    "nutrition_items": 160,
    "db": "E:\\mcd-nutrition-expert\\skills\\mcd-nutrition-expert\\data\\health.db"
  }
}
```

---

## 二、营养目标

### `POST /api/target`

计算全天营养目标，并落库档案 + 今日目标快照。

**请求体**
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `gender` | string | ✅ | `male` / `female` |
| `age` | int | ✅ | 年龄 |
| `height_cm` | float | ✅ | 身高（cm） |
| `weight_kg` | float | ✅ | 体重（kg） |
| `activity` | string | | `sedentary`/`light`/`moderate`/`active`/`very_active`，默认 `light` |
| `goal` | string | | `cut`/`recomp`/`maintain`/`bulk`，默认 `maintain` |
| `body_fat_pct` | float | | 体脂率，填了会叠加 Katch-McArdle 修正 |

**响应**
```json
{
  "target": {
    "bmr": 1712, "tdee": 2397, "target_kcal": 1918,
    "protein_g": 138, "carb_g": 240, "fat_g": 64,
    "sodium_limit_mg": 1950, "bmi": 22.9, "bmi_label": "正常",
    "ideal_weight_kg": 68.4, "meal_kcal_share": 0.34
  },
  "tips": ["控卡期优先高蛋白低脂单品……"],
  "activity_levels": { "light": { "label": "轻度活动", "desc": "...", "factor": 1.55 } },
  "goals": { "cut": { "label": "减脂", "desc": "..." } }
}
```

---

## 三、SP 矩阵

### `GET /api/matrix`

返回全局 SP 四象限图数据 + 单品定位 + 贴合度。

**Query 参数**
| 参数 | 说明 |
|---|---|
| `profile` | 可选。格式 `gender,age,height,weight,activity,goal[,bodyFat]`，例：`male,25,175,70,light,cut`。传入后计算目标点并给出 `fit` |

**响应**
```json
{
  "points": [
    { "name": "巨无霸", "category": "汉堡", "x": 10.35, "y": 187.3,
      "kcal": 513, "protein": 26, "fat": 42, "carb": 27, "sodium": 961,
      "quadrant": "sweet_point", "fit": 0.62 }
  ],
  "axes": { "x": { "label": "营养密度 (g/100kcal)" }, "y": { "label": "钠负担 (mg/100kcal)" } },
  "quadrants": {
    "sweet_point": { "label": "主力区", "desc": "...", "count": 42, "avg_kcal": 380, "avg_sodium": 720 },
    "double_edge": { "label": "双刃区", "count": 31 },
    "treat":       { "label": "解馋区", "count": 48 },
    "filler":      { "label": "填充区", "count": 39 }
  },
  "thresholds": { "x": 8.42, "y": 96.5 },
  "target_point": { "x": 19.7, "y": 101.6 },
  "summary": "共 160 个单品……",
  "recommend": [ /* top10 单品，字段同 points */ ]
}
```

**象限键值**：`sweet_point` 主力区 / `double_edge` 双刃区 / `treat` 解馋区 / `filler` 填充区

---

## 四、套餐推荐

### `POST /api/recommend`

多目标约束求解 + SP 贴合度排序。

**请求体**：同 `/api/target`（全部字段）。
**Query**：`top_n`（默认 3）

**响应**
```json
{
  "target": { "...": "同 /api/target" },
  "tips": ["..."],
  "combos": [
    {
      "items": [
        { "name": "麦辣鸡腿汉堡", "count": 1, "kcal": 486, "protein": 25,
          "fat": 22, "carb": 46, "sodium": 1180 },
        { "name": "无糖可口可乐中杯", "count": 1, "kcal": 0, "protein": 0,
          "fat": 0, "carb": 0, "sodium": 15 }
      ],
      "total": { "kcal": 637, "protein": 44, "fat": 29, "carb": 58, "sodium": 1878 },
      "score": 0.84,
      "fit": 0.78,
      "quadrant": "sweet_point",
      "reason": "高蛋白低钠……",
      "day_target": {
        "day_target_kcal": 1771, "day_target_protein_g": 128,
        "meal_target_kcal": 602, "meal_kcal_share": 0.34,
        "kcal_share_of_day": 0.36
      }
    }
  ],
  "singles": [ /* top10 单品 */ ],
  "summary": "..."
}
```

> ⚠️ 注意 `combos[].day_target` 是**全天目标**，`meal_target_kcal` 才是**单餐目标**。
> 前端展示「本餐占全天 X%」必须用 `kcal_share_of_day`。

---

## 五、门店

### `GET /api/stores`

**Query 参数**

| 参数 | 默认 | 说明 |
|---|---|---|
| `be_type` | 1 | 门店类型 |
| `search_type` | 1 | 搜索类型 |
| `city` | — | 城市 |
| `keyword` | — | 关键词 |

**响应**
```json
{ "stores": [ { "storeCode": "...", "storeName": "麦当劳(XX店)", "address": "...", "distance": "..." } ] }
```

---

## 六、菜单

### `GET /api/menu`

门店菜单 + 每条目 SP 象限与营养标注。

**Query 参数**

| 参数 | 默认 | 说明 |
|---|---|---|
| `store_code` | 必填 | 门店编码 |
| `order_type` | 1 | 订餐方式 |
| `be_type` | 1 | 门店类型 |
| `be_code` | — | 门店编码（部分场景需要） |
| `profile` | — | 同 `/api/matrix` |

**响应**
```json
{
  "categories": [
    {
      "name": "汉堡",
      "items": [
        { "name": "巨无霸套餐", "price": 29.9, "kcal": 720, "protein": 35,
          "sodium": 1400, "quadrant": "sweet_point", "fit": 0.6,
          "is_combo": true, "estimated": false,
          "components": ["巨无霸", "中薯条", "中可乐"] },
        { "name": "龙焰鸡腿堡", "price": 24.0, "kcal": 520, "protein": 28,
          "sodium": 980, "quadrant": "sweet_point",
          "estimated": true }
      ]
    }
  ],
  "coverage": { "total": 98, "annotated": 86, "ratio": 0.88 }
}
```

- `is_combo: true` → 套餐，指标由内含单品按热量加权聚合
- `estimated: true` → 营养表未收录，按同品类中位单品近似

---

## 七、优惠券

### `GET /api/coupons`

**Query 参数**：`store_code`（可选，传了才返回门店可用券）、`order_type`、`be_type`、`be_code`

**响应**
```json
{
  "available": [ { "couponId": "...", "couponName": "免费脆薯饼", "value": 0 } ],
  "mine": [ { "couponId": "...", "status": "unused" } ],
  "store_usable": [ ... ],
  "available_error": null,
  "mine_error": null,
  "store_usable_error": null
}
```

> 单个接口失败不影响其他字段返回，错误写入对应的 `*_error`。

### `POST /api/coupons/claim`

一键领券（自动绑定账户当前可领的券）。

**响应**
```json
{ "ok": true, "result": { "...": "MCP 返回原样" } }
```

---

## 八、购物车与订单

### `POST /api/cart`

计价预览（不落单）。

**请求体**
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `store_code` | string | ✅ | 门店编码 |
| `items` | array | | 商品项：`{ "productCode": "...", "quantity": 1 }` |
| `order_type` | int | | 默认 1 |
| `be_type` | int | | 默认 1 |
| `be_code` | string | | — |
| `need_tableware` | bool | | 默认 false |

> 服务端兼容 `code`/`count`/`num`/`qty` 等别名并自动转为 `productCode`/`quantity`，
> 同时剔除接口不认识的字段。前端直接传 `productCode` + `quantity` 最稳。

**响应**
```json
{
  "price": 2690, "price_yuan": 26.9,
  "originalPrice": 3200, "originalPrice_yuan": 32.0,
  "discount": 510, "discount_yuan": 5.1,
  "productList": [ { "name": "巨无霸套餐", "subtotal": 2690, "subtotal_yuan": 26.9 } ]
}
```

> ⚠️ 原始字段是**分**，`*_yuan` 是**元**。前端请使用 `*_yuan`。

### `POST /api/order`

创建订单。

**请求体**：`store_code`、`items`，另加

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `take_way_code` | string | `eat-in` | 取餐方式 |
| `remark` | string | — | 备注 |

**响应**：同 `/api/cart` 的结构，附带订单号等 MCP 原始字段。

---

## 九、健康记录

### `POST /api/records`

写入一餐记录。

**请求体**
| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `log_date` | string | 今天 | `YYYY-MM-DD` |
| `meal_type` | string | `午餐` | 餐次 |
| `items` | array | `[]` | 商品项：`{ "name","kcal","protein","fat","carb","sodium" }`（字段名兼容 `protein_g`/`sodium_mg` 等） |
| `quadrant` | string | — | SP 象限 |
| `store_name` | string | — | 门店名 |
| `order_no` | string | — | 订单号 |

**响应**
```json
{
  "ok": true,
  "result": { "log_date": "2026-10-25", "meal_id": 3, "kcal": 637 },
  "summary": { "days": 7, "avg_kcal": 1850, "avg_protein": 121, "avg_sodium": 1980, "...": "..." },
  "today": { "log_date": "2026-10-25", "kcal": 637, "protein": 44, "...": "..." }
}
```

### `GET /api/records`

**Query 参数**：`days`（默认 30）、`date_str`（指定某天则只返回该日详情）

**响应**（不带 `date_str`）
```json
{
  "trend": [ { "log_date": "2026-10-25", "kcal": 1850, "protein": 121, "weight_kg": 70.0 } ],
  "summary": { "...": "近 N 天汇总 + 象限分布" },
  "recent": [ { "log_date": "...", "meal_type": "午餐", "kcal": 637, "items": [] } ],
  "profile": { "gender": "male", "age": 25, "...": "..." }
}
```

### `POST /api/records/weight`

记录体重。**请求体**：`{ "log_date": "2026-10-25", "weight_kg": 69.8 }`

### `DELETE /api/records`

删除某天全部记录。**Query**：`date_str=2026-10-25`

---

## 十、账户

### `GET /api/account`

返回 MCP 账户信息（会员状态、积分等）。

---

## 十一、状态码

| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 参数校验失败（如档案数值不合理、`profile` 解析失败） |
| 404 | 路由不存在 |
| 502 | MCP 服务调用失败（`detail` 含原始错误） |
| 503 | MCP Token 缺失或无效 |

---

## 十二、示例：完整闭环

```bash
BASE=http://127.0.0.1:8848

# 1. 建档
curl -s -X POST $BASE/api/target -H "Content-Type: application/json" \
  -d '{"gender":"male","age":25,"height_cm":175,"weight_kg":70,"activity":"light","goal":"cut"}'

# 2. 套餐推荐
curl -s -X POST $BASE/api/recommend -H "Content-Type: application/json" \
  -d '{"gender":"male","age":25,"height_cm":175,"weight_kg":70,"activity":"light","goal":"cut"}'

# 3. 找门店
curl -s "$BASE/api/stores?city=上海"

# 4. 看菜单（带 SP 标注）
curl -s "$BASE/api/menu?store_code=<STORE_CODE>"

# 5. 领券
curl -s -X POST $BASE/api/coupons/claim

# 6. 计价
curl -s -X POST $BASE/api/cart -H "Content-Type: application/json" \
  -d '{"store_code":"<STORE_CODE>","items":[{"productCode":"<CODE>","quantity":1}]}'

# 7. 下单
curl -s -X POST $BASE/api/order -H "Content-Type: application/json" \
  -d '{"store_code":"<STORE_CODE>","items":[{"productCode":"<CODE>","quantity":1}]}'

# 8. 记一笔
curl -s -X POST $BASE/api/records -H "Content-Type: application/json" \
  -d '{"meal_type":"午餐","items":[{"name":"麦辣鸡腿汉堡","kcal":486,"protein":25,"fat":22,"carb":46,"sodium":1180}]}'

# 9. 看趋势
curl -s "$BASE/api/records?days=7"
```

---

**相关文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [../MCP_INTEGRATION.md](../MCP_INTEGRATION.md)
