/**
 * main.js — General utilities for non-scenario pages
 */

document.addEventListener('DOMContentLoaded', () => {
  initLoginTabs();
  initFlashDismiss();
});

/**
 * Tab switching on the login page.
 */
function initLoginTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  if (!tabBtns.length) return;

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.tab;

      // Update button active state
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      // Show/hide tab content
      document.querySelectorAll('.tab-content').forEach(tc => {
        tc.classList.remove('active');
      });
      const content = document.getElementById('tab-' + target);
      if (content) content.classList.add('active');
    });
  });
}

/**
 * Auto-dismiss flash messages after 5 seconds.
 */
function initFlashDismiss() {
  const alerts = document.querySelectorAll('.alert');
  alerts.forEach(alert => {
    setTimeout(() => {
      alert.style.transition = 'opacity 0.5s ease';
      alert.style.opacity = '0';
      setTimeout(() => alert.remove(), 500);
    }, 5000);
  });
}
