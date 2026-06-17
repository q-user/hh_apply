#!/bin/bash
echo "[$(date)] Running startup tasks..."

# echo "Current user: $(whoami)"
# echo "$CONFIG_DIR"

# Используем переменные окружения, переданные в контейнер
RESUME_ID=${RESUME_ID}

# Выполняем цепочку. В новом многосервисном деплое (issue #11) рассылка
# откликов идёт через hh_apply_worker, а не напрямую: на старте только
# обновляем токен, поднимаем резюме и готовим свежие черновики.
# Отправку одобренных черновиков берёт на себя hh_apply_worker.
HH_PROFILE_ID=${HH_PROFILE_ID:-default}
/usr/local/bin/python -u -m hh_applicant_tool refresh-token
/usr/local/bin/python -u -m hh_applicant_tool update-resumes --id "$RESUME_ID"
/usr/local/bin/python -u -m hh_applicant_tool prepare-vacancies --search-profile "$HH_PROFILE_ID" --ai

echo "[$(date)] Startup tasks finished."
