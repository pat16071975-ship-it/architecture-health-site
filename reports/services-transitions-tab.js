(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  frame.addEventListener('load', () => {
    const d = frame.contentDocument;
    const w = frame.contentWindow;
    if (!d || !w) return;

    const tabs = d.querySelector('.tabs');
    if (!tabs || d.getElementById('azTransitionsTab')) return;

    const link = d.createElement('a');
    link.id = 'azTransitionsTab';
    link.className = 'btn';
    link.href = './transitions.html';
    link.target = '_top';
    link.textContent = 'Переходы пациентов';
    link.setAttribute('aria-label', 'Открыть аналитику переходов пациентов между специалистами');
    tabs.appendChild(link);
  });
})();
