#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
VIEWER_DIR="${WORKSPACE_ROOT}/lerobot-dataset-visualizer"
BUN_BIN="${HOME}/.bun/bin/bun"
BUILD_ID_PATH="${VIEWER_DIR}/.next/BUILD_ID"
BACKEND_VENV="${VIEWER_DIR}/.venv"
BACKEND_PYTHON="${BACKEND_VENV}/bin/python"
PYTHON_BIN="/usr/bin/python3"

if [[ ! -d "${VIEWER_DIR}/.git" ]]; then
  echo "Missing viewer checkout at ${VIEWER_DIR}" >&2
  echo "Clone https://github.com/RPM-lab-UMN/lerobot-dataset-visualizer.git into the shared workspace first." >&2
  exit 1
fi

if [[ ! -f "${VIEWER_DIR}/src/utils/videoClient.ts" || ! -f "${VIEWER_DIR}/backend/app.py" ]]; then
  echo "Viewer checkout does not contain the required native-depth adapter." >&2
  echo "Update the lab viewer checkout, then rerun this script." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Missing node. Install it first, for example:" >&2
  echo "  sudo apt-get install -y nodejs npm" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing npm. Install it first, for example:" >&2
  echo "  sudo apt-get install -y nodejs npm" >&2
  exit 1
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing ${PYTHON_BIN}." >&2
  exit 1
fi

if [[ ! -x "${BUN_BIN}" ]]; then
  if ! command -v curl >/dev/null 2>&1; then
    echo "Missing curl, and bun is not installed at ${BUN_BIN}" >&2
    exit 1
  fi
  echo "Installing bun under ${HOME}/.bun ..."
  curl -fsSL https://bun.sh/install | bash
fi

if [[ ! -x "${BUN_BIN}" ]]; then
  echo "bun installation did not produce ${BUN_BIN}" >&2
  exit 1
fi

echo "Preparing viewer checkout at ${VIEWER_DIR}"
(
  cd "${VIEWER_DIR}"
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
    "${BUN_BIN}" install --frozen-lockfile
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy \
    "${BUN_BIN}" run build
)

echo "Preparing native-depth video adapter"
"${PYTHON_BIN}" -m venv "${BACKEND_VENV}"
"${BACKEND_PYTHON}" -m pip install --upgrade pip
"${BACKEND_PYTHON}" -m pip install -r "${VIEWER_DIR}/backend/requirements.txt"

if [[ ! -f "${BUILD_ID_PATH}" ]]; then
  echo "Viewer build completed but ${BUILD_ID_PATH} is missing." >&2
  exit 1
fi

echo "Viewer environment ready."
echo "Viewer repo: ${VIEWER_DIR}"
echo "bun: ${BUN_BIN}"
echo "build marker: ${BUILD_ID_PATH}"
echo "video adapter python: ${BACKEND_PYTHON}"
echo
echo "You can now use Open Viewer from the operator console, or launch it manually with:"
echo "  cd ${VIEWER_DIR}"
echo "  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy ${BUN_BIN} start"
