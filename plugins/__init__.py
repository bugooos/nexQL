"""
nexql.plugins — Plugin loader and lifecycle manager.
"""
from .loader import PluginLoader, PluginRegistry, NexQLPlugin

__all__ = ["PluginLoader", "PluginRegistry", "NexQLPlugin"]
