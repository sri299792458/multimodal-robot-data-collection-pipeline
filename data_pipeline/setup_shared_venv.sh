#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_ROOT="$(cd "${ROOT_DIR}/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
LEROBOT_DIR="${WORKSPACE_ROOT}/lerobot"
LEROBOT_REVISION="7e241bd630a3719a56157a497ce5d08f244784f1"
PYTHON_BIN="/usr/bin/python3"
BOOTSTRAP_PYTHON="${PYTHON:-python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Missing ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -d "${LEROBOT_DIR}/.git" ]]; then
  echo "Missing lerobot checkout at ${LEROBOT_DIR}" >&2
  exit 1
fi

actual_lerobot_revision="$(git -C "${LEROBOT_DIR}" rev-parse HEAD)"
if [[ "${actual_lerobot_revision}" != "${LEROBOT_REVISION}" ]]; then
  cat >&2 <<EOF
The lerobot checkout is not at the tested v0.6.1 revision.
Expected: ${LEROBOT_REVISION}
Actual:   ${actual_lerobot_revision}

Update it explicitly, then rerun this script:
  git -C "${LEROBOT_DIR}" fetch origin tag v0.6.1
  git -C "${LEROBOT_DIR}" switch --detach v0.6.1
EOF
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  if "${PYTHON_BIN}" -m venv --help >/dev/null 2>&1; then
    set +e
    "${PYTHON_BIN}" -m venv --system-site-packages "${VENV_DIR}"
    status=$?
    set -e
  else
    status=1
  fi

  if [[ ${status} -ne 0 ]]; then
    "${BOOTSTRAP_PYTHON}" -m pip show virtualenv >/dev/null 2>&1 || \
      "${BOOTSTRAP_PYTHON}" -m pip install --user virtualenv
    "${BOOTSTRAP_PYTHON}" -m virtualenv -p "${PYTHON_BIN}" --system-site-packages "${VENV_DIR}"
  fi
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip "setuptools<80" wheel
"${VENV_DIR}/bin/python" -m pip install -r "${ROOT_DIR}/data_pipeline/requirements-converter.txt"
"${VENV_DIR}/bin/python" -m pip install -r "${ROOT_DIR}/data_pipeline/requirements-teleop.txt"
"${VENV_DIR}/bin/python" -m pip install -r "${ROOT_DIR}/data_pipeline/requirements-operator-console.txt"
"${VENV_DIR}/bin/python" -m pip install torch==2.7.1 torchvision==0.22.1
"${VENV_DIR}/bin/python" -m pip install --no-deps -e "${LEROBOT_DIR}"

echo "Converter environment ready at ${VENV_DIR}"
echo "Activate with: source ${VENV_DIR}/bin/activate"
