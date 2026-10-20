from goad.log import Log
from goad.utils import *
from goad.goadpath import GoadPath
from goad.provisioner.ansible.ansible import Ansible
import grp
import os


class DockerAnsibleProvisionerCmd(Ansible):
    provisioner_name = 'docker'

    def __init__(self, lab_name, provider):
        super().__init__(lab_name, provider)
        self.remote_project_path = '/goad'
        configured = os.environ.get('GOAD_DOCKER_LAUNCHER')
        launchers = {'docker': ['docker'], 'pkexec docker': ['pkexec', 'docker']}
        if configured is not None:
            if configured not in launchers:
                raise ValueError('GOAD_DOCKER_LAUNCHER must be docker or pkexec docker')
            self.docker_command = launchers[configured]
        else:
            self.docker_command = launchers['docker' if self.is_current_user_in_docker_group() else 'pkexec docker']
        self.check_docker_image()

    def is_current_user_in_docker_group(self):
        try:
            docker_gid = grp.getgrnam('docker').gr_gid
            if docker_gid in {*os.getgroups(), os.getegid()}:
                Log.info('Current user is in docker group')
                return True
            else:
                Log.info('Current user is not in docker group; using pkexec for Docker')
                return False
        except (KeyError, OSError):
            return False

    def check_docker_image(self):
        result = self.command.run_process(
            self.docker_command + ['image', 'inspect', 'goadansible'],
            path=GoadPath.get_project_path(),
        )
        if result is None or result.returncode != 0:
            Log.error('Docker image goadansible not found')
            Log.info('Build the Docker image through goad_docker.sh')
        else:
            Log.success('Docker image exist')

    def run_playbook(self, playbook, inventories, tries=3, timeout=30, playbook_path=None):
        remote_inventories = []
        for inventory in inventories:
            remote_inventories.append(Utils.transform_local_path_to_remote_path(inventory, self.remote_project_path))
        command = f'-i {" -i ".join(remote_inventories)} {playbook}'
        Log.info(f'Run playbook : {playbook} with inventory file(s) : {", ".join(remote_inventories)}')
        run_complete = False
        nb_try = 0
        if playbook_path is not None:
            ansible_path = Utils.transform_local_path_to_remote_path(playbook_path, self.remote_project_path)
        else:
            ansible_path = '/goad/ansible'
        while not run_complete:
            nb_try += 1
            run_complete = self.command.run_docker_ansible(command, GoadPath.get_project_path(), ansible_path, self.docker_command)
            if not run_complete and nb_try > tries:
                Log.error('3 fails abort.')
                break
        return run_complete
