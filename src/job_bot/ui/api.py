"""The :class:`Api` JS-bridge for the ``job_bot.ui`` slice (VSA — Issue #150).

This is the slimmed-down replacement for the 673-LOC legacy
``hh_applicant_tool.ui.api.Api`` (a pywebview ``js_api`` class that
exposed ~30 methods to the embedded webview).  The public surface is
**byte-for-byte identical** — the same method names, the same return
shapes — so the existing ``js/app.js`` code keeps working without
changes.  The only structural difference is the constructor: the new
:class:`Api` takes a :class:`UiApiContext` instead of a
``HHApplicantTool``.

Each of the 19 public methods is a 1-3 line dispatch into the
context's port.  The :class:`_ProgressHandler` logging bridge stays
here because it references the :class:`Api` instance; pure helpers
(``_mask_secrets``, ``_build_command_from_params``, the coerce
functions) live in :mod:`job_bot.ui._helpers`.
"""

from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import threading
from contextlib import redirect_stdout
from typing import Any

from ._helpers import (
    MASK_VALUE,
    MASKED_KEYS,
    _build_command_from_params,
    _mask_secrets,
    _merge_config,
    _strip_masked,
)
from .ports import UiApiContext
from .presets import PresetValidationError

logger = logging.getLogger(__package__)


class _ProgressHandler(logging.Handler):
    """Logging handler that pushes records to the webview via the API.

    Holds a reference to the :class:`Api` and calls
    ``self._api._send_progress`` for each emitted record.  The new
    :meth:`Api._send_progress` is itself a 1-line dispatch into the
    context's ``progress_sink``, so the logging → JS round-trip is
    preserved across the refactor.
    """

    def __init__(self, api: "Api") -> None:
        super().__init__(logging.INFO)
        self._api = api
        self._count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._count += 1
            self._api._send_progress(self._count, 0, msg)
        except Exception:  # noqa: BLE001  # logging emit — must not raise
            pass


class Api:
    """pywebview ``js_api`` bridge, decoupled from ``HHApplicantTool``.

    Each public method is a thin dispatch into the
    :class:`UiApiContext`.  The dispatch is intentionally 1-3 lines
    so the file stays under the 500-LOC budget set by issue #150.

    Three state bits live on the instance (not the context) because
    they describe a *session*, not a *port*:

    * ``self._auth_running`` — set by :meth:`start_login` while the
      Playwright auth worker thread is in flight.
    * ``self._cancel_event`` — set by :meth:`apply_vacancies` to
      signal the use case to stop.
    * ``self._is_running`` / ``self._apply_lock`` — guard against
      re-entrant :meth:`apply_vacancies` calls (the UI shouldn't
      let that happen, but a defensive check costs nothing).
    """

    def __init__(self, ctx: UiApiContext) -> None:
        self._ctx = ctx
        self._cancel_event: threading.Event | None = None
        self._is_running: bool = False
        self._auth_running: bool = False
        self._auth_thread: threading.Thread | None = None
        self._apply_lock = threading.Lock()

    # ─── context surface (for tests / introspection) ─────────────

    @property
    def context(self) -> UiApiContext:
        """Expose the :class:`UiApiContext` for tests and introspection."""
        return self._ctx

    # ─── internal sinks → context dispatch ───────────────────────

    def _send_progress(
        self, current: int, total: int, message: str = ""
    ) -> None:
        self._ctx.progress_sink(current, total, message)

    def _send_auth_event(self, event: str, message: str = "") -> None:
        self._ctx.auth_event_sink(event, message)

    # ─── helpers (kept here — pure logic, no service-locator) ────

    @staticmethod
    def _is_invalid_grant(exc: BaseException) -> bool:
        msg = str(exc).lower()
        return (
            "invalid_grant" in msg or "token has already been refreshed" in msg
        )

    def _clear_token(self) -> None:
        self._ctx.clear_token()

    # ─── public methods (1-3 line dispatches) ────────────────────

    def get_status(self) -> dict[str, Any]:
        if self._auth_running:
            return {"authorized": False, "user": None, "auth_running": True}
        client = self._ctx.api_client
        if not client.access_token and not client.refresh_token:
            return {"authorized": False, "user": None, "reason": "no_token"}
        try:
            user = self._ctx.get_me()
            return {"authorized": True, "user": user}
        except Exception as e:  # noqa: BLE001  # UI method — tests force generic Exception
            logger.warning("get_status error: %s", e)
            reason = "error"
            if self._is_invalid_grant(e):
                self._clear_token()
                reason = "token_invalid"
            return {
                "authorized": False,
                "user": None,
                "reason": reason,
                "error": str(e),
            }

    def start_login(self) -> dict[str, Any]:
        if self._auth_running:
            return {"status": "error", "message": "Авторизация уже выполняется"}
        try:
            import playwright  # noqa: F401
        except ImportError:
            return {
                "status": "error",
                "message": (
                    "Не установлен Playwright. Выполните в терминале:\n\n"
                    "  pip install 'hh-applicant-tool[playwright]'\n"
                    "  playwright install chromium"
                ),
            }

        self._auth_running = True
        self._clear_token()
        self._send_auth_event(
            "started", "Запуск браузера для входа на hh.ru..."
        )

        def _worker() -> None:
            event = "error"
            message = "Ошибка авторизации"
            try:
                from hh_applicant_tool.operations.authorize import (
                    Operation as AuthOp,
                )

                op = AuthOp()
                parser = argparse.ArgumentParser()
                op.setup_parser(parser)
                args = parser.parse_args(["--no-headless", "--manual"])
                op.run(self._ctx.api_client, args)  # type: ignore[arg-type]

                if self._ctx.api_client.access_token:
                    try:
                        token = self._ctx.api_client.get_access_token()  # type: ignore[attr-defined]
                        self._ctx.config.save_token(token)
                    except AttributeError:
                        pass
                    event = "done"
                    message = "Авторизация прошла успешно"
                else:
                    message = (
                        "Авторизация не завершена. Окно браузера была закрыто."
                    )
            except Exception as e:  # noqa: BLE001  # auth worker thread
                logger.error("start_login worker error: %s", e)
                detail = str(e) or e.__class__.__name__
                message = f"Ошибка авторизации: {detail}"
            finally:
                self._auth_running = False
                self._send_auth_event(event, message)

        thread = threading.Thread(target=_worker, daemon=True)
        self._auth_thread = thread
        thread.start()
        return {"status": "started"}

    def logout(self) -> dict[str, Any]:
        self._clear_token()
        return {"status": "ok"}

    def get_resumes(self) -> list[dict]:
        client = self._ctx.api_client
        if not client.access_token and not client.refresh_token:
            return []
        try:
            return self._ctx.get_resumes()
        except Exception as e:  # noqa: BLE001  # UI method
            if self._is_invalid_grant(e):
                self._clear_token()
                logger.warning("get_resumes invalid_grant: cleared token")
            else:
                logger.error("get_resumes error: %s", e)
            return []

    def get_config(self) -> dict[str, Any]:
        return _mask_secrets(dict(self._ctx.config))

    def save_config(self, updates: dict[str, Any]) -> dict[str, str]:
        try:
            clean = {
                k: _strip_masked(v)
                for k, v in updates.items()
                if k not in MASKED_KEYS and v != MASK_VALUE
            }
            merged = {
                key: _merge_config(self._ctx.config.get(key), value)
                for key, value in clean.items()
            }
            self._ctx.config.save(**merged)
            return {"status": "ok"}
        except (OSError, ValueError, TypeError) as e:
            logger.error("save_config error: %s", e)
            return {
                "status": "error",
                "message": "Ошибка сохранения конфигурации",
            }

    def list_presets(self) -> list[str]:
        return self._ctx.presets.list_names()

    def save_preset(self, name: str, params: dict[str, Any]) -> dict[str, str]:
        try:
            self._ctx.presets.save(name, params)
            return {"status": "ok"}
        except PresetValidationError as e:
            return {"status": "error", "message": str(e)}
        except (OSError, ValueError, TypeError, sqlite3.Error) as e:
            logger.error("save_preset error: %s", e)
            return {"status": "error", "message": "Ошибка сохранения пресета"}

    def load_preset(self, name: str) -> dict[str, Any] | None:
        return self._ctx.presets.load(name)

    def delete_preset(self, name: str) -> None:
        self._ctx.presets.delete(name)

    def get_last_used_params(self) -> dict[str, Any] | None:
        return self._ctx.presets.load_last_used()

    def save_last_used_params(self, params: dict[str, Any]) -> None:
        try:
            self._ctx.presets.save_last_used(params)
        except PresetValidationError as e:
            logger.warning("save_last_used_params rejected: %s", e)
        except (OSError, ValueError, TypeError, sqlite3.Error) as e:
            logger.error("save_last_used_params error: %s", e)

    def get_negotiations_from_db(self) -> list[dict]:
        try:
            conn = self._ctx.storage.negotiations.conn
            cur = conn.execute(
                """
                SELECT n.id, n.state, n.vacancy_id, n.employer_id,
                       n.created_at,
                       v.name AS vacancy_name,
                       v.alternate_url AS vacancy_url,
                       e.name AS employer_name
                FROM negotiations n
                LEFT JOIN vacancies v ON v.id = n.vacancy_id
                LEFT JOIN employers e ON e.id = n.employer_id
                ORDER BY n.created_at DESC
                LIMIT 500
                """
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        except sqlite3.Error as e:
            logger.error("get_negotiations_from_db error: %s", e)
            return []

    def refresh_negotiations(self, status: str = "active") -> dict:
        try:
            from hh_applicant_tool.storage.models.negotiation import (
                NegotiationModel,
            )

            count = 0
            for item in self._ctx.get_negotiations(status):
                model = NegotiationModel.from_dict(item)
                self._ctx.storage.negotiations.save(model)
                count += 1
            return {"status": "ok", "count": count}
        except Exception as e:  # noqa: BLE001  # composes HH API + DB + model conversion
            logger.error("refresh_negotiations error: %s", e)
            return {
                "status": "error",
                "message": "Ошибка синхронизации откликов",
            }

    def get_statistics(self) -> dict:
        try:
            conn = self._ctx.storage.negotiations.conn
            stats: dict[str, Any] = {}

            cur = conn.execute(
                "SELECT state, count(*) FROM negotiations GROUP BY state"
            )
            stats["by_state"] = dict(cur.fetchall())

            cur = conn.execute(
                "SELECT reason, count(*) FROM skipped_vacancies GROUP BY reason"
            )
            stats["skipped_by_reason"] = dict(cur.fetchall())

            cur = conn.execute(
                "SELECT date(created_at) AS day, count(*)"
                " FROM negotiations"
                " WHERE created_at >= date('now', '-30 days')"
                " GROUP BY day ORDER BY day"
            )
            stats["daily_negotiations"] = dict(cur.fetchall())

            cur = conn.execute(
                "SELECT date(created_at) AS day, count(*)"
                " FROM skipped_vacancies"
                " WHERE created_at >= date('now', '-30 days')"
                " GROUP BY day ORDER BY day"
            )
            stats["daily_skipped"] = dict(cur.fetchall())

            stats["total_negotiations"] = sum(stats["by_state"].values())
            stats["total_skipped"] = sum(stats["skipped_by_reason"].values())

            return stats
        except sqlite3.Error as e:
            logger.error("get_statistics error: %s", e)
            return {}

    def apply_vacancies(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._apply_lock:
            if self._is_running:
                return {
                    "status": "error",
                    "message": "Операция уже выполняется",
                }
            self._is_running = True

            cancel_event = threading.Event()
            self._cancel_event = cancel_event

        self._ctx.presets.save_last_used(params)

        handler = _ProgressHandler(self)
        pkg_logger = logging.getLogger("hh_applicant_tool")
        pkg_logger.addHandler(handler)

        class _PrintCapture(io.StringIO):
            def write(self_inner, s: str) -> int:
                s = s.rstrip("\n")
                if s:
                    handler._count += 1
                    self._send_progress(handler._count, 0, s)
                return len(s)

        try:
            params = dict(params)
            api_delay = params.pop("api_delay", None)
            if api_delay is not None:
                try:
                    self._ctx.api_client.delay = float(api_delay)
                except (ValueError, TypeError):
                    pass

            command = _build_command_from_params(params)

            use_case = self._ctx.apply_use_case_factory(
                system_prompt=command.system_prompt,
                use_ai=command.use_ai,
                send_email=command.send_email,
            )

            with redirect_stdout(_PrintCapture()):
                use_case.execute(command, cancel_event=cancel_event)

            if cancel_event.is_set():
                return {"status": "cancelled"}
            return {"status": "ok"}
        except Exception as e:  # noqa: BLE001  # top-level use-case execution
            logger.error("apply_vacancies error: %s", e)
            return {
                "status": "error",
                "message": "Ошибка выполнения операции",
            }
        finally:
            pkg_logger.removeHandler(handler)
            with self._apply_lock:
                self._cancel_event = None
                self._is_running = False

    def cancel_apply(self) -> None:
        with self._apply_lock:
            event = self._cancel_event
            if event is not None:
                event.set()

    def get_areas(self) -> list[dict]:
        try:

            def flatten(nodes: list, result: list, depth: int = 0) -> None:
                for node in nodes:
                    result.append(
                        {
                            "id": node["id"],
                            "name": ("  " * depth) + node["name"],
                        }
                    )
                    if node.get("areas"):
                        flatten(node["areas"], result, depth + 1)

            data = self._ctx.api_client.get("/areas")
            result: list[dict] = []
            flatten(data, result)
            return result
        except Exception as e:  # noqa: BLE001  # network/parsing — best-effort
            logger.error("get_areas error: %s", e)
            return []

    def get_professional_roles(self) -> list[dict]:
        try:
            data = self._ctx.api_client.get("/professional_roles")
            result = []
            for cat in data.get("categories", []):
                for role in cat.get("roles", []):
                    result.append({"id": role["id"], "name": role["name"]})
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("get_professional_roles error: %s", e)
            return []

    def get_industries(self) -> list[dict]:
        try:
            data = self._ctx.api_client.get("/industries")
            result = []
            for item in data:
                result.append({"id": item["id"], "name": item["name"]})
                for sub in item.get("industries", []):
                    result.append({"id": sub["id"], "name": "  " + sub["name"]})
            return result
        except Exception as e:  # noqa: BLE001
            logger.error("get_industries error: %s", e)
            return []


__all__ = ["Api"]
