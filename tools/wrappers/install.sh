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
typeset -g EXOCORTEX_CODEX_STATUS=\"${ROOT_DIR}/tools/wrappers/codex_status.py\"
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
exocortex_codex_post_run() {
  local _started_at=\"\$1\"
  local _cwd=\"\$2\"
  local _tty=\"\$3\"
  [[ -f \"\${EXOCORTEX_CODEX_STATUS}\" ]] || return 0
  python3 \"\${EXOCORTEX_CODEX_STATUS}\" record --started-at \"\${_started_at}\" --cwd \"\${_cwd}\" --tty \"\${_tty}\" >/dev/null 2>&1 || true
}
codex() {
  local _exocortex_cwd=\"\$PWD\"
  local _exocortex_started_at=\$(date +%s)
  local _exocortex_tty=\"\"
  [[ -t 1 ]] && _exocortex_tty=\$(tty 2>/dev/null || true)
  \"\${EXOCORTEX_WRAPPER_BIN}/codex\" \"\$@\"
  local exit_code=\$?
  exocortex_codex_post_run \"\${_exocortex_started_at}\" \"\${_exocortex_cwd}\" \"\${_exocortex_tty}\"
  return \$exit_code
}
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
if [[ -n \"\${POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS[*]:-}\" ]]; then
  function prompt_exocortex_codex_status() {
    [[ -f \"\${EXOCORTEX_CODEX_STATUS}\" ]] || return
    local tty_path=\"\"
    [[ -t 1 ]] && tty_path=\$(tty 2>/dev/null || true)
    local text
    text=\$(python3 \"\${EXOCORTEX_CODEX_STATUS}\" prompt --tty \"\${tty_path}\" --cwd \"\$PWD\" 2>/dev/null) || return
    [[ -n \"\${text}\" ]] || return
    p10k segment -f 109 -i 'C' -t \"\${text}\"
  }
  if (( \${POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS[(Ie)exocortex_codex_status]} == 0 )); then
    POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS=(exocortex_codex_status \${POWERLEVEL9K_RIGHT_PROMPT_ELEMENTS[@]})
  fi
fi
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
