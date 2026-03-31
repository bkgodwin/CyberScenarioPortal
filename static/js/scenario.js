/**
 * scenario.js — Client-side scenario engine
 *
 * This module drives the single-page scenario experience:
 *  - Tracks current phase, score, decisions, and elapsed time
 *  - Sends choices to /api/submit_choice and updates the DOM
 *  - Saves the completed attempt to /api/save_attempt
 */

// ── State ─────────────────────────────────────────────────────────────────
let currentPhaseId  = null;   // id of the currently displayed phase
let totalScore      = 0;      // running total score
let startTime       = null;   // Date object when scenario started
let timerInterval   = null;   // setInterval handle for the clock
let decisions       = [];     // [{phase_id, choice_id, score_impact, is_correct}]
let pendingNextPhase = null;  // next phase object returned by API, waiting for "Continue"
let scenarioComplete = false; // true once the final phase is reached

// Derived from embedded SCENARIO_DATA (injected by the template)
const scenario     = SCENARIO_DATA;
const phases       = scenario.phases;
// Count of phases that have choices (exclude terminal "complete" phases)
const activePhases = phases.filter(p => p.choices && p.choices.length > 0);

// ── DOM helpers ───────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Init ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startTime = new Date();
  startTimer();

  // Begin at the first phase
  const firstPhase = phases[0];
  if (firstPhase) renderPhase(firstPhase);
});

// ── Timer ─────────────────────────────────────────────────────────────────

/**
 * Start the elapsed-time ticker and update the display every second.
 */
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
 * Render a phase: update narrative, logs, choices, and progress bar.
 * @param {Object} phase  — phase object from scenario JSON
 */
function renderPhase(phase) {
  currentPhaseId = phase.id;

  // Hide outcome panel and completion screen
  $('outcome-panel').classList.add('hidden');
  $('completion-screen').classList.add('hidden');
  $('choices-grid').classList.remove('hidden');

  // Phase name and narrative
  $('phase-name').textContent = phase.name || phase.id;
  $('phase-narrative').textContent = phase.narrative || '';

  // Update log panel for blue-team scenarios
  renderLogs(phase.logs || []);

  // Render choices
  renderChoices(phase.choices || []);

  // Progress bar
  updateProgress(phase.id);

  // Scroll to top of phase container
  $('phase-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * Render SIEM log entries for a phase.
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

  // Attach listeners via addEventListener (avoids inline handler injection)
  grid.querySelectorAll('.choice-btn').forEach(btn => {
    btn.addEventListener('click', () => makeChoice(btn.dataset.choiceId));
  });
}

/**
 * Update the progress bar based on the current phase index.
 * @param {string} phaseId
 */
function updateProgress(phaseId) {
  const idx   = activePhases.findIndex(p => p.id === phaseId);
  const total = activePhases.length;

  if (idx === -1 || total === 0) {
    // Terminal phase — fill bar
    $('progress-bar').style.width = '100%';
    $('progress-label').textContent = 'Complete';
    return;
  }

  const pct = ((idx + 1) / total) * 100;
  $('progress-bar').style.width = pct + '%';
  $('progress-label').textContent = `Phase ${idx + 1} of ${total}`;
}

// ── Choice Handling ───────────────────────────────────────────────────────

/**
 * Called when the student clicks a choice button.
 * Sends the choice to the API and displays the outcome.
 * @param {string} choiceId
 */
async function makeChoice(choiceId) {
  // Disable all choice buttons to prevent double-clicking
  document.querySelectorAll('.choice-btn').forEach(btn => {
    btn.disabled = true;
  });

  try {
    const res = await fetch('/api/submit_choice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario_id: scenario.id,
        phase_id:    currentPhaseId,
        choice_id:   choiceId,
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();

    // Record this decision
    decisions.push({
      phase_id:     currentPhaseId,
      phase_name:   phases.find(p => p.id === currentPhaseId)?.name || currentPhaseId,
      choice_id:    choiceId,
      choice_text:  document.getElementById('choice-' + choiceId)?.textContent?.trim() || choiceId,
      score_impact: data.score_impact,
      is_correct:   data.is_correct,
    });

    // Update running score
    totalScore += data.score_impact;
    $('score-display').textContent = totalScore;

    // Highlight the selected button
    const selectedBtn = document.getElementById('choice-' + choiceId);
    if (selectedBtn) {
      selectedBtn.classList.add(data.is_correct ? 'selected-correct' : 'selected-wrong');
    }

    // Store next phase for "Continue" button
    pendingNextPhase = data.next_phase;

    // Show outcome
    showOutcome(data);

  } catch (err) {
    console.error('Error submitting choice:', err);
    alert('Error submitting your choice. Please try again.');
    // Re-enable buttons on error
    document.querySelectorAll('.choice-btn').forEach(btn => {
      btn.disabled = false;
    });
  }
}

/**
 * Display the outcome panel after a choice is made.
 * @param {Object} data  — API response from /api/submit_choice
 */
function showOutcome(data) {
  const panel = $('outcome-panel');

  $('outcome-icon').textContent  = data.is_correct ? '✅' : '❌';
  $('outcome-text').textContent  = data.outcome;

  const scoreEl = $('outcome-score');
  const sign    = data.score_impact >= 0 ? '+' : '';
  scoreEl.textContent = `${sign}${data.score_impact} points`;
  scoreEl.className   = 'outcome-score ' + (data.score_impact >= 0 ? 'positive' : 'negative');

  // Determine button label
  const isTerminal = !data.next_phase || !data.next_phase.choices || data.next_phase.choices.length === 0;
  $('continue-btn').textContent = isTerminal ? 'View Results →' : 'Continue →';

  panel.classList.remove('hidden');
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Continue / Completion ─────────────────────────────────────────────────

/**
 * Called when the student clicks the "Continue" button after seeing an outcome.
 * Either advances to the next phase or shows the completion screen.
 */
async function continueScenario() {
  const nextPhase = pendingNextPhase;

  if (!nextPhase || !nextPhase.choices || nextPhase.choices.length === 0) {
    // No more choices — scenario is complete
    await finishScenario(nextPhase);
    return;
  }

  // Advance to next phase
  renderPhase(nextPhase);
  pendingNextPhase = null;
}

/**
 * Show the completion screen and persist the attempt.
 * @param {Object|null} finalPhase  — the terminal phase (e.g., "complete")
 */
async function finishScenario(finalPhase) {
  stopTimer();
  scenarioComplete = true;

  const timeTaken = getElapsedSeconds();

  // Render completion UI
  $('choices-grid').classList.add('hidden');
  $('outcome-panel').classList.add('hidden');

  // Update phase name to the final phase name if available
  if (finalPhase) {
    $('phase-name').textContent = finalPhase.name || 'Complete';
    $('phase-narrative').textContent = finalPhase.narrative || '';
  }

  // Fill completion screen
  $('final-score-value').textContent = totalScore;
  renderDecisionBreakdown();

  $('completion-screen').classList.remove('hidden');
  $('completion-screen').scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Progress bar to 100%
  $('progress-bar').style.width = '100%';
  $('progress-label').textContent = 'Complete';

  // Persist attempt
  try {
    await fetch('/api/save_attempt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scenario_id:  scenario.id,
        decisions:    decisions,
        total_score:  totalScore,
        time_taken:   timeTaken,
      }),
    });
  } catch (err) {
    console.error('Could not save attempt:', err);
  }
}

/**
 * Render the per-decision breakdown table on the completion screen.
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

  container.innerHTML = rows;
}

// ── Log Viewer Toggle ─────────────────────────────────────────────────────

/**
 * Toggle visibility of the SIEM log panel body.
 */
function toggleLogs() {
  const body = $('log-body');
  const btn  = $('log-toggle-btn');
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
