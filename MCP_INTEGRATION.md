# MCP 集成说明

本项目基于 **麦当劳官方 MCP Server**（`https://mcp.mcd.cn`）构建，所有门店、菜单、营养、优惠券、订单数据均来自官方接口，无任何模拟或爬取数据。

---

## 一、接入方式

### 1.1 协议

| 项目 | 值 |
|---|---|
| 传输协议 | MCP **Streamable HTTP** |
| 端点 | `https://mcp.mcd.cn` |
| 鉴权 | `Authorization: Bearer <TOKEN>` |
| 工具总数 | **35 个** |

### 1.2 客户端实现

全部 MCP 交互收敛在 `scripts/mcp_client.py`，是项目唯一的数据出口。

```python
class McdMcpClient:
    def _rpc(self, method: str, params: dict):
        """底层 JSON-RPC 调用：
        - Bearer 鉴权
        - MIN_INTERVAL = 0.12s  限流
        - MAX_RETRY = 3        重试
        - 429 / 5xx            指数退避
        - 401 / 403            抛出可读错误
        - SSE `data:` 前缀兼容
        """
```

### 1.3 双通道返回

MCP 工具返回同时包含两个通道，项目按需选择：

| 通道 | 位置 | 用途 |
|---|---|---|
| 结构化数据 | `result.structuredContent.data` | **程序化调用首选**（门店、菜单、优惠券、订单） |
| 文本描述 | `result.content[0].text` | 给 LLM 阅读的自然语言 |

> ⚠️ **重要例外**：`list-nutrition-foods` 的 `data` 字段是**伪表格字符串**，不是 JSON。
> 格式为 `[160]{字段1,字段2,...}:\n  行1\n  行2...`，必须用 `combo_solver.parse_nutrition_payload()` 解析。

```python
# 结构化调用
data = client.call("query-nearby-stores", {...})

# 伪表格调用
raw = client.call_raw("list-nutrition-foods", {})
foods = parse_nutrition_payload(raw)   # → List[FoodItem]
```

---

## 二、本项目实际使用的工具

### 2.1 营养数据

| 工具 | 用途 | 返回通道 |
|---|---|---|
| `list-nutrition-foods` | 160 个餐品的完整营养成分（热量/蛋白/脂肪/碳水/钠/钙） | `content[0].text`（伪表格） |

### 2.2 门店与菜单

| 工具 | 用途 |
|---|---|
| `query-nearby-stores` | 按城市查附近门店（门店名、地址、营业时段） |
| `query-meals` | 拉取门店完整菜单（14 个分类 / 87 个条目） |
| `query-meal-detail` | 拉取单个套餐的内含单品明细（用于 SP 指标聚合） |

### 2.3 优惠券

| 工具 | 用途 |
|---|---|
| `available-coupons` | 可领取的优惠券列表 |
| `query-my-coupons` | 我的卡包 |
| `auto-bind-coupons` | 一键领券 |
| `query-store-coupons` | 指定门店可用的券 |

### 2.4 订单

| 工具 | 用途 |
|---|---|
| `calculate-price` | 计价（返回**分**） |
| `create-order` | 下单 |
| `query-my-orders` | 我的订单 |
| `query-order-detail` | 订单详情 |

### 2.5 其他

| 工具 | 用途 |
|---|---|
| `query-my-account` | 账户信息 |
| `query-campaign-calendar` | 活动日历 |

---

## 三、踩坑记录（真实踩过的坑）

### 坑 1：`calculate-price` 的 items 字段名

```jsonc
// ❌ 错误：不报错，静默返回全 0 价格（极难排查）
{"items": [{"code": "521741", "count": 1}]}

// ✅ 正确：契约是 productCode + quantity
{"items": [{"productCode": "521741", "quantity": 1}]}
```

**应对**：`server.py` 的 `_normalize_items()` 做字段名兼容转换，前端可以继续用 `code`。

### 坑 2：价格单位不一致

| 接口 | 单位 | 示例 |
|---|---|---|
| `query-meals` 的 `currentPrice` | **元**（字符串） | `"29.9"` |
| `calculate-price` / `create-order` | **分**（整数） | `2690` |

**应对**：`server.py` 的 `_to_yuan()` 统一归一化（数值 ≥ 100 视为分，字符串带小数点视为已是元）。

### 坑 3：`list-nutrition-foods` 返回伪表格

不是 JSON，而是文本表格。必须按 `[N]{字段}:` 的格式解析，且部分字段可能为 `null`。

### 坑 4：门店状态

`query-nearby-stores` 返回的门店可能处于「暂停营业」状态，需逐个验证（实测 `3230250` 停业、`3230034` 正常）。

### 坑 5：套餐类菜单条目无法直接匹配营养表

门店菜单 87 个条目中 **56% 是「三件套 / 四件套 / 分享餐」**，而营养表只有 160 个单品。直接按名字匹配命中率仅 35%。

**应对**（`server.py` 的 `api_menu`）：

```
1. 全名精确匹配
2. 去括号 / 去套餐后缀再匹配
3. 去前缀修饰词（「龙焰鸡腿堡」→「鸡腿堡」）
4. 双向包含匹配（取最长命中，排除「无糖」误配）
5. 品类关键词回退（「龙焰鸡腿堡」→ 同类中位单品，标记 estimated）
6. 以上全失败 → 调 query-meal-detail 拿内含单品，按热量加权聚合 SP 指标
```

实测 SP 标注覆盖率从 35% 提升到 **88%**。

---

## 四、数据流

```
用户档案 ──→ nutrition_engine ──→ 全天目标 (BMR/TDEE)
                                    │
                          ┌─────────┴─────────┐
                          ↓                   ↓
                   单餐目标(×0.34)      SP 目标点
                          │                   │
                          ↓                   ↓
                  combo_solver 求解    sp_matrix 四象限
                          │                   │
                          └─────────┬─────────┘
                                    ↓
              /api/recommend  →  前端「套餐推荐」视图
                                    ↓
                          用户选单 → 购物车
                                    ↓
                    /api/menu（SP 标注）→ /api/cart → /api/order
                                    ↓
                          /api/records → SQLite
                                    ↓
                    /api/records → 前端「健康可视化」视图
```

---

## 五、错误处理

| 场景 | 处理 |
|---|---|
| 401 / 403 | `McpError` 抛出可读错误信息，提示 Token 无效 |
| 429 限流 | 指数退避重试，最多 3 次 |
| 5xx | 同上 |
| 工具不存在 | 捕获后降级（如 SP 聚合失败则该条目不显示 SP 标签，不影响其他条目） |
| 套餐明细拉取失败 | 降级为「无 SP 标注」，前端仍可正常点餐 |

前端所有接口调用都包裹了 try-catch，失败时在对应视图内展示错误卡片，不影响其他视图。

---

## 六、Token 管理

```bash
# .env（已被 .gitignore 排除）
MCD_MCP_URL=https://mcp.mcd.cn
MCD_MCP_TOKEN=<your_mcd_mcp_token>
```

`mcp_client.py` 内置极简 `.env` 加载器，**Token 只从环境变量读取**，不硬编码在任何 Python 文件中。
