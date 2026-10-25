# workbuddy.md

> WorkBuddy Skill 使用说明 —— 麦当劳营养膳食专家

本项目以 **WorkBuddy Skill** 形态交付，可直接在 WorkBuddy 会话中调用，也可作为独立 Web 应用运行。

---

## 一、这是什么

一个把「控卡期吃麦当劳」从玄学变成可计算问题的 Skill：

1. 读你的身体数据 → 算出每日消耗与营养目标
2. 用 **SP 矩阵**给 160 个麦当劳餐品做四象限定位
3. 用多目标约束求解器配出**结构合理**的套餐
4. 带你进店点餐、领券、下单
5. 吃完自动记入健康档案，长期追踪趋势

---

## 二、在 WorkBuddy 中安装

### 方式 A：从仓库目录加载

```bash
git clone https://github.com/ChinaOpenClaw/mcd-nutrition-expert.git
```

在 WorkBuddy 会话中：

```
把 E:/mcd-nutrition-expert/skills/mcd-nutrition-expert 目录作为 Skill 加载
```

### 方式 B：复制 Skill 目录

```bash
cp -r mcd-nutrition-expert/skills/mcd-nutrition-expert ~/.workbuddy/skills/
```

### 配置 Token

```bash
cd ~/.workbuddy/skills/mcd-nutrition-expert
cp .env.example .env
# 填入你的麦当劳 MCP Token
```

```ini
MCD_MCP_URL=https://mcp.mcd.cn
MCD_MCP_TOKEN=<your_token>
```

Token 只从环境变量读取，不会出现在任何代码或提交中。

---

## 三、触发方式

### 3.1 显式指令

```
用 mcd-nutrition-expert 帮我配一个减脂期的麦当劳套餐
```

```
打开麦当劳营养膳食专家，我想要增肌方案
```

### 3.2 自然语言（推荐）

Skill 的 `description` 覆盖了以下语义，命中即自动加载：

- 「我想减肥但还想吃麦当劳」
- 「帮我算一下我一天该吃多少」
- 「麦当劳哪些东西钠低」
- 「今天中午吃麦当劳点什么」
- 「帮我领张券然后点单」

---

## 四、典型工作流

### 场景 A：控卡期配餐（最常用）

```
用户：男，25 岁，175cm，70kg，体脂 18%，想减脂
```

Skill 依次完成：

1. **建档** → BMR 1610 kcal，TDEE 2214 kcal，全天目标 1771 kcal / 蛋白 140 g
2. **单餐折算** → 一餐目标 602 kcal（全天 × 0.34）
3. **SP 矩阵** → 156 个餐品四象限定位，目标点 (18.73, 96.06)
4. **套餐求解** → 给出 3 个结构合法的方案：

```
方案 1  637 kcal（占全天 36%）· 蛋白 44g · 钠 1878mg
   麦辣鸡腿汉堡 + 无糖可口可乐中杯 + 蜜汁BBQ风味薄皮脆汁鸡-带骨里脊
   ⚠️ 钠摄入 1878 mg，已达上限的 94%，建议搭配无糖饮品并注意当日其他饮食的钠来源。

方案 2  511 kcal（占全天 29%）· 蛋白 43g · 钠 1781mg
   那么大鸡排 + 蒜了吧黄油风味薄皮脆汁鸡-带骨里脊 + 无糖可口可乐中杯
```

5. **策略建议** → 优先选烤制/水煮类单品；饮品一律选无糖（0 热量且钠极低）

### 场景 B：进店点餐

```
用户：我要去沈阳三好街那家店吃饭
```

Skill 会：

1. 查附近门店（`query-nearby-stores`）→ 列出门店名、地址、营业时段
2. 你点击门店 → 拉取 14 个分类 / 87 个条目的完整菜单
3. **每个菜单条目都带 SP 象限标签 + 贴合度 + 营养四宫格**
4. 套餐条目自动展开内含单品：`含：龙焰鸡腿堡 / 中薯条 / 可乐中杯`
5. 加入购物车 → 实时计价 → 确认下单

### 场景 C：领券省钱

```
用户：有什么券可以领？
```

Skill 列出可领取的券，一键领取，并展示卡包与本店可用券。

### 场景 D：健康追踪

```
用户：我这周吃得怎么样？
```

Skill 读取 SQLite 档案，返回：

- 体重趋势折线
- 每日热量摄入 vs 目标双线
- 象限分布条形图（主力区占比多少）
- 超钠天数统计

---

## 五、脚本直接调用

Skill 的每个模块都可独立运行：

```bash
cd skills/mcd-nutrition-expert/scripts

# 营养目标计算（离线）
python nutrition_engine.py --demo

# 套餐求解器自检（离线，内嵌样例数据）
python combo_solver.py --selftest

# SP 矩阵（需要 MCP Token）
python sp_matrix.py

# 启动 Web 应用
python -m uvicorn server:app --host 127.0.0.1 --port 8899
```

---

## 六、接口清单

Web 应用启动后可用 `curl` 直接调用：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 服务状态 + MCP 工具数 + 营养条目数 |
| POST | `/api/target` | 计算营养目标 |
| GET | `/api/matrix` | SP 矩阵全量数据 |
| POST | `/api/recommend` | 套餐推荐（含 SP 单品推荐） |
| GET | `/api/stores` | 附近门店 |
| GET | `/api/menu` | 门店菜单（带 SP 标注） |
| GET | `/api/coupons` | 优惠券（可领取/我的/本店可用） |
| POST | `/api/coupons/claim` | 一键领券 |
| POST | `/api/cart` | 计价 |
| POST | `/api/order` | 下单 |
| GET | `/api/records` | 健康记录查询 |
| POST | `/api/records` | 写入一餐记录 |
| POST | `/api/records/weight` | 记录体重 |
| DELETE | `/api/records` | 删除某日记录 |
| GET | `/api/account` | 账户信息 |

示例：

```bash
curl -X POST http://127.0.0.1:8899/api/recommend -H "Content-Type: application/json" \
  -d '{"gender":"male","age":25,"height_cm":175,"weight_kg":70,
       "activity":"light","goal":"cut","body_fat_pct":18}'
```

---

## 七、约束与边界

| 项 | 说明 |
|---|---|
| Token | 只读环境变量；`.env` 已被 gitignore |
| 健康数据 | 仅存本地 SQLite，不上传 |
| 数据来源 | 全部来自麦当劳官方 MCP，无模拟数据 |
| 结果性质 | 供参考，不构成医疗/营养建议 |
| 门店状态 | 需实际核验，部分门店可能暂停营业 |

---

## 八、目录结构

```
skills/mcd-nutrition-expert/
├── SKILL.md              # Skill 定义（触发词 + 工作流）
├── .env.example          # Token 占位符
├── scripts/
│   ├── nutrition_engine.py   # BMR/TDEE/目标计算
│   ├── combo_solver.py       # 约束求解器
│   ├── sp_matrix.py          # SP 四象限
│   ├── mcp_client.py         # MCP 客户端
│   ├── health_store.py       # SQLite 档案
│   └── server.py             # FastAPI 后端
├── web/index.html        # 单文件前端
├── references/           # 参考资料
└── data/                 # 本地数据库（gitignore）
```
