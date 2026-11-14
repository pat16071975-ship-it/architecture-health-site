(() => {
  document.querySelectorAll('.t2-photo').forEach(gallery => {
    const frames = [...gallery.querySelectorAll('.t2-frame')];
    let index = 0;
    gallery.querySelectorAll('[data-step]').forEach(button => {
      button.addEventListener('click', () => {
        frames[index].hidden = true;
        index = (index + Number(button.dataset.step) + frames.length) % frames.length;
        frames[index].hidden = false;
        gallery.querySelector('.t2-count').textContent = `${index + 1} / ${frames.length}`;
      });
    });
  });
  const search = document.querySelector('.cataloghead input');
  const update = () => document.body.classList.toggle('searching', !!search.value.trim());
  search.addEventListener('input', update);
  document.querySelector('#panel').addEventListener('click', () => requestAnimationFrame(update));
  update();
})();
