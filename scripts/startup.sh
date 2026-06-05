#!/bin/bash
echo "[$(date)] Running startup tasks..."

# echo "Current user: $(whoami)"
# echo "$CONFIG_DIR"

# Используем переменные окружения, переданные в контейнер
RESUME_ID=${RESUME_ID}
SEARCH_QUERY=${SEARCH_QUERY}

# Выполняем цепочку
/usr/local/bin/python -u -m hh_applicant_tool refresh-token
/usr/local/bin/python -u -m hh_applicant_tool update-resumes --id "$RESUME_ID"
/usr/local/bin/python -u -m hh_applicant_tool apply-vacancies --resume-id "$RESUME_ID" --area 113 --search "$SEARCH_QUERY" -f --ai --ai-filter heavy

echo "[$(date)] Startup tasks finished."
