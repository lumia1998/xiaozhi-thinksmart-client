/* The supplied portrait has its eyes removed; these two overlays share the original coordinates. */
(() => {
  const avatar = document.getElementById('avatar');
  const eyes = [...avatar.querySelectorAll('.avatar-eye')];
  const image = avatar.querySelector('img');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const still = new URLSearchParams(location.search).has('still');
  let timer = 0, doubleTimer = 0;
  const frames = [
    { transform: 'scale(1, 1)', offset: 0 },
    { transform: 'scale(1.06, .1)', offset: .4 },
    { transform: 'scale(1.06, .1)', offset: .55 },
    { transform: 'scale(1, 1)', offset: 1 },
  ];
  const visible = () => !document.hidden && !reduced.matches && !still && image.naturalWidth > 0 && !avatar.classList.contains('static')
    && !document.querySelector('.sheet.on, .ringing, .power-off.on');

  function blink() {
    if (!visible()) return;
    eyes.forEach(eye => {
      eye.getAnimations().forEach(animation => animation.cancel());
      eye.animate(frames, { duration: 250, easing: 'ease-out' });
    });
  }

  function stop() {
    clearTimeout(timer);
    clearTimeout(doubleTimer);
    timer = doubleTimer = 0;
    eyes.forEach(eye => eye.getAnimations().forEach(animation => animation.cancel()));
  }

  function schedule() {
    clearTimeout(timer);
    if (document.hidden || reduced.matches || still || avatar.classList.contains('static')) return;
    timer = setTimeout(() => {
      blink();
      if (Math.random() < .2) doubleTimer = setTimeout(blink, 320);
      schedule();
    }, 2200 + Math.random() * 3800);
  }

  function resume() { stop(); schedule(); }
  document.addEventListener('visibilitychange', resume);
  reduced.addEventListener('change', resume);
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', resume);
  // A static portrait is preferable to detached eyes if the separate base image fails to load.
  function fallback() {
    if (avatar.classList.contains('static')) return;
    avatar.classList.add('static');
    stop();
    image.src = '/static/v2/avatar/avatar.webp';
  }
  image.addEventListener('error', fallback);
  if (image.complete && !image.naturalWidth) fallback();
  schedule();
})();
