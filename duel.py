"""
duel.py — Red vs Blue Duel: in-memory lobby + game engine for Flask-SocketIO.

Lobbies and games are stored in plain Python dicts so no database is needed.
The module is designed to be easy to extend to 2v2 or team modes later.
"""

import uuid
import random
from datetime import datetime, timezone
from typing import Callable

# ── In-memory state ───────────────────────────────────────────────────────────
# Keyed by lobby_id (str)
lobbies: dict = {}
# Keyed by lobby_id (str); created when a lobby transitions to "in_game"
games: dict = {}


# ── Attacker action definitions ───────────────────────────────────────────────
ATTACKER_ACTIONS = {
    "phishing": {
        "label": "Phishing Attack",
        "icon": "🎣",
        "base_progress": 15,
        "detection_risk": 0.30,
        "description": "Send deceptive emails to harvest credentials.",
        "countered_by": ["mfa_enforcement", "user_awareness_training"],
    },
    "password_spray": {
        "label": "Password Spray",
        "icon": "🔑",
        "base_progress": 10,
        "detection_risk": 0.40,
        "description": "Try common passwords across many accounts.",
        "countered_by": ["mfa_enforcement"],
    },
    "exploit_vuln": {
        "label": "Exploit Vulnerability",
        "icon": "💣",
        "base_progress": 22,
        "detection_risk": 0.20,
        "description": "Leverage an unpatched software vulnerability.",
        "countered_by": ["firewall_hardening"],
    },
    "lateral_movement": {
        "label": "Lateral Movement",
        "icon": "↔️",
        "base_progress": 18,
        "detection_risk": 0.35,
        "description": "Move through the network to reach sensitive systems.",
        "countered_by": ["network_segmentation"],
    },
    "data_exfil": {
        "label": "Data Exfiltration",
        "icon": "📤",
        "base_progress": 30,
        "detection_risk": 0.50,
        "description": "Attempt to extract sensitive data from the network.",
        "countered_by": ["firewall_hardening", "edr"],
        "requires_progress": 60,  # only unlocked when attacker_progress >= 60
    },
}

# ── Analyst defense definitions ───────────────────────────────────────────────
ANALYST_DEFENSES = {
    "firewall_hardening": {
        "label": "Firewall Hardening",
        "icon": "🔥",
        "description": "Restricts inbound/outbound traffic — counters exploits and exfil.",
    },
    "edr": {
        "label": "Endpoint Detection (EDR)",
        "icon": "🖥️",
        "description": "Detects malicious behaviour on endpoints — raises detection across all attacks.",
    },
    "network_segmentation": {
        "label": "Network Segmentation",
        "icon": "🌐",
        "description": "Isolates network zones — counters lateral movement.",
    },
    "mfa_enforcement": {
        "label": "MFA Enforcement",
        "icon": "📱",
        "description": "Requires multi-factor auth — counters phishing and password sprays.",
    },
    "user_awareness_training": {
        "label": "User Awareness Training",
        "icon": "📚",
        "description": "Educates users to recognise attacks — counters phishing.",
    },
}

# ── Analyst in-game action definitions ────────────────────────────────────────
ANALYST_ACTIONS = {
    "investigate_alert": {
        "label": "Investigate Alert",
        "icon": "🔍",
        "detection_gain": 15,
        "progress_reduction": 0,
        "description": "Analyse an alert to gather intelligence.",
        "is_containment": False,
    },
    "block_ip": {
        "label": "Block IP",
        "icon": "🚫",
        "detection_gain": 20,
        "progress_reduction": 8,
        "description": "Block the attacker's IP address.",
        "is_containment": True,
    },
    "isolate_host": {
        "label": "Isolate Host",
        "icon": "🔒",
        "detection_gain": 25,
        "progress_reduction": 15,
        "description": "Quarantine a compromised host to stop the spread.",
        "is_containment": True,
    },
    "reset_credentials": {
        "label": "Reset Credentials",
        "icon": "🔄",
        "detection_gain": 10,
        "progress_reduction": 10,
        "description": "Force-reset credentials to revoke stolen access.",
        "is_containment": False,
    },
    "ignore": {
        "label": "Ignore",
        "icon": "👁️",
        "detection_gain": 0,
        "progress_reduction": 0,
        "description": "Do nothing and hope it goes away.",
        "is_containment": False,
    },
}

# ── Log templates ─────────────────────────────────────────────────────────────
_LOG_TEMPLATES = {
    "phishing": [
        "[MAIL] Suspicious link clicked by user {user} — credential harvest suspected",
        "[AUTH] Login from unusual location for account {user}",
        "[SIEM] Phishing indicator detected in email gateway",
    ],
    "password_spray": [
        "[AUTH] Multiple failed logins across {n} accounts in 60 seconds",
        "[IDS] High-volume authentication failures from {ip}",
        "[SIEM] Password spray pattern detected on domain controller",
    ],
    "exploit_vuln": [
        "[IDS] Exploit payload detected targeting service on port {port}",
        "[SIEM] CVE-{cve} exploitation attempt logged",
        "[FW] Anomalous outbound beacon to {ip}",
    ],
    "lateral_movement": [
        "[SIEM] Unusual SMB activity from {host} to {host2}",
        "[IDS] Pass-the-hash attempt detected",
        "[AUTH] Service account {user} accessed new system {host}",
    ],
    "data_exfil": [
        "[DLP] Large data transfer to external IP {ip} — {mb} MB",
        "[FW] Egress spike: {mb} MB to unknown destination",
        "[SIEM] Data exfiltration pattern detected — immediate action required",
    ],
    "investigate_alert": [
        "[SOC] Alert {id} investigated — threat confirmed",
        "[SIEM] Analyst escalated alert — attacker TTPs identified",
    ],
    "block_ip": [
        "[FW] IP {ip} added to deny list",
        "[SOC] Blocking rule applied for malicious host {ip}",
    ],
    "isolate_host": [
        "[EDR] Host {host} quarantined — network access revoked",
        "[SOC] Endpoint isolation completed for {host}",
    ],
    "reset_credentials": [
        "[AUTH] Credentials reset for account {user} — tokens invalidated",
        "[AD] Forced password change applied to {n} affected accounts",
    ],
    "ignore": [
        "[SOC] Alert {id} marked as low priority — no action taken",
    ],
}

_RANDOM_IPS    = ["10.0.2.15", "192.168.1.44", "172.16.5.200", "203.0.113.42", "185.234.219.7"]
_RANDOM_HOSTS  = ["WS-FINANCE-01", "DC-PROD-02", "SRV-HR-03", "LAPTOP-CEO", "FILE-SRV-01"]
_RANDOM_USERS  = ["jsmith", "alee", "tmartin", "dbrown", "swhite"]
_RANDOM_PORTS  = [443, 8080, 3389, 445, 22]
_RANDOM_CVES   = ["2023-44487", "2024-21413", "2023-23397", "2022-30190"]


def _make_log(action_id: str) -> str:
    """Return a realistic log line for the given action."""
    templates = _LOG_TEMPLATES.get(action_id, ["[SYS] Activity detected"])
    tmpl = random.choice(templates)
    return tmpl.format(
        ip=random.choice(_RANDOM_IPS),
        host=random.choice(_RANDOM_HOSTS),
        host2=random.choice(_RANDOM_HOSTS),
        user=random.choice(_RANDOM_USERS),
        port=random.choice(_RANDOM_PORTS),
        cve=random.choice(_RANDOM_CVES),
        n=random.randint(10, 50),
        mb=random.randint(50, 500),
        id=str(uuid.uuid4())[:8].upper(),
    )


# ── Lobby helpers ──────────────────────────────────────────────────────────────

def create_lobby(name: str, username: str, sid: str) -> dict:
    """Create a new lobby and return its dict."""
    lobby_id = str(uuid.uuid4())
    lobby = {
        "id": lobby_id,
        "name": name.strip() or f"{username}'s lobby",
        "players": [{"username": username, "sid": sid}],
        "status": "waiting",
    }
    lobbies[lobby_id] = lobby
    return lobby


def join_lobby(lobby_id: str, username: str, sid: str) -> tuple[dict | None, str]:
    """
    Attempt to join a lobby.

    Returns (lobby, error_message).  error_message is '' on success.
    """
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return None, "Lobby not found."
    if lobby["status"] == "in_game":
        return None, "Game already in progress."
    if len(lobby["players"]) >= 2:
        return None, "Lobby is full."
    # Prevent the same user from joining twice
    if any(p["username"] == username for p in lobby["players"]):
        return None, "You are already in this lobby."
    lobby["players"].append({"username": username, "sid": sid})
    return lobby, ""


def leave_lobby(lobby_id: str, username: str) -> dict | None:
    """
    Remove *username* from the lobby.  Deletes empty lobbies.

    Returns the updated lobby (or None if deleted).
    """
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return None
    lobby["players"] = [p for p in lobby["players"] if p["username"] != username]
    if not lobby["players"]:
        del lobbies[lobby_id]
        return None
    # Reset status if a player leaves mid-game
    lobby["status"] = "waiting"
    return lobby


def get_lobbies_list() -> list:
    """Return a JSON-serialisable list of all lobbies."""
    result = []
    for lb in lobbies.values():
        result.append({
            "id": lb["id"],
            "name": lb["name"],
            "players": [p["username"] for p in lb["players"]],
            "status": lb["status"],
        })
    return result


# ── Game helpers ───────────────────────────────────────────────────────────────

def start_game(lobby_id: str) -> dict:
    """
    Initialise game state for *lobby_id*.

    First player is attacker, second is analyst.
    Returns the initial game state dict.
    """
    lobby = lobbies[lobby_id]
    attacker = lobby["players"][0]
    analyst  = lobby["players"][1]

    game = {
        "id": lobby_id,
        "lobby_name": lobby["name"],
        "attacker": attacker,
        "analyst":  analyst,
        "phase": "setup",           # "setup" → analyst picks defenses → "playing" → "ended"
        "turn": 1,
        "current_player": "attacker",
        "attacker_progress": 0,
        "detection_level": 0,
        "analyst_defenses": [],     # filled during setup phase
        "logs": [],
        "alerts": [],
        "winner": None,
        "win_reason": "",
        "status": "active",
        "last_containment": False,  # tracks if last analyst action was containment
    }
    games[lobby_id] = game
    lobby["status"] = "in_game"
    return game


def analyst_select_defenses(lobby_id: str, defenses: list) -> tuple[dict | None, str]:
    """
    Store the analyst's chosen defenses and advance to 'playing' phase.

    Exactly 2 defenses must be chosen.
    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["phase"] != "setup":
        return None, "Defense selection phase already passed."
    valid = set(ANALYST_DEFENSES.keys())
    chosen = [d for d in defenses if d in valid]
    if len(chosen) != 2:
        return None, "Select exactly 2 defenses."
    game["analyst_defenses"] = chosen
    game["phase"] = "playing"
    # Log the defense selections
    for d in chosen:
        label = ANALYST_DEFENSES[d]["label"]
        game["logs"].append(f"[SETUP] Analyst deployed defense: {label}")
    return game, ""


def process_attacker_action(lobby_id: str, action_id: str) -> tuple[dict | None, str]:
    """
    Process an attacker action and return updated game state.

    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["status"] != "active":
        return None, "Game is over."
    if game["phase"] != "playing":
        return None, "Game not in playing phase."
    if game["current_player"] != "attacker":
        return None, "Not attacker's turn."

    action = ATTACKER_ACTIONS.get(action_id)
    if not action:
        return None, "Unknown action."

    # Check unlock requirement
    if action.get("requires_progress", 0) > game["attacker_progress"]:
        return None, f"Need {action['requires_progress']}% progress to use this action."

    # Calculate progress gain — reduced if countered by active defense
    progress_gain = action["base_progress"]
    countered = [c for c in action["countered_by"] if c in game["analyst_defenses"]]
    if countered:
        progress_gain = max(3, progress_gain // 2)  # halved, minimum 3

    # EDR increases detection risk across all attacks
    detection_risk = action["detection_risk"]
    if "edr" in game["analyst_defenses"]:
        detection_risk = min(0.90, detection_risk + 0.20)

    game["attacker_progress"] = min(100, game["attacker_progress"] + progress_gain)

    # Detection roll
    detected = random.random() < detection_risk
    if detected:
        game["detection_level"] = min(100, game["detection_level"] + 5)
        alert = f"⚠️ ALERT: Suspicious activity — {action['label']} detected!"
        game["alerts"].append(alert)

    log_line = _make_log(action_id)
    game["logs"].append(log_line)

    # Win check
    _check_win(game)

    if game["status"] == "active":
        game["current_player"] = "analyst"
        game["turn"] += 1

    return game, ""


def process_analyst_action(lobby_id: str, action_id: str) -> tuple[dict | None, str]:
    """
    Process an analyst action and return updated game state.

    Returns (game_state, error).
    """
    game = games.get(lobby_id)
    if not game:
        return None, "Game not found."
    if game["status"] != "active":
        return None, "Game is over."
    if game["phase"] != "playing":
        return None, "Game not in playing phase."
    if game["current_player"] != "analyst":
        return None, "Not analyst's turn."

    action = ANALYST_ACTIONS.get(action_id)
    if not action:
        return None, "Unknown action."

    game["detection_level"] = min(100, game["detection_level"] + action["detection_gain"])
    game["attacker_progress"] = max(0, game["attacker_progress"] - action["progress_reduction"])
    game["last_containment"] = action["is_containment"]

    log_line = _make_log(action_id)
    game["logs"].append(log_line)

    # Win check
    _check_win(game)

    if game["status"] == "active":
        game["current_player"] = "attacker"

    return game, ""


def _check_win(game: dict) -> None:
    """Mutate *game* to set winner/status if a win condition is met."""
    if game["attacker_progress"] >= 100:
        game["status"] = "ended"
        game["phase"]  = "ended"
        game["winner"] = "attacker"
        game["win_reason"] = "Attacker successfully exfiltrated all target data!"
        game["logs"].append("[GAME OVER] Attacker wins — data breach complete!")
    elif game["detection_level"] >= 100 and game["last_containment"]:
        game["status"] = "ended"
        game["phase"]  = "ended"
        game["winner"] = "analyst"
        game["win_reason"] = "Analyst detected and contained the threat!"
        game["logs"].append("[GAME OVER] Analyst wins — threat fully contained!")


def get_game_state_for_player(lobby_id: str, role: str) -> dict | None:
    """
    Return a trimmed game-state dict appropriate for *role* ('attacker'/'analyst').

    Both roles see all public fields; the analyst also sees defense info.
    """
    game = games.get(lobby_id)
    if not game:
        return None
    return {
        "id": game["id"],
        "lobby_name": game["lobby_name"],
        "attacker_username": game["attacker"]["username"],
        "analyst_username": game["analyst"]["username"],
        "phase": game["phase"],
        "turn": game["turn"],
        "current_player": game["current_player"],
        "attacker_progress": game["attacker_progress"],
        "detection_level": game["detection_level"],
        "analyst_defenses": game["analyst_defenses"],
        "logs": game["logs"][-30:],     # last 30 log lines
        "alerts": game["alerts"][-10:],  # last 10 alerts
        "winner": game["winner"],
        "win_reason": game["win_reason"],
        "status": game["status"],
        "your_role": role,
        "attacker_actions": _attacker_actions_payload(game),
        "analyst_actions": _analyst_actions_payload(),
        "analyst_defenses_info": ANALYST_DEFENSES,
    }


def _attacker_actions_payload(game: dict) -> list:
    """Build list of attacker actions with lock status based on current progress."""
    actions = []
    for action_id, action in ATTACKER_ACTIONS.items():
        required = action.get("requires_progress", 0)
        actions.append({
            "id": action_id,
            "label": action["label"],
            "icon": action["icon"],
            "description": action["description"],
            "locked": game["attacker_progress"] < required,
            "requires_progress": required,
        })
    return actions


def _analyst_actions_payload() -> list:
    """Build list of analyst in-game actions."""
    return [
        {
            "id": action_id,
            "label": action["label"],
            "icon": action["icon"],
            "description": action["description"],
            "is_containment": action["is_containment"],
        }
        for action_id, action in ANALYST_ACTIONS.items()
    ]


def save_duel_result(
    attempts_file: str,
    lobby_id: str,
    load_json_fn: Callable[[str], list],
    save_json_fn: Callable[[str, list], None],
) -> None:
    """
    Append duel result records to the shared attempts JSON file.

    One record is written for each player so both appear in their dashboards.
    """
    game = games.get(lobby_id)
    if not game or game["status"] != "ended":
        return

    now = datetime.now(timezone.utc).isoformat()
    attacker_username = game["attacker"]["username"]
    analyst_username  = game["analyst"]["username"]
    winner = game["winner"]

    records = [
        {
            "id": str(uuid.uuid4()),
            "username": attacker_username,
            "scenario_id": "duel_red_vs_blue",
            "duel": True,
            "opponent": analyst_username,
            "role": "attacker",
            "result": "win" if winner == "attacker" else "lose",
            "total_score": game["attacker_progress"],
            "time_taken": 0,
            "hints_used": 0,
            "longest_streak": 0,
            "timestamp": now,
        },
        {
            "id": str(uuid.uuid4()),
            "username": analyst_username,
            "scenario_id": "duel_red_vs_blue",
            "duel": True,
            "opponent": attacker_username,
            "role": "analyst",
            "result": "win" if winner == "analyst" else "lose",
            "total_score": game["detection_level"],
            "time_taken": 0,
            "hints_used": 0,
            "longest_streak": 0,
            "timestamp": now,
        },
    ]

    try:
        attempts = load_json_fn(attempts_file)
        attempts.extend(records)
        save_json_fn(attempts_file, attempts)
    except Exception:
        pass  # best-effort; never crash the game on a save failure
