"""Deterministic storage and expiry defaults for ordinary pantry intake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re


_LOCATION_ALIASES = {
    "冷藏": "冷藏",
    "冰箱": "冷藏",
    "保鲜": "冷藏",
    "fridge": "冷藏",
    "refrigerator": "冷藏",
    "refrigerated": "冷藏",
    "冷冻": "冷冻",
    "冰柜": "冷冻",
    "冷冻室": "冷冻",
    "freezer": "冷冻",
    "frozen": "冷冻",
    "常温": "常温",
    "橱柜": "常温",
    "柜子": "常温",
    "厨房": "常温",
    "pantry": "常温",
    "ambient": "常温",
}

_FROZEN = re.compile(
    r"速冻|冷冻|冻品|冰淇淋|雪糕|冻饺|冻馄饨|冻丸|冰块|frozen",
    re.IGNORECASE,
)
_REFRIGERATED = re.compile(
    r"酸奶|牛奶|鲜奶|奶酪|芝士|黄油|鸡蛋|鸭蛋|豆花|豆腐|豆干|鲜肉|"
    r"猪肉|牛肉|羊肉|鸡肉|鸭肉|鱼|虾|蟹|海鲜|生菜|菠菜|油麦菜|白菜|"
    r"芹菜|香菜|西兰花|蘑菇|菌菇|辣椒|青椒|玉米|苹果|梨|葡萄|草莓|"
    r"蓝莓|熟食|剩菜|便当|三明治|蛋糕",
    re.IGNORECASE,
)
_AMBIENT = re.compile(
    r"大米|小米|糙米|面粉|燕麦|挂面|意面|干面|方便面|粉丝|豆类|"
    r"花生|瓜子|坚果|饼干|薯片|零食|罐头|酱|油|盐|糖|茶叶|咖啡豆|"
    r"奶粉|蛋白粉|香蕉|土豆|洋葱|大蒜|红薯|紫薯|干货",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PantryDefaults:
    storage_location: str
    storage_location_source: str
    expires_at: datetime
    expiry_source: str


def _explicit_location(
    storage_location: str | None,
    source_text: str,
) -> str | None:
    candidates = []
    if storage_location is not None:
        candidates.append(storage_location.strip())
    candidates.append(source_text)
    for candidate in candidates:
        lowered = candidate.lower()
        for alias, normalized in _LOCATION_ALIASES.items():
            if alias.lower() in lowered:
                return normalized
    return None


def _inferred_location(food_name: str, source_text: str) -> str:
    evidence = f"{food_name} {source_text}"
    if _FROZEN.search(evidence):
        return "冷冻"
    if _REFRIGERATED.search(evidence):
        return "冷藏"
    if _AMBIENT.search(evidence):
        return "常温"
    return "常温"


def _shelf_life_days(food_name: str, location: str) -> int:
    if location == "冷冻":
        if re.search(r"速冻|水饺|馄饨|丸", food_name):
            return 180
        return 90
    if location == "冷藏":
        if re.search(r"鲜肉|猪肉|牛肉|羊肉|鸡肉|鸭肉|鱼|虾|蟹|海鲜", food_name):
            return 3
        if re.search(r"豆花|豆腐|剩菜|熟食|便当|三明治", food_name):
            return 3
        if re.search(r"生菜|菠菜|油麦菜|香菜|蘑菇|菌菇|草莓|蓝莓", food_name):
            return 5
        if re.search(r"酸奶|鲜奶|牛奶", food_name):
            return 10
        if re.search(r"鸡蛋|鸭蛋", food_name):
            return 21
        return 10
    if re.search(r"大米|小米|糙米|面粉|燕麦|豆类|干货", food_name):
        return 365
    if re.search(r"罐头|方便面|挂面|意面|粉丝|饼干|薯片|坚果|花生|瓜子", food_name):
        return 180
    if re.search(r"香蕉", food_name):
        return 5
    if re.search(r"土豆|洋葱|大蒜|红薯|紫薯", food_name):
        return 30
    return 30


def resolve_pantry_defaults(
    *,
    food_name: str,
    source_text: str,
    added_at: datetime,
    storage_location: str | None,
    expires_at: datetime | None,
) -> PantryDefaults:
    """Return explicit facts unchanged and fill only omitted pantry metadata."""

    if added_at.tzinfo is None or added_at.utcoffset() is None:
        raise ValueError("added_at must include a timezone offset")
    explicit_location = _explicit_location(storage_location, source_text)
    location = explicit_location or _inferred_location(food_name, source_text)
    location_source = "user" if explicit_location is not None else "inferred"
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset")
        resolved_expiry = expires_at
        expiry_source = "user"
    else:
        resolved_expiry = added_at + timedelta(
            days=_shelf_life_days(food_name, location)
        )
        expiry_source = "estimated"
    if resolved_expiry <= added_at:
        raise ValueError("expires_at must be later than added_at")
    return PantryDefaults(
        storage_location=location,
        storage_location_source=location_source,
        expires_at=resolved_expiry,
        expiry_source=expiry_source,
    )
