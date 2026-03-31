"""
Cybersecurity Scenario Portal - Flask Backend
"""

import os
import json
import uuid
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash)
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cyberportal-secret-key-change-in-prod')

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SCENARIOS_DIR  = os.path.join(BASE_DIR, 'scenarios')
DATA_DIR       = os.path.join(BASE_DIR, 'data')
USERS_FILE     = os.path.join(DATA_DIR, 'users.json')
ATTEMPTS_FILE  = os.path.join(DATA_DIR, 'attempts.json')

ALLOWED_EXTENSIONS = {'json'}

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_json(path):
    """Read and return parsed JSON from *path*."""
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise RuntimeError(f"Required data file not found: {path}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupted data file {path}: {exc}")


def save_json(path, data):
    """Serialise *data* to *path* with pretty-printing."""
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def load_scenarios():
    """
    Walk the scenarios/ directory and return a dict keyed by scenario id.
    Every .json file whose top-level 'id' field matches the file stem is loaded.
    """
    scenarios = {}
    for fname in os.listdir(SCENARIOS_DIR):
        if fname.endswith('.json'):
            path = os.path.join(SCENARIOS_DIR, fname)
            try:
                data = load_json(path)
                scenarios[data['id']] = data
            except (KeyError, json.JSONDecodeError):
                pass  # skip malformed files
    return scenarios


def get_phase(scenario, phase_id):
    """Return the phase dict with *phase_id* from *scenario*, or None."""
    for phase in scenario.get('phases', []):
        if phase['id'] == phase_id:
            return phase
    return None


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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
    """Login page — two tabs: student (username only) and teacher (username + password)."""
    if 'username' in session:
        # Already logged in — send to the right dashboard
        if session.get('role') == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        login_type = request.form.get('login_type', 'student')
        username   = request.form.get('username', '').strip().lower()

        if not username:
            flash('Username cannot be empty.', 'danger')
            return render_template('login.html')

        users = load_json(USERS_FILE)

        if login_type == 'teacher':
            password = request.form.get('password', '')
            # Validate against teachers list using hashed password comparison
            teacher = next(
                (t for t in users['teachers'] if t['username'] == username),
                None
            )
            if teacher and check_password_hash(teacher.get('password_hash', ''), password):
                session['username'] = username
                session['role']     = 'teacher'
                return redirect(url_for('teacher_dashboard'))
            else:
                flash('Invalid teacher credentials.', 'danger')
                return render_template('login.html')

        else:  # student login — create account on first visit
            if username not in users['students']:
                users['students'].append(username)
                save_json(USERS_FILE, users)
            session['username'] = username
            session['role']     = 'student'
            return redirect(url_for('dashboard'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    """Clear session and redirect to login."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    """Student dashboard — lists all available scenarios and recent attempts."""
    scenarios = load_scenarios()
    attempts  = load_json(ATTEMPTS_FILE)
    # Only show this student's attempts, most recent first
    my_attempts = sorted(
        [a for a in attempts if a.get('username') == session['username']],
        key=lambda a: a.get('timestamp', ''),
        reverse=True
    )[:10]
    return render_template(
        'dashboard.html',
        scenarios=list(scenarios.values()),
        attempts=my_attempts
    )


@app.route('/scenario/<scenario_id>')
@login_required
def scenario_view(scenario_id):
    """Render the interactive scenario page."""
    scenarios = load_scenarios()
    scenario  = scenarios.get(scenario_id)
    if not scenario:
        flash('Scenario not found.', 'danger')
        return redirect(url_for('dashboard'))

    # Pass the full scenario as JSON so the JS engine can drive the flow
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

    # Locate the chosen option
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
    data = request.get_json(force=True)

    attempt = {
        'id':          str(uuid.uuid4()),
        'username':    session['username'],
        'scenario_id': data.get('scenario_id'),
        'decisions':   data.get('decisions', []),
        'total_score': data.get('total_score', 0),
        'time_taken':  data.get('time_taken', 0),   # seconds
        'timestamp':   datetime.utcnow().isoformat() + 'Z',
    }

    attempts = load_json(ATTEMPTS_FILE)
    attempts.append(attempt)
    save_json(ATTEMPTS_FILE, attempts)

    return jsonify({'status': 'ok', 'attempt_id': attempt['id']})


# ── Teacher routes ────────────────────────────────────────────────────────────

@app.route('/teacher')
@login_required
@teacher_required
def teacher_dashboard():
    """Teacher dashboard — view all attempts, upload scenarios, reset students."""
    scenarios = load_scenarios()
    attempts  = load_json(ATTEMPTS_FILE)
    users     = load_json(USERS_FILE)

    # Group attempts by student for display
    grouped = {}
    for attempt in sorted(attempts, key=lambda a: a.get('timestamp', ''), reverse=True):
        uname = attempt.get('username', 'unknown')
        grouped.setdefault(uname, []).append(attempt)

    return render_template(
        'teacher.html',
        scenarios=scenarios,
        grouped_attempts=grouped,
        students=users.get('students', []),
        all_attempts=attempts,
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
        # Validate that the file is valid JSON with required fields
        try:
            content = f.read()
            scenario_data = json.loads(content)
            if 'id' not in scenario_data or 'phases' not in scenario_data:
                flash('Invalid scenario JSON: missing "id" or "phases".', 'danger')
                return redirect(url_for('teacher_dashboard'))
            # Use the scenario id as the filename for consistency
            safe_name = secure_filename(scenario_data['id'] + '.json')
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

    attempts = load_json(ATTEMPTS_FILE)
    filtered = [a for a in attempts if a.get('username') != username]
    save_json(ATTEMPTS_FILE, filtered)

    removed = len(attempts) - len(filtered)
    return jsonify({'status': 'ok', 'removed': removed})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SCENARIOS_DIR, exist_ok=True)
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug, port=5000)
