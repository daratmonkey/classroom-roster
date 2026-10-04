const classList = document.querySelector('#class-list');
const classDialog = document.querySelector('#class-dialog');
const personDialog = document.querySelector('#person-dialog');
const classForm = document.querySelector('#class-form');
const personForm = document.querySelector('#person-form');
let classes = [];
let selectedClass = null;
let selectedRole = 'student';
let activeRosterClass = null;
let toastTimer;
const palette = [
  ['#f0edff', '#7566da'], ['#e6f7ef', '#48a77d'], ['#fff0e8', '#d98e65'],
  ['#e9f3ff', '#568cc8'], ['#fff5dc', '#c69844'], ['#fdebf2', '#cc7898']
];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || 'Something went wrong.');
    error.payload = data;
    throw error;
  }
  return data;
}
function esc(value) {
  return String(value).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}
function initials(name) {
  return name.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();
}
function notify(message) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}
function renderPerson(person) {
  const isStudent = person.role === 'student';
  return `<div class="person-row ${isStudent ? 'student-row' : ''}">
    <span class="person-avatar">${esc(initials(person.name))}</span>
    <span class="person-info"><span class="person-name">${esc(person.name)}</span><span class="person-user">@${esc(person.username)}</span></span>
    <span class="person-ip">${esc(person.ip_address)}</span>
    ${isStudent ? `<button class="seed-person ${person.seeded ? 'is-seeded' : ''}" data-seed-person="${person.id}" ${person.seeded ? 'disabled' : ''} title="${person.seeded ? 'Environment marked seeded' : 'Test: mark this student seeded'}">${person.seeded ? '✓ Seeded' : 'Seed'}</button>` : ''}
    <button class="remove-person" data-delete-person="${person.id}" title="Remove ${esc(person.name)}" aria-label="Remove ${esc(person.name)}">×</button>
  </div>`;
}
function renderClass(room) {
  const students = room.people.filter(p => p.role === 'student');
  const instructors = room.people.filter(p => p.role === 'instructor');
  const seededCount = students.filter(person => person.seeded).length;
  const seeded = students.length > 0 && seededCount === students.length;
  return `<article class="class-card" data-class-card="${room.id}" data-class-open="${room.id}" tabindex="0" aria-label="Open ${esc(room.name)} roster">
    <div class="card-head"><div><button class="class-title-button" type="button" data-open-roster="${room.id}" aria-label="Open ${esc(room.name)} roster"><span class="class-name">${esc(room.name)}</span></button><div class="card-meta">${students.length} student${students.length === 1 ? '' : 's'} · ${instructors.length} instructor${instructors.length === 1 ? '' : 's'}</div><div class="class-seed-status"><span class="seed-indicator ${seeded ? 'is-seeded' : ''}" title="${seeded ? 'Environment seeded' : 'Environment not seeded'}" aria-label="${seeded ? 'Environment seeded' : 'Environment not seeded'}"></span>${seeded ? 'Environment seeded' : 'Environment not seeded'}${seededCount && !seeded ? ` (${seededCount}/${students.length})` : ''}</div></div>
    <div class="card-tools"><button class="icon-button" data-delete-class="${room.id}" title="Delete class" aria-label="Delete ${esc(room.name)}">⌫</button></div></div>
    <div class="card-foot"><button class="add-person" data-seed-class="${room.id}">✦ Seed environment</button><button class="add-person" data-seed-test-class="${room.id}">✓ Seed test</button><button class="add-person" data-run-command="${room.id}">› Run command</button><button class="add-person" data-add-person="${room.id}">＋ Add participant</button></div>
  </article>`;
}
function renderRoster(room) {
  const instructors = room.people.filter(person => person.role === 'instructor');
  const students = room.people.filter(person => person.role === 'student');
  const section = (people, role) => `<section class="roster-section"><h3>${role === 'student' ? 'Students' : 'Instructors'} <span class="role-count">${people.length}</span></h3>${people.length ? people.map(renderPerson).join('') : `<div class="empty-role">No ${role}s added yet.</div>`}</section>`;
  document.querySelector('#roster-title').textContent = room.name;
  document.querySelector('#roster-content').innerHTML = `${section(students, 'student')}${section(instructors, 'instructor')}`;
}
function openRoster(classId) {
  const room = classes.find(item => item.id === classId);
  if (!room) return;
  activeRosterClass = classId;
  renderRoster(room);
  document.querySelector('#roster-dialog').showModal();
}
document.querySelector('#roster-dialog').addEventListener('close', () => { activeRosterClass = null; });
function updateStats() {
  const instructors = classes.reduce((n, c) => n + c.people.filter(p => p.role === 'instructor').length, 0);
  const students = classes.reduce((n, c) => n + c.people.filter(p => p.role === 'student').length, 0);
  document.querySelector('#stat-classes').textContent = classes.length;
  document.querySelector('#stat-instructors').textContent = instructors;
  document.querySelector('#stat-students').textContent = students;
  document.querySelector('#class-count').textContent = classes.length;
  document.querySelector('#side-count').textContent = classes.length;
}
function render() {
  updateStats();
  const query = document.querySelector('#search').value.trim().toLowerCase();
  const filtered = classes.filter(room => !query || room.name.toLowerCase().includes(query) || room.people.some(p => `${p.name} ${p.username} ${p.ip_address}`.toLowerCase().includes(query)));
  classList.innerHTML = filtered.map(room => renderClass(room, classes.indexOf(room))).join('');
  document.querySelector('#empty-state').hidden = classes.length > 0 || query.length > 0;
  classList.hidden = classes.length === 0 || filtered.length === 0;
  if (classes.length && !filtered.length) {
    classList.hidden = false;
    classList.innerHTML = '<p class="no-results">No classes or people match your search.</p>';
  }
}
async function refresh() {
  classes = await api('/api/classes');
  render();
  if (activeRosterClass !== null && document.querySelector('#roster-dialog').open) {
    const room = classes.find(item => item.id === activeRosterClass);
    if (room) renderRoster(room);
    else document.querySelector('#roster-dialog').close();
  }
}
function openClassDialog() {
  classForm.reset();
  classDialog.showModal();
  setTimeout(() => document.querySelector('#class-name').focus(), 0);
}
function openPersonDialog(classId, role = 'student') {
  selectedClass = classId;
  selectedRole = role;
  personForm.reset();
  document.querySelector('#form-error').textContent = '';
  const room = classes.find(c => c.id === classId);
  document.querySelector('#person-title').textContent = `Add to ${room?.name || 'class'}`;
  setRole(role);
  personDialog.showModal();
  setTimeout(() => document.querySelector('#person-name').focus(), 0);
}
function setRole(role) {
  selectedRole = role;
  document.querySelectorAll('.role-choice').forEach(button => button.classList.toggle('selected', button.dataset.role === role));
}
const themeToggle = document.querySelector('#theme-toggle');
function applyTheme(theme) {
  const isDark = theme === 'dark';
  document.documentElement.dataset.theme = isDark ? 'dark' : 'light';
  themeToggle.setAttribute('aria-pressed', String(isDark));
  themeToggle.setAttribute('aria-label', `Switch to ${isDark ? 'light' : 'dark'} mode`);
  themeToggle.title = `Switch to ${isDark ? 'light' : 'dark'} mode`;
  document.querySelector('#theme-icon').textContent = isDark ? '☼' : '☾';
  document.querySelector('#theme-label').textContent = isDark ? 'Light mode' : 'Dark mode';
  document.querySelector('meta[name="theme-color"]').content = isDark ? '#131522' : '#f5f7fb';
}
applyTheme(document.documentElement.dataset.theme || 'light');
themeToggle.addEventListener('click', () => {
  const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(nextTheme);
  try { localStorage.setItem('classroom-theme', nextTheme); } catch (_) { /* Theme still works for this visit. */ }
});
let activeJob = null;
async function openCredentials() {
  const passwordField = document.querySelector('#ssh-password');
  passwordField.value = '';
  passwordField.type = 'password';
  document.querySelector('#credential-error').textContent = '';
  document.querySelector('#reveal-password').textContent = 'Reveal saved';
  const note = document.querySelector('#credential-note');
  try {
    const current = await api('/api/ansible/credentials');
    note.textContent = current.configured
      ? 'A password is saved in Windows Credential Manager. Reveal it or enter a replacement.'
      : 'No password saved yet.';
    document.querySelector('#reveal-password').disabled = !current.configured;
  } catch (error) { note.textContent = error.message; }
  document.querySelector('#credentials-dialog').showModal();
}
function openJobDialog(classId, mode) {
  const room = classes.find(item => item.id === classId);
  const students = room?.people.filter(person => person.role === 'student') || [];
  if (!students.length) { notify('Add a student before running a class job.'); return; }
  activeJob = { classId, mode };
  const isSeed = mode === 'playbook';
  document.querySelector('#job-form').reset();
  document.querySelector('#job-error').textContent = '';
  document.querySelector('#job-output').hidden = true;
  document.querySelector('#job-output').textContent = '';
  document.querySelector('#job-title').textContent = isSeed ? 'Seed environment' : 'Run command';
  document.querySelector('#job-copy').textContent = isSeed
    ? `Run an Ansible playbook on every student in ${room.name}. The seed indicator updates only after a successful playbook run.`
    : `Run one command on every student in ${room.name} using Ansible's command module.`;
  document.querySelector('#job-targets').textContent = `${students.length} student${students.length === 1 ? '' : 's'} · SSH user root · ${students.map(person => person.ip_address).join(', ')}`;
  document.querySelector('#playbook-field').hidden = !isSeed;
  document.querySelector('#command-field').hidden = isSeed;
  document.querySelector('#playbook-path').required = isSeed;
  document.querySelector('#command-text').required = !isSeed;
  document.querySelector('#job-submit').textContent = isSeed ? 'Run seed playbook' : 'Run command on students';
  document.querySelector('#confirm-label').textContent = `I understand this will run as root on ${students.length} student machine${students.length === 1 ? '' : 's'}.`;
  document.querySelector('#job-dialog').showModal();
}
document.querySelector('#credentials-open').addEventListener('click', openCredentials);
document.querySelector('#reveal-password').addEventListener('click', async event => {
  const field = document.querySelector('#ssh-password');
  const button = event.currentTarget;
  if (field.type === 'text') {
    field.type = 'password';
    button.textContent = 'Reveal saved';
    return;
  }
  try {
    const saved = await api('/api/ansible/credentials?reveal=1');
    field.value = saved.password;
    field.type = 'text';
    button.textContent = 'Hide password';
  } catch (error) { document.querySelector('#credential-error').textContent = error.message; }
});
document.querySelector('#credentials-form').addEventListener('submit', async event => {
  event.preventDefault();
  const errorBox = document.querySelector('#credential-error');
  errorBox.textContent = '';
  try {
    await api('/api/ansible/credentials', { method: 'POST', body: JSON.stringify({
      username: 'root', password: document.querySelector('#ssh-password').value
    }) });
    document.querySelector('#ssh-password').value = '';
    document.querySelector('#ssh-password').type = 'password';
    document.querySelector('#credential-note').textContent = 'Default root password saved in Windows Credential Manager.';
    document.querySelector('#reveal-password').disabled = false;
    notify('Credentials saved securely.');
  } catch (error) { errorBox.textContent = error.message; }
});
document.querySelector('#credentials-dialog').addEventListener('close', () => {
  document.querySelector('#ssh-password').value = '';
  document.querySelector('#ssh-password').type = 'password';
  document.querySelector('#reveal-password').textContent = 'Reveal saved';
});
document.querySelector('#job-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!activeJob) return;
  const errorBox = document.querySelector('#job-error');
  const output = document.querySelector('#job-output');
  const submit = document.querySelector('#job-submit');
  errorBox.textContent = '';
  output.hidden = true;
  const payload = { confirmed: document.querySelector('#job-confirm').checked };
  if (activeJob.mode === 'playbook') payload.playbook_path = document.querySelector('#playbook-path').value.trim();
  else payload.command = document.querySelector('#command-text').value.trim();
  submit.disabled = true;
  submit.textContent = 'Running…';
  try {
    const action = activeJob.mode === 'playbook' ? 'seed' : 'run';
    const result = await api(`/api/classes/${activeJob.classId}/${action}`, { method: 'POST', body: JSON.stringify(payload) });
    await refresh();
    output.textContent = result.stdout || `Completed on ${result.target_count} student host${result.target_count === 1 ? '' : 's'}.`;
    if (result.stderr) output.textContent += `\n${result.stderr}`;
    output.hidden = false;
    notify(activeJob.mode === 'playbook' ? 'Seed playbook completed.' : 'Command completed.');
  } catch (error) {
    errorBox.textContent = error.message;
    const detail = error.payload && [error.payload.stdout, error.payload.stderr].filter(Boolean).join('\n');
    if (detail) { output.textContent = detail; output.hidden = false; }
  } finally {
    submit.disabled = false;
    submit.textContent = activeJob?.mode === 'playbook' ? 'Run seed playbook' : 'Run command on students';
  }
});
document.querySelector('#new-class').addEventListener('click', openClassDialog);
document.querySelector('#empty-add').addEventListener('click', openClassDialog);
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.querySelectorAll('.role-choice').forEach(button => button.addEventListener('click', () => setRole(button.dataset.role)));
document.querySelector('#search').addEventListener('input', render);
classForm.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    await api('/api/classes', { method: 'POST', body: JSON.stringify({ name: document.querySelector('#class-name').value }) });
    classDialog.close();
    await refresh();
    notify('Class created.');
  } catch (error) { notify(error.message); }
});
personForm.addEventListener('submit', async event => {
  event.preventDefault();
  const payload = {
    role: selectedRole,
    name: document.querySelector('#person-name').value,
    username: document.querySelector('#person-username').value,
    ip_address: document.querySelector('#person-ip').value
  };
  try {
    await api(`/api/classes/${selectedClass}/people`, { method: 'POST', body: JSON.stringify(payload) });
    personDialog.close();
    await refresh();
    notify(`${selectedRole === 'student' ? 'Student' : 'Instructor'} added.`);
  } catch (error) { document.querySelector('#form-error').textContent = error.message; }
});
classList.addEventListener('click', async event => {
  const add = event.target.closest('[data-add-person]');
  const seed = event.target.closest('[data-seed-class]');
  const seedTest = event.target.closest('[data-seed-test-class]');
  const runCommand = event.target.closest('[data-run-command]');
  const openButton = event.target.closest('[data-open-roster]');
  const removePerson = event.target.closest('[data-delete-person]');
  const removeClass = event.target.closest('[data-delete-class]');
  const card = event.target.closest('[data-class-card]');
  try {
    if (add) openPersonDialog(Number(add.dataset.addPerson));
    else if (seed) openJobDialog(Number(seed.dataset.seedClass), 'playbook');
    else if (seedTest) {
      const result = await api(`/api/classes/${seedTest.dataset.seedTestClass}/seed-test`, { method: 'POST', body: '{}' });
      await refresh();
      notify(`Test marked ${result.seeded_count} student environment${result.seeded_count === 1 ? '' : 's'} seeded.`);
    }
    else if (runCommand) openJobDialog(Number(runCommand.dataset.runCommand), 'command');
    else if (openButton) openRoster(Number(openButton.dataset.openRoster));
    else if (removePerson && confirm('Remove this person from the class?')) {
      await api(`/api/people/${removePerson.dataset.deletePerson}`, { method: 'DELETE' });
      await refresh();
      notify('Person removed.');
    }
    else if (removeClass && confirm('Delete this class and everyone in its roster? This cannot be undone.')) {
      await api(`/api/classes/${removeClass.dataset.deleteClass}`, { method: 'DELETE' });
      await refresh();
      notify('Class deleted.');
    }
    else if (card && !event.target.closest('button')) openRoster(Number(card.dataset.classOpen));
  } catch (error) { notify(error.message); }
});
document.querySelector('#roster-content').addEventListener('click', async event => {
  const seedPerson = event.target.closest('[data-seed-person]');
  const removePerson = event.target.closest('[data-delete-person]');
  try {
    if (seedPerson) {
      const result = await api(`/api/people/${seedPerson.dataset.seedPerson}/seed-test`, { method: 'POST', body: '{}' });
      await refresh();
      notify('Test marked this student environment seeded.');
    } else if (removePerson && confirm('Remove this person from the class?')) {
      await api(`/api/people/${removePerson.dataset.deletePerson}`, { method: 'DELETE' });
      await refresh();
      notify('Person removed.');
    }
  } catch (error) { notify(error.message); }
});
document.querySelectorAll('[data-roster-add]').forEach(button => button.addEventListener('click', () => {
  if (activeRosterClass !== null) openPersonDialog(activeRosterClass, 'student');
}));
classList.addEventListener('keydown', event => {
  const card = event.target.closest('[data-class-card]');
  if (!card || event.target !== card || !['Enter', ' '].includes(event.key)) return;
  event.preventDefault();
  openRoster(Number(card.dataset.classOpen));
});
refresh().catch(error => {
  classList.innerHTML = `<p class="load-error">Couldn't load your local roster: ${esc(error.message)}. Restart the app server and refresh.</p>`;
  classList.hidden = false;
});
