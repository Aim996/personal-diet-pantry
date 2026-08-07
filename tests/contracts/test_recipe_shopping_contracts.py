from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from personal_diet_pantry.service import DietService

from tests.contracts.helpers import pantry_add_payload, snapshot_business_tables


RECIPE = {
    "name": "番茄炒蛋",
    "yield_quantity": "2",
    "yield_unit": "portion",
    "source_text": "记住番茄炒蛋：2个鸡蛋加1个番茄，做两份",
    "ingredients": [
        {
            "food_name": "鸡蛋",
            "normalized_name": "egg",
            "quantity": "2",
            "unit": "piece",
        },
        {
            "food_name": "番茄",
            "normalized_name": "tomato",
            "quantity": "1",
            "unit": "piece",
        },
    ],
}


def _dispatch(
    service: DietService,
    domain: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return service.dispatch(
        {"domain": domain, "action": action, "payload": payload}
    )


def test_save_recipe_then_suggest_uses_current_pantry_without_writing(
    service: DietService,
) -> None:
    saved = _dispatch(service, "meal", "save_recipe", deepcopy(RECIPE))
    assert saved["ok"] is True
    assert saved["data"]["recipe"]["name"] == "番茄炒蛋"
    assert len(saved["data"]["recipe"]["ingredients"]) == 2

    before = snapshot_business_tables(service.connection)
    missing = _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 3, "max_missing_items": 2},
    )
    assert missing["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    assert missing["data"]["candidates"][0]["name"] == "番茄炒蛋"
    assert missing["data"]["candidates"][0]["missing_ingredients"] == [
        "鸡蛋",
        "番茄",
    ]

    egg = pantry_add_payload() | {"expires_at": "2099-08-05T00:00:00Z"}
    tomato = pantry_add_payload() | {
        "food_name": "番茄",
        "normalized_name": "tomato",
        "quantity": "4",
        "source_text": "买了4个番茄",
        "expires_at": "2099-08-05T00:00:00Z",
    }
    assert _dispatch(service, "pantry", "add", egg)["ok"] is True
    assert _dispatch(service, "pantry", "add", tomato)["ok"] is True

    available = _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 3, "max_missing_items": 0},
    )
    candidate = available["data"]["candidates"][0]
    assert candidate["missing_ingredients"] == []
    assert candidate["pantry_coverage"] == "1"
    assert candidate["candidate_only"] is True


def test_preview_meal_plan_is_bounded_and_read_only(
    service: DietService,
) -> None:
    assert _dispatch(service, "meal", "save_recipe", deepcopy(RECIPE))["ok"] is True
    before = snapshot_business_tables(service.connection)

    result = _dispatch(
        service,
        "meal",
        "preview_meal_plan",
        {"meal_type": "dinner", "limit": 3},
    )

    assert result["ok"] is True
    assert len(result["data"]["candidates"]) <= 3
    assert all(item["candidate_only"] is True for item in result["data"]["candidates"])
    assert snapshot_business_tables(service.connection) == before


def test_recipe_suggestions_prioritize_usable_expiring_inventory(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    egg_recipe = deepcopy(RECIPE) | {
        "name": "煎蛋",
        "source_text": "记住煎蛋用两个鸡蛋",
        "ingredients": [deepcopy(RECIPE["ingredients"][0])],
    }
    tomato_recipe = deepcopy(RECIPE) | {
        "name": "凉拌番茄",
        "source_text": "记住凉拌番茄用一个番茄",
        "ingredients": [deepcopy(RECIPE["ingredients"][1])],
    }
    assert _dispatch(service, "meal", "save_recipe", egg_recipe)["ok"] is True
    assert _dispatch(service, "meal", "save_recipe", tomato_recipe)["ok"] is True
    egg = pantry_add_payload() | {"expires_at": "2026-07-31T00:00:00Z"}
    tomato = pantry_add_payload() | {
        "food_name": "番茄",
        "normalized_name": "tomato",
        "quantity": "4",
        "source_text": "买了4个番茄",
        "expires_at": "2026-08-20T00:00:00Z",
    }
    assert _dispatch(service, "pantry", "add", egg)["ok"] is True
    assert _dispatch(service, "pantry", "add", tomato)["ok"] is True

    result = _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 2, "max_missing_items": 0},
    )

    candidates = result["data"]["candidates"]
    assert candidates[0]["name"] == "煎蛋"
    assert candidates[0]["expiring_ingredients"] == ["鸡蛋"]
    assert "优先使用临期食材" in candidates[0]["reasons"]


def test_recipe_suggestions_never_treat_expired_inventory_as_edible(
    service: DietService,
) -> None:
    service._clock = lambda: datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
    egg_recipe = deepcopy(RECIPE) | {
        "name": "煎蛋",
        "source_text": "记住煎蛋用两个鸡蛋",
        "ingredients": [deepcopy(RECIPE["ingredients"][0])],
    }
    assert _dispatch(service, "meal", "save_recipe", egg_recipe)["ok"] is True
    expired_egg = pantry_add_payload() | {
        "added_at": "2026-07-20T00:00:00Z",
        "expires_at": "2026-07-28T00:00:00Z",
    }
    assert _dispatch(service, "pantry", "add", expired_egg)["ok"] is True

    permissive = _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 3, "max_missing_items": 2},
    )
    candidate = permissive["data"]["candidates"][0]
    assert candidate["available_ingredients"] == []
    assert candidate["expiring_ingredients"] == []
    assert candidate["missing_ingredients"] == ["鸡蛋"]

    strict = _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 3, "max_missing_items": 0},
    )
    assert strict["data"]["candidates"] == []


def test_shopping_preview_commit_query_cancel_never_creates_inventory(
    service: DietService,
) -> None:
    payload = {
        "title": "本周补货",
        "source_text": "这周买鸡蛋和牛奶",
        "items": [
            {
                "food_name": "鸡蛋",
                "normalized_name": "egg",
                "quantity": "12",
                "unit": "piece",
                "reason": "早餐",
            },
            {
                "food_name": "牛奶",
                "normalized_name": "milk",
                "quantity": "2",
                "unit": "pack",
            },
        ],
    }
    before = snapshot_business_tables(service.connection)
    preview = _dispatch(
        service,
        "pantry",
        "preview_shopping_list",
        deepcopy(payload),
    )
    assert preview["ok"] is True
    assert snapshot_business_tables(service.connection) == before
    handle = preview["data"]["workflow"]["commit_handle"]

    committed = _dispatch(
        service,
        "pantry",
        "commit_shopping_list",
        {"commit_handle": handle},
    )
    replayed = _dispatch(
        service,
        "pantry",
        "commit_shopping_list",
        {"commit_handle": handle},
    )
    assert committed["ok"] is True
    assert replayed == committed
    assert committed["data"]["shopping_list"]["status"] == "active"
    assert len(committed["data"]["shopping_list"]["items"]) == 2
    assert (
        service.connection.execute("SELECT count(*) FROM pantry_batches").fetchone()[0]
        == 0
    )

    queried = _dispatch(
        service,
        "pantry",
        "query_shopping_list",
        {"status": "active"},
    )
    assert queried["ok"] is True
    assert len(queried["data"]["shopping_lists"]) == 1
    list_handle = queried["data"]["shopping_lists"][0]["workflow"][
        "shopping_list_handle"
    ]

    cancelled = _dispatch(
        service,
        "pantry",
        "cancel_shopping_list",
        {
            "shopping_list_handle": list_handle,
            "source_text": "这份清单不用了",
        },
    )
    cancel_replay = _dispatch(
        service,
        "pantry",
        "cancel_shopping_list",
        {
            "shopping_list_handle": list_handle,
            "source_text": "这份清单不用了",
        },
    )
    assert cancelled["ok"] is True
    assert cancel_replay == cancelled
    assert cancelled["data"]["shopping_list"]["status"] == "cancelled"
    assert (
        service.connection.execute("SELECT count(*) FROM pantry_batches").fetchone()[0]
        == 0
    )


def test_recipe_and_shopping_writes_are_human_readable_and_undoable(
    service: DietService,
) -> None:
    assert _dispatch(service, "meal", "save_recipe", deepcopy(RECIPE))["ok"] is True
    recent_recipe = _dispatch(
        service,
        "transaction",
        "get_recent",
        {"operation": "undo", "operation_type": "meal_plan", "limit": 1},
    )
    recipe_candidate = recent_recipe["data"]["candidates"][0]
    assert "菜谱" in recipe_candidate["summary"]
    assert "番茄炒蛋" in recipe_candidate["summary"]
    undone_recipe = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": recipe_candidate["workflow"][
                "operation_handle"
            ]
        },
    )
    assert undone_recipe["ok"] is True
    assert _dispatch(
        service,
        "meal",
        "suggest_recipes",
        {"limit": 3, "max_missing_items": 2},
    )["data"]["candidates"] == []

    preview = _dispatch(
        service,
        "pantry",
        "preview_shopping_list",
        {
            "title": "周末采购",
            "source_text": "周末买鸡蛋",
            "items": [
                {
                    "food_name": "鸡蛋",
                    "normalized_name": "egg",
                    "quantity": "12",
                    "unit": "piece",
                }
            ],
        },
    )
    assert _dispatch(
        service,
        "pantry",
        "commit_shopping_list",
        {"commit_handle": preview["data"]["workflow"]["commit_handle"]},
    )["ok"] is True
    recent_list = _dispatch(
        service,
        "transaction",
        "get_recent",
        {"operation": "undo", "operation_type": "reminder_manage", "limit": 1},
    )
    list_candidate = recent_list["data"]["candidates"][0]
    assert "购物清单" in list_candidate["summary"]
    assert "周末采购" in list_candidate["summary"]
    undone_list = _dispatch(
        service,
        "transaction",
        "undo",
        {
            "operation_handle": list_candidate["workflow"][
                "operation_handle"
            ]
        },
    )
    assert undone_list["ok"] is True
    assert _dispatch(
        service,
        "pantry",
        "query_shopping_list",
        {},
    )["data"]["shopping_lists"] == []
