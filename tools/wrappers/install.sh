#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
ROOT_DIR=${SCRIPT_DIR:h:h}
WRAPPER_BIN="${ROOT_DIR}/tools/wrappers/bin"
TARGET_FILE="${1:-$HOME/.zshrc}"
BEGIN_MARKER="# >>> ExoCortex wrappers >>>"
END_MARKER="# <<< ExoCortex wrappers <<<"
BLOCK="${BEGIN_MARKER}
export PATH=\"${WRAPPER_BIN}:\$PATH\"
typeset -g EXOCORTEX_WRAPPER_BIN=\"${WRAPPER_BIN}\"
typeset -g EXOCORTEX_DOCTOR=\"${ROOT_DIR}/tools/wrappers/doctor.py\"
typeset -g EXOCORTEX_DOCTOR_STAMP=\"\${TMPDIR:-/tmp}/exocortex_wrapper_doctor_\${USER}.stamp\"
typeset -g EXOCORTEX_DOCTOR_LOG=\"\${TMPDIR:-/tmp}/exocortex_wrapper_doctor_\${USER}.log\"
typeset -ga exocortex_nvm_bins
exocortex_nvm_bins=(\$HOME/.nvm/versions/node/*/bin(N))
typeset -g EXOCORTEX_NODE_BIN=\"\${exocortex_nvm_bins[-1]:-}\"
exocortex_wrapper_startup_check() {
  [[ -o interactive ]] || return 0
  [[ \"\${EXOCORTEX_STARTUP_CHECK_RUNNING:-0}\" == \"1\" ]] && return 0
  local today=\$(date +%F)
  local last_run=\"\"
  [[ -r \"\${EXOCORTEX_DOCTOR_STAMP}\" ]] && last_run=\$(<\"\${EXOCORTEX_DOCTOR_STAMP}\")
  [[ \"\${last_run}\" == \"\${today}\" ]] && return 0
  [[ -f \"\${EXOCORTEX_DOCTOR}\" ]] || return 0
  EXOCORTEX_STARTUP_CHECK_RUNNING=1 python3 \"\${EXOCORTEX_DOCTOR}\" --skip-shell-check --json >\"\${EXOCORTEX_DOCTOR_LOG}\" 2>&1
  local exit_code=\$?
  print -r -- \"\${today}\" >| \"\${EXOCORTEX_DOCTOR_STAMP}\"
  if [[ \$exit_code -ne 0 ]]; then
    echo \"[ExoCortex] wrapper doctor detected an issue. Run exocortex-doctor for details.\" >&2
  fi
}
codex() { \"\${EXOCORTEX_WRAPPER_BIN}/codex\" \"\$@\"; }
claude() { \"\${EXOCORTEX_WRAPPER_BIN}/claude\" \"\$@\"; }
gemini() {
  local _exocortex_old_path=\"\$PATH\"
  if [[ -n \"\${EXOCORTEX_NODE_BIN}\" ]]; then
    export PATH=\"\${EXOCORTEX_NODE_BIN}:\$PATH\"
  fi
  \"\${EXOCORTEX_WRAPPER_BIN}/gemini\" \"\$@\"
  local exit_code=\$?
  export PATH=\"\$_exocortex_old_path\"
  return \$exit_code
}
exocortex_wrapper_startup_check
${END_MARKER}"

mkdir -p "${TARGET_FILE:A:h}"
touch "${TARGET_FILE}"

if grep -Fq "${BEGIN_MARKER}" "${TARGET_FILE}"; then
  temp_file=$(mktemp)
  awk -v begin="${BEGIN_MARKER}" -v end="${END_MARKER}" '
    $0 == begin { in_block=1; next }
    $0 == end { in_block=0; next }
    !in_block { print }
  ' "${TARGET_FILE}" > "${temp_file}"
  mv "${temp_file}" "${TARGET_FILE}"
fi

printf '\n%s\n' "${BLOCK}" >> "${TARGET_FILE}"
echo "Installed ExoCortex wrapper shell block in ${TARGET_FILE}"
