"""
Cybersecurity Scenario Portal - Flask Backend
"""

import os
import json
import uuid
import secrets
import tempfile
from datetime import datetime, timezone
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import duel as duel_engine

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(32)

# Initialise SocketIO with threading async mode (compatible with Python 3.13+)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SCENARIOS_DIR  = os.path.join(BASE_DIR, 'scenarios')
DATA_DIR       = os.path.join(BASE_DIR, 'data')
USERS_FILE     = os.path.join(DATA_DIR, 'users.json')
ATTEMPTS_FILE  = os.path.join(DATA_DIR, 'attempts.json')

ALLOWED_EXTENSIONS = {'json'}

# ── In-memory SID tracking ────────────────────────────────────────────────────
sid_to_game  = {}   # sid -> lobby_id (game rooms)
sid_to_lobby = {}   # sid -> lobby_id (lobby rooms)

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path):
    """Read and return parsed JSON from *path*."""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            content = fh.read().strip()
            if not content:
                return [] if path == ATTEMPTS_FILE else {}
            return json.loads(content)
    except FileNotFoundError:
        raise RuntimeError(f"Required data file not found: {path}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupted data file {path}: {exc}")


def save_json(path, data):
    """Serialise *data* to *path* atomically (write temp then rename)."""
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_settings():
    """Load users.json and return the settings dict with defaults."""
    try:
        users = load_json(USERS_FILE)
    except RuntimeError:
        return {'signup_enabled': True, 'duel_enabled': True, 'disabled_scenarios': []}
    settings = users.get('settings', {})
    settings.setdefault('signup_enabled', True)
    settings.setdefault('duel_enabled', True)
    settings.setdefault('disabled_scenarios', [])
    return settings


def get_student(entry):
    """Return a normalised student dict regardless of old (str) or new (dict) format."""
    if isinstance(entry, str):
        return {'username': entry, 'name': entry.title(), 'email': '', 'password_hash': ''}
    return entry


def load_scenarios():
    """
    Walk the scenarios/ directory and return a dict keyed by scenario id.
    Every .json file whose top-level 'id' field matches the file stem is loaded.
    """
    scenarios = {}
    if not os.path.isdir(SCENARIOS_DIR):
        return scenarios
    for fname in os.listdir(SCENARIOS_DIR):
        if fname.endswith('.json'):
            path = os.path.join(SCENARIOS_DIR, fname)
            try:
                data = load_json(path)
                scenarios[data['id']] = data
            except (KeyError, RuntimeError) as e:
                app.logger.warning("Skipping malformed scenario file %s: %s", fname, e)
    return scenarios


def get_phase(scenario, phase_id):
    """Return the phase dict with *phase_id* from *scenario*, or None."""
    for phase in scenario.get('phases', []):
        if phase['id'] == phase_id:
            return phase
    return None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# ── Security headers ──────────────────────────────────────────────────────────

@app.after_request
def set_security_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self' ws: wss:;"
    )
    return response


# ── Auth decorators ──────────────────────────────────────────────────────────

def login_required(f):
    """Redirect to login if the user is not in session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in first.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def teacher_required(f):
    """Redirect unless the session role is 'teacher'."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'teacher':
            flash('Teacher access required.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def login():
    """Login page — tabs: student login, teacher login, register."""
    if 'username' in session:
        if session.get('role') == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        return redirect(url_for('dashboard'))

    settings = get_settings()

    if request.method == 'POST':
        login_type = request.form.get('login_type', 'student')
        username   = request.form.get('username', '').strip().lower()

        if not username:
            flash('Username cannot be empty.', 'danger')
            return render_template('login.html', signup_enabled=settings['signup_enabled'])

        users = load_json(USERS_FILE)

        if login_type == 'teacher':
            password = request.form.get('password', '')
            teacher = next(
                (t for t in users.get('teachers', []) if t['username'] == username),
                None
            )
            if teacher and check_password_hash(teacher.get('password_hash', ''), password):
                session['username'] = username
                session['role']     = 'teacher'
                return redirect(url_for('teacher_dashboard'))
            else:
                flash('Invalid teacher credentials.', 'danger')
                return render_template('login.html', signup_enabled=settings['signup_enabled'])

        else:  # student login
            # Find student by username or email
            student_entry = None
            for s in users.get('students', []):
                sd = get_student(s)
                if sd['username'] == username or (sd.get('email') and sd['email'] == username):
                    student_entry = sd
                    break

            if student_entry is None:
                # Legacy auto-create (no password, username-only)
                new_student = {
                    'username':      username,
                    'name':          username.title(),
                    'email':         '',
                    'password_hash': '',
                }
                users.setdefault('students', []).append(new_student)
                save_json(USERS_FILE, users)
                session['username'] = username
                session['role']     = 'student'
                return redirect(url_for('dashboard'))

            # If student has a password hash, require password
            if student_entry.get('password_hash'):
                password = request.form.get('password', '')
                if not password:
                    flash('Password required for this account.', 'danger')
                    return render_template('login.html', signup_enabled=settings['signup_enabled'])
                if not check_password_hash(student_entry['password_hash'], password):
                    flash('Invalid password.', 'danger')
                    return render_template('login.html', signup_enabled=settings['signup_enabled'])

            session['username'] = student_entry['username']
            session['role']     = 'student'
            return redirect(url_for('dashboard'))

    return render_template('login.html', signup_enabled=settings['signup_enabled'])


@app.route('/register', methods=['POST'])
def register():
    """Register a new student account."""
    settings = get_settings()
    if not settings.get('signup_enabled', True):
        flash('Registration is currently disabled.', 'danger')
        return redirect(url_for('login'))

    username = request.form.get('username', '').strip().lower()
    name     = request.form.get('name', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    confirm  = request.form.get('confirm_password', '')

    errors = []
    if not username:
        errors.append('Username is required.')
    if not name:
        errors.append('Name is required.')
    if not email:
        errors.append('Email is required.')
    if len(password) < 6:
        errors.append('Password must be at least 6 characters.')
    if password != confirm:
        errors.append('Passwords do not match.')

    if errors:
        for e in errors:
            flash(e, 'danger')
        return redirect(url_for('login'))

    users = load_json(USERS_FILE)
    for s in users.get('students', []):
        sd = get_student(s)
        if sd['username'] == username:
            flash('Username already taken.', 'danger')
            return redirect(url_for('login'))
        if sd.get('email') and sd['email'] == email:
            flash('Email already registered.', 'danger')
            return redirect(url_for('login'))

    new_student = {
        'username':      username,
        'name':          name,
        'email':         email,
        'password_hash': generate_password_hash(password, method='pbkdf2:sha256'),
    }
    users.setdefault('students', []).append(new_student)
    save_json(USERS_FILE, users)

    session['username'] = username
    session['role']     = 'student'
    flash('Account created successfully!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/logout')
def logout():
    """Clear session and redirect to login."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    """Student dashboard — lists available scenarios and recent attempts."""
    settings  = get_settings()
    scenarios = load_scenarios()
    disabled  = settings.get('disabled_scenarios', [])
    visible   = {k: v for k, v in scenarios.items() if k not in disabled}

    try:
        attempts = load_json(ATTEMPTS_FILE)
    except RuntimeError:
        attempts = []
    my_attempts = sorted(
        [a for a in attempts if a.get('username') == session['username']],
        key=lambda a: a.get('timestamp', ''),
        reverse=True
    )[:10]
    return render_template(
        'dashboard.html',
        scenarios=list(visible.values()),
        attempts=my_attempts,
        duel_enabled=settings.get('duel_enabled', True),
    )


@app.route('/scenarios')
@login_required
def scenarios_view():
    """Redirect to dashboard#scenarios anchor."""
    return redirect(url_for('dashboard') + '#scenarios')


@app.route('/scenario/<scenario_id>')
@login_required
def scenario_view(scenario_id):
    """Render the interactive scenario page."""
    settings = get_settings()
    if scenario_id in settings.get('disabled_scenarios', []):
        flash('This scenario is currently disabled.', 'warning')
        return redirect(url_for('dashboard'))

    scenarios = load_scenarios()
    scenario  = scenarios.get(scenario_id)
    if not scenario:
        flash('Scenario not found.', 'danger')
        return redirect(url_for('dashboard'))

    scenario_json = json.dumps(scenario)
    return render_template('scenario.html', scenario=scenario, scenario_json=scenario_json)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route('/api/submit_choice', methods=['POST'])
@login_required
def api_submit_choice():
    """
    Receive a student's choice and return the outcome + next-phase data.

    Body: { scenario_id, phase_id, choice_id }
    Returns: { outcome, score_impact, is_correct, next_phase_id,
               next_phase, is_complete }
    """
    data        = request.get_json(force=True)
    scenario_id = data.get('scenario_id')
    phase_id    = data.get('phase_id')
    choice_id   = data.get('choice_id')

    scenarios = load_scenarios()
    scenario  = scenarios.get(scenario_id)
    if not scenario:
        return jsonify({'error': 'Scenario not found'}), 404

    phase = get_phase(scenario, phase_id)
    if not phase:
        return jsonify({'error': 'Phase not found'}), 404

    choice = next((c for c in phase.get('choices', []) if c['id'] == choice_id), None)
    if not choice:
        return jsonify({'error': 'Choice not found'}), 404

    next_phase_id = choice.get('next_phase')
    next_phase    = get_phase(scenario, next_phase_id) if next_phase_id else None
    is_complete   = (not next_phase) or (not next_phase.get('choices'))

    return jsonify({
        'outcome':       choice['outcome'],
        'score_impact':  choice['score_impact'],
        'is_correct':    choice['is_correct'],
        'next_phase_id': next_phase_id,
        'next_phase':    next_phase,
        'is_complete':   is_complete,
    })


@app.route('/api/save_attempt', methods=['POST'])
@login_required
def api_save_attempt():
    """
    Persist a completed scenario attempt.

    Body: { scenario_id, decisions: [{phase_id, choice_id, score_impact}],
            total_score, time_taken }
    """
    data        = request.get_json(force=True)
    scenario_id = data.get('scenario_id')

    # Validate that the scenario exists
    scenarios = load_scenarios()
    if scenario_id not in scenarios:
        return jsonify({'error': 'Invalid scenario_id'}), 400

    attempt = {
        'id':             str(uuid.uuid4()),
        'username':       session['username'],
        'scenario_id':    scenario_id,
        'decisions':      data.get('decisions', []),
        'total_score':    data.get('total_score', 0),
        'time_taken':     data.get('time_taken', 0),
        'hints_used':     data.get('hints_used', 0),
        'longest_streak': data.get('longest_streak', 0),
        'timestamp':      datetime.now(timezone.utc).isoformat(),
    }

    try:
        attempts = load_json(ATTEMPTS_FILE)
    except RuntimeError:
        attempts = []
    attempts.append(attempt)
    save_json(ATTEMPTS_FILE, attempts)

    return jsonify({'status': 'ok', 'attempt_id': attempt['id']})


# ── Teacher routes ────────────────────────────────────────────────────────────

@app.route('/teacher')
@login_required
@teacher_required
def teacher_dashboard():
    """Teacher dashboard — view attempts, manage users, scenarios, settings."""
    scenarios = load_scenarios()
    try:
        attempts = load_json(ATTEMPTS_FILE)
    except RuntimeError:
        attempts = []
    users    = load_json(USERS_FILE)
    settings = get_settings()

    students     = [get_student(s) for s in users.get('students', [])]
    scenario_ids = list(scenarios.keys())

    # Build gradebook: {username: {scenario_id: best_score}}
    gradebook = {st['username']: {} for st in students}
    for attempt in attempts:
        if attempt.get('duel'):
            continue  # gradebook tracks individual scenario performance only, not competitive duels
        uname = attempt.get('username', '')
        sid   = attempt.get('scenario_id', '')
        score = attempt.get('total_score', 0)
        if uname in gradebook and sid in scenario_ids:
            if score > gradebook[uname].get(sid, -1):
                gradebook[uname][sid] = score

    grouped = {}
    for attempt in sorted(attempts, key=lambda a: a.get('timestamp', ''), reverse=True):
        uname = attempt.get('username', 'unknown')
        grouped.setdefault(uname, []).append(attempt)

    return render_template(
        'teacher.html',
        scenarios=scenarios,
        scenario_list=list(scenarios.values()),
        grouped_attempts=grouped,
        students=students,
        all_attempts=attempts,
        gradebook=gradebook,
        scenario_ids=scenario_ids,
        settings=settings,
    )


@app.route('/teacher/upload', methods=['POST'])
@login_required
@teacher_required
def teacher_upload():
    """Accept a JSON scenario file upload and save it to scenarios/."""
    if 'scenario_file' not in request.files:
        flash('No file part in the request.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    f = request.files['scenario_file']
    if f.filename == '':
        flash('No file selected.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    if f and allowed_file(f.filename):
        try:
            content       = f.read()
            scenario_data = json.loads(content)
            if 'id' not in scenario_data or 'phases' not in scenario_data:
                flash('Invalid scenario JSON: missing "id" or "phases".', 'danger')
                return redirect(url_for('teacher_dashboard'))
            # Use os.path.basename + secure_filename to prevent path traversal
            safe_name = os.path.basename(secure_filename(scenario_data['id'] + '.json'))
            save_path = os.path.join(SCENARIOS_DIR, safe_name)
            with open(save_path, 'w', encoding='utf-8') as fh:
                fh.write(content.decode('utf-8'))
            flash(f'Scenario "{scenario_data.get("title", safe_name)}" uploaded successfully.', 'success')
        except (json.JSONDecodeError, UnicodeDecodeError):
            flash('File is not valid JSON.', 'danger')
    else:
        flash('Only .json files are allowed.', 'danger')

    return redirect(url_for('teacher_dashboard'))


@app.route('/teacher/reset', methods=['POST'])
@login_required
@teacher_required
def teacher_reset():
    """Remove all attempts for a given student username."""
    data     = request.get_json(force=True) or {}
    username = data.get('username', '').strip()

    if not username:
        return jsonify({'error': 'username required'}), 400

    try:
        attempts = load_json(ATTEMPTS_FILE)
    except RuntimeError:
        attempts = []
    filtered = [a for a in attempts if a.get('username') != username]
    save_json(ATTEMPTS_FILE, filtered)

    removed = len(attempts) - len(filtered)
    return jsonify({'status': 'ok', 'removed': removed})


@app.route('/teacher/add_user', methods=['POST'])
@login_required
@teacher_required
def teacher_add_user():
    """Add a new student account."""
    username = request.form.get('username', '').strip().lower()
    name     = request.form.get('name', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()

    if not username:
        flash('Username is required.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    users = load_json(USERS_FILE)
    for s in users.get('students', []):
        sd = get_student(s)
        if sd['username'] == username:
            flash(f'Username "{username}" already exists.', 'danger')
            return redirect(url_for('teacher_dashboard'))

    new_student = {
        'username':      username,
        'name':          name or username.title(),
        'email':         email,
        'password_hash': generate_password_hash(password, method='pbkdf2:sha256') if password else '',
    }
    users.setdefault('students', []).append(new_student)
    save_json(USERS_FILE, users)
    flash(f'Student "{username}" added successfully.', 'success')
    return redirect(url_for('teacher_dashboard'))


@app.route('/teacher/delete_user', methods=['POST'])
@login_required
@teacher_required
def teacher_delete_user():
    """Remove a student and their attempts."""
    data     = request.get_json(force=True) or {}
    username = data.get('username', '').strip()

    if not username:
        return jsonify({'error': 'username required'}), 400

    users  = load_json(USERS_FILE)
    before = len(users.get('students', []))
    users['students'] = [
        s for s in users.get('students', [])
        if get_student(s)['username'] != username
    ]
    if len(users['students']) == before:
        return jsonify({'error': 'User not found'}), 404

    save_json(USERS_FILE, users)

    try:
        attempts = load_json(ATTEMPTS_FILE)
    except RuntimeError:
        attempts = []
    save_json(ATTEMPTS_FILE, [a for a in attempts if a.get('username') != username])

    return jsonify({'status': 'ok', 'username': username})


@app.route('/teacher/change_password', methods=['POST'])
@login_required
@teacher_required
def teacher_change_password():
    """Change a student or teacher password."""
    data         = request.get_json(force=True) or {}
    username     = data.get('username', '').strip()
    role         = data.get('role', 'student')
    new_password = data.get('new_password', '').strip()

    if not username or not new_password:
        return jsonify({'error': 'username and new_password required'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    users    = load_json(USERS_FILE)
    new_hash = generate_password_hash(new_password, method='pbkdf2:sha256')

    if role == 'teacher':
        for t in users.get('teachers', []):
            if t['username'] == username:
                t['password_hash'] = new_hash
                save_json(USERS_FILE, users)
                return jsonify({'status': 'ok'})
        return jsonify({'error': 'Teacher not found'}), 404
    else:
        for i, s in enumerate(users.get('students', [])):
            sd = get_student(s)
            if sd['username'] == username:
                sd['password_hash'] = new_hash
                users['students'][i] = sd
                save_json(USERS_FILE, users)
                return jsonify({'status': 'ok'})
        return jsonify({'error': 'Student not found'}), 404


@app.route('/teacher/change_own_password', methods=['POST'])
@login_required
@teacher_required
def teacher_change_own_password():
    """Allow a teacher to change their own password."""
    data         = request.get_json(force=True) or {}
    current_pw   = data.get('current_password', '')
    new_password = data.get('new_password', '').strip()

    if not current_pw or not new_password:
        return jsonify({'error': 'current_password and new_password required'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    username = session['username']
    users    = load_json(USERS_FILE)
    teacher  = next((t for t in users.get('teachers', []) if t['username'] == username), None)
    if not teacher:
        return jsonify({'error': 'Teacher not found'}), 404
    if not check_password_hash(teacher.get('password_hash', ''), current_pw):
        return jsonify({'error': 'Current password is incorrect'}), 403

    teacher['password_hash'] = generate_password_hash(new_password, method='pbkdf2:sha256')
    save_json(USERS_FILE, users)
    return jsonify({'status': 'ok'})


@app.route('/teacher/update_settings', methods=['POST'])
@login_required
@teacher_required
def teacher_update_settings():
    """Update portal settings (signup enabled, duel enabled, disabled scenarios)."""
    users = load_json(USERS_FILE)
    users.setdefault('settings', {})

    signup_enabled     = request.form.get('signup_enabled') == 'on'
    duel_enabled       = request.form.get('duel_enabled') == 'on'
    all_scenario_ids   = list(load_scenarios().keys())
    enabled_scenarios  = request.form.getlist('enabled_scenarios')
    disabled_scenarios = [s for s in all_scenario_ids if s not in enabled_scenarios]

    users['settings']['signup_enabled']    = signup_enabled
    users['settings']['duel_enabled']      = duel_enabled
    users['settings']['disabled_scenarios'] = disabled_scenarios

    save_json(USERS_FILE, users)
    flash('Settings updated.', 'success')
    return redirect(url_for('teacher_dashboard'))


# ── Duel routes ───────────────────────────────────────────────────────────────

@app.route('/duel')
@login_required
def duel_lobby():
    """Render the Red vs Blue Duel lobby page."""
    settings = get_settings()
    if not settings.get('duel_enabled', True):
        flash('Duel mode is currently disabled.', 'warning')
        return redirect(url_for('dashboard'))
    return render_template(
        'lobby.html',
        analyst_defenses=duel_engine.ANALYST_DEFENSES,
    )


@app.route('/duel/game/<lobby_id>')
@login_required
def duel_game(lobby_id):
    """Render the game screen for a specific lobby."""
    username = session['username']
    game = duel_engine.games.get(lobby_id)
    if not game:
        flash('Game not found.', 'danger')
        return redirect(url_for('duel_lobby'))

    if game['attacker']['username'] == username:
        role = 'attacker'
    elif game['analyst']['username'] == username:
        role = 'analyst'
    else:
        flash('You are not in this game.', 'danger')
        return redirect(url_for('duel_lobby'))

    return render_template(
        'game.html',
        lobby_id=lobby_id,
        role=role,
        analyst_defenses=duel_engine.ANALYST_DEFENSES,
        attacker_actions=duel_engine.ATTACKER_ACTIONS,
        analyst_actions=duel_engine.ANALYST_ACTIONS,
    )


# ── Duel Socket.IO events ─────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    """Client connected — no special handling needed."""
    pass


@socketio.on('disconnect')
def on_disconnect():
    """Client disconnected — clean up game and lobby state."""
    sid = request.sid

    # Handle active game disconnect
    lobby_id = sid_to_game.pop(sid, None)
    if lobby_id:
        game = duel_engine.games.get(lobby_id)
        if game and game.get('status') != 'ended':
            username     = session.get('username', '')
            opponent_sid = _get_opponent_sid(game, username)
            duel_engine.games.pop(lobby_id, None)
            if opponent_sid:
                socketio.emit('opponent_left', {
                    'msg': 'Your opponent disconnected. The game has ended.'
                }, to=opponent_sid)

    # Handle lobby disconnect
    lobby_id = sid_to_lobby.pop(sid, None)
    if lobby_id:
        username = session.get('username', '')
        if username:
            updated = duel_engine.leave_lobby(lobby_id, username)
            if updated:
                socketio.emit('player_left', {
                    'lobby_id': lobby_id,
                    'players': [p['username'] for p in updated['players']],
                }, to=lobby_id)
            socketio.emit(
                'update_lobbies',
                {'lobbies': duel_engine.get_lobbies_list()},
                broadcast=True,
            )


def _get_opponent_sid(game, username):
    """Return the SID of the player who is NOT *username*."""
    if game['attacker']['username'] == username:
        return game['analyst'].get('sid')
    elif game['analyst']['username'] == username:
        return game['attacker'].get('sid')
    return None


@socketio.on('create_lobby')
def on_create_lobby(data):
    """
    Create a new lobby and broadcast the updated lobby list.

    Data: { lobby_name: str }
    """
    username = session.get('username')
    if not username:
        emit('error', {'msg': 'Not authenticated.'})
        return

    name  = data.get('lobby_name', '').strip()
    lobby = duel_engine.create_lobby(name, username, request.sid)
    join_room(lobby['id'])
    sid_to_lobby[request.sid] = lobby['id']
    emit('lobby_created', {'lobby_id': lobby['id'], 'lobby_name': lobby['name']})
    emit('update_lobbies', {'lobbies': duel_engine.get_lobbies_list()}, broadcast=True)


@socketio.on('join_lobby')
def on_join_lobby(data):
    """
    Join an existing lobby.

    Data: { lobby_id: str }
    """
    username = session.get('username')
    if not username:
        emit('error', {'msg': 'Not authenticated.'})
        return

    lobby_id = data.get('lobby_id', '')
    lobby, err = duel_engine.join_lobby(lobby_id, username, request.sid)
    if err:
        emit('error', {'msg': err})
        return

    join_room(lobby_id)
    sid_to_lobby[request.sid] = lobby_id
    emit('player_joined', {
        'lobby_id': lobby_id,
        'players': [p['username'] for p in lobby['players']],
    }, to=lobby_id)
    emit('update_lobbies', {'lobbies': duel_engine.get_lobbies_list()}, broadcast=True)

    if len(lobby['players']) == 2:
        game = duel_engine.start_game(lobby_id)
        emit('start_game', {
            'lobby_id': lobby_id,
            'attacker': game['attacker']['username'],
            'analyst':  game['analyst']['username'],
        }, to=lobby_id)
        emit('update_lobbies', {'lobbies': duel_engine.get_lobbies_list()}, broadcast=True)


@socketio.on('leave_lobby')
def on_leave_lobby(data):
    """
    Leave a lobby.

    Data: { lobby_id: str }
    """
    username = session.get('username')
    if not username:
        return

    lobby_id = data.get('lobby_id', '')
    updated  = duel_engine.leave_lobby(lobby_id, username)
    leave_room(lobby_id)
    sid_to_lobby.pop(request.sid, None)

    if updated:
        emit('player_left', {
            'lobby_id': lobby_id,
            'players': [p['username'] for p in updated['players']],
        }, to=lobby_id)
    emit('update_lobbies', {'lobbies': duel_engine.get_lobbies_list()}, broadcast=True)


@socketio.on('get_lobbies')
def on_get_lobbies():
    """Return the current lobby list to the requesting client."""
    emit('update_lobbies', {'lobbies': duel_engine.get_lobbies_list()})


@socketio.on('join_game_room')
def on_join_game_room(data):
    """
    Join the Socket.IO room for a specific game and receive the initial state.
    Also updates the player's SID in the game state (new connection after redirect).

    Data: { lobby_id: str }
    """
    username = session.get('username')
    lobby_id = data.get('lobby_id', '')
    game     = duel_engine.games.get(lobby_id)
    if not game:
        emit('error', {'msg': 'Game not found.'})
        return

    join_room(lobby_id)

    # Update the player's SID (connection changes after page redirect)
    role = None
    if game['attacker']['username'] == username:
        game['attacker']['sid'] = request.sid
        role = 'attacker'
    elif game['analyst']['username'] == username:
        game['analyst']['sid'] = request.sid
        role = 'analyst'
    else:
        emit('error', {'msg': 'You are not a player in this game.'})
        return

    sid_to_game[request.sid] = lobby_id

    state = duel_engine.get_game_state_for_player(lobby_id, role)
    emit('update_game_state', state)


@socketio.on('leave_game')
def on_leave_game(data):
    """
    Explicit leave_game event — same cleanup as a disconnect.

    Data: { lobby_id: str }
    """
    username = session.get('username', '')
    lobby_id = data.get('lobby_id', '')
    game     = duel_engine.games.get(lobby_id)
    sid_to_game.pop(request.sid, None)

    if game and game.get('status') != 'ended':
        opponent_sid = _get_opponent_sid(game, username)
        duel_engine.games.pop(lobby_id, None)
        if opponent_sid:
            socketio.emit('opponent_left', {
                'msg': 'Your opponent left the game.'
            }, to=opponent_sid)


@socketio.on('select_defenses')
def on_select_defenses(data):
    """
    Analyst selects 2 defenses during the setup phase.

    Data: { lobby_id: str, defenses: [str, str] }
    """
    username = session.get('username')
    lobby_id = data.get('lobby_id', '')
    defenses = data.get('defenses', [])

    game = duel_engine.games.get(lobby_id)
    if not game:
        emit('error', {'msg': 'Game not found.'})
        return
    if game['analyst']['username'] != username:
        emit('error', {'msg': 'Only the analyst can select defenses.'})
        return

    state, err = duel_engine.analyst_select_defenses(lobby_id, defenses)
    if err:
        emit('error', {'msg': err})
        return

    _broadcast_game_state(lobby_id)


@socketio.on('player_action')
def on_player_action(data):
    """
    A player submits an action during gameplay.

    Data: { lobby_id: str, action_id: str }
    """
    username  = session.get('username')
    lobby_id  = data.get('lobby_id', '')
    action_id = data.get('action_id', '')

    game = duel_engine.games.get(lobby_id)
    if not game:
        emit('error', {'msg': 'Game not found.'})
        return

    if game['attacker']['username'] == username:
        state, err = duel_engine.process_attacker_action(lobby_id, action_id)
    elif game['analyst']['username'] == username:
        state, err = duel_engine.process_analyst_action(lobby_id, action_id)
    else:
        emit('error', {'msg': 'You are not a player in this game.'})
        return

    if err:
        emit('error', {'msg': err})
        return

    if state['status'] == 'ended':
        duel_engine.save_duel_result(ATTEMPTS_FILE, lobby_id, load_json, save_json)

    _broadcast_game_state(lobby_id)


def _broadcast_game_state(lobby_id: str) -> None:
    """
    Emit tailored game-state updates to each player in *lobby_id*.

    The attacker receives their own view; the analyst receives theirs.
    """
    game = duel_engine.games.get(lobby_id)
    if not game:
        return
    attacker_sid = game['attacker']['sid']
    analyst_sid  = game['analyst']['sid']

    attacker_state = duel_engine.get_game_state_for_player(lobby_id, 'attacker')
    analyst_state  = duel_engine.get_game_state_for_player(lobby_id, 'analyst')

    socketio.emit('update_game_state', attacker_state, to=attacker_sid)
    socketio.emit('update_game_state', analyst_state,  to=analyst_sid)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    host  = os.environ.get('FLASK_HOST', '0.0.0.0')
    socketio.run(app, host=host, debug=debug, port=5000)
