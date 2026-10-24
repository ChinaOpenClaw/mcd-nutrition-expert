# -*- coding: utf-8 -*-
"""
麦当劳营养膳食专家 · 健康记录存储
================================================
用 SQLite 记录用户每日饮食与身体数据，支撑「健康可视化」模块：
体重趋势、热量收支、营养素达成率、钠摄入超标天数、SP 象限分布。

设计要点
--------
1. **纯标准库 sqlite3**，零外部依赖，数据库文件默认落在 `data/health.db`。
2. **每日汇总表 + 明细表**分离：
   · `daily_log`  —— 每天一行：体重、目标热量、实际摄入、各项营养素
   · `meal_log`   —— 每餐一行：餐品明细，可展开
   这样查询趋势是 O(天数)，不需要每次扫描明细。
3. **同一天多次记录自动累加**，用 `UPSERT` 语义（先查后累加）实现。
4. 提供 30 天趋势聚合查询，供前端画图。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "health.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_log (
    log_date      TEXT PRIMARY KEY,
    weight_kg     REAL,
    target_kcal   INTEGER,
    target_protein REAL,
    target_carb   REAL,
    target_fat    REAL,
    target_sodium INTEGER,
    consumed_kcal INTEGER DEFAULT 0,
    consumed_protein REAL DEFAULT 0,
    consumed_fat  REAL DEFAULT 0,
    consumed_carb REAL DEFAULT 0,
    consumed_sodium INTEGER DEFAULT 0,
    order_count   INTEGER DEFAULT 0,
    note          TEXT,
    created_at    TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS meal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date    TEXT NOT NULL,
    meal_type   TEXT,
    items       TEXT,
    kcal        INTEGER DEFAULT 0,
    protein     REAL DEFAULT 0,
    fat         REAL DEFAULT 0,
    carb        REAL DEFAULT 0,
    sodium      INTEGER DEFAULT 0,
    quadrant    TEXT,
    store_name  TEXT,
    order_no    TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS profile (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    gender       TEXT,
    age          INTEGER,
    height_cm    REAL,
    activity     TEXT,
    goal         TEXT,
    body_fat_pct REAL,
    updated_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_meal_log_date ON meal_log(log_date);
"""


class HealthStore:
    """健康记录存储"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("MCD_HEALTH_DB", DEFAULT_DB)
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    # ---------- 档案 ----------

    def save_profile(self, p: Dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO profile (id, gender, age, height_cm, activity, goal,"
                " body_fat_pct, updated_at) VALUES (1,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET gender=excluded.gender,"
                " age=excluded.age, height_cm=excluded.height_cm,"
                " activity=excluded.activity, goal=excluded.goal,"
                " body_fat_pct=excluded.body_fat_pct, updated_at=excluded.updated_at",
                (p.get("gender"), p.get("age"), p.get("height_cm"),
                 p.get("activity"), p.get("goal"), p.get("body_fat_pct"),
                 _now()),
            )

    def get_profile(self) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM profile WHERE id=1").fetchone()
        return dict(row) if row else None

    # ---------- 每日记录 ----------

    def ensure_day(self, log_date: str, targets: Optional[Dict[str, Any]] = None,
                   weight_kg: Optional[float] = None) -> None:
        """确保某天的记录存在（用于初始化目标）"""
        with self._conn() as c:
            row = c.execute("SELECT log_date FROM daily_log WHERE log_date=?",
                            (log_date,)).fetchone()
            if row:
                # ⚠️ 行已存在时也要补写目标：早先的记录可能是先于「建档」写入的，
                #    此时目标列为 NULL；若直接 return，后续算出的目标永远落不了库，
                #    趋势图上的「目标线」会一直是空的。
                t = targets or {}
                sets, vals = [], []
                for col in ("target_kcal", "target_protein", "target_carb",
                            "target_fat", "target_sodium"):
                    v = t.get(col)
                    if v is not None:
                        sets.append("%s=?" % col)
                        vals.append(v)
                if weight_kg is not None:
                    sets.append("weight_kg=?")
                    vals.append(weight_kg)
                if sets:
                    sets.append("updated_at=?")
                    vals.append(_now())
                    vals.append(log_date)
                    c.execute("UPDATE daily_log SET %s WHERE log_date=?"
                              % ",".join(sets), vals)
                return
            t = targets or {}
            c.execute(
                "INSERT INTO daily_log (log_date, weight_kg, target_kcal,"
                " target_protein, target_carb, target_fat, target_sodium,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (log_date, weight_kg, t.get("target_kcal"), t.get("target_protein"),
                 t.get("target_carb"), t.get("target_fat"), t.get("target_sodium"),
                 _now(), _now()),
            )

    def add_meal(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """
        记录一餐。

        entry: {
          'log_date': '2026-10-25',   # 可选，默认今天
          'meal_type': '午餐',
          'items': [{'name':..., 'kcal':..., 'protein':..., 'fat':...,
                     'carb':..., 'sodium':..., 'category':...}],
          'quadrant': 'sweet_point',
          'store_name': '麦当劳XX餐厅',
          'order_no': '...'
        }
        """
        log_date = entry.get("log_date") or date.today().isoformat()
        items = entry.get("items") or []

        def _num(item: Dict[str, Any], *names) -> float:
            """营养字段兼容取值。

            前端 / MCP 返回的字段名带单位后缀（protein_g / sodium_mg），
            而库内约定用无后缀名（protein / sodium）。两种都支持。
            """
            for n in names:
                v = item.get(n)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            return 0.0

        kcal = int(sum(_num(i, "kcal", "energy_kcal") for i in items))
        protein = float(sum(_num(i, "protein", "protein_g") for i in items))
        fat = float(sum(_num(i, "fat", "fat_g") for i in items))
        carb = float(sum(_num(i, "carb", "carbohydrate", "carb_g") for i in items))
        sodium = int(sum(_num(i, "sodium", "sodium_mg") for i in items))

        with self._conn() as c:
            c.execute(
                "INSERT INTO meal_log (log_date, meal_type, items, kcal, protein,"
                " fat, carb, sodium, quadrant, store_name, order_no, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (log_date, entry.get("meal_type"), json.dumps(items, ensure_ascii=False),
                 kcal, protein, fat, carb, sodium, entry.get("quadrant"),
                 entry.get("store_name"), entry.get("order_no"), _now()),
            )
            # 日汇总累加
            row = c.execute("SELECT log_date FROM daily_log WHERE log_date=?",
                            (log_date,)).fetchone()
            if not row:
                c.execute(
                    "INSERT INTO daily_log (log_date, consumed_kcal,"
                    " consumed_protein, consumed_fat, consumed_carb,"
                    " consumed_sodium, order_count, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (log_date, kcal, protein, fat, carb, sodium, 1, _now(), _now()))
            else:
                c.execute(
                    "UPDATE daily_log SET consumed_kcal=consumed_kcal+?,"
                    " consumed_protein=consumed_protein+?, consumed_fat=consumed_fat+?,"
                    " consumed_carb=consumed_carb+?, consumed_sodium=consumed_sodium+?,"
                    " order_count=order_count+1, updated_at=? WHERE log_date=?",
                    (kcal, protein, fat, carb, sodium, _now(), log_date))

        return {"log_date": log_date, "kcal": kcal, "protein": protein,
                "fat": fat, "carb": carb, "sodium": sodium}

    def set_weight(self, log_date: str, weight_kg: float) -> None:
        self.ensure_day(log_date, weight_kg=weight_kg)

    def delete_day(self, log_date: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM meal_log WHERE log_date=?", (log_date,))
            c.execute("DELETE FROM daily_log WHERE log_date=?", (log_date,))

    # ---------- 查询 ----------

    def day_detail(self, log_date: str) -> Dict[str, Any]:
        with self._conn() as c:
            d = c.execute("SELECT * FROM daily_log WHERE log_date=?",
                          (log_date,)).fetchone()
            meals = c.execute(
                "SELECT * FROM meal_log WHERE log_date=? ORDER BY id", (log_date,)
            ).fetchall()
        day = dict(d) if d else {"log_date": log_date}
        out_meals = []
        for m in meals:
            md = dict(m)
            try:
                md["items"] = json.loads(md.get("items") or "[]")
            except Exception:
                md["items"] = []
            out_meals.append(md)
        day["meals"] = out_meals
        return day

    def trend(self, days: int = 30, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """最近 N 天的每日汇总（补齐无记录的日期）"""
        end = end_date or date.today().isoformat()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        start_d = end_d - timedelta(days=days - 1)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM daily_log WHERE log_date BETWEEN ? AND ?"
                " ORDER BY log_date", (start_d.isoformat(), end)).fetchall()
        by_date = {r["log_date"]: dict(r) for r in rows}
        out = []
        for i in range(days):
            d = (start_d + timedelta(days=i)).isoformat()
            r = by_date.get(d)
            if r is None:
                out.append({"log_date": d, "weight_kg": None, "target_kcal": None,
                            "consumed_kcal": 0, "consumed_protein": 0,
                            "consumed_fat": 0, "consumed_carb": 0,
                            "consumed_sodium": 0, "order_count": 0})
            else:
                out.append(r)
        return out

    def summary(self, days: int = 30) -> Dict[str, Any]:
        """健康可视化核心指标"""
        tr = self.trend(days)
        active = [r for r in tr if r.get("consumed_kcal")]
        weights = [(r["log_date"], r["weight_kg"]) for r in tr if r.get("weight_kg")]
        kcal_vals = [r["consumed_kcal"] for r in active]
        sod_vals = [r["consumed_sodium"] for r in active]
        tgt = [r["target_kcal"] for r in active if r.get("target_kcal")]

        weight_change = None
        if len(weights) >= 2:
            weight_change = round(weights[-1][1] - weights[0][1], 1)

        over_sodium_days = 0
        for r in active:
            lim = r.get("target_sodium")
            if lim and r.get("consumed_sodium", 0) > lim:
                over_sodium_days += 1

        # SP 象限分布（近 N 天）
        with self._conn() as c:
            qrows = c.execute(
                "SELECT quadrant, COUNT(*) n, SUM(kcal) k FROM meal_log"
                " WHERE log_date >= ? GROUP BY quadrant",
                (tr[0]["log_date"],)).fetchall()
        quadrant_dist = {r["quadrant"] or "unknown": {"meals": r["n"], "kcal": r["k"] or 0}
                         for r in qrows}

        return {
            "days": days,
            "active_days": len(active),
            "avg_kcal": round(sum(kcal_vals) / len(kcal_vals)) if kcal_vals else 0,
            "avg_target_kcal": round(sum(tgt) / len(tgt)) if tgt else 0,
            "avg_sodium": round(sum(sod_vals) / len(sod_vals)) if sod_vals else 0,
            "over_sodium_days": over_sodium_days,
            "total_orders": sum(r.get("order_count", 0) for r in tr),
            "weight_change": weight_change,
            "weight_points": [{"date": d, "weight": w} for d, w in weights],
            "quadrant_dist": quadrant_dist,
        }

    def recent_meals(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, log_date, meal_type, items, kcal, protein, fat, carb,"
                " sodium, quadrant, store_name, order_no FROM meal_log"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["items"] = json.loads(d.get("items") or "[]")
            except Exception:
                d["items"] = []
            out.append(d)
        return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    import tempfile
    s = HealthStore(os.path.join(tempfile.gettempdir(), "mcd_health_test.db"))
    s.ensure_day(date.today().isoformat(),
                  {"target_kcal": 2000, "target_protein": 120,
                   "target_carb": 220, "target_fat": 60, "target_sodium": 2000})
    s.add_meal({"meal_type": "午餐", "items": [
        {"name": "巨无霸", "kcal": 513, "protein": 27, "fat": 26,
         "carb": 42, "sodium": 961},
        {"name": "中薯条", "kcal": 289, "protein": 4, "fat": 12,
         "carb": 38, "sodium": 165},
    ], "quadrant": "double_edge"})
    print("当天汇总：", json.dumps(s.day_detail(date.today().isoformat()),
                                   ensure_ascii=False)[:400])
    print("摘要：", json.dumps(s.summary(7), ensure_ascii=False, indent=1)[:500])
    os.remove(s.db_path)
