# Classroom Roster

A small local web app for keeping multiple class rosters in a SQLite database.

## Run

1. Make sure Python 3 is installed.
2. Open a terminal in this folder.
3. Run `python app.py`.
4. Open **http://127.0.0.1:8765** in your browser.
5. Stop the server with `Ctrl+C` in the terminal.

The server listens on localhost only; it is not exposed to your local network. The database file `classroom_roster.sqlite3` is created in this folder and is loaded each time the app starts. Set the `PORT` environment variable to use a different port.

## Features

- Create and delete classes.
- Add instructors and students with name, username, and IPv4/IPv6 address from one participant form.
- Browse compact class tiles; open a class to view its roster.
- Search classes and roster details; see class and role totals.
- Remove individual people from a class.
- Test-only seed controls mark all students in a class or a single student as seeded without contacting any hosts; use **Seed environment** for the real Ansible playbook.
- SQLite-backed persistence with validation.
- Ansible class jobs: seed a class from a WSL playbook, run a command with Ansible's `command` module, and see per-student seed status.
- A default root SSH password stored in Windows Credential Manager (masked in the app unless explicitly revealed).

## Ansible

The app invokes Ansible inside the default WSL distribution (`Ubuntu`). Install Ansible there and make sure `ansible-playbook` and `ansible-vault` are available. Password-authentication jobs create a short-lived encrypted Ansible Vault group-vars file alongside a temporary inventory under WSL temporary storage. The playbook field expects an absolute Linux/WSL path. The command dialog runs a simple argv command without a shell; both job types show the target IPs and require confirmation before running as root. A successful seed playbook marks the class's targeted students as seeded; this is run-status tracking, not verification that a particular flag exists.

The default SSH account is `root`, and its shared password is stored in the current Windows user's Credential Manager. Reveal is explicit. Use this only on lab machines you administer; shared root credentials grant full control of every selected student host. The app runs locally and targets only students in the selected class.

## Tests

Run `python -m unittest -v test_app`.
