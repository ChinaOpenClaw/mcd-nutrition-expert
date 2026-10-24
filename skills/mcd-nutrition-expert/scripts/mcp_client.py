# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · MCP 客户端
================================================
封装麦当劳中国官方 MCP Server（https://mcp.mcd.cn）的调用。

传输：Streamable HTTP
鉴权：请求头 `Authorization: Bearer <MCP_TOKEN>`

关键实现要点
------------
1. **优先读 structuredContent** —— 该 Server 的每个工具返回都带
   `result.structuredContent`，其中 `data` 字段是**结构化 JSON**；
   而 `result.content[0].text` 是给 LLM 看的自然语言描述（含 markdown
   表格 / 解释文字）。程序化调用必须用 structuredContent，否则要写
   脆弱的文本解析器。
   例外：`list-nutrition-foods` 的 data 是**伪表格字符串**
       （`[160]{字段列表}:` + 每行一条），需专用解析器（见 combo_solver.py）。

2. **Token 只从环境变量读取**，绝不硬编码进代码或配置文件。
   支持 `MCD_MCP_TOKEN` 环境变量，或 `.env` 文件。

3. **限流保护** —— 官方限制每 Token 每分钟 600 次请求，
   本客户端内置最小间隔与重试退避。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

MCP_URL = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
MIN_INTERVAL = 0.12          # 最小请求间隔（秒），防触发官方限流
MAX_RETRY = 3


class McpError(RuntimeError):
    """MCP 调用失败"""


def _load_dotenv() -> None:
    """极简 .env 加载（不引入 python-dotenv 依赖）"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


class McdMcpClient:
    """麦当劳 MCP Server 客户端"""

    def __init__(self, token: Optional[str] = None, url: Optional[str] = None,
                 timeout: int = 40):
        _load_dotenv()
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        self.url = url or MCP_URL
        self.timeout = timeout
        self._last_call = 0.0
        self._id = 0
        if not self.token:
            raise McpError(
                "未找到 MCP Token。请设置环境变量 MCD_MCP_TOKEN，"
                "或在项目根目录创建 .env 文件写入 MCD_MCP_TOKEN=你的Token。"
                "申请地址：https://open.mcd.cn/mcp"
            )

    # ---------- 底层 ----------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _throttle(self) -> None:
        gap = time.time() - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        self._last_call = time.time()

    def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }).encode("utf-8")

        req = urllib.request.Request(
            self.url,
            data=payload,
            method="POST",
            headers={
                "Authorization": "Bearer %s" % self.token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )

        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRY):
            self._throttle()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                # 兼容 SSE 风格返回
                if raw.startswith("event:") or raw.startswith("data:"):
                    for line in raw.split("\n"):
                        line = line.strip()
                        if line.startswith("data:"):
                            raw = line[5:].strip()
                            break
                obj = json.loads(raw)
                if "error" in obj:
                    raise McpError("MCP 返回错误：%s" % json.dumps(obj["error"], ensure_ascii=False))
                return obj
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:400]
                except Exception:
                    pass
                last_err = McpError("HTTP %s %s" % (e.code, body))
                if e.code in (401, 403):
                    raise McpError("MCP Token 无效或已过期（HTTP %s），请重新申请：https://open.mcd.cn/mcp" % e.code)
                if e.code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if e.code >= 500:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise McpError("MCP 请求失败：%s" % last_err)

    # ---------- 公开 API ----------

    def call(self, tool: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """
        调用一个工具，返回其 `data` 字段（结构化）。

        若 data 是字符串且不是 JSON，返回原字符串（伪表格由调用方另行解析）。
        """
        obj = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        result = obj.get("result", {})
        if result.get("isError"):
            text = ""
            c = result.get("content") or []
            if c:
                text = c[0].get("text", "")
            raise McpError("工具 %s 调用失败：%s" % (tool, text[:300]))

        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            if sc.get("success") is False:
                raise McpError("业务错误（%s）：%s" % (tool, sc.get("message", "未知")))
            return sc.get("data")
        # 回退：解析 content 文本
        c = result.get("content") or []
        if c:
            text = c[0].get("text", "")
            m = _find_json(text)
            if m is not None:
                return m.get("data")
        return None

    def call_raw(self, tool: str, args: Optional[Dict[str, Any]] = None) -> str:
        """返回工具的原始文本（用于伪表格类接口）"""
        obj = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        c = (obj.get("result") or {}).get("content") or []
        return c[0].get("text", "") if c else ""

    def list_tools(self) -> List[Dict[str, Any]]:
        obj = self._rpc("tools/list", {})
        return (obj.get("result") or {}).get("tools", [])

    # ---------- 业务封装 ----------

    def nutrition_foods(self) -> List[Dict[str, Any]]:
        """餐品营养成分列表（返回原始 data，字符串形态由 combo_solver 解析）"""
        return self.call("list-nutrition-foods", {})

    def nearby_stores(self, be_type: int = 1, search_type: int = 1,
                      city: Optional[str] = None,
                      keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """查询可点餐门店。beType: 1-到店自提 5-得来速"""
        args: Dict[str, Any] = {"beType": be_type, "searchType": search_type}
        if city:
            args["city"] = city
        if keyword:
            args["keyword"] = keyword
        data = self.call("query-nearby-stores", args)
        return data if isinstance(data, list) else []

    def store_menu(self, store_code: str, order_type: int = 1,
                   be_type: int = 1, be_code: Optional[str] = None) -> Dict[str, Any]:
        """拉取门店菜单：{'categories': [...], 'meals': {code: {...}}}"""
        args: Dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_code:
            args["beCode"] = be_code
        data = self.call("query-meals", args)
        return data if isinstance(data, dict) else {"categories": [], "meals": {}}

    def meal_detail(self, store_code: str, code: str, order_type: int = 1,
                    be_type: int = 1, be_code: Optional[str] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "code": code,
        }
        if be_type != 1 and be_code:
            args["beCode"] = be_code
        data = self.call("query-meal-detail", args)
        return data if isinstance(data, dict) else {}

    def available_coupons(self) -> List[Dict[str, Any]]:
        """可领取的优惠券"""
        data = self.call("available-coupons", {})
        return data if isinstance(data, list) else []

    def my_coupons(self) -> List[Dict[str, Any]]:
        """我已拥有的券。

        ⚠️ `query-my-coupons` 的 data 是 **dict**（{"coupons":[...]}），
        而 `available-coupons` 的是 **list**。
        早期统一按 list 取，导致卡包恒为空。
        """
        data = self.call("query-my-coupons", {})
        if isinstance(data, dict):
            return data.get("coupons") or data.get("list") or []
        return data if isinstance(data, list) else []

    def auto_bind_coupons(self) -> Any:
        """一键领券"""
        return self.call("auto-bind-coupons", {})

    def store_coupons(self, store_code: str, order_type: int = 1,
                      be_type: int = 1, be_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """指定门店可用券"""
        args: Dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
        }
        if be_type != 1 and be_code:
            args["beCode"] = be_code
        data = self.call("query-store-coupons", args)
        return data if isinstance(data, list) else []

    def calculate_price(self, store_code: str, items: List[Dict[str, Any]],
                        order_type: int = 1, be_type: int = 1,
                        be_code: Optional[str] = None,
                        need_tableware: bool = False) -> Dict[str, Any]:
        """
        计价。items 元素形如
        {'productCode': '1100', 'quantity': 1, 'couponId': '...'}

        ⚠️ 返回的价格单位为**分**，展示需除以 100。
        """
        args: Dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
            "needTableware": need_tableware,
        }
        if be_type != 1 and be_code:
            args["beCode"] = be_code
        data = self.call("calculate-price", args)
        return data if isinstance(data, dict) else {}

    def create_order(self, store_code: str, items: List[Dict[str, Any]],
                     take_way_code: str = "eat-in", order_type: int = 1,
                     be_type: int = 1, be_code: Optional[str] = None,
                     need_tableware: bool = False,
                     remark: Optional[str] = None) -> Dict[str, Any]:
        """创建订单"""
        args: Dict[str, Any] = {
            "storeCode": store_code,
            "orderType": order_type,
            "beType": be_type,
            "items": items,
            "takeWayCode": take_way_code,
            "needTableware": need_tableware,
        }
        if be_type != 1 and be_code:
            args["beCode"] = be_code
        if remark:
            args["remark"] = remark
        data = self.call("create-order", args)
        return data if isinstance(data, dict) else {}

    def my_account(self) -> Dict[str, Any]:
        """积分账户"""
        data = self.call("query-my-account", {})
        return data if isinstance(data, dict) else {}

    def campaign_calendar(self, specified_date: Optional[str] = None) -> Any:
        """营销活动日历"""
        args = {"specifiedDate": specified_date} if specified_date else {}
        return self.call("campaign-calendar", args)

    def my_orders(self) -> Any:
        """历史订单"""
        return self.call("order-list", {})

    def order_detail(self, order_no: str) -> Any:
        """订单详情"""
        return self.call("query-order", {"orderNo": order_no})


def _find_json(text: str) -> Optional[Dict[str, Any]]:
    """从可能混有 markdown 的文本里找出第一个 JSON 对象"""
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


if __name__ == "__main__":
    import sys
    c = McdMcpClient()
    print("MCP 连接成功，工具数量：", len(c.list_tools()))
    stores = c.nearby_stores()
    print("门店数：", len(stores))
    for s in stores[:3]:
        print("  -", s.get("storeName"), "|", s.get("storeCode"))
    menu = c.store_menu(stores[0]["storeCode"]) if stores else {}
    print("菜单分类数：", len(menu.get("categories", [])),
          "餐品数：", len(menu.get("meals", {})))
    foods = c.call_raw("list-nutrition-foods", {})
    print("营养数据文本长度：", len(foods))
