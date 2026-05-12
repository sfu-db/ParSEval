#!/usr/bin/env bash
set -euo pipefail

backend=${1:-sqlite}
mkdir -p test-results
OUT_FILE="test-results/results-${backend}.txt"

echo "Running tests for backend: ${backend}" | tee "$OUT_FILE"

if [ "${backend}" = "sqlite" ]; then
  echo "Using SQLite (no external service)." | tee -a "$OUT_FILE"
  uv run python -m unittest discover -v -s tests 2>&1 | tee -a "$OUT_FILE"
  exit 0
fi

echo "Waiting for DB service to become ready..." | tee -a "$OUT_FILE"
sleep 15

if [ "${backend}" = "postgres" ]; then
  export PARSEVAL_DB_DIALECT=postgres
  export PARSEVAL_DB_HOST=127.0.0.1
  export PARSEVAL_DB_PORT=5432
  export PARSEVAL_DB_USER=${POSTGRES_USER:-postgres}
  export PARSEVAL_DB_PASSWORD=${POSTGRES_PASSWORD:-postgres}
  export PARSEVAL_DB_NAME=${POSTGRES_DB:-parseval_test}
elif [ "${backend}" = "mysql" ]; then
  export PARSEVAL_DB_DIALECT=mysql
  export PARSEVAL_DB_HOST=127.0.0.1
  export PARSEVAL_DB_PORT=3306
  export PARSEVAL_DB_USER=${MYSQL_USER:-mysql}
  export PARSEVAL_DB_PASSWORD=${MYSQL_PASSWORD:-mysql}
  export PARSEVAL_DB_NAME=${MYSQL_DATABASE:-parseval_test}
fi

echo "Environment variables set for ${backend}:"
echo "PARSEVAL_DB_DIALECT=${PARSEVAL_DB_DIALECT}" | tee -a "$OUT_FILE"
echo "PARSEVAL_DB_HOST=${PARSEVAL_DB_HOST}" | tee -a "$OUT_FILE"
echo "PARSEVAL_DB_PORT=${PARSEVAL_DB_PORT}" | tee -a "$OUT_FILE"


uv run python -m unittest discover -v -s tests 2>&1 | tee -a "$OUT_FILE"

echo "Test run completed for ${backend}. Results saved to ${OUT_FILE}." | tee -a "$OUT_FILE"
