'use strict';
document.addEventListener('click', event => {
  const trigger = event.target.closest('a[href="#booking"],a[href="#mission"]');
  if (trigger) {
    event.preventDefault();
    const requested = trigger.textContent.includes('Читать полностью') ? '#mission' : trigger.getAttribute('href');
    (document.querySelector(requested) || document.querySelector('#booking'))?.showModal();
  }
  if (event.target.closest('.dialog-close')) event.target.closest('dialog').close();
  const menu = event.target.closest('.menu-toggle');
  if (menu) {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(open));
    document.querySelector('#main-nav').classList.toggle('is-open', open);
  }
  const filter = event.target.closest('[data-part]');
  if (filter) {
    const part = filter.dataset.part;
    document.querySelectorAll('[data-part]').forEach(button => button.setAttribute('aria-pressed', String(button === filter)));
    document.querySelectorAll('.doctor').forEach(card => { card.hidden = !card.dataset.parts.split(',').includes(part); });
  }
  if (event.target.closest('.cookie-accept')) {
    document.querySelector('.cookie-banner').hidden = true;
    try { localStorage.setItem('az-cookie-notice', 'accepted'); } catch {}
  }
});
try { if (localStorage.getItem('az-cookie-notice') === 'accepted') document.querySelector('.cookie-banner').hidden = true; } catch {}
// Close native dialogs by clicking their backdrop, without submitting any form.
document.querySelectorAll('dialog').forEach(dialog => dialog.addEventListener('click', event => {
  const r = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close();
}));
