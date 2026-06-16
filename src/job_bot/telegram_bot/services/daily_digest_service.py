"""Ежедневный Telegram-дайджест по подготовленным черновикам (issue #8).

VSA — single source of truth lives here, in the ``job_bot.telegram_bot``
slice. The legacy module :mod:`hh_applicant_tool.services.daily_digest`
is kept as a thin deprecation shim that re-exports the public surface
from this module (issue #54 deprecation contract, see issue #92).

Сервис собирает статистику из ``application_drafts`` (status='prepared'),
группирует её по ``search_profile_id`` (с разбивкой ``has_test`` /
``без тестов``) и отправляет утреннее сообщение через
:class:`TelegramTransport`.

Идемпотентность: после успешной отправки в ``settings`` сохраняется
``telegram.last_digest_date`` (ISO-формат ``YYYY-MM-DD``). Повторный вызов
в тот же календарный день — no-op (если не передан ``force=True``).

Зависимости передаются через конструктор (DI):
- ``storage`` — :class:`StorageFacade` для чтения черновиков и профилей
  и записи idempotency-флага в ``settings``;
- ``transport`` — :class:`TelegramTransport` для отправки сообщения
  (мокируется в тестах);
- ``config`` — словарь с секцией ``telegram`` (для резолва ``chat_id``);
- ``clock`` — порт :class:`Clock` для детерминированного времени
  (по умолчанию — :class:`SystemClock` → ``datetime.now()``);
- ``ai_client`` — опциональный :class:`AIClientPort` для короткой
  AI-аннотации к дайджесту.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from hh_applicant_tool.ai.base import AIError
from hh_applicant_tool.storage.facade import StorageFacade
from job_bot.telegram_bot.telegram_transport import (
    TelegramTransport,
    TelegramTransportError,
)

if TYPE_CHECKING:
    from hh_applicant_tool.application.ports import AIClientPort, Clock

logger = logging.getLogger(__package__)

# Ключ idempotency-флага в таблице ``settings``.
LAST_DIGEST_KEY = "telegram.last_digest_date"

# Ключи конфига для приёма chat_id (от самого явного к самому мягкому).
_DIGEST_CHAT_ID_KEYS = ("digest_chat_id", "chat_id")


# ─── DTO ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DraftGroup:
    """Группа черновиков для одного ``search_profile_id``.

    Attributes:
        search_profile_id: id профиля (``None`` — черновики без профиля,
            обычно миграционные данные).
        profile_name: человеко-читаемое имя профиля (из
            ``search_profiles.name``); fallback — id или ``"(без профиля)"``.
        total: сколько всего черновиков в группе.
        with_tests: из них сколько с тестами (``has_test=True``).
        without_tests: из них сколько без тестов.
        average_score: средний ``relevance_score`` (округлённый до int);
            ``None``, если ни у одного черновика в группе нет score.
    """

    search_profile_id: str | None
    profile_name: str
    total: int
    with_tests: int
    without_tests: int
    average_score: int | None


@dataclass(frozen=True)
class DigestResult:
    """Результат :meth:`DailyDigestService.send`.

    Attributes:
        sent: ``True``, если сообщение реально отправлено в Telegram.
        skipped_reason: ``None`` если отправлено; иначе — короткая причина
            (``"already_sent"``, ``"no_chat_id"``, ``"no_telegram_config"``,
            ``"send_failed"``).
        total_drafts: сколько всего ``prepared``-черновиков учтено
            (для всех групп суммарно; ``0`` для пустой БД).
        message: итоговое сообщение, которое было (или не было) отправлено.
            Полезно для логирования и unit-тестов.
    """

    sent: bool
    skipped_reason: str | None = None
    total_drafts: int = 0
    message: str = ""


# ─── Сервис ──────────────────────────────────────────────────────────


class DailyDigestService:
    """Формирует и отправляет ежедневный Telegram-дайджест (issue #8).

    Attributes:
        storage: фасад хранилища для чтения черновиков/профилей и
            сохранения idempotency-флага.
        transport: Telegram-транспорт (обычно уже сконфигурированный
            :class:`TelegramTransport`).
        config: словарь с секцией ``telegram`` (используется для
            ``digest_chat_id`` / ``chat_id`` / ``allowed_user_ids``).
            Если не передан — считаем, что конфиг отсутствует.
        clock: порт времени; если не передан — fallback
            :class:`infrastructure.time.SystemClock` (``datetime.now()``).
        ai_client: опциональный AI-порт для однострочной аннотации.
            При сбое AI дайджест всё равно отправляется без аннотации.
    """

    def __init__(
        self,
        storage: StorageFacade,
        transport: TelegramTransport,
        config: Mapping[str, Any] | None = None,
        *,
        clock: Clock | None = None,
        ai_client: AIClientPort | None = None,
    ):
        self._storage = storage
        self._transport = transport
        # Mapping — принимаем как dict, так и ``Config`` (подкласс dict).
        self._config: Mapping[str, Any] = config if config is not None else {}
        self._clock: Clock = (
            clock if clock is not None else self._default_clock()
        )
        self._ai_client = ai_client

    @staticmethod
    def _default_clock() -> Clock:
        # Ленивый импорт, чтобы не ловить цикл ``services`` → ``infrastructure``
        # → ``application.use_cases`` → ``services`` при загрузке модуля.
        from job_bot.shared.utils.clock import SystemClock

        return SystemClock()

    # ─── Публичные свойства (для тестов и DI-контейнера) ──────────

    @property
    def clock(self) -> Clock:
        return self._clock

    # ─── Чтение данных ────────────────────────────────────────────

    def collect_groups(self) -> list[DraftGroup]:
        """Группирует ``prepared``-черновики по ``search_profile_id``.

        Сортировка: по убыванию ``total`` (самые «жирные» группы сверху),
        затем по ``profile_name`` для стабильного вывода.
        """
        # Один проход агрегации — эффективнее, чем N+1 на каждый профиль.
        # ``AVG`` возвращает float; ``COUNT(has_test=1)`` даёт «с тестами».
        rows = self._storage.application_drafts.conn.execute(
            """
            SELECT
                search_profile_id,
                COUNT(*) AS total,
                SUM(CASE WHEN has_test = 1 THEN 1 ELSE 0 END) AS with_tests,
                SUM(CASE WHEN has_test = 0 OR has_test IS NULL
                         THEN 1 ELSE 0 END) AS without_tests,
                AVG(relevance_score) AS avg_score
            FROM application_drafts
            WHERE status = :status
            GROUP BY search_profile_id
            """,
            {"status": "prepared"},
        ).fetchall()

        # Резолвим имена профилей одним проходом, чтобы не плодить запросы.
        profile_names: dict[str, str] = {
            p.id: p.name for p in self._storage.search_profiles.find()
        }

        groups: list[DraftGroup] = []
        for row in rows:
            profile_id = row["search_profile_id"]
            avg_raw = row["avg_score"]
            avg_score: int | None = (
                int(round(avg_raw)) if avg_raw is not None else None
            )
            if profile_id is None:
                profile_name = "(без профиля)"
            else:
                profile_name = profile_names.get(profile_id, profile_id)
            groups.append(
                DraftGroup(
                    search_profile_id=profile_id,
                    profile_name=profile_name,
                    total=int(row["total"]),
                    with_tests=int(row["with_tests"] or 0),
                    without_tests=int(row["without_tests"] or 0),
                    average_score=avg_score,
                )
            )

        groups.sort(key=lambda g: (-g.total, g.profile_name))
        return groups

    # ─── Форматирование ────────────────────────────────────────────

    @staticmethod
    def format_message(
        groups: list[DraftGroup],
        total: int = 0,
        ai_summary: str | None = None,
    ) -> str:
        """Собирает русскоязычное сообщение для Telegram.

        Args:
            groups: сгруппированные черновики (см. :meth:`collect_groups`).
            total: суммарное количество черновиков (для шапки).
            ai_summary: опциональная строка-аннотация от AI; добавляется
                после шапки отдельной строкой ``💡 …``.
        """
        lines: list[str] = ["Доброе утро! ☀️", ""]
        if not groups:
            lines.append("Сегодня нет подготовленных черновиков к ревью.")
            return "\n".join(lines)

        lines.append(f"Готово к ревью: {total} вакансий")
        if ai_summary:
            lines.append("")
            lines.append(f"💡 {ai_summary.strip()}")
        lines.append("")
        for group in groups:
            lines.append(f"{group.profile_name}:")
            lines.append(
                f"• новых: {group.total} "
                f"(с тестами: {group.with_tests}, "
                f"без: {group.without_tests})"
            )
            if group.average_score is not None:
                lines.append(f"• средний score: {group.average_score}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    # ─── Идемпотентность ───────────────────────────────────────────

    def today(self) -> date:
        """Возвращает текущую календарную дату через :attr:`clock`."""
        return self._clock.now().date()

    def already_sent_today(self, today: date | None = None) -> bool:
        """``True``, если дайджест уже отправлялся в указанный день.

        Args:
            today: проверяемая дата; ``None`` — берём из :attr:`clock`.
        """
        if today is None:
            today = self.today()
        stored = self._storage.settings.get_value(LAST_DIGEST_KEY)
        if not stored:
            return False
        return str(stored) == today.isoformat()

    def _mark_sent_today(self, today: date) -> None:
        """Сохраняет дату последней успешной отправки в ``settings``."""
        self._storage.settings.set_value(LAST_DIGEST_KEY, today.isoformat())

    # ─── Резолв chat_id ────────────────────────────────────────────

    def _telegram_cfg(self) -> Mapping[str, Any] | None:
        cfg = self._config.get("telegram") if self._config else None
        if not isinstance(cfg, Mapping):
            return None
        return cfg

    def _resolve_chat_id(self) -> int | None:
        """Резолвит chat_id для отправки дайджеста.

        Приоритет:
        1. ``telegram.digest_chat_id`` (явная настройка для дайджеста);
        2. ``telegram.chat_id`` (общий chat_id бота);
        3. первый ``telegram.allowed_user_ids`` (владелец бота — он же
           единственный получатель, если нет явного digest-чата).

        Returns:
            ``int`` chat_id или ``None``, если ничего не нашли.
        """
        cfg = self._telegram_cfg()
        if cfg is None:
            return None
        for key in _DIGEST_CHAT_ID_KEYS:
            value = cfg.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                logger.warning(
                    "telegram.%s=%r не похоже на int chat_id", key, value
                )
        for uid in cfg.get("allowed_user_ids") or ():
            try:
                return int(uid)
            except (TypeError, ValueError):
                continue
        return None

    # ─── AI-аннотация (опционально) ────────────────────────────────

    def _generate_ai_summary(
        self, groups: list[DraftGroup], total: int
    ) -> str | None:
        """Возвращает однострочную AI-аннотацию или ``None``.

        Любая ошибка AI логируется и не ломает отправку дайджеста — это
        вспомогательное украшение, а не источник истины.
        """
        if self._ai_client is None or not groups:
            return None
        # Короткий промпт — нас интересует одна короткая фраза по-русски.
        breakdown = ", ".join(
            f"{g.profile_name} {g.total}"
            for g in groups[:5]  # верхние 5 групп, остальное не критично
        )
        prompt = (
            "Ты помощник в Telegram-дайджесте вакансий. "
            "Сформулируй одной короткой фразой (до 100 символов) на русском, "
            "что сегодня интересного в подборке. Без эмодзи, без кавычек.\n"
            f"Всего вакансий: {total}. Топ-профили: {breakdown}."
        )
        try:
            return self._ai_client.complete(prompt).strip() or None
        except AIError as ex:
            # AI-аннотация — вспомогательное украшение дайджеста; сбой LLM
            # не должен ломать отправку самого дайджеста.
            logger.warning("AI-аннотация для дайджеста не удалась: %s", ex)
            return None

    # ─── Главная точка входа ──────────────────────────────────────

    def send(self, force: bool = False) -> DigestResult:
        """Собирает статистику и отправляет дайджест (с учётом идемпотентности).

        Args:
            force: ``True`` — игнорировать same-day idempotency и слать
                повторно. Используется CLI-флагом ``--send-digest-now``
                (issue #8) и ручными триггерами из бота.

        Returns:
            :class:`DigestResult` с информацией о факте и причине отправки.
        """
        today = self.today()

        if self._telegram_cfg() is None:
            logger.info("daily_digest: telegram config отсутствует — skip")
            return DigestResult(
                sent=False,
                skipped_reason="no_telegram_config",
                message="",
            )

        chat_id = self._resolve_chat_id()
        if chat_id is None:
            logger.info(
                "daily_digest: chat_id не задан (digest_chat_id / "
                "chat_id / allowed_user_ids) — skip"
            )
            return DigestResult(
                sent=False,
                skipped_reason="no_chat_id",
                message="",
            )

        if not force and self.already_sent_today(today):
            logger.info(
                "daily_digest: уже отправлено %s — skip (force=%s)",
                today.isoformat(),
                force,
            )
            return DigestResult(
                sent=False,
                skipped_reason="already_sent",
                message="",
            )

        groups = self.collect_groups()
        total = sum(g.total for g in groups)
        ai_summary = self._generate_ai_summary(groups, total)
        message = self.format_message(groups, total, ai_summary=ai_summary)

        try:
            self._transport.send_message(chat_id, message)
        except TelegramTransportError as exc:
            # ВАЖНО: при сбое отправки не помечаем «отправлено» —
            # иначе придётся ждать следующего дня, чтобы повторить.
            logger.error("daily_digest: Telegram send_message упал: %s", exc)
            return DigestResult(
                sent=False,
                skipped_reason="send_failed",
                total_drafts=total,
                message=message,
            )

        # Успех — фиксируем idempotency-флаг.
        self._mark_sent_today(today)
        logger.info(
            "daily_digest: отправлено в chat_id=%s (drafts=%s, groups=%s)",
            chat_id,
            total,
            len(groups),
        )
        return DigestResult(
            sent=True,
            total_drafts=total,
            message=message,
        )


__all__ = (
    "LAST_DIGEST_KEY",
    "DailyDigestService",
    "DigestResult",
    "DraftGroup",
)
