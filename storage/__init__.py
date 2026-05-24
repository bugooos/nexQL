"""
nexql.storage — Persistence layer.
"""
from .store import DataStore

StorageEngine = DataStore   # public alias
Collection    = dict        # collections are plain dicts in this implementation
Database      = dict        # databases too

__all__ = ["StorageEngine", "DataStore", "Collection", "Database"]
