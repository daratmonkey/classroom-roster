import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ansible_runner import ansible_location_label, run_ansible_job, uses_wsl
from credential_store import get_credential_store

ROOT = Path(__file__).resolve().parent


def connect(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('instructor', 'student')),
            name TEXT NOT NULL,
            username TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(people)")}
    if "seeded_at" not in columns:
        connection.execute("ALTER TABLE people ADD COLUMN seeded_at TEXT")
    return connection


@contextmanager
def database(db_path):
    connection = connect(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_classes(db_path):
    with database(db_path) as db:
        classes = db.execute("SELECT id, name FROM classes ORDER BY name COLLATE NOCASE").fetchall()
        result = []
        for classroom in classes:
            people = db.execute(
                "SELECT id, role, name, username, ip_address, seeded_at FROM people WHERE class_id = ? ORDER BY role, name COLLATE NOCASE",
                (classroom["id"],),
            ).fetchall()
            result.append({
                "id": classroom["id"], "name": classroom["name"],
                "people": [{**dict(person), "seeded": bool(person["seeded_at"])} for person in people],
            })
        return result


def make_handler(db_path, credential_store, ansible_runner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def execute_class_job(self, class_id, body, mode):
            if body.get("confirmed") is not True:
                return self.send_json(400, {"error": "Confirm that you want to run this job on every student in the class."})
            try:
                credentials = credential_store.load()
            except Exception:
                return self.send_json(500, {"error": "Could not access the saved credential store entry."})
            if not credentials:
                return self.send_json(400, {"error": "Set the default root password in Credentials before running Ansible."})
            with database(db_path) as db:
                students = db.execute(
                    "SELECT id, name, ip_address FROM people WHERE class_id = ? AND role = 'student' ORDER BY name COLLATE NOCASE",
                    (class_id,),
                ).fetchall()
            if not students:
                return self.send_json(400, {"error": "Add at least one student with an IP address first."})
            hosts = [{"id": student["id"], "name": student["name"],
                      "ip_address": student["ip_address"], "username": credentials["username"]}
                     for student in students]
            job = {"mode": mode, "hosts": hosts}
            if mode == "playbook":
                playbook_path = str(body.get("playbook_path", "")).strip()
                if not playbook_path or len(playbook_path) > 1024:
                    return self.send_json(400, {"error": "Enter an absolute path to an Ansible playbook."})
                job["playbook_path"] = playbook_path
            elif mode == "command":
                command = str(body.get("command", "")).strip()
                if not command or len(command) > 2048:
                    return self.send_json(400, {"error": "Enter a command (2,048 characters max)."})
                job["command"] = command
            try:
                result = ansible_runner(job, credentials["password"])
            except Exception as error:
                result = {"ok": False, "error": str(error)}
            if not result.get("ok"):
                return self.send_json(502, {
                    "ok": False, "error": result.get("error", "Ansible job failed."),
                    "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""),
                })
            if mode == "playbook":
                with database(db_path) as db:
                    placeholders = ",".join("?" for _ in students)
                    db.execute(f"UPDATE people SET seeded_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                               [student["id"] for student in students])
            return self.send_json(200, {
                "ok": True, "target_count": len(hosts), "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            })

        def read_json(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 16_384:
                    raise ValueError
                return json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                raise ValueError("Send a valid JSON request.")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/classes":
                return self.send_json(200, list_classes(db_path))
            if path == "/api/ansible/credentials":
                try:
                    credentials = credential_store.load()
                except Exception:
                    return self.send_json(500, {"error": "Could not access the saved credential store."})
                location = getattr(credential_store, "location", "the credential store")
                if not credentials:
                    return self.send_json(200, {"configured": False, "username": "root", "password": "",
                                                "store": location})
                reveal = parse_qs(urlparse(self.path).query).get("reveal") == ["1"]
                return self.send_json(200, {
                    "configured": True, "username": credentials["username"], "store": location,
                    "password": credentials["password"] if reveal else "••••••••",
                })
            if path == "/api/platform":
                return self.send_json(200, {
                    "credential_store": getattr(credential_store, "location", "the credential store"),
                    "ansible_location": ansible_location_label(),
                    "ansible_in_wsl": uses_wsl(),
                    "playbook_path_hint": ("An absolute Linux path inside the WSL distribution."
                                           if uses_wsl() else "An absolute path on this machine."),
                })
            if path == "/" or path in ("/index.html", "/app.js", "/styles.css"):
                target = ROOT / ("index.html" if path == "/" else path.lstrip("/"))
                if target.is_file() and target.parent == ROOT:
                    data = target.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self.send_json(404, {"error": "Not found."})

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self.read_json()
                if not isinstance(body, dict):
                    raise ValueError("Send a JSON object.")
                if path == "/api/ansible/credentials":
                    username = str(body.get("username", "root")).strip()
                    password = str(body.get("password", ""))
                    credential_store.save(username, password)
                    return self.send_json(200, {"configured": True, "username": "root"})
                test_class_match = re.fullmatch(r"/api/classes/(\d+)/seed-test", path)
                if test_class_match:
                    class_id = int(test_class_match.group(1))
                    with database(db_path) as db:
                        if db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone() is None:
                            return self.send_json(404, {"error": "Class not found."})
                        cursor = db.execute(
                            "UPDATE people SET seeded_at = CURRENT_TIMESTAMP WHERE class_id = ? AND role = 'student'",
                            (class_id,),
                        )
                        seeded_count = cursor.rowcount
                    return self.send_json(200, {"ok": True, "seeded_count": seeded_count, "test_only": True})
                test_person_match = re.fullmatch(r"/api/people/(\d+)/seed-test", path)
                if test_person_match:
                    person_id = int(test_person_match.group(1))
                    with database(db_path) as db:
                        cursor = db.execute(
                            "UPDATE people SET seeded_at = CURRENT_TIMESTAMP WHERE id = ? AND role = 'student'",
                            (person_id,),
                        )
                    if cursor.rowcount == 0:
                        return self.send_json(404, {"error": "Student not found."})
                    return self.send_json(200, {"ok": True, "seeded": True, "test_only": True})
                job_match = re.fullmatch(r"/api/classes/(\d+)/(seed|run)", path)
                if job_match:
                    return self.execute_class_job(int(job_match.group(1)), body,
                                                  "playbook" if job_match.group(2) == "seed" else "command")
                with database(db_path) as db:
                    if path == "/api/classes":
                        name = str(body.get("name", "")).strip()
                        if not name or len(name) > 100:
                            return self.send_json(400, {"error": "Class name is required (100 characters max)."})
                        try:
                            cursor = db.execute("INSERT INTO classes(name) VALUES (?)", (name,))
                        except sqlite3.IntegrityError:
                            return self.send_json(409, {"error": "A class with that name already exists."})
                        return self.send_json(201, {"id": cursor.lastrowid, "name": name, "people": []})

                    match = re.fullmatch(r"/api/classes/(\d+)/people", path)
                    if match:
                        class_id = int(match.group(1))
                        if db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone() is None:
                            return self.send_json(404, {"error": "Class not found."})
                        role = str(body.get("role", "")).strip().lower()
                        name = str(body.get("name", "")).strip()
                        username = str(body.get("username", "")).strip()
                        ip_text = str(body.get("ip_address", "")).strip()
                        if role not in ("instructor", "student"):
                            return self.send_json(400, {"error": "Choose instructor or student."})
                        if not name or not username or len(name) > 100 or len(username) > 80:
                            return self.send_json(400, {"error": "Name and username are required."})
                        try:
                            ip_address = str(ipaddress.ip_address(ip_text))
                        except ValueError:
                            return self.send_json(400, {"error": "Enter a valid IPv4 or IPv6 address."})
                        cursor = db.execute(
                            "INSERT INTO people(class_id, role, name, username, ip_address) VALUES (?, ?, ?, ?, ?)",
                            (class_id, role, name, username, ip_address),
                        )
                        return self.send_json(201, {"id": cursor.lastrowid, "role": role, "name": name,
                                                     "username": username, "ip_address": ip_address,
                                                     "seeded": False, "seeded_at": None})
                return self.send_json(404, {"error": "Not found."})
            except ValueError as error:
                return self.send_json(400, {"error": str(error)})
            except sqlite3.Error:
                return self.send_json(500, {"error": "The local database could not complete that request."})
            except OSError:
                return self.send_json(500, {"error": "Could not access the saved credential store."})

        def do_DELETE(self):
            path = urlparse(self.path).path
            person_match = re.fullmatch(r"/api/people/(\d+)", path)
            class_match = re.fullmatch(r"/api/classes/(\d+)", path)
            if not person_match and not class_match:
                return self.send_json(404, {"error": "Not found."})
            table = "people" if person_match else "classes"
            record_id = int((person_match or class_match).group(1))
            with database(db_path) as db:
                cursor = db.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
                if cursor.rowcount == 0:
                    return self.send_json(404, {"error": "Record not found."})
            return self.send_json(200, {"deleted": True})

    return Handler


def create_server(db_path, host="0.0.0.0", port=8000, credential_store=None, ansible_runner=None):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connect(db_path).close()
    credential_store = credential_store or get_credential_store()
    ansible_runner = ansible_runner or run_ansible_job
    server = ThreadingHTTPServer((host, port), make_handler(db_path, credential_store, ansible_runner))
    server.credential_store_location = getattr(credential_store, "location", "the credential store")
    return server


if __name__ == "__main__":
    db_path = ROOT / "classroom_roster.sqlite3"
    server = create_server(db_path, port=int(os.environ.get("PORT", "8765")))
    print(f"Classroom Roster is running at http://{server.server_address[0]}:{server.server_port}")
    print(f"Local database: {db_path}")
    print(f"Credentials: {server.credential_store_location}")
    print(f"Ansible runs on: {ansible_location_label()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
