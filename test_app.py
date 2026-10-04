import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app import create_server


class FakeCredentials:
    def __init__(self):
        self.password = None

    def save(self, username, password):
        self.username = username
        self.password = password

    def load(self):
        if self.password is None:
            return None
        return {"username": self.username, "password": self.password}


class ClassroomRosterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        self.credentials = FakeCredentials()
        self.jobs = []
        self.fail_jobs = False

        def runner(job, password):
            self.jobs.append((job, password))
            if self.fail_jobs:
                return {"ok": False, "error": "Ansible failed on a host."}
            return {"ok": True, "stdout": "playbook complete", "stderr": ""}

        self.server = create_server(self.db_path, credential_store=self.credentials, ansible_runner=runner)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request(self, path, method="GET", payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            status = error.code
            payload = json.loads(error.read())
            error.close()
            return status, payload

    def test_can_create_class_and_add_instructor_and_student(self):
        status, classroom = self.request("/api/classes", "POST", {"name": "Biology 101"})
        self.assertEqual(status, 201)
        class_id = classroom["id"]
        for role, person in (
            ("instructor", {"name": "Dr. Ada", "username": "ada", "ip_address": "192.168.1.10"}),
            ("student", {"name": "Sam Lee", "username": "sam", "ip_address": "192.168.1.11"}),
        ):
            status, saved = self.request(f"/api/classes/{class_id}/people", "POST", {**person, "role": role})
            self.assertEqual(status, 201)
            self.assertEqual(saved["role"], role)
        status, classes = self.request("/api/classes")
        self.assertEqual(status, 200)
        self.assertEqual(classes[0]["name"], "Biology 101")
        self.assertEqual({person["role"] for person in classes[0]["people"]}, {"instructor", "student"})

    def test_credentials_are_masked_unless_explicitly_revealed(self):
        status, _ = self.request("/api/ansible/credentials", "POST", {"username": "root", "password": "lab-secret"})
        self.assertEqual(status, 200)
        _, hidden = self.request("/api/ansible/credentials")
        _, revealed = self.request("/api/ansible/credentials?reveal=1")
        self.assertEqual(hidden["username"], "root")
        self.assertEqual(hidden["password"], "••••••••")
        self.assertNotIn("lab-secret", json.dumps(hidden))
        self.assertEqual(revealed["password"], "lab-secret")

    def test_run_command_uses_only_students_and_requires_confirmation(self):
        _, room = self.request("/api/classes", "POST", {"name": "Lab"})
        class_id = room["id"]
        for role, username, ip in (("student", "s1", "10.0.0.11"), ("instructor", "teacher", "10.0.0.10")):
            self.request(f"/api/classes/{class_id}/people", "POST", {
                "role": role, "name": username, "username": username, "ip_address": ip
            })
        self.request("/api/ansible/credentials", "POST", {"username": "root", "password": "lab-secret"})
        status, _ = self.request(f"/api/classes/{class_id}/run", "POST", {"command": "uname -a"})
        self.assertEqual(status, 400)
        self.assertEqual(self.jobs, [])
        status, result = self.request(f"/api/classes/{class_id}/run", "POST", {
            "command": "uname -a", "confirmed": True
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        job, password = self.jobs[0]
        self.assertEqual(password, "lab-secret")
        self.assertEqual(job["mode"], "command")
        self.assertEqual([host["ip_address"] for host in job["hosts"]], ["10.0.0.11"])
        self.assertEqual(job["hosts"][0]["username"], "root")

    def test_seed_action_records_success_only_after_playbook_succeeds(self):
        _, room = self.request("/api/classes", "POST", {"name": "Lab"})
        class_id = room["id"]
        _, student = self.request(f"/api/classes/{class_id}/people", "POST", {
            "role": "student", "name": "Student", "username": "s1", "ip_address": "10.0.0.11"
        })
        self.request("/api/ansible/credentials", "POST", {"username": "root", "password": "lab-secret"})
        status, result = self.request(f"/api/classes/{class_id}/seed", "POST", {
            "playbook_path": "/home/teacher/seed.yml", "confirmed": True
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        job, _ = self.jobs[0]
        self.assertEqual(job["mode"], "playbook")
        self.assertEqual(job["playbook_path"], "/home/teacher/seed.yml")
        _, classrooms = self.request("/api/classes")
        saved_student = classrooms[0]["people"][0]
        self.assertEqual(saved_student["id"], student["id"])
        self.assertTrue(saved_student["seeded"])

    def test_failed_seed_playbook_does_not_mark_students_seeded(self):
        _, room = self.request("/api/classes", "POST", {"name": "Lab"})
        class_id = room["id"]
        self.request(f"/api/classes/{class_id}/people", "POST", {
            "role": "student", "name": "Student", "username": "s1", "ip_address": "10.0.0.11"
        })
        self.request("/api/ansible/credentials", "POST", {"username": "root", "password": "lab-secret"})
        self.fail_jobs = True
        status, result = self.request(f"/api/classes/{class_id}/seed", "POST", {
            "playbook_path": "/home/teacher/seed.yml", "confirmed": True
        })
        self.assertEqual(status, 502)
        self.assertFalse(result["ok"])
        _, classrooms = self.request("/api/classes")
        self.assertFalse(classrooms[0]["people"][0]["seeded"])

    def test_seed_test_marks_all_students_without_running_ansible(self):
        _, room = self.request("/api/classes", "POST", {"name": "Lab"})
        class_id = room["id"]
        for role, username, ip in (("student", "s1", "10.0.0.11"), ("student", "s2", "10.0.0.12"), ("instructor", "teacher", "10.0.0.10")):
            self.request(f"/api/classes/{class_id}/people", "POST", {
                "role": role, "name": username, "username": username, "ip_address": ip
            })
        status, result = self.request(f"/api/classes/{class_id}/seed-test", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["seeded_count"], 2)
        self.assertEqual(self.jobs, [])
        _, classrooms = self.request("/api/classes")
        people = classrooms[0]["people"]
        self.assertEqual(sum(person["seeded"] for person in people), 2)
        self.assertFalse(next(person for person in people if person["role"] == "instructor")["seeded"])

    def test_individual_seed_test_marks_only_selected_student(self):
        _, room = self.request("/api/classes", "POST", {"name": "Lab"})
        student_ids = []
        for username, ip in (("s1", "10.0.0.11"), ("s2", "10.0.0.12")):
            _, person = self.request(f"/api/classes/{room['id']}/people", "POST", {
                "role": "student", "name": username, "username": username, "ip_address": ip
            })
            student_ids.append(person["id"])
        _, instructor = self.request(f"/api/classes/{room['id']}/people", "POST", {
            "role": "instructor", "name": "Teacher", "username": "t1", "ip_address": "10.0.0.10"
        })
        status, _ = self.request(f"/api/people/{instructor['id']}/seed-test", "POST", {})
        self.assertEqual(status, 404)
        status, result = self.request(f"/api/people/{student_ids[0]}/seed-test", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["seeded"], True)
        _, classrooms = self.request("/api/classes")
        seeded = {person["id"] for person in classrooms[0]["people"] if person["seeded"]}
        self.assertEqual(seeded, {student_ids[0]})
        self.assertEqual(self.jobs, [])

    def test_homepage_has_accessible_theme_toggle(self):
        with urllib.request.urlopen(self.base + "/") as response:
            markup = response.read().decode()
        self.assertIn('id="theme-toggle"', markup)
        self.assertIn('aria-label="Switch to dark mode"', markup)
        self.assertIn('id="credentials-open"', markup)
        self.assertIn('id="job-dialog"', markup)
        with urllib.request.urlopen(self.base + "/app.js") as response:
            script = response.read().decode()
        with urllib.request.urlopen(self.base + "/styles.css") as response:
            styles = response.read().decode()
        self.assertIn("localStorage.setItem('classroom-theme'", script)
        self.assertIn('data-seed-class', script)
        self.assertIn('data-run-command', script)
        self.assertIn("'seed' : 'run'", script)
        self.assertIn(':root[data-theme="dark"]', styles)
        self.assertIn('.seed-indicator', styles)
        self.assertIn('id="class-list"', markup)
        self.assertIn('id="roster-dialog"', markup)
        self.assertIn('data-class-open', script)
        self.assertIn('data-seed-class', script)
        self.assertIn('data-run-command', script)
        self.assertIn('data-seed-test-class', script)
        self.assertIn('data-seed-person', script)
        self.assertIn('＋ Add participant', script)
        self.assertNotIn('course-mark', script)
        self.assertIn('data-roster-add', markup)
        self.assertNotIn('data-roster-add="student"', markup)
        self.assertIn('Environment not seeded', script)
        self.assertIn('function renderRoster(room)', script)
        self.assertNotIn('<div class="card-body">', script)
        self.assertIn('.class-grid{', styles)
        self.assertIn('overflow-y:auto', styles)
        self.assertIn('grid-auto-rows:255px', styles)
        self.assertIn('height:255px', styles)
        self.assertIn('max-height:min(540px,68vh)', styles)
        self.assertIn('/styles.css', markup)

    def test_class_data_persists_when_server_is_reopened(self):
        _, created = self.request("/api/classes", "POST", {"name": "History"})
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(self.db_path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, classes = self.request("/api/classes")
        self.assertEqual(classes[0]["id"], created["id"])
        self.assertEqual(classes[0]["name"], "History")

    def test_rejects_missing_class_name_and_invalid_role(self):
        status, _ = self.request("/api/classes", "POST", {"name": " "})
        self.assertEqual(status, 400)
        _, created = self.request("/api/classes", "POST", {"name": "Chemistry"})
        status, _ = self.request(f"/api/classes/{created['id']}/people", "POST", {
            "role": "observer", "name": "Pat", "username": "pat", "ip_address": "127.0.0.1"
        })
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
