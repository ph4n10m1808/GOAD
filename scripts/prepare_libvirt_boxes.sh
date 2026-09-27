#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --lab <GOAD|GOAD-Light|GOAD-Mini|MINILAB|NHA|SCCM|DRACARYS> [--extension <name>]... [--dry-run]"
  echo "Environment: GOAD_VAGRANT_HOME"
}

lab=""
extensions=()
dry_run=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --lab)
      lab="${2:-}"
      shift 2
      ;;
    --extension)
      extensions+=("${2:-}")
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$lab" ]]; then
  usage >&2
  exit 2
fi

for command_name in vagrant qemu-img virsh python3; do
  if ! command -v "$command_name" >/dev/null; then
    echo "Missing command: $command_name" >&2
    exit 1
  fi
done

export VAGRANT_HOME="${GOAD_VAGRANT_HOME:-/mnt/SSD_DATA/.vagrant.d}"
mkdir -p "$VAGRANT_HOME"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! vagrant plugin list | grep -q '^vagrant-libvirt '; then
  echo "Missing mandatory Vagrant plugin: vagrant-libvirt" >&2
  exit 1
fi

pool_name="GOAD"
if ! virsh -c qemu:///system pool-info "$pool_name" >/dev/null; then
  echo "Cannot access libvirt storage pool: $pool_name" >&2
  exit 1
fi
pool_path="$(virsh -c qemu:///system pool-dumpxml "$pool_name" | python3 -c 'import sys, xml.etree.ElementTree as ET; print(ET.parse(sys.stdin).findtext("target/path"))')" || exit 1
if [[ "$pool_path" != "/mnt/SSD_DATA/Virtualization/KVM/GOAD" ]]; then
  echo "GOAD storage pool must target /mnt/SSD_DATA/Virtualization/KVM/GOAD; found: $pool_path" >&2
  exit 1
fi
if ! virsh -c qemu:///system pool-refresh "$pool_name" >/dev/null; then
  echo "Cannot refresh libvirt storage pool: $pool_name" >&2
  exit 1
fi
echo "Using VAGRANT_HOME: $VAGRANT_HOME"
echo "Using libvirt storage pool: $pool_name"

boxes=()
add_box() {
  local name="$1"
  local version="${2:-}"
  boxes+=("$name|$version")
}

case "$lab" in
  GOAD)
    add_box StefanScherer/windows_2019 2021.05.15
    add_box StefanScherer/windows_2016 2017.12.14
    add_box StefanScherer/windows_2016 2019.02.14
    ;;
  GOAD-Light|GOAD-Mini)
    add_box StefanScherer/windows_2019 2021.05.15
    ;;
  MINILAB)
    add_box mayfly/windows_server2019
    add_box mayfly/windows10
    ;;
  NHA|SCCM)
    add_box mayfly/windows_server2019
    ;;
  DRACARYS)
    add_box GOAD/WindowsServer2025 2026.02.09
    add_box bento/ubuntu-24.04 202510.26.0
    ;;
  *)
    echo "Unsupported lab: $lab" >&2
    exit 2
    ;;
esac

for extension in "${extensions[@]}"; do
  case "$extension" in
    exchange)
      add_box StefanScherer/windows_2019 2021.05.15
      ;;
    ws01)
      add_box mayfly/windows10
      ;;
    elk|guacamole|lx01|wazuh)
      add_box bento/ubuntu-22.04
      ;;
    *)
      echo "Unsupported extension: $extension" >&2
      exit 2
      ;;
  esac
done

box_present() {
  local name="$1"
  local provider="$2"
  local version="${3:-}"
  if [[ -n "$version" ]]; then
    vagrant box list | awk -v name="$name" -v provider="$provider" -v version="$version" \
      '$1 == name && $2 == "(" provider "," && $3 == version ")" { found=1 } END { exit !found }'
  else
    vagrant box list | awk -v name="$name" -v provider="$provider" \
      '$1 == name && $2 == "(" provider "," { found=1 } END { exit !found }'
  fi
}

for entry in "${boxes[@]}"; do
  name="${entry%%|*}"
  version="${entry#*|}"

  if box_present "$name" libvirt "$version"; then
    echo "Already prepared: $name ($version, libvirt)"
    continue
  fi

  if ! box_present "$name" virtualbox "$version"; then
    add_args=(box add "$name" --provider virtualbox)
    if [[ -n "$version" ]]; then
      add_args+=(--box-version "$version")
    fi
    if [[ "$dry_run" == true ]]; then
      printf 'Would run: vagrant'
      printf ' %q' "${add_args[@]}"
      printf '\n'
    else
      vagrant "${add_args[@]}"
    fi
  fi

  if [[ -z "$version" ]]; then
    version="$(vagrant box list --machine-readable | awk -F, -v name="$name" \
      '$3 == "box-name" { current=$4; provider="" } \
       $3 == "box-provider" { provider=$4 } \
       $3 == "box-version" && current == name && provider == "virtualbox" { print $4; exit }')"
    if [[ -z "$version" ]]; then
      echo "Cannot determine cached VirtualBox version for $name" >&2
      exit 1
    fi
  fi
  convert_args=(
    "$script_dir/convert_vagrant_box_to_libvirt.py"
    "$name"
    --version "$version"
    --vagrant-home "$VAGRANT_HOME"
  )
  if [[ "$dry_run" == true ]]; then
    printf 'Would run: python3'
    printf ' %q' "${convert_args[@]}"
    printf '\n'
  else
    python3 "${convert_args[@]}"
    if ! box_present "$name" libvirt "$version"; then
      echo "Conversion finished but Vagrant cannot find $name ($version, libvirt)" >&2
      exit 1
    fi
  fi
done

if [[ "$dry_run" == true ]]; then
  echo "Dry run complete; no boxes were downloaded or converted."
else
  echo "Libvirt boxes for $lab are ready in $VAGRANT_HOME"
  echo "Use: export VAGRANT_HOME='$VAGRANT_HOME'"
  echo "VM storage: /mnt/SSD_DATA/Virtualization/KVM/GOAD (pool GOAD)"
fi
