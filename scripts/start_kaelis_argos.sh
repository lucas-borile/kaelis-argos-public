#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-8080}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
FRONTEND_URL="http://${FRONTEND_HOST}:${FRONTEND_PORT}"
LOG_DIR="${ROOT_DIR}/logs/runtime"
BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"

mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"

if [[ -x "${ROOT_DIR}/.venv312/bin/python" ]]; then
  PYTHON="${ROOT_DIR}/.venv312/bin/python"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON="${ROOT_DIR}/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Python nao encontrado. Instale Python 3.12 ou crie .venv312."
  exit 1
fi

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo
  echo "Encerrando Kaelis Argos..."
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" >/dev/null 2>&1; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" >/dev/null 2>&1; then
    kill "${FRONTEND_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

port_is_open() {
  local port="$1"
  lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempt

  for attempt in {1..40}; do
    if /usr/bin/curl -fsS "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done

  echo "${label} nao respondeu em ${url}."
  return 1
}

echo "Kaelis Argos"
echo "Projeto: ${ROOT_DIR}"
echo "Python: ${PYTHON}"
echo

if ! "${PYTHON}" - <<'PY' >/dev/null 2>&1
import fastapi, uvicorn
PY
then
  echo "Dependencias do backend nao parecem instaladas neste Python."
  echo "Execute:"
  echo "  ${PYTHON} -m pip install -r backend/requirements.txt"
  exit 1
fi

if port_is_open "${BACKEND_PORT}"; then
  echo "Backend: porta ${BACKEND_PORT} ja esta em uso. Vou reutilizar o servico existente."
else
  echo "Iniciando backend em ${BACKEND_URL}..."
  (
    cd "${ROOT_DIR}/backend"
    "${PYTHON}" -m uvicorn main:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
  ) >"${BACKEND_LOG}" 2>&1 &
  BACKEND_PID="$!"
fi

if port_is_open "${FRONTEND_PORT}"; then
  echo "Frontend: porta ${FRONTEND_PORT} ja esta em uso. Vou reutilizar o servico existente."
else
  echo "Iniciando frontend em ${FRONTEND_URL}..."
  (
    cd "${ROOT_DIR}/frontend"
    "${PYTHON}" -m http.server "${FRONTEND_PORT}" --bind "${FRONTEND_HOST}"
  ) >"${FRONTEND_LOG}" 2>&1 &
  FRONTEND_PID="$!"
fi

wait_for_url "${BACKEND_URL}/health" "Backend" || {
  echo
  echo "Ultimas linhas do log do backend:"
  tail -n 80 "${BACKEND_LOG}" || true
  exit 1
}

wait_for_url "${FRONTEND_URL}" "Frontend" || {
  echo
  echo "Ultimas linhas do log do frontend:"
  tail -n 80 "${FRONTEND_LOG}" || true
  exit 1
}

echo
echo "Kaelis Argos esta rodando:"
echo "  Frontend: ${FRONTEND_URL}"
echo "  Backend:  ${BACKEND_URL}"
echo
echo "Logs:"
echo "  ${BACKEND_LOG}"
echo "  ${FRONTEND_LOG}"
echo
echo "Pressione Ctrl+C nesta janela para encerrar os servicos iniciados por este lancador."

if command -v open >/dev/null 2>&1; then
  open "${FRONTEND_URL}" >/dev/null 2>&1 || true
fi

while true; do
  sleep 3600
done
