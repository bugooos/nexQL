"""Legacy team/enterprise compatibility module."""

from __future__ import annotations

import importlib
import time


_TEAMS: set[str] = set()


def create_team(name: str, members: list | None = None) -> dict:
    mod = importlib.import_module("nexql.runtime.team_features")
    members = members or []
    owner = str(members[0]) if members else "owner"
    team_id = f"team_{int(time.time() * 1000)}"
    team = mod.create_team(team_id, name, owner)
    _TEAMS.add(team_id)
    for member in members[1:]:
        mod.add_team_member(team_id, str(member))
    return team


def get_team_analytics(team_id: str) -> dict:
    # Keep compatibility contract: return analytics even when no event stream is provided.
    mod = importlib.import_module("nexql.runtime.team_features")
    return mod.team_analytics(team_id, [])
