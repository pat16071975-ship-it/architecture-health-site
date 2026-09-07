(() => {
  'use strict';

  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  function install() {
    const d = frame.contentDocument;
    if (!d || d.getElementById('az-mobile-report-fix')) return;

    const style = d.createElement('style');
    style.id = 'az-mobile-report-fix';
    style.textContent = `
      @media (max-width: 700px) {
        html, body {
          overflow-y: auto !important;
          overscroll-behavior-y: auto !important;
          -webkit-overflow-scrolling: touch;
        }

        .az-sticky-report-scroll,
        .table-wrap,
        .matrix-wrap {
          max-height: none !important;
          overflow-x: auto !important;
          overflow-y: visible !important;
          overscroll-behavior: auto !important;
          touch-action: pan-x pan-y !important;
          -webkit-overflow-scrolling: touch;
        }

        .az-sticky-report-table .az-sticky-report-first-head,
        .az-sticky-report-table .az-sticky-report-first-col {
          width: 30vw !important;
          min-width: 30vw !important;
          max-width: 30vw !important;
          white-space: normal !important;
          overflow-wrap: anywhere !important;
        }
      }
    `;
    (d.head || d.documentElement).appendChild(style);
  }

  frame.addEventListener('load', () => {
    install();
    setTimeout(install, 100);
    setTimeout(install, 500);
  });

  if (frame.contentDocument?.readyState === 'interactive' || frame.contentDocument?.readyState === 'complete') {
    install();
  }
})();
