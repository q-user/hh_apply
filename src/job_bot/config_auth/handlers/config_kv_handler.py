"""Dotted-path KV helpers for the :class:`AppConfig` JSON file (issue #147).

The legacy ``hh_applicant_tool.operations.config`` op kept three
free functions (``get_value``, ``set_value``, ``del_value``) for
manipulating the on-disk config with dotted paths like
``"telegram.bot_token"``. This handler centralises them so other slices
(``cli.config``, the upcoming ``web UI``, etc.) can share the same
helpers — and so the CLI op can be tested against a stable contract
without depending on a real :class:`AppConfig` instance.

The handler is stateless: every helper takes a ``data: dict`` so the
caller owns the mutation site. Use :class:`ConfigKVHandler` directly —
it has no constructor arguments, but the class shape is kept so the
dispatcher can inject a future per-profile variant.

Also includes a :meth:`parse_scalar` helper that converts
``"true"`` / ``"false"`` / numeric strings into their native types.
"""

from __future__ import annotations

from typing import Any


class ConfigKVHandler:
    """Stateless dotted-path KV helpers for an :class:`AppConfig` dict."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @staticmethod
    def get_value(data: dict[str, Any], path: str) -> Any:
        """Return the value at ``path`` in ``data``, or ``None``.

        Example: ``get_value({"a": {"b": 1}}, "a.b") == 1``.
        Returns ``None`` if any segment is missing.
        """
        node: Any = data
        for key in path.split("."):
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @staticmethod
    def set_value(data: dict[str, Any], path: str, value: Any) -> None:
        """Set ``data[path] = value``, creating intermediate dicts.

        Example: ``set_value(data, "a.b.c", 1)`` creates
        ``{"a": {"b": {"c": 1}}}``.
        """
        keys = path.split(".")
        for key in keys[:-1]:
            data = data.setdefault(key, {})
        data[keys[-1]] = value

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    @staticmethod
    def del_value(data: dict[str, Any], path: str) -> bool:
        """Delete ``data[path]``.

        Returns ``True`` if a key was removed, ``False`` otherwise.
        """
        keys = path.split(".")
        for key in keys[:-1]:
            if not isinstance(data, dict) or key not in data:
                return False
            data = data[key]

        try:
            del data[keys[-1]]
        except KeyError:
            return False
        return True

    # ------------------------------------------------------------------
    # Scalar coercion
    # ------------------------------------------------------------------

    @staticmethod
    def parse_scalar(value: str) -> Any:
        """Parse a CLI string into a Python scalar.

        * ``"true"`` / ``"false"`` → ``bool``
        * numeric strings → ``int`` or ``float`` (float when there's a dot)
        * ``"null"`` → ``None``
        * anything else → the original ``str``
        """
        if value == "null":
            return None
        if value in ("true", "false"):
            return "t" in value
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value


__all__ = ["ConfigKVHandler"]
