const tabs = [...document.querySelectorAll('[role="tab"]')];
const nav = [...document.querySelectorAll('[data-section]')];
function selectSection(id, focus = false) {
  if (!tabs.some(tab => tab.getAttribute('aria-controls') === id)) id = 'quizzes';
  for (const tab of tabs) {
    const selected = tab.getAttribute('aria-controls') === id;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    document.getElementById(tab.getAttribute('aria-controls')).hidden = !selected;
    if (selected && focus) tab.focus();
  }
  for (const link of nav) link.classList.toggle('selected', link.dataset.section === id);
}
for (const tab of tabs) {
  tab.addEventListener('click', () => {selectSection(tab.getAttribute('aria-controls'));history.replaceState(null, '', '#' + tab.getAttribute('aria-controls'));});
  tab.addEventListener('keydown', event => {
    const index = tabs.indexOf(tab);
    const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
    if (next !== null) {event.preventDefault();selectSection(tabs[next].getAttribute('aria-controls'), true);history.replaceState(null, '', '#' + tabs[next].getAttribute('aria-controls'));}
  });
}
for (const link of nav) link.addEventListener('click', event => {event.preventDefault();selectSection(link.dataset.section);history.replaceState(null, '', '#' + link.dataset.section);document.getElementById(link.dataset.section).scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});});
window.addEventListener('hashchange', () => selectSection(location.hash.slice(1)));
selectSection(location.hash.slice(1));
const answers = [...document.querySelectorAll('[data-answer]')];
const feedback = document.querySelector('.quiz-feedback');
const reveal = document.querySelector('.quiz-reveal');
const clock = document.querySelector('.quiz-clock span');
let countdown;
for (const answer of answers) answer.addEventListener('click', () => {
  clearInterval(countdown);
  reveal.hidden = true;
  for (const item of answers) {
    item.disabled = true;
    item.classList.remove('selected', 'incorrect', 'correct');
    item.setAttribute('aria-pressed', String(item === answer));
  }
  answer.classList.add('selected');
  let remaining = 3;
  clock.textContent = String(remaining);
  feedback.textContent = 'Prediction locked. Reveal in 3…';
  countdown = setInterval(() => {
    remaining--;
    clock.textContent = remaining > 0 ? String(remaining) : '✓';
    if (remaining > 0) {feedback.textContent = `Prediction locked. Reveal in ${remaining}…`;return;}
    clearInterval(countdown);
    answer.classList.add(answer.dataset.answer);
    document.querySelector('[data-answer="correct"]').classList.add('correct');
    reveal.hidden = false;
    feedback.textContent = answer.dataset.answer === 'correct' ? 'You called it. What gave it away?' : 'Surprised? Watch the reveal and compare your guess.';
  }, 1000);
});
document.getElementById('quiz-reset').addEventListener('click', () => {
  clearInterval(countdown);
  for (const answer of answers) {answer.disabled = false;answer.classList.remove('selected', 'incorrect', 'correct');answer.removeAttribute('aria-pressed');}
  reveal.hidden = true;
  clock.textContent = '?';
  feedback.textContent = 'Illustrative example · choose your prediction.';
});
