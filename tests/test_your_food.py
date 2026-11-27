import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.repositories.recommendations import RecommendationRepository
from app.schemas.catalog import CatalogMenu, MenuSnapshot
from app.schemas.llm import LLMSelectionResponse
from app.services.catalog import CatalogLoader, CatalogService


@pytest.mark.parametrize("content", [
    [], [""], ["   "], ["🍗 nasi", "米", "▛▀▀▜"],
    ["a", "lebih panjang"], ["\t", "baris\nberikutnya"],
    ["x" * 2001], ["baris"] * 1001,
])
def test_your_food_preserves_content_without_art_constraints(content):
    menu = CatalogMenu(
        id="menu-test", name="Menu", description="Isi katalog",
        is_active=True, your_food=content,
    )
    snapshot = CatalogService.make_snapshot(menu)
    assert menu.your_food == snapshot.your_food == content
    assert snapshot.model_dump()["your_food"] == content
    assert "ascii_art" not in snapshot.model_dump()
    if snapshot.your_food:
        snapshot.your_food.append("tambahan")
        assert menu.your_food == content


@pytest.mark.parametrize("value", ["teks", {}, [1], [None], [True]])
def test_your_food_still_requires_array_of_strings(value):
    with pytest.raises(ValidationError):
        CatalogMenu(
            id="menu-test", name="Menu", description="Isi katalog",
            is_active=True, your_food=value,
        )


def test_current_catalog_loads_and_preserves_every_food():
    path = Path(__file__).resolve().parents[1] / "app/data/menus.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    catalog = CatalogLoader(path).load()
    assert [menu.your_food for menu in catalog.menus] == [
        menu["your_food"] for menu in raw["menus"]
    ]


def test_menu_and_recommendation_endpoints_use_your_food(api, tmp_path):
    content = ["  ▛▀▀▜  ", "🍗\t nasi\n", "", "x" * 2001] + ["米"] * 1001
    path = tmp_path / "menus.json"
    path.write_text(json.dumps({"version": 1, "menus": [{
        "id": "menu-unicode", "name": "Menu Unicode", "description": "Pilihan lokal",
        "is_active": True, "your_food": content,
    }]}, ensure_ascii=False), encoding="utf-8")
    loader = CatalogLoader(path)
    loader.load()
    api.app.state.catalog_service = CatalogService(loader)
    listing = api.client.get("/menus")
    detail = api.client.get("/menus/menu-unicode")
    assert listing.status_code == detail.status_code == 200
    assert listing.json()["items"][0]["your_food"] == content
    assert detail.json()["your_food"] == content

    api.limiter.acquire_recommendation = AsyncMock(return_value=SimpleNamespace())
    api.limiter.release_recommendation = AsyncMock()
    provider = AsyncMock(return_value=LLMSelectionResponse(
        menu_id="menu-unicode", selection_reason="Sesuai konteks daerah",
    ))
    api.app.state.llm_client = SimpleNamespace(model="test-model", select_menu=provider)
    created = api.client.post("/recommendations", headers=api.headers, json={
        "region_name": "Pesisir", "geographic_description": "Wilayah pesisir utara",
    })
    assert created.status_code == 201
    assert created.json()["menu"]["your_food"] == content
    saved = api.client.get("/recommendations/" + created.json()["id"], headers=api.headers)
    history = api.client.get("/recommendations", headers=api.headers)
    assert saved.status_code == history.status_code == 200
    assert saved.json() == created.json()
    assert created.json() in history.json()["items"]
    assert "ascii_art" not in saved.json()["menu"]
    candidates = provider.call_args.args[0].model_dump()["candidates"]
    assert all("your_food" not in candidate for candidate in candidates)


def test_legacy_history_is_exposed_with_current_field(api, monkeypatch):
    legacy = {
        "id": "removed-menu", "name": "Menu lama", "description": "Snapshot lama",
        "ascii_art": ["+---+", "| o |", "+---+"],
    }
    record = SimpleNamespace(
        id=UUID(int=3), region_name="Pesisir", geographic_description="Pesisir utara",
        selection_reason="Bahan lokal", menu_snapshot=legacy,
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(RecommendationRepository, "get_for_user", AsyncMock(return_value=record))
    monkeypatch.setattr(RecommendationRepository, "list_for_user", AsyncMock(return_value=([record], False)))
    detail = api.client.get(f"/recommendations/{record.id}", headers=api.headers)
    listing = api.client.get("/recommendations", headers=api.headers)
    assert detail.status_code == listing.status_code == 200
    for item in [detail.json(), *listing.json()["items"]]:
        assert item["menu"]["your_food"] == legacy["ascii_art"]
        assert "ascii_art" not in item["menu"]
    assert "your_food" not in legacy


def test_openapi_exposes_only_current_food_field_without_size_constraints(api):
    specification = api.client.get("/openapi.json").json()
    assert '"ascii_art"' not in json.dumps(specification)
    food = MenuSnapshot.model_json_schema()["properties"]["your_food"]
    assert food["type"] == "array"
    assert food["items"]["type"] == "string"
    assert not {"maxItems", "minItems"} & food.keys()
    assert not {"maxLength", "minLength", "pattern"} & food["items"].keys()
