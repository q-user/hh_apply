PRAGMA foreign_keys = OFF;
-- На всякий случай выключаем проверки
BEGIN;
-- ===================== application_drafts =====================
-- Подготовленные черновики откликов: один на пару (resume_id, vacancy_id).
-- Разделяет фазы подготовки (prepare-vacancies) и отправки (apply-worker).
CREATE TABLE IF NOT EXISTS application_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_profile_id TEXT,
    resume_id TEXT NOT NULL,
    vacancy_id INTEGER NOT NULL,
    employer_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new',
    relevance_score INTEGER,
    success_probability INTEGER,
    relevance_reason TEXT,
    analysis_json TEXT,
    full_vacancy_json TEXT,
    cover_letter TEXT,
    cover_letter_status TEXT,
    has_test BOOLEAN DEFAULT 0,
    test_status TEXT,
    hh_response_url TEXT,
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (resume_id, vacancy_id)
);
-- ===================== application_test_answers =====================
-- Сгенерированные/отредактированные ответы на тесты HH, привязанные к черновику.
CREATE TABLE IF NOT EXISTS application_test_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    question TEXT,
    answer_type TEXT,
    options_json TEXT,
    generated_answer TEXT,
    selected_solution_id TEXT,
    review_status TEXT DEFAULT 'generated',
    reviewer_comment TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (draft_id, task_id)
);
-- ===================== apply_jobs =====================
-- Очередь асинхронной отправки откликов. Один job на черновик (UNIQUE draft_id).
CREATE TABLE IF NOT EXISTS apply_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    next_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    locked_at DATETIME,
    locked_by TEXT,
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    chat_id INTEGER,
    UNIQUE (draft_id)
);
-- ===================== telegram_sessions =====================
-- Состояние FSM интерактивного ревью в Telegram: по одной записи на chat_id.
CREATE TABLE IF NOT EXISTS telegram_sessions (
    chat_id INTEGER PRIMARY KEY,
    user_id INTEGER,
    state TEXT NOT NULL DEFAULT 'idle',
    draft_id INTEGER,
    current_test_answer_id INTEGER,
    payload_json TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ===================== employers =====================
CREATE TABLE IF NOT EXISTS employers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT,
    description TEXT,
    site_url TEXT,
    area_id INTEGER,
    area_name TEXT,
    alternate_url TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ===================== contacts =====================
CREATE TABLE IF NOT EXISTS vacancy_contacts (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))) NOT NULL,
    vacancy_id INTEGER NOT NULL,
    -- Все это избыточные поля
    vacancy_alternate_url TEXT,
    vacancy_name TEXT,
    vacancy_area_id INTEGER,
    vacancy_area_name TEXT,
    vacancy_salary_from INTEGER,
    vacancy_salary_to INTEGER,
    vacancy_currency VARCHAR(3),
    vacancy_gross BOOLEAN,
    --
    employer_id INTEGER,
    employer_name TEXT,
    --
    name TEXT,
    email TEXT,
    phone_numbers TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (vacancy_id, email)
);
-- ===================== vacancies =====================
CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    area_id INTEGER,
    area_name TEXT,
    salary_from INTEGER,
    salary_to INTEGER,
    currency VARCHAR(3),
    gross BOOLEAN,
    published_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    remote BOOLEAN,
    experience TEXT,
    professional_roles TEXT,
    alternate_url TEXT
);
-- ===================== negotiations =====================
CREATE TABLE IF NOT EXISTS negotiations (
    id INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    vacancy_id INTEGER NOT NULL,
    employer_id INTEGER,
    -- Может обнулиться при блокировке раб-о-тодателя
    chat_id INTEGER NOT NULL,
    resume_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ===================== settings =====================
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- ===================== resumes =====================
CREATE TABLE IF NOT EXISTS resumes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT,
    alternate_url TEXT,
    status_id TEXT,
    status_name TEXT,
    can_publish_or_update BOOLEAN,
    total_views INTEGER DEFAULT 0,
    new_views INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ===================== search_profiles =====================
-- Сохранённый профиль поиска вакансий. Определяет, какие вакансии искать,
-- какое резюме использовать, какие правила релевантности и AI-фильтрации
-- применять. Источник истины для prepare-vacancies (issue #5) и
-- опционального --search-profile флага в apply-vacancies.
CREATE TABLE IF NOT EXISTS search_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resume_id TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    search_params TEXT,
    relevance_rules TEXT,
    ai_filter_mode TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- ===================== ИНДЕКСЫ ДЛЯ СТАТИСТИКИ =====================
-- Чтобы выборка для отправки на сервер по updated_at не тормозила
CREATE INDEX IF NOT EXISTS idx_vac_upd ON vacancies(updated_at);
CREATE INDEX IF NOT EXISTS idx_emp_upd ON employers(updated_at);
CREATE INDEX IF NOT EXISTS idx_neg_upd ON negotiations(updated_at);
-- ===================== ТРИГГЕРЫ (Всегда обновляют дату) =====================
-- Убрал условие WHEN. Теперь при любом UPDATE дата актуализируется принудительно.
CREATE TRIGGER IF NOT EXISTS trg_resumes_updated
AFTER
UPDATE ON resumes BEGIN
UPDATE resumes
SET updated_at = CURRENT_TIMESTAMP
WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_employers_updated
AFTER
UPDATE ON employers BEGIN
UPDATE employers
SET updated_at = CURRENT_TIMESTAMP
WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_vacancy_contacts_updated
AFTER
UPDATE ON vacancy_contacts BEGIN
UPDATE vacancy_contacts
SET updated_at = CURRENT_TIMESTAMP
WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_vacancies_updated
AFTER
UPDATE ON vacancies BEGIN
UPDATE vacancies
SET updated_at = CURRENT_TIMESTAMP
WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_negotiations_updated
AFTER
UPDATE ON negotiations BEGIN
UPDATE negotiations
SET updated_at = CURRENT_TIMESTAMP
WHERE id = OLD.id;
END;
-- ===================== employer_sites =====================
CREATE TABLE IF NOT EXISTS employer_sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employer_id INTEGER NOT NULL,
    site_url TEXT NOT NULL,
    ip_address TEXT,
    title TEXT,
    description TEXT,
    generator TEXT,
    server_name TEXT,
    powered_by TEXT,
    emails TEXT,
    subdomains TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- Уникальность пары: один работодатель — один конкретный сайт
    UNIQUE (employer_id, site_url)
);

-- ===================== skipped_vacancies =====================
CREATE TABLE IF NOT EXISTS skipped_vacancies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resume_id TEXT NOT NULL DEFAULT '',
    vacancy_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    alternate_url TEXT,
    name TEXT,
    employer_name TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (resume_id, vacancy_id)
);
-- ===================== ИНДЕКСЫ =====================
CREATE INDEX IF NOT EXISTS idx_emp_site_upd ON employer_sites(updated_at);
CREATE INDEX IF NOT EXISTS idx_skipped_vac_resume ON skipped_vacancies(resume_id, vacancy_id);

-- Быстрый поиск черновиков по статусу (для prepare-vacancies / digest)
CREATE INDEX IF NOT EXISTS idx_app_drafts_status ON application_drafts(status);
-- Фильтрация дайджеста по профилю
CREATE INDEX IF NOT EXISTS idx_app_drafts_profile ON application_drafts(search_profile_id);
-- Ответы тестов по черновику (обход в порядке создания)
CREATE INDEX IF NOT EXISTS idx_app_test_answers_draft ON application_test_answers(draft_id);
-- Очередь задач воркера: claimed by (status, next_attempt_at)
CREATE INDEX IF NOT EXISTS idx_apply_jobs_queue ON apply_jobs(status, next_attempt_at);
-- Поиск активных профилей для prepare-vacancies
CREATE INDEX IF NOT EXISTS idx_search_profiles_enabled ON search_profiles(enabled);

-- ===================== ТРИГГЕРЫ =====================
CREATE TRIGGER IF NOT EXISTS trg_employer_sites_updated
AFTER UPDATE ON employer_sites
BEGIN
    UPDATE employer_sites
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_application_drafts_updated
AFTER UPDATE ON application_drafts
BEGIN
    UPDATE application_drafts
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_application_test_answers_updated
AFTER UPDATE ON application_test_answers
BEGIN
    UPDATE application_test_answers
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_apply_jobs_updated
AFTER UPDATE ON apply_jobs
BEGIN
    UPDATE apply_jobs
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS trg_telegram_sessions_updated
AFTER UPDATE ON telegram_sessions
BEGIN
    UPDATE telegram_sessions
    SET updated_at = CURRENT_TIMESTAMP
    WHERE chat_id = OLD.chat_id;
END;
CREATE TRIGGER IF NOT EXISTS trg_search_profiles_updated
AFTER UPDATE ON search_profiles
BEGIN
    UPDATE search_profiles
    SET updated_at = CURRENT_TIMESTAMP
    WHERE id = OLD.id;
END;
COMMIT;
