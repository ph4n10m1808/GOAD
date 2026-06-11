#!/usr/bin/env bash

set -u

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
goad_root="$(cd "$script_dir/.." && pwd)"
workspace_dir="${GOAD_WORKSPACE_DIR:-$goad_root/workspace}"
force=0
dry_run=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Destroy every Vagrant VM managed under the GOAD workspace.

Options:
  -f, --force              Do not ask for confirmation
  -n, --dry-run            Show what would be destroyed, but do nothing
  -w, --workspace <path>   Workspace directory to scan (default: $workspace_dir)
  -h, --help               Show this help

Examples:
  scripts/destroy_all_vagrant.sh --dry-run
  scripts/destroy_all_vagrant.sh --force
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -f|--force)
      force=1
      shift
      ;;
    -n|--dry-run)
      dry_run=1
      shift
      ;;
    -w|--workspace)
      if [ "${2:-}" = "" ]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      workspace_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! command -v vagrant >/dev/null 2>&1; then
  echo "Vagrant was not found in PATH." >&2
  exit 1
fi

if [ ! -d "$workspace_dir" ]; then
  echo "Workspace directory not found: $workspace_dir" >&2
  exit 1
fi

provider_dirs=()
while IFS= read -r -d '' vagrantfile; do
  provider_dirs+=("$(dirname "$vagrantfile")")
done < <(find "$workspace_dir" -mindepth 3 -maxdepth 3 -path "*/provider/Vagrantfile" -print0 | sort -z)

if [ "${#provider_dirs[@]}" -eq 0 ]; then
  echo "No GOAD Vagrant providers found under: $workspace_dir"
  exit 0
fi

echo "GOAD Vagrant providers to destroy:"
for provider_dir in "${provider_dirs[@]}"; do
  echo " - $provider_dir"
done

if [ "$dry_run" -eq 1 ]; then
  echo
  echo "Dry run only. No VM was destroyed."
  exit 0
fi

if [ "$force" -ne 1 ]; then
  if [ ! -t 0 ]; then
    echo "Refusing to destroy VMs without an interactive confirmation. Re-run with --force." >&2
    exit 1
  fi

  echo
  printf "Type 'destroy' to destroy these Vagrant VMs: "
  read -r answer
  if [ "$answer" != "destroy" ]; then
    echo "Aborted."
    exit 0
  fi
fi

failed=0
for provider_dir in "${provider_dirs[@]}"; do
  echo
  echo "[+] Destroying Vagrant VMs in $provider_dir"
  if (cd "$provider_dir" && vagrant destroy -f); then
    echo "[+] Destroyed: $provider_dir"
  else
    echo "[!] Failed: $provider_dir" >&2
    failed=1
  fi
done

exit "$failed"
