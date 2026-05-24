"""
nexql/plugins/loader.py
───────────────────────
Plugin system for Piql runtime extensions.

WHY THIS EXISTS:
  The original codebase had plugin-like stubs inside sdk_integration_features.py
  but no actual plugin loading mechanism.  This module provides a proper
  plugin architecture so third parties can extend:
    • Custom directives
    • Custom execution handlers (e.g. SQL backend)
    • Custom serializers / transports
    • Custom schema sources

PLUGIN CONTRACT:
  A plugin is a Python module (or package) that exports a class subclassing
    PiqlPlugin.  At load time, the loader calls plugin.register(runtime).

SCALABILITY:
  This is the hook for distributed execution backends, AI-assisted planners,
  and alternative storage engines.

PLUGIN DISCOVERY:
  1. Built-in plugins registered directly via loader.register_builtin()
    2. Directory scan: ~/.piql-workbench/plugins/*.py
    3. Entry-point based (future: pip install piql-plugin-postgres)
"""

from __future__ import annotations
import importlib
import importlib.util
import json
import sys
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ─── Plugin base class ────────────────────────────────────────────────────────

class NexQLPlugin(ABC):
    """Base class for all Piql plugins."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier (snake_case)."""
        ...

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return ""

    def register(self, runtime: "PluginRuntime") -> None:
        """Called once when the plugin is loaded. Register hooks here."""
        pass

    def on_load(self) -> None:
        """Called after register() completes successfully."""
        pass

    def on_unload(self) -> None:
        """Called when the plugin is removed from the runtime."""
        pass


# ─── Plugin runtime context ───────────────────────────────────────────────────

class PluginRuntime:
    """
    The context object passed to Plugin.register().
    Plugins call methods on this to hook into the execution pipeline.
    """

    def __init__(self) -> None:
        self._directive_handlers:   dict[str, Callable] = {}
        self._method_handlers:      dict[str, Callable] = {}
        self._pre_execution_hooks:  list[Callable]      = []
        self._post_execution_hooks: list[Callable]      = []
        self._serializers:          dict[str, Callable] = {}

    def register_directive(self, name: str, handler: Callable) -> None:
        """Register a custom directive handler (e.g. @ratelimit)."""
        self._directive_handlers[name] = handler

    def register_method_handler(self, method: str, handler: Callable) -> None:
        """Register a custom execution method (replaces built-in read/create/etc)."""
        self._method_handlers[method] = handler

    def add_pre_execution_hook(self, hook: Callable) -> None:
        """Hook called before every query execution. Signature: hook(plan) -> plan | None."""
        self._pre_execution_hooks.append(hook)

    def add_post_execution_hook(self, hook: Callable) -> None:
        """Hook called after every query execution. Signature: hook(result) -> result."""
        self._post_execution_hooks.append(hook)

    def register_serializer(self, fmt: str, serializer: Callable) -> None:
        """Register a custom response serializer (e.g. 'msgpack', 'protobuf')."""
        self._serializers[fmt] = serializer

    def apply_pre_hooks(self, plan: Any) -> Any:
        for hook in self._pre_execution_hooks:
            result = hook(plan)
            if result is not None:
                plan = result
        return plan

    def apply_post_hooks(self, result: Any) -> Any:
        for hook in self._post_execution_hooks:
            result = hook(result) or result
        return result

    def get_directive_handler(self, name: str) -> Optional[Callable]:
        return self._directive_handlers.get(name)

    def get_method_handler(self, method: str) -> Optional[Callable]:
        return self._method_handlers.get(method)

    def serialize(self, result: Any, fmt: str = "json") -> bytes:
        handler = self._serializers.get(fmt)
        if handler:
            return handler(result)
        import json as _json
        return _json.dumps(result, default=str).encode()


# ─── Plugin registry ──────────────────────────────────────────────────────────

@dataclass
class PluginEntry:
    plugin:  NexQLPlugin
    enabled: bool = True
    error:   str  = ""


class PluginLoader:
    """
    Discovers, loads, and manages Piql plugins.

    Usage:
        loader = PluginLoader()
        loader.load_directory(Path.home() / '.piql-workbench' / 'plugins')
        runtime = loader.build_runtime()
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginEntry] = {}
        self._runtime: PluginRuntime = PluginRuntime()

    # ── Registration ─────────────────────────────────────────────────────────

    def register_builtin(self, plugin: NexQLPlugin) -> None:
        """Manually register a built-in plugin instance."""
        try:
            plugin.register(self._runtime)
            plugin.on_load()
            self._plugins[plugin.name] = PluginEntry(plugin=plugin, enabled=True)
        except Exception as exc:
            self._plugins[plugin.name] = PluginEntry(
                plugin=plugin, enabled=False, error=str(exc))

    def load_directory(self, directory: Path) -> list[str]:
        """
        Scan *directory* for *.py files, import them, and look for a
        class subclassing NexQLPlugin.  Returns list of loaded plugin names.
        """
        if not directory.exists():
            return []
        loaded = []
        for py_file in sorted(directory.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                spec   = importlib.util.spec_from_file_location(py_file.stem, py_file)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, NexQLPlugin)
                            and attr is not NexQLPlugin):
                        instance = attr()
                        self.register_builtin(instance)
                        loaded.append(instance.name)
            except Exception as exc:
                print(f"[plugin-loader] Failed to load {py_file.name}: {exc}", flush=True)
        return loaded

    def unregister(self, name: str) -> bool:
        entry = self._plugins.pop(name, None)
        if entry:
            try:
                entry.plugin.on_unload()
            except Exception:
                pass
            return True
        return False

    # ── Accessors ─────────────────────────────────────────────────────────────

    def build_runtime(self) -> PluginRuntime:
        return self._runtime

    def list_plugins(self) -> list[dict]:
        return [
            {
                "name":        e.plugin.name,
                "version":     e.plugin.version,
                "description": e.plugin.description,
                "enabled":     e.enabled,
                "error":       e.error,
            }
            for e in self._plugins.values()
        ]

    def get_plugin(self, name: str) -> Optional[NexQLPlugin]:
        entry = self._plugins.get(name)
        return entry.plugin if entry and entry.enabled else None


# ─── Built-in example plugins ─────────────────────────────────────────────────

class RateLimitPlugin(NexQLPlugin):
    """Built-in @ratelimit directive handler."""

    @property
    def name(self) -> str:
        return "rate_limit"

    @property
    def description(self) -> str:
        return "Enforces @rate directive on subscription queries"

    def register(self, runtime: PluginRuntime) -> None:
        runtime.register_directive("rate", self._handle_rate)

    @staticmethod
    def _handle_rate(field_meta: dict, context: dict) -> bool:
        """Return True if the field should be included at this moment."""
        return True   # production: check rolling window


class MsgPackPlugin(NexQLPlugin):
    """Built-in msgpack serializer (if msgpack is installed)."""

    @property
    def name(self) -> str:
        return "msgpack_serializer"

    @property
    def description(self) -> str:
        return "Adds msgpack binary serialisation format"

    def register(self, runtime: PluginRuntime) -> None:
        # Prefer dynamic import so msgpack remains an optional dependency.
        logger = logging.getLogger(__name__)
        spec = importlib.util.find_spec("msgpack")
        if spec is None:
            logger.debug("msgpack not available; skipping msgpack serializer registration")
            return
        try:
            msgpack = importlib.import_module("msgpack")
            runtime.register_serializer("msgpack", lambda obj: msgpack.packb(obj, use_bin_type=True))
        except Exception as exc:
            # If msgpack exists but fails at import or registration, log and skip.
            logger.debug("Failed to initialize msgpack serializer: %s", exc)


# ─── Default global loader ────────────────────────────────────────────────────

_DEFAULT_LOADER: Optional[PluginLoader] = None


def get_default_loader() -> PluginLoader:
    global _DEFAULT_LOADER
    if _DEFAULT_LOADER is None:
        _DEFAULT_LOADER = PluginLoader()
        _DEFAULT_LOADER.register_builtin(RateLimitPlugin())
        _DEFAULT_LOADER.register_builtin(MsgPackPlugin())
    return _DEFAULT_LOADER
