// Tema iniziale applicato prima del montaggio di React (vedi index.html).
// File separato per la CSP: script-src 'self', niente script inline (P31).
(function () {
  try {
    var t = localStorage.getItem('plenora-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', t);
    document.documentElement.style.backgroundColor = t === 'light' ? '#EFF2F9' : '#07090F';
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();
