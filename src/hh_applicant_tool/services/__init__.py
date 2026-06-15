"""Сервисный слой для подготовки и отправки откликов.

Все legacy-сервисы (issue #54) были удалены в issue #142
(Phase D shim removal). VSA-эквиваленты живут в
``job_bot.application_prep.handlers`` / ``job_bot.vacancy_search.handlers``
и доступны через соответствующие слайсы.

Этот ``__init__.py`` остаётся как маркер пакета, чтобы существующие
импорты ``from hh_applicant_tool import services`` (если такие есть)
не падали. Внутренний ``services/`` пакет сейчас пуст; shim-файлы
удалены в коммите ``refactor(vsa): remove application_prep service
shims + utils.config (Refs #142)``.
"""
