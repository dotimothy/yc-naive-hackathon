// Segmented playback avoids relying on byte-range support from static hosting.
const demo = document.querySelector('video[data-stream]');
if (demo) {
  const stream = demo.dataset.stream;
  const fallback = demo.querySelector('source[type="video/mp4"]')?.getAttribute('src');
  const nativeHls = Boolean(demo.canPlayType('application/vnd.apple.mpegurl'));
  if (nativeHls || window.Hls?.isSupported()) {
    demo.querySelectorAll('source').forEach(source => source.remove());
    demo.removeAttribute('src');
    if (nativeHls) {
      demo.src = stream;
      demo.addEventListener('error', () => { if (fallback) demo.src = fallback; }, {once:true});
    } else {
      const player = new window.Hls({backBufferLength:10, maxBufferLength:12, maxMaxBufferLength:20});
      player.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        const resume = !demo.paused;
        const timestamp = demo.currentTime;
        player.destroy();
        if (!fallback) return;
        demo.src = fallback;
        demo.addEventListener('loadedmetadata', () => {
          demo.currentTime = timestamp;
          if (resume) demo.play().catch(() => {});
        }, {once:true});
      });
      player.loadSource(stream);
      player.attachMedia(demo);
      window.addEventListener('pagehide', event => { if (!event.persisted) player.destroy(); });
    }
  }
}
