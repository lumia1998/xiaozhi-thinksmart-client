const MediaCards = (() => {
  let active = null;
  let heartbeat = null;
  let reports = Promise.resolve();

  async function sendReport(playing, heartbeat) {
    if (new URLSearchParams(location.search).has('demo')) return true;
    try {
      const response = await fetch('/api/media/playback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playing, heartbeat }),
        signal: AbortSignal.timeout(5000),
      });
      return response.ok && (await response.json()).ok;
    } catch {
      return false;
    }
  }

  function report(playing, heartbeat = false) {
    reports = reports.then(() => sendReport(playing, heartbeat));
    return reports;
  }

  function pauseAll() {
    clearInterval(heartbeat);
    heartbeat = null;
    const previous = active;
    active = null;
    if (previous) {
      previous.pause();
      void report(false);
    }
  }

  function validUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
    } catch {
      return false;
    }
  }

  function render(container, items) {
    for (const item of items) {
      if (!['image', 'video', 'audio'].includes(item.kind) || !validUrl(item.url)) continue;
      const figure = document.createElement('figure');
      figure.className = 'media-card';
      const element = document.createElement(item.kind === 'image' ? 'img' : item.kind);
      const title = item.title || { image: '图片', video: '视频', audio: '音频' }[item.kind];
      element.setAttribute('aria-label', title);
      element.setAttribute('loading', 'lazy');
      if (item.kind === 'image') {
        element.alt = title;
        element.decoding = 'async';
        element.referrerPolicy = 'no-referrer';
      } else {
        element.controls = true;
        element.preload = 'none';
        if (item.kind === 'video') {
          element.width = 640;
          element.height = 360;
          element.playsInline = true;
        }
        element.addEventListener('play', async () => {
          if (active !== element) pauseAll();
          active = element;
          const ready = await report(true);
          if (active !== element) return;
          if (!ready) {
            pauseAll();
            status.textContent = '暂时无法播放，请稍后重试';
            return;
          }
          clearInterval(heartbeat);
          heartbeat = setInterval(async () => {
            if (!element.isConnected || !await report(true, true)) pauseAll();
          }, 5000);
        });
        for (const event of ['pause', 'ended']) element.addEventListener(event, () => {
          if (active === element) pauseAll();
        });
      }
      const caption = document.createElement('figcaption');
      caption.textContent = title;
      const status = document.createElement('span');
      status.className = 'media-status';
      status.setAttribute('role', 'status');
      element.addEventListener('error', () => {
        status.textContent = '媒体加载失败或格式不受支持';
        if (active === element) pauseAll();
      });
      element.addEventListener(item.kind === 'image' ? 'load' : 'loadeddata', () => { status.textContent = ''; });
      element.src = item.url;
      figure.append(element, caption, status);
      container.appendChild(figure);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) pauseAll();
  });
  window.addEventListener('pagehide', pauseAll);
  return { render, pauseAll };
})();
