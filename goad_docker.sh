#!/usr/bin/env bash
set -euo pipefail

conda_env_name="${GOAD_CONDA_ENV:-goad-docker}"
python_version="${GOAD_PYTHON_VERSION:-3.11}"
pip_requirements="noansible_requirements.yml"
docker_image="goadansible"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Use /mnt/ssd_data for vagrant data (boxes, etc.)
export VAGRANT_HOME=/mnt/SSD_DATA/.vagrant.d

cd "$script_dir"

if ! command -v conda >/dev/null 2>&1; then
  echo "[-] conda not found in PATH. Please install Miniconda/Anaconda first."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[-] docker not found in PATH. Please install Docker first."
  exit 1
fi

if conda_setup="$(conda shell.bash hook 2>/dev/null)"; then
  eval "$conda_setup"
else
  conda_base="$(conda info --base)"
  if [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1090
    source "$conda_base/etc/profile.d/conda.sh"
  else
    echo "[-] unable to initialize conda shell integration"
    exit 1
  fi
fi

conda_env_exists() {
  conda env list | awk 'NF && $1 !~ /^#/ {print $1}' | grep -Fxq "$conda_env_name"
}

install_dependencies() {
  if [ ! -f "$pip_requirements" ]; then
    echo "[-] missing pip requirements file: $pip_requirements"
    exit 1
  fi

  echo "[+] installing Python requirements from $pip_requirements"
  python -m pip install --upgrade pip
  python -m pip install -r "$pip_requirements"
}

if conda_env_exists; then
  echo "[+] conda env '$conda_env_name' found"
else
  echo "[+] conda env '$conda_env_name' not found, creating it with Python $python_version"
  conda create -y -n "$conda_env_name" "python=$python_version"
  conda activate "$conda_env_name"
  install_dependencies
fi

if [ "${CONDA_DEFAULT_ENV:-}" != "$conda_env_name" ]; then
  conda activate "$conda_env_name"
fi

if id -nG | tr ' ' '\n' | grep -Fxq docker; then
  echo "[+] current user is in the docker group"
  docker_command=(docker)
else
  echo "[*] current user is not in the docker group; using sudo"
  docker_command=(sudo docker)
fi

if ! "${docker_command[@]}" image inspect "$docker_image" >/dev/null 2>&1; then
  echo "[+] building Docker image '$docker_image'"
  "${docker_command[@]}" build -t "$docker_image" .
  echo "[+] Docker image '$docker_image' created"
else
  echo "[+] Docker image '$docker_image' found"
fi

echo "[+] starting GOAD with Docker; local and runner provisioners are disabled"
set +e
python goad.py -m docker -d local -d runner "$@"
status=$?
set -e

conda deactivate >/dev/null 2>&1 || true
exit "$status"
