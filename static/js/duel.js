/**
 * duel.js — Red vs Blue Duel client-side logic.
 *
 * Handles both the lobby page and the game page.
 * Detected by checking window.LOBBY_ID — present only on game.html.
 */

(function () {
  'use strict';

  // ── Connect to Socket.IO ──────────────────────────────────────────────────
  const socket = io();
  // Expose socket so inline scripts (e.g. beforeunload in game.html) can use it
  window._duelSocket = socket;

  // ── Page detection ────────────────────────────────────────────────────────
  const IS_GAME_PAGE  = typeof window.LOBBY_ID  !== 'undefined';
  const IS_LOBBY_PAGE = !IS_GAME_PAGE;

  // ── Utility: toast notification ───────────────────────────────────────────
  function showToast(msg, type) {
    const toast = document.getElementById('duel-toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = 'duel-toast duel-toast-' + (type || 'info');
    toast.style.display = 'block';
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.style.display = 'none'; }, 4000);
  }

  // ── Error handler (both pages) ────────────────────────────────────────────
  socket.on('error', (data) => {
    showToast('⚠️ ' + (data.msg || 'An error occurred.'), 'error');
  });

  // ═══════════════════════════════════════════════════════════════════════════
  // LOBBY PAGE
  // ═══════════════════════════════════════════════════════════════════════════
  if (IS_LOBBY_PAGE) {
    let myLobbyId = null; // track which lobby I'm in

    // Request current lobby list on connect
    socket.on('connect', () => {
      socket.emit('get_lobbies');
    });

    // ── Create lobby ────────────────────────────────────────────────────────
    const createBtn  = document.getElementById('create-lobby-btn');
    const nameInput  = document.getElementById('lobby-name-input');

    if (createBtn) {
      createBtn.addEventListener('click', () => {
        const name = nameInput.value.trim();
        socket.emit('create_lobby', { lobby_name: name });
        nameInput.value = '';
      });
      // Allow Enter key in input
      nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') createBtn.click();
      });
    }

    // ── Lobby created (self) ────────────────────────────────────────────────
    socket.on('lobby_created', (data) => {
      myLobbyId = data.lobby_id;
      showToast('🏟️ Lobby "' + data.lobby_name + '" created!', 'success');
    });

    // ── Render lobby list ───────────────────────────────────────────────────
    socket.on('update_lobbies', (data) => {
      renderLobbies(data.lobbies);
    });

    function renderLobbies(lobbies) {
      const container = document.getElementById('lobby-list');
      const countBadge = document.getElementById('lobby-count');
      const tpl = document.getElementById('lobby-row-tpl');
      if (!container || !tpl) return;

      const waiting = lobbies.filter(l => l.status === 'waiting');
      if (countBadge) countBadge.textContent = waiting.length;

      container.innerHTML = '';

      if (lobbies.length === 0) {
        container.innerHTML = '<div class="empty-state small"><span class="empty-icon">🏟️</span><p>No lobbies open yet — be the first!</p></div>';
        return;
      }

      lobbies.forEach(lobby => {
        const row = tpl.content.cloneNode(true).querySelector('.lobby-row');
        row.dataset.lobbyId = lobby.id;

        row.querySelector('.lobby-name-text').textContent = lobby.name;

        // Slot labels
        const p1 = lobby.players[0] || null;
        const p2 = lobby.players[1] || null;
        row.querySelector('.slot-attacker .slot-label').textContent = p1 ? '⚔️ ' + p1 : 'Waiting…';
        row.querySelector('.slot-analyst  .slot-label').textContent = p2 ? '🛡️ ' + p2 : 'Waiting…';

        // Status badge
        const badge = row.querySelector('.lobby-status-badge');
        if (lobby.status === 'in_game') {
          badge.textContent  = '🎮 In Game';
          badge.className    = 'lobby-status-badge status-ingame';
        } else {
          badge.textContent  = '⏳ Waiting';
          badge.className    = 'lobby-status-badge status-waiting';
        }

        // Join / Leave button logic
        const joinBtn  = row.querySelector('.join-btn');
        const leaveBtn = row.querySelector('.leave-btn');

        const isMine  = (lobby.id === myLobbyId);
        const isMember = lobby.players.includes(window.CURRENT_USER);
        const isFull   = lobby.players.length >= 2;

        if (isMember) {
          joinBtn.style.display  = 'none';
          leaveBtn.style.display = '';
          leaveBtn.addEventListener('click', () => {
            socket.emit('leave_lobby', { lobby_id: lobby.id });
            myLobbyId = null;
          });
        } else {
          if (isFull || lobby.status === 'in_game') {
            joinBtn.disabled = true;
            joinBtn.textContent = 'Full';
          } else {
            joinBtn.addEventListener('click', () => {
              socket.emit('join_lobby', { lobby_id: lobby.id });
              myLobbyId = lobby.id;
            });
          }
        }

        container.appendChild(row);
      });
    }

    // ── Game started — redirect both players ────────────────────────────────
    socket.on('start_game', (data) => {
      showToast('🎮 Game starting!', 'success');
      setTimeout(() => {
        window.location.href = '/duel/game/' + data.lobby_id;
      }, 800);
    });

    // ── Player joined notification ──────────────────────────────────────────
    socket.on('player_joined', (data) => {
      if (data.lobby_id === myLobbyId && !data.players.includes(window.CURRENT_USER)) {
        showToast('👥 ' + data.players[data.players.length - 1] + ' joined your lobby!', 'info');
      }
    });

    // ── Player left notification ────────────────────────────────────────────
    socket.on('player_left', () => {
      showToast('🚪 A player left the lobby.', 'warning');
    });
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // GAME PAGE
  // ═══════════════════════════════════════════════════════════════════════════
  if (IS_GAME_PAGE) {
    const lobbyId   = window.LOBBY_ID;
    const myRole    = window.PLAYER_ROLE;  // 'attacker' or 'analyst'

    // Join the game Socket.IO room when connected
    socket.on('connect', () => {
      socket.emit('join_game_room', { lobby_id: lobbyId });
    });

    // ── Defense selection (analyst setup phase) ──────────────────────────────
    const defenseForm   = document.getElementById('defense-form');
    const confirmBtn    = document.getElementById('confirm-defenses-btn');
    const checkboxes    = defenseForm ? defenseForm.querySelectorAll('.defense-checkbox') : [];

    if (defenseForm) {
      checkboxes.forEach(cb => {
        cb.addEventListener('change', () => {
          const checked = defenseForm.querySelectorAll('.defense-checkbox:checked');
          const count = checked.length;
          // Disable unchecked if already at 2
          checkboxes.forEach(c => {
            if (!c.checked) c.disabled = count >= 2;
          });
          confirmBtn.disabled = count !== 2;
          confirmBtn.textContent = `Confirm Defenses (${count}/2 selected)`;
        });
      });

      defenseForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const selected = Array.from(
          defenseForm.querySelectorAll('.defense-checkbox:checked')
        ).map(c => c.value);
        socket.emit('select_defenses', { lobby_id: lobbyId, defenses: selected });
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Defenses confirmed!';
      });
    }

    // ── Attacker action buttons ───────────────────────────────────────────────
    document.querySelectorAll('.attacker-action-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const actionId = btn.dataset.actionId;
        socket.emit('player_action', { lobby_id: lobbyId, action_id: actionId });
        setActionsDisabled(true);
      });
    });

    // ── Analyst action buttons ────────────────────────────────────────────────
    document.querySelectorAll('.analyst-action-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const actionId = btn.dataset.actionId;
        socket.emit('player_action', { lobby_id: lobbyId, action_id: actionId });
        setActionsDisabled(true);
      });
    });

    function setActionsDisabled(disabled) {
      document.querySelectorAll('.attacker-action-btn, .analyst-action-btn').forEach(b => {
        b.disabled = disabled;
      });
    }

    // ── Opponent left mid-game ────────────────────────────────────────────────
    socket.on('opponent_left', (data) => {
      // Hide all action panels and show a dedicated message
      ['setup-panel','attacker-wait-setup','attacker-actions',
       'analyst-actions','waiting-turn','game-over-panel'].forEach(hide);

      const panel = document.getElementById('game-over-panel');
      if (panel) {
        panel.style.display = '';
        const box = document.getElementById('game-over-box');
        if (box) box.className = 'game-over-box game-over-lose';
        const icon  = document.getElementById('game-over-icon');
        const title = document.getElementById('game-over-title');
        const reason = document.getElementById('game-over-reason');
        if (icon)   icon.textContent  = '🚪';
        if (title)  title.textContent = 'Opponent Left';
        if (reason) reason.textContent = data.msg || 'Your opponent left the game.';
      }
      showToast('🚪 ' + (data.msg || 'Your opponent left.'), 'warning');
    });

    // ── Main state update handler ─────────────────────────────────────────────
    socket.on('update_game_state', (state) => {
      renderGameState(state);
    });

    function renderGameState(state) {
      // ── Progress bars ────────────────────────────────────────────────────
      const ap = state.attacker_progress;
      const dl = state.detection_level;
      setProgress('attacker-progress-fill', 'attacker-progress-pct', ap);
      setProgress('detection-fill', 'detection-pct', dl);

      // ── Turn indicator ────────────────────────────────────────────────────
      const turnLabel = document.getElementById('turn-label');
      const turnNum   = document.getElementById('turn-num');
      const turnInd   = document.getElementById('turn-indicator');
      if (state.phase === 'setup') {
        turnLabel.textContent = '🛡️ Analyst Setup Phase';
        if (turnNum) turnNum.textContent = '';
        if (turnInd) turnInd.className = 'turn-indicator turn-analyst';
      } else if (state.phase === 'playing') {
        const isMyTurn = state.current_player === myRole;
        if (state.current_player === 'attacker') {
          turnLabel.textContent = isMyTurn ? '⚔️ Your Turn — Attack!' : '⚔️ Attacker\'s Turn';
          if (turnInd) turnInd.className = 'turn-indicator turn-attacker';
        } else {
          turnLabel.textContent = isMyTurn ? '🛡️ Your Turn — Respond!' : '🛡️ Analyst\'s Turn';
          if (turnInd) turnInd.className = 'turn-indicator turn-analyst';
        }
        if (turnNum) turnNum.textContent = 'Turn ' + state.turn;
      } else if (state.phase === 'ended') {
        turnLabel.textContent = '🏁 Game Over';
        if (turnNum) turnNum.textContent = '';
      }

      // ── Phase badge ───────────────────────────────────────────────────────
      const phaseBadge = document.getElementById('game-phase-badge');
      if (phaseBadge) {
        phaseBadge.textContent =
          state.phase === 'setup'   ? 'Setup' :
          state.phase === 'playing' ? 'Playing' : 'Game Over';
        phaseBadge.className =
          'phase-badge phase-' + state.phase;
      }

      // ── Action panels ─────────────────────────────────────────────────────
      hide('setup-panel');
      hide('attacker-wait-setup');
      hide('attacker-actions');
      hide('analyst-actions');
      hide('waiting-turn');
      hide('game-over-panel');

      if (state.phase === 'setup') {
        if (myRole === 'analyst') {
          show('setup-panel');
        } else {
          show('attacker-wait-setup');
        }
      } else if (state.phase === 'playing') {
        const isMyTurn = state.current_player === myRole;
        if (isMyTurn) {
          if (myRole === 'attacker') {
            show('attacker-actions');
            updateAttackerButtons(state);
            setActionsDisabled(false);
          } else {
            show('analyst-actions');
            setActionsDisabled(false);
          }
        } else {
          show('waiting-turn');
          const wt = document.getElementById('waiting-text');
          if (wt) wt.textContent =
            state.current_player === 'attacker'
              ? '⚔️ Attacker is choosing their move…'
              : '🛡️ Analyst is responding…';
        }
      } else if (state.phase === 'ended') {
        show('game-over-panel');
        renderGameOver(state);
      }

      // ── Logs ──────────────────────────────────────────────────────────────
      renderLogs(state.logs);

      // ── Alerts ───────────────────────────────────────────────────────────
      renderAlerts(state.alerts);
    }

    function updateAttackerButtons(state) {
      const actions = state.attacker_actions || [];
      actions.forEach(action => {
        const btn = document.querySelector(`.attacker-action-btn[data-action-id="${action.id}"]`);
        if (!btn) return;
        btn.disabled = action.locked;
        const lockEl = btn.querySelector('.action-lock');
        if (lockEl) lockEl.style.display = action.locked ? 'inline' : 'none';
        if (action.locked) {
          btn.title = `Requires ${action.requires_progress}% attacker progress`;
        }
      });
    }

    function renderLogs(logs) {
      const container = document.getElementById('log-container');
      if (!container) return;
      if (!logs || logs.length === 0) {
        container.innerHTML = '<p class="log-empty">No log entries yet.</p>';
        return;
      }
      container.innerHTML = logs.slice().reverse().map(line => {
        const cls = line.includes('GAME OVER') ? 'log-line log-critical'
                  : line.includes('SETUP')     ? 'log-line log-setup'
                  : line.includes('ALERT')     ? 'log-line log-alert'
                  : 'log-line';
        return `<div class="${cls}">${escapeHtml(line)}</div>`;
      }).join('');
    }

    function renderAlerts(alerts) {
      const container = document.getElementById('alerts-container');
      if (!container) return;
      if (!alerts || alerts.length === 0) {
        container.innerHTML = '<p class="log-empty">No alerts yet.</p>';
        return;
      }
      container.innerHTML = alerts.slice().reverse().map(a =>
        `<div class="alert-line">${escapeHtml(a)}</div>`
      ).join('');
    }

    function renderGameOver(state) {
      const box    = document.getElementById('game-over-box');
      const icon   = document.getElementById('game-over-icon');
      const title  = document.getElementById('game-over-title');
      const reason = document.getElementById('game-over-reason');
      const sub    = document.getElementById('game-over-sub');

      const iWon = state.winner === myRole;

      if (icon)  icon.textContent  = iWon ? '🏆' : '💀';
      if (title) title.textContent = iWon ? 'You Win!' : 'You Lose!';
      if (reason) reason.textContent = state.win_reason || '';
      if (sub) {
        sub.textContent = iWon
          ? `🎉 Congratulations! Score: ${myRole === 'attacker' ? state.attacker_progress : state.detection_level}%`
          : `Better luck next time. Your score: ${myRole === 'attacker' ? state.attacker_progress : state.detection_level}%`;
      }
      if (box) {
        box.className = 'game-over-box ' + (iWon ? 'game-over-win' : 'game-over-lose');
      }
    }

    function setProgress(fillId, pctId, value) {
      const fill = document.getElementById(fillId);
      const pct  = document.getElementById(pctId);
      if (fill) fill.style.width = value + '%';
      if (pct)  pct.textContent  = value + '%';
    }

    function show(id) {
      const el = document.getElementById(id);
      if (el) el.style.display = '';
    }

    function hide(id) {
      const el = document.getElementById(id);
      if (el) el.style.display = 'none';
    }

    function escapeHtml(str) {
      return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
  } // end IS_GAME_PAGE

})();
