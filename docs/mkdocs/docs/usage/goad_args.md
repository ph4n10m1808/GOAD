# Argument mode

Use `./goad.sh --help` for the current parser. The wrapper accepts the same options as `goad.py`.

| Option | Purpose |
| --- | --- |
| `-t`, `--task` | Select a task, including `install`, `install_extension`, start/stop/status and destructive lifecycle operations. Omit for the console. |
| `-l`, `--lab`; `-p`, `--provider`; `-m`, `--method` | Select lab/provider/provisioner for a new installation. Defaults: GOAD/vmware/local. |
| `-ip`, `--ip_range` | Lab IPv4 prefix, for example192.168.56. |
| `-i`, `--instance` | Exact existing instance ID; use the complete workspace ID, not its short prefix. |
| `-e`, `--extensions` | Repeat for distinct extensions; `install_extension` processes them in order and stops on the first failure. |
| `--fleet-url`, `--fleet-ca`, `--agent-version` | Fleet HTTPS endpoint, local public CA and exact version for `install_extension elastic_agent`. Token uses the hidden prompt. |
| `-a`, `--ansible_only`; `-r`, `--run_playbook` | Existing-instance `install` provisioning only, or one selected playbook. |
| `-d`, `--disable_dependencies` | Repeat only for intentionally excluded dependency checks. |

Create a new lab:

```bash
./goad.sh -t install -l GOAD-Light -p libvirt -m local -ip 192.168.56
```

Prepare the pinned libvirt boxes/storage/network first; see [provider preparation](../providers/libvirt.md). This command creates/provisions VMs and is not a read-only check.

Install monitoring extensions on an existing lab without rebuilding it:

```bash
./goad.sh -t install_extension -i INSTANCE_ID -e sysmon -e elastic_agent \
  --fleet-url https://fleet.example.com:8220 --fleet-ca /path/to/public-ca.crt --agent-version 9.5.6
```

Load the existing instance's saved topology and Exchange inventory. Use its Windows Fleet enrollment token, never the Zeek/server token. Base/Exchange provisioning does not implicitly install monitoring. Restarting the full `install` task is broader than retrying one extension; use the [console commands](goad_console.md) for scoped provisioning and supported removal.
