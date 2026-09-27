import sys
import os
from goad.command.cmd import Command
import subprocess
import xml.etree.ElementTree as ET

from goad.goadpath import GoadPath
from goad.log import Log
from goad.utils import Utils


class LinuxCommand(Command):

    def __init__(self):
        super().__init__()
        self.vagrant_bin = 'vagrant'
        self.terraform_bin = 'terraform'

    # CHECK
    def check_gem(self, gem_name):
        try:
            result = subprocess.run(['gem', 'list'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if gem_name in result.stdout:
                Log.success(f'ruby gem {gem_name} is installed')
                return True
            else:
                Log.warning(f'ruby gem {gem_name} not installed')
                return False
        except FileNotFoundError:
            Log.error("Ruby or gem is not installed or not found in PATH.")
            return False

    def check_vmware(self):
        return self.is_in_path('vmrun')

    def check_vmware_utility(self):
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', '--quiet', 'vagrant-vmware-utility'],
                check=True
            )
            Log.success(f'vmware utility is installed')
            return True
        except subprocess.CalledProcessError:
            Log.error("vagrant-vmware-utility is not installed")
            return False

    def check_ovftool(self):
        try:
            result = subprocess.run(['ovftool', '-v'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd='.')
            fields = result.stdout.split(' ')
            if len(fields) > 2:
                version = fields[2]
                Log.success(f'Ovftool version {version} is installed')
                return True
            else:
                Log.error(f'Failed to parse ovftool version')
                return False
        except FileNotFoundError:
            Log.error("ovftool is not installed or not found in PATH.")
            return False

    def check_virtualbox(self):
        return self.is_in_path('VBoxManage')

    def check_libvirt(self, min_disk_gb=120):
        checks = [
            self.is_in_path('virsh'),
            self.is_in_path('qemu-system-x86_64'),
        ]
        if not all(checks):
            return False
        if not self._validate_libvirt_pool_path():
            return False

        pool_name = 'GOAD'
        result = subprocess.run(
            ['virsh', '-c', 'qemu:///system', 'pool-info', '--bytes', pool_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            Log.error(f'Cannot access libvirt storage pool {pool_name}: {result.stderr.strip()}')
            return False

        available = None
        active = False
        for line in result.stdout.splitlines():
            key, _, value = line.partition(':')
            if key.strip() == 'State':
                active = value.strip() == 'running'
            elif key.strip() == 'Available':
                try:
                    available = int(value.strip())
                except ValueError:
                    available = None

        if not active:
            Log.error(f'libvirt storage pool {pool_name} is not running')
            return False
        if available is None:
            Log.error(f'Cannot determine free space for libvirt storage pool {pool_name}')
            return False

        free_disk_gb = available / (1024 ** 3)
        if free_disk_gb < min_disk_gb:
            Log.error(f'not enough free space in libvirt pool {pool_name}, only {free_disk_gb:.1f} GB available')
            return False

        connection = subprocess.run(
            ['virsh', '-c', 'qemu:///system', 'list', '--all'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if connection.returncode != 0:
            Log.error(f'Cannot connect to qemu:///system: {connection.stderr.strip()}')
            return False

        Log.success(f'libvirt qemu:///system and storage pool {pool_name} are ready ({free_disk_gb:.1f} GB free)')
        return True

    def _validate_libvirt_pool_path(self):
        expected = '/mnt/SSD_DATA/Virtualization/KVM/GOAD'
        try:
            result = subprocess.run(
                ['virsh', '-c', 'qemu:///system', 'pool-dumpxml', 'GOAD'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except OSError as error:
            Log.error(f'Cannot inspect GOAD storage pool: {error}')
            return False
        if result.returncode != 0:
            Log.error(f'Cannot inspect GOAD storage pool: {result.stderr.strip()}')
            return False
        try:
            actual = ET.fromstring(result.stdout).findtext('target/path')
        except ET.ParseError:
            actual = None
        if actual != expected:
            Log.error(f'GOAD storage pool must target {expected}; found {actual!r}. '
                      'Create or correct the pool before starting VMs.')
            return False
        return True

    def refresh_libvirt_pool(self):
        if not self._validate_libvirt_pool_path():
            return False
        pool_name = 'GOAD'
        result = subprocess.run(
            ['virsh', '-c', 'qemu:///system', 'pool-refresh', pool_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            Log.error(f'Cannot refresh libvirt storage pool {pool_name}: {result.stderr.strip()}')
            return False
        Log.success(f'libvirt storage pool {pool_name} refreshed')
        return True

    def check_ludus(self):
        return self.is_in_path('ludus')

    # RUN
    def run_ludus(self, args, path, api_key, user_id='', impersonation=False):
        env = os.environ.copy()
        if "LUDUS_API_KEY" not in os.environ:
            Log.info('Using api key from config file')
            env["LUDUS_API_KEY"] = api_key
        else:
            Log.info('Using api key from env')
        result = None
        try:
            command = 'ludus '
            if impersonation:
                command += f'--user {user_id} '
            command += args
            Log.info('CWD: ' + Utils.get_relative_path(str(path)))
            Log.cmd(command)
            result = subprocess.run(command, cwd=path, stderr=sys.stderr, stdout=sys.stdout, shell=True, env=env)
        except subprocess.CalledProcessError as e:
            Log.error(f"An error occurred while running the command: {e}")
        return result.returncode == 0

    def run_ludus_result(self, command, path, api_key, do_log=True, user_id='', impersonation=False):
        result = None
        env = os.environ.copy()
        if "LUDUS_API_KEY" not in os.environ:
            Log.info('Using api key from config file')
            env["LUDUS_API_KEY"] = api_key
        else:
            Log.info('Using api key from env')
        try:
            cmd = ['ludus']
            if impersonation:
                cmd += ['--user', user_id]
            cmd += command
            if do_log:
                Log.info('CWD: ' + Utils.get_relative_path(str(path)))
                Log.cmd(' '.join(cmd))
            result = subprocess.run(cmd, cwd=path,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    text=True,
                                    env=env
                                    )
            if result.returncode != 0:
                print(f"Error: {result.stderr}")
                return None

            return result.stdout
        except subprocess.CalledProcessError as e:
            Log.error(f"An error occurred while running the command: {e}")
        return None

    def run_docker_ansible(self, args, path, ansible_path, sudo):
        result = None
        try:
            ansible_command = 'ansible-playbook '
            ansible_command += args
            command = f"{sudo} docker run -ti --rm --network host -h goadansible -v {GoadPath.get_project_path()}:/goad -w {ansible_path} goadansible /bin/bash -c '{ansible_command}'"
            Log.cmd(command)
            result = subprocess.run(command, cwd=path, stderr=sys.stderr, stdout=sys.stdout, shell=True)
        except subprocess.CalledProcessError as e:
            Log.error(f"An error occurred while running the command: {e}")
            return False
        return result.returncode == 0
