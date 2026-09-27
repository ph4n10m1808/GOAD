#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

fake_bin="$tmpdir/bin"
log="$tmpdir/commands.log"
mkdir -p "$fake_bin" "$tmpdir/home" "$tmpdir/conda-base"
touch "$log"

cat > "$fake_bin/conda" <<'SH'
#!/usr/bin/env bash
printf 'conda %s\n' "$*" >> "$FAKE_CONDA_LOG"

if [ "${1:-}" = "shell.bash" ] && [ "${2:-}" = "hook" ]; then
  cat <<'HOOK'
conda() {
  if [ "${1:-}" = "activate" ]; then
    printf 'conda activate %s\n' "$2" >> "$FAKE_CONDA_LOG"
    export CONDA_DEFAULT_ENV="$2"
    return 0
  fi
  if [ "${1:-}" = "deactivate" ]; then
    printf 'conda deactivate\n' >> "$FAKE_CONDA_LOG"
    unset status
    unset CONDA_DEFAULT_ENV
    return 0
  fi
  command conda "$@"
}
HOOK
  exit 0
fi

if [ "${1:-}" = "info" ] && [ "${2:-}" = "--base" ]; then
  printf '%s\n' "$FAKE_CONDA_BASE"
  exit 0
fi

if [ "${1:-}" = "env" ] && [ "${2:-}" = "list" ]; then
  printf '# conda environments:\n'
  printf 'base                  *  %s\n' "$FAKE_CONDA_BASE"
  if [ "${FAKE_CONDA_HAS_GOAD:-no}" = "yes" ]; then
    printf 'goad                     %s/envs/goad\n' "$FAKE_CONDA_BASE"
  fi
  exit 0
fi

if [ "${1:-}" = "create" ]; then
  exit 0
fi

exit 0
SH

cat > "$fake_bin/python" <<'SH'
#!/usr/bin/env bash
printf 'python %s\n' "$*" >> "$FAKE_CONDA_LOG"
exit "${FAKE_PYTHON_EXIT_CODE:-0}"
SH
cp "$fake_bin/python" "$fake_bin/python3"

cat > "$fake_bin/ansible-galaxy" <<'SH'
#!/usr/bin/env bash
printf 'ansible-galaxy %s\n' "$*" >> "$FAKE_CONDA_LOG"
exit 0
SH

chmod +x "$fake_bin/conda" "$fake_bin/python" "$fake_bin/python3" "$fake_bin/ansible-galaxy"

run_goad_sh() {
  local has_env="$1"
  FAKE_CONDA_HAS_GOAD="$has_env" \
    FAKE_CONDA_LOG="$log" \
    FAKE_CONDA_BASE="$tmpdir/conda-base" \
    HOME="$tmpdir/home" \
    PATH="$fake_bin:$PATH" \
    bash "$repo_root/goad.sh" -t status
}

assert_log_contains() {
  local expected="$1"
  if ! grep -Fxq "$expected" "$log"; then
    echo "Missing log line: $expected" >&2
    echo "--- command log ---" >&2
    cat "$log" >&2
    exit 1
  fi
}

assert_log_not_contains() {
  local unexpected="$1"
  if grep -Fxq "$unexpected" "$log"; then
    echo "Unexpected log line: $unexpected" >&2
    echo "--- command log ---" >&2
    cat "$log" >&2
    exit 1
  fi
}

run_goad_sh no
assert_log_contains "conda shell.bash hook"
assert_log_contains "conda env list"
assert_log_contains "conda create -y -n goad python=3.11"
assert_log_contains "conda activate goad"
assert_log_contains "python -m pip install --upgrade pip"
assert_log_contains "python -m pip install -r requirements_311.yml"
assert_log_contains "ansible-galaxy install -r ansible/requirements_311.yml"
assert_log_contains "python goad.py -t status"

: > "$log"
run_goad_sh yes
assert_log_contains "conda shell.bash hook"
assert_log_contains "conda env list"
assert_log_contains "conda activate goad"
assert_log_contains "python goad.py -t status"
assert_log_not_contains "conda create -y -n goad python=3.11"
assert_log_not_contains "python -m pip install -r requirements_311.yml"
assert_log_not_contains "ansible-galaxy install -r ansible/requirements_311.yml"


# Preserve the application exit code even if conda cleanup unsets "status".
export FAKE_PYTHON_EXIT_CODE=7
goad_test_exit_code=0
run_goad_sh yes || goad_test_exit_code=$?
if [ "$goad_test_exit_code" -ne 7 ]; then
  echo "Expected exit code 7, got $goad_test_exit_code" >&2
  exit 1
fi
assert_log_contains "conda deactivate"
