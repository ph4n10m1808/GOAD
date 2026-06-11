#!/usr/bin/env bash

py=python3
venv="$HOME/.goad/.venv"
requirement_file="requirements.yml"
conda_env="${GOAD_CONDA_ENV:-goad}"
conda_python="${GOAD_CONDA_PYTHON:-3.11}"
prefer_conda="${GOAD_PREFER_CONDA:-1}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir" || exit 1

find_conda() {
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return 0
  fi

  for candidate in "$HOME/miniconda3/bin/conda" "$HOME/anaconda3/bin/conda" "/opt/miniconda3/bin/conda" "/opt/anaconda3/bin/conda"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

select_requirement_file() {
  local python_cmd="$1"
  local version
  local version_numeric

  version=$("$python_cmd" --version 2>&1 | awk '{print $2}')
  echo "Python version in use : $version"
  version_numeric=$(echo "$version" | awk -F. '{printf "%d%02d%02d\n", $1, $2, $3}')

  if [ "$version_numeric" -ge 30800 ]; then
      echo 'python version >= 3.8 ok'
      if [ "$version_numeric" -lt 31100 ]; then
        requirement_file="requirements.yml"
      else
        requirement_file="requirements_311.yml"
      fi
  else
      echo "Python version is < 3.8 please update python before install"
      exit 1
  fi
}

install_goad_requirements() {
  "$py" -m pip install --upgrade pip
  export SETUPTOOLS_USE_DISTUTILS=stdlib
  "$py" -m pip install -r "$requirement_file"
  cd ansible || exit 1
  ansible-galaxy install -r "$requirement_file"
  cd - >/dev/null || exit 1
}

conda_bin=$(find_conda || true)
if [ "$prefer_conda" != "0" ] && [ "$GOAD_USE_VENV" != "1" ] && [ -n "$conda_bin" ]; then
  conda_base=$("$conda_bin" info --base 2>/dev/null || true)
  if [ -n "$conda_base" ] && [ -f "$conda_base/etc/profile.d/conda.sh" ]; then
    source "$conda_base/etc/profile.d/conda.sh"
  else
    eval "$("$conda_bin" shell.bash hook)"
  fi

  mkdir -p "$HOME/.goad"
  if ! conda env list | awk '{print $1}' | grep -qx "$conda_env"; then
    echo "[+] conda env '$conda_env' not found, creating it with python $conda_python"
    conda create -y -n "$conda_env" "python=$conda_python" pip
  fi

  conda activate "$conda_env" || exit 1
  py=python
  select_requirement_file "$py"

  conda_marker="$HOME/.goad/.conda_${conda_env}_installed"
  if [ ! -f "$conda_marker" ]; then
    echo "[+] installing GOAD requirements in conda env '$conda_env'"
    install_goad_requirements
    touch "$conda_marker"
  fi

  "$py" goad.py "$@"
  goad_exit=$?
  conda deactivate
  exit $goad_exit
fi

if [ ! -d "$venv" ]
then
  # Get the Python version (removes 'Python' from output)
  select_requirement_file "$py"

  if [ "$("$py" -m venv -h 2>/dev/null | grep -i 'usage:')" ]; then
    echo "venv module is installed. continue"
  else
    echo "venv module is not installed."
    echo "please install $py-venv according to your system"
    echo "exit"
    exit 0
  fi

  echo '[+] venv not found, start python venv creation'
  mkdir -p ~/.goad
  "$py" -m venv "$venv"
  source "$venv/bin/activate"
  if [ $? -eq 0 ]; then
    install_goad_requirements
  else
    echo "Error in venv creation"
    rm -rf $venv
    exit 0
  fi
fi

# launch the app
source "$venv/bin/activate"
"$py" goad.py "$@"
deactivate
