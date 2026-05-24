"""Legacy SDK integration compatibility module."""

from __future__ import annotations

import importlib


def generate_sdk(language: str, schema: dict, config: dict) -> dict:
    mod = importlib.import_module("nexql.sdk.integration_features")
    return mod.generate_sdk(language, schema or {}, config or {})


def create_webhook(name: str, url: str, events: list | None = None) -> dict:
    event_type = "query.executed"
    if events:
        event_type = str(events[0])
    mod = importlib.import_module("nexql.sdk.integration_features")
    return mod.create_webhook(event_type=event_type, url=url)
