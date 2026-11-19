(() => {
  const controls = document.querySelector('.tools');
  if (!controls) return;
  const buttons = Array.from(controls.querySelectorAll('button'));
  const cards = Array.from(document.querySelectorAll('#other-cases > li'));
  const output = document.getElementById('filter-status');
  controls.hidden = false;
  controls.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || !controls.contains(button)) return;
    const topic = button.value;
    let count = 0;
    for (const card of cards) {
      card.hidden = topic !== 'all' && !card.dataset.topics.split(' ').includes(topic);
      if (!card.hidden) count++;
    }
    for (const item of buttons) item.setAttribute('aria-pressed', String(item === button));
    output.textContent = 'Количество историй в списке: ' + count;
  });
})();
