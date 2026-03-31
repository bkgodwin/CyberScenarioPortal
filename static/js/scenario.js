/**
 * scenario.js — Client-side scenario engine
 *
 * Features:
 *  - Branching multi-path scenarios (choices lead to different next phases)
 *  - Hint system with a configurable score penalty
 *  - Animated score change pop-ups
 *  - Correct-answer streak tracker
 *  - Achievement badges / multiple endings based on final score
 *  - Persists completed attempts to /api/save_attempt
 */

// ── State ─────────────────────────────────────────────────────────────────
let currentPhaseId   = null;   // id of the currently displayed phase
let totalScore       = 0;      // running total score
let startTime        = null;   // Date when scenario started
let timerInterval    = null;   // setInterval handle for the clock
let decisions        = [];     // array of decision records
let pendingNextPhase = null;   // next phase object waiting for "Continue"
let scenarioComplete = false;  // true once the terminal phase is reached

// Gamification state
let hintsUsed      = 0;        // number of hints revealed
let currentStreak  = 0;        // consecutive correct answers
let longestStreak  = 0;        // peak streak during scenario

const HINT_PENALTY = 5;        // score deducted per hint

// Derived from SCENARIO_DATA embedded by the template
const scenario     = SCENARIO_DATA;
const phases       = scenario.phases;
// Active phases are those that present choices (non-terminal)
const activePhases = phases.filter(p => p.choices && p.choices.length > 0);

// ── DOM helpers ───────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startTime = new Date();
  startTimer();
  updateStreak(null); // initialise streak display

  const firstPhase = phases[0];
  if (firstPhase) renderPhase(firstPhase);
});

// ── Timer ─────────────────────────────────────────────────────────────────

function startTimer() {
  timerInterval = setInterval(() => {
    const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
    const m = String(Math.floor(elapsed / 60)).padStart(2, '0');
    const s = String(elapsed % 60).padStart(2, '0');
    $('timer-display').textContent = `${m}:${s}`;
  }, 1000);
}

function stopTimer() {
  clearInterval(timerInterval);
}

function getElapsedSeconds() {
  return Math.floor((Date.now() - startTime.getTime()) / 1000);
}

// ── Phase Rendering ───────────────────────────────────────────────────────

/**
 * Render a phase: narrative, logs, choices, progress, and hint button.
 * @param {Object} phase
 */
function renderPhase(phase) {
  currentPhaseId = phase.id;

  // Reset panels
  $('outcome-panel').classList.add('hidden');
  $('completion-screen').classList.add('hidden');
  $('choices-grid').classList.remove('hidden');

  // Content
  $('phase-name').textContent      = phase.name || phase.id;
  $('phase-narrative').textContent = phase.narrative || '';

  renderLogs(phase.logs || []);
  renderChoices(phase.choices || []);
  updateProgress();

  // Hint button — show only when the phase has a hint
  const hintBtn   = $('hint-btn');
  const hintPanel = $('hint-panel');
  if (hintBtn && hintPanel) {
    hintPanel.classList.add('hidden');
    if (phase.hint) {
      hintBtn.classList.remove('hidden');
      hintBtn.disabled    = false;
      hintBtn.textContent = `💡 Request Hint (−${HINT_PENALTY} pts)`;
    } else {
      hintBtn.classList.add('hidden');
    }
  }

  $('phase-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Render SIEM log entries for the current phase.
 * @param {Array} logs
 */
function renderLogs(logs) {
  const logBody = $('log-body');
  if (!logs || logs.length === 0) {
    logBody.innerHTML = '<div class="log-placeholder">No logs for this phase.</div>';
    return;
  }
  logBody.innerHTML = logs.map(log =>
    `<div class="log-entry">
       <span class="log-time">[${escapeHtml(log.time)}]</span>
       <span class="log-source">${escapeHtml(log.source)}</span>
       <span class="log-event">${escapeHtml(log.event)}</span>
     </div>`
  ).join('');
}

/**
 * Render choice buttons for the current phase.
 * @param {Array} choices
 */
function renderChoices(choices) {
  const grid = $('choices-grid');
  if (!choices || choices.length === 0) {
    grid.innerHTML = '';
    return;
  }
  grid.innerHTML = choices.map(choice =>
    `<button class="choice-btn"
             data-choice-id="${escapeHtml(choice.id)}"
             id="choice-${escapeHtml(choice.id)}">
       ${escapeHtml(choice.text)}
     </button>`
  ).join('');

  grid.querySelectorAll('.choice-btn').forEach(btn => {
    btn.addEventListener('click', () => makeChoice(btn.dataset.choiceId));
  });
}

/**
 * Update the progress bar.  For branching scenarios the total is an estimate,
 * so we show a decision count rather than "Phase N of M".
 */
function updateProgress() {
  const made     = decisions.length;
  const estTotal = Math.max(activePhases.length, made + 1);
  // Smoothly advance the bar; cap at 92% while still active
  const pct = Math.min(((made + 0.5) / estTotal) * 100, 92);

  $('progress-bar').style.width = pct + '%';
  $('progress-label').textContent = `Decision ${made + 1}`;
}

// ── Hint System ───────────────────────────────────────────────────────────

/**
 * Reveal the hint for the current phase at a score cost.
 * Called by the hint button's onclick.
 */
function requestHint() {
  const phase = phases.find(p => p.id === currentPhaseId);
  if (!phase || !phase.hint) return;

  totalScore -= HINT_PENALTY;
  hintsUsed++;

  $('score-display').textContent = totalScore;
  animateScoreChange(-HINT_PENALTY);

  $('hint-text').textContent = phase.hint;
  $('hint-panel').classList.remove('hidden');

  const hintBtn = $('hint-btn');
  if (hintBtn) {
    hintBtn.disabled    = true;
    hintBtn.textContent = `💡 Hint Used (−${HINT_PENALTY} pts)`;
  }
}

// ── Score Animation ───────────────────────────────────────────────────────

/**
 * Create a floating "+N" or "−N" pop-up next to the score display.
 * @param {number} delta  positive or negative score change
 */
function animateScoreChange(delta) {
  const anchor = $('score-display');
  if (!anchor) return;

  const popup = document.createElement('div');
  popup.className  = 'score-popup ' + (delta >= 0 ? 'score-popup-pos' : 'score-popup-neg');
  popup.textContent = (delta >= 0 ? '+' : '') + delta;

  // Insert next to the score display
  anchor.parentElement.style.position = 'relative';
  anchor.parentElement.appendChild(popup);

  // Two-frame trick ensures the transition fires
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      popup.classList.add('score-popup-animate');
      setTimeout(() => popup.remove(), 900);
    });
  });
}

// ── Streak Tracker ────────────────────────────────────────────────────────

/**
 * Update the streak counter after a choice.
 * Pass null on init to just refresh the display.
 * @param {boolean|null} isCorrect
 */
function updateStreak(isCorrect) {
  if (isCorrect === true) {
    currentStreak++;
    if (currentStreak > longestStreak) longestStreak = currentStreak;
  } else if (isCorrect === false) {
    currentStreak = 0;
  }

  const el = $('streak-display');
  if (!el) return;

  if (currentStreak === 0) {
    el.textContent = '—';
    el.className   = 'streak-value';
  } else {
    const flames   = '🔥'.repeat(Math.min(currentStreak, 3));
    el.textContent = currentStreak + ' ' + flames;
    el.className   = 'streak-value' + (currentStreak >= 3 ? ' streak-hot' : '');
  }
}

// ── Choice Handling ───────────────────────────────────────────────────────

/**
 * Submit a choice to the backend and show the outcome.
 * @param {string} choiceId
 */
async function makeChoice(choiceId) {
  // Disable all buttons to prevent double-clicks
  document.querySelectorAll('.choice-btn').forEach(btn => {
    btn.disabled = true;
  });

  try {
    const res = await fetch('/api/submit_choice', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario_id: scenario.id,
        phase_id:    currentPhaseId,
        choice_id:   choiceId,
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();

    // Record decision
    decisions.push({
      phase_id:     currentPhaseId,
      phase_name:   phases.find(p => p.id === currentPhaseId)?.name || currentPhaseId,
      choice_id:    choiceId,
      choice_text:  phases.find(p => p.id === currentPhaseId)
                          ?.choices?.find(c => c.id === choiceId)?.text || choiceId,
      score_impact: data.score_impact,
      is_correct:   data.is_correct,
    });

    // Update score
    totalScore += data.score_impact;
    $('score-display').textContent = totalScore;
    animateScoreChange(data.score_impact);

    // Streak
    updateStreak(data.is_correct);

    // Highlight the selected button
    const selectedBtn = document.getElementById('choice-' + choiceId);
    if (selectedBtn) {
      selectedBtn.classList.add(data.is_correct ? 'selected-correct' : 'selected-wrong');
    }

    pendingNextPhase = data.next_phase;
    showOutcome(data);

  } catch (err) {
    console.error('Error submitting choice:', err);
    alert('Error submitting your choice. Please try again.');
    document.querySelectorAll('.choice-btn').forEach(btn => {
      btn.disabled = false;
    });
  }
}

/**
 * Display the outcome panel after a choice is made.
 * @param {Object} data  API response from /api/submit_choice
 */
function showOutcome(data) {
  const panel = $('outcome-panel');

  $('outcome-icon').textContent = data.is_correct ? '✅' : '❌';
  $('outcome-text').textContent = data.outcome;

  const scoreEl = $('outcome-score');
  const sign    = data.score_impact >= 0 ? '+' : '';
  scoreEl.textContent = `${sign}${data.score_impact} points`;
  scoreEl.className   = 'outcome-score ' + (data.score_impact >= 0 ? 'positive' : 'negative');

  const isTerminal = !data.next_phase || !data.next_phase.choices || data.next_phase.choices.length === 0;
  $('continue-btn').textContent = isTerminal ? 'View Results →' : 'Continue →';

  panel.classList.remove('hidden');
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Continue / Completion ─────────────────────────────────────────────────

/**
 * Advance to the next phase or show the completion screen.
 */
async function continueScenario() {
  const nextPhase = pendingNextPhase;

  if (!nextPhase || !nextPhase.choices || nextPhase.choices.length === 0) {
    await finishScenario(nextPhase);
    return;
  }

  renderPhase(nextPhase);
  pendingNextPhase = null;
}

/**
 * Show the completion screen and persist the attempt.
 * @param {Object|null} finalPhase  the terminal (choice-less) phase, if any
 */
async function finishScenario(finalPhase) {
  stopTimer();
  scenarioComplete = true;

  const timeTaken = getElapsedSeconds();

  $('choices-grid').classList.add('hidden');
  $('outcome-panel').classList.add('hidden');

  if (finalPhase) {
    $('phase-name').textContent      = finalPhase.name || 'Complete';
    $('phase-narrative').textContent = finalPhase.narrative || '';
  }

  $('progress-bar').style.width   = '100%';
  $('progress-label').textContent = 'Complete';

  $('final-score-value').textContent = totalScore;

  renderEnding(totalScore);
  renderDecisionBreakdown();

  $('completion-screen').classList.remove('hidden');
  $('completion-screen').scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Persist attempt
  try {
    await fetch('/api/save_attempt', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario_id:    scenario.id,
        decisions:      decisions,
        total_score:    totalScore,
        time_taken:     timeTaken,
        hints_used:     hintsUsed,
        longest_streak: longestStreak,
      }),
    });
  } catch (err) {
    console.error('Could not save attempt:', err);
  }
}

// ── Ending / Badge ─────────────────────────────────────────────────────────

/**
 * Look up and display the appropriate ending/badge for the player's final score.
 * The scenario JSON's "endings" array must be sorted by min_score descending.
 * @param {number} score
 */
function renderEnding(score) {
  const endingPanel = $('ending-panel');
  if (!endingPanel) return;

  const endings = scenario.endings;
  if (!endings || !endings.length) {
    endingPanel.classList.add('hidden');
    return;
  }

  // Find the first (highest) tier the player qualifies for
  const ending = endings.find(e => score >= e.min_score);
  if (!ending) {
    endingPanel.classList.add('hidden');
    return;
  }

  const badgeEl = $('ending-badge');
  const titleEl = $('ending-title');
  const descEl  = $('ending-description');

  if (badgeEl) badgeEl.textContent = ending.badge || '🏅';
  if (titleEl) titleEl.textContent = ending.title || '';
  if (descEl)  descEl.textContent  = ending.description || '';

  endingPanel.classList.remove('hidden');
}

// ── Decision Breakdown ─────────────────────────────────────────────────────

/**
 * Render the per-decision table on the completion screen.
 */
function renderDecisionBreakdown() {
  const container = $('decision-breakdown');
  if (!decisions.length) {
    container.innerHTML = '<p style="color:var(--text-muted);text-align:center">No decisions recorded.</p>';
    return;
  }

  const rows = decisions.map(d => {
    const sign = d.score_impact >= 0 ? '+' : '';
    const cls  = d.score_impact >= 0 ? 'pos' : 'neg';
    return `<div class="decision-row">
      <span class="decision-phase">${escapeHtml(d.phase_name)}</span>
      <span class="decision-choice">${escapeHtml(d.choice_text)}</span>
      <span class="decision-points ${cls}">${sign}${d.score_impact} pts</span>
    </div>`;
  }).join('');

  // Append hint penalty summary row if hints were used
  const hintRow = hintsUsed > 0
    ? `<div class="decision-row hint-summary-row">
         <span class="decision-phase">Hint Penalties</span>
         <span class="decision-choice">${hintsUsed} hint${hintsUsed > 1 ? 's' : ''} revealed</span>
         <span class="decision-points neg">−${hintsUsed * HINT_PENALTY} pts</span>
       </div>`
    : '';

  container.innerHTML = rows + hintRow;
}

// ── Log Viewer Toggle ─────────────────────────────────────────────────────

/**
 * Toggle visibility of the SIEM log panel.
 */
function toggleLogs() {
  const body      = $('log-body');
  const btn       = $('log-toggle-btn');
  const collapsed = body.classList.toggle('collapsed');
  btn.textContent = collapsed ? '▶ Expand' : '▼ Collapse';
}

// ── Utility ───────────────────────────────────────────────────────────────

/**
 * Escape HTML special characters to prevent XSS when inserting into innerHTML.
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  if (typeof str !== 'string') return String(str);
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
