import os

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.scanner.walker as walker_mod
from app.config import Config


def test_public_config_defaults(session):
    with TestClient(main_mod.create_app()) as c:
        cfg = c.get("/api/config").json()  # available before sign-in
    assert cfg["app_name"] == "Photo Audit"
    assert cfg["map_tile_url"].startswith("https://")


def test_public_config_custom_and_map_off(session, monkeypatch):
    monkeypatch.setenv("APP_NAME", "Smith Family Photos")
    monkeypatch.setenv("MAP_TILE_URL", "off")
    monkeypatch.setattr(main_mod, "config", Config())
    with TestClient(main_mod.create_app()) as c:
        cfg = c.get("/api/config").json()
    assert cfg == {"app_name": "Smith Family Photos", "map_tile_url": None,
                   "map_attribution": "© OpenStreetMap contributors"}


@pytest.mark.parametrize("bad", ["quarantine", "a/b", ".x/y"])
def test_quarantine_dirname_must_be_hidden_single_folder(monkeypatch, bad):
    monkeypatch.setenv("QUARANTINE_DIRNAME", bad)
    with pytest.raises(ValueError):
        Config()


def test_walker_skips_custom_quarantine_and_nas_system_dirs(library, monkeypatch):
    monkeypatch.setenv("QUARANTINE_DIRNAME", "@Deleted")
    monkeypatch.setattr(walker_mod, "config", Config())
    for rel in ("Alex/a.jpg", "@Deleted/2024-01-01/Alex/b.jpg", "#recycle/c.jpg", "Alex/.@__thumb/d.jpg"):
        full = os.path.join(library, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "wb").close()
    assert [r for r, _s, _m in walker_mod.walk_library(library)] == ["Alex/a.jpg"]
