"""Run Ansible jobs locally on Linux/macOS, or through WSL when the host is Windows."""

import json
import os
import subprocess
import sys


# Runs inside the target Linux environment. Kept as a -c script so the shared
# root password travels on stdin instead of appearing in a process argument list.
_ANSIBLE_SCRIPT = r'''
import json, os, pathlib, secrets, shlex, shutil, subprocess, sys, tempfile
request = json.load(sys.stdin)
password = request.pop("password")
try:
    executable = shutil.which("ansible-playbook")
    vault_executable = shutil.which("ansible-vault")
    if not executable or not vault_executable:
        raise RuntimeError("Ansible is not installed, or ansible-playbook/ansible-vault are not on PATH.")
    hosts = request.get("hosts", [])
    if not hosts:
        raise RuntimeError("This class has no student hosts to target.")
    inventory_hosts = {}
    for index, host in enumerate(hosts, 1):
        inventory_hosts["student_" + str(index)] = {
            "ansible_host": host["ip_address"],
            "ansible_user": host.get("username", "root"),
            "ansible_connection": "ssh",
            "ansible_become": False,
        }
    inventory = {"all": {"children": {"students": {"hosts": inventory_hosts}}}}
    temp_root = "/dev/shm" if os.path.isdir("/dev/shm") else "/tmp"
    with tempfile.TemporaryDirectory(prefix="classroom-ansible-", dir=temp_root) as temp_dir:
        os.chmod(temp_dir, 0o700)
        temp = pathlib.Path(temp_dir)
        inventory_path = temp / "inventory.json"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        os.chmod(inventory_path, 0o600)
        group_vars = temp / "group_vars" / "students"
        group_vars.mkdir(parents=True, mode=0o700)
        credential_file = group_vars / "vault.yml"
        credential_file.write_text("ansible_password: " + json.dumps(password) + "\n", encoding="utf-8")
        os.chmod(credential_file, 0o600)
        vault_password_file = temp / "vault-password"
        vault_password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        os.chmod(vault_password_file, 0o600)
        encrypted = subprocess.run(
            [vault_executable, "encrypt", "--vault-password-file", str(vault_password_file), str(credential_file)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if encrypted.returncode:
            raise RuntimeError("Ansible Vault could not encrypt the temporary SSH credential.")
        mode = request["mode"]
        if mode == "command":
            argv = shlex.split(request["command"], posix=True)
            if not argv:
                raise RuntimeError("Enter a command to run.")
            playbook = [{
                "name": "Classroom command",
                "hosts": "students",
                "gather_facts": False,
                "tasks": [{
                    "name": "Run requested command",
                    "ansible.builtin.command": {"argv": argv},
                }],
            }]
            playbook_path = pathlib.Path(temp_dir) / "command-playbook.json"
            playbook_path.write_text(json.dumps(playbook), encoding="utf-8")
            os.chmod(playbook_path, 0o600)
            cwd = temp_dir
        elif mode == "playbook":
            playbook_path = pathlib.Path(request["playbook_path"]).expanduser()
            if not playbook_path.is_absolute() or not playbook_path.is_file():
                raise RuntimeError("Playbook path must be an existing absolute path in the Ansible environment.")
            cwd = str(playbook_path.parent)
        else:
            raise RuntimeError("Unsupported Ansible job type.")
        result = subprocess.run(
            [executable, "-i", str(inventory_path), "--vault-password-file", str(vault_password_file),
             "--forks", "5", str(playbook_path)],
            cwd=cwd, capture_output=True, text=True, timeout=300, check=False,
        )
        stdout = result.stdout[-12000:]
        stderr = result.stderr[-12000:]
        if password:
            stdout = stdout.replace(password, "[REDACTED]")
            stderr = stderr.replace(password, "[REDACTED]")
        print(json.dumps({"ok": result.returncode == 0, "returncode": result.returncode,
                          "stdout": stdout, "stderr": stderr}))
except subprocess.TimeoutExpired:
    print(json.dumps({"ok": False, "error": "Ansible timed out after 300 seconds."}))
except Exception as error:
    print(json.dumps({"ok": False, "error": str(error)}))
'''


def uses_wsl():
    """True when the app must shell into a WSL distribution to reach Ansible."""
    return os.name == "nt"


def ansible_location_label():
    """Human-readable description of where Ansible runs, for UI copy."""
    if uses_wsl():
        return f"WSL distribution {wsl_distro()}"
    return "this machine"


def wsl_distro():
    return os.environ.get("CLASSROOM_WSL_DISTRO", "Ubuntu")


def _command(distro):
    if uses_wsl():
        return ["wsl.exe", "-d", distro or wsl_distro(), "--", "python3", "-c", _ANSIBLE_SCRIPT], True
    return [sys.executable or "python3", "-c", _ANSIBLE_SCRIPT], False


def run_ansible_job(job, password, distro=None, timeout=330):
    """Run one playbook or generated command playbook without argv secrets."""
    argv, through_wsl = _command(distro)
    payload = {**job, "password": password}
    try:
        result = subprocess.run(
            argv,
            input=json.dumps(payload), capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except FileNotFoundError:
        if through_wsl:
            return {"ok": False, "error": "WSL was not found. Install WSL and configure a Linux distribution."}
        return {"ok": False, "error": "Python was not found, so the Ansible job could not start."}
    except subprocess.TimeoutExpired:
        where = "WSL Ansible job" if through_wsl else "Ansible job"
        return {"ok": False, "error": f"The {where} timed out after {timeout} seconds."}
    try:
        response = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        where = "WSL Ansible" if through_wsl else "Ansible"
        return {"ok": False, "error": f"Could not read the {where} response.", "stderr": result.stderr[-4000:]}
    return response