(() => {
  const HASH_PARAM = 'azimport';
  const IMPORT_SESSION_KEY = 'az-secure-import-key-v1';
  const IMPORT_MARKER = 'az-secure-imported:az-report-through-2026-08-30-v1';
  const SEED_MARKER = 'az-management-seed-2026-07';
  const META_URL = './secure-import-20260830.meta.json?v=20260831-1';
  const EXPECTED_ID = 'az-report-through-2026-08-30-v1';
  let running = false;

  const bytesFromBase64Url = value => {
    const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
    return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
  };
  const hex = bytes => [...new Uint8Array(bytes)].map(v => v.toString(16).padStart(2, '0')).join('');
  async function sha256Hex(bytes) { return hex(await crypto.subtle.digest('SHA-256', bytes)); }

  function captureImportKey() {
    const hash = location.hash.startsWith('#') ? location.hash.slice(1) : '';
    if (!hash) return;
    const params = new URLSearchParams(hash);
    const key = params.get(HASH_PARAM);
    if (!key) return;
    sessionStorage.setItem(IMPORT_SESSION_KEY, key);
    history.replaceState(null, '', location.pathname + location.search);
  }

  async function readPackage() {
    const metaResponse = await fetch(META_URL, {cache: 'no-store'});
    if (!metaResponse.ok) throw new Error(`Import metadata HTTP ${metaResponse.status}`);
    const meta = await metaResponse.json();
    if (!meta || meta.id !== EXPECTED_ID || meta.compression !== 'gzip' || !Array.isArray(meta.parts)) {
      throw new Error('Unexpected import metadata');
    }
    const chunks = await Promise.all(meta.parts.map(async path => {
      const response = await fetch(path + '?v=20260831-1', {cache: 'no-store'});
      if (!response.ok) throw new Error(`Import chunk HTTP ${response.status}`);
      return (await response.text()).trim();
    }));
    return {meta, ciphertext: chunks.join('')};
  }

  async function decryptPackage(importKey, meta, ciphertext) {
    const rawKey = bytesFromBase64Url(importKey);
    if (rawKey.byteLength !== 32) throw new Error('Invalid import key');
    const key = await crypto.subtle.importKey('raw', rawKey, {name: 'AES-GCM'}, false, ['decrypt']);
    const compressed = new Uint8Array(await crypto.subtle.decrypt({
      name: 'AES-GCM',
      iv: bytesFromBase64Url(meta.iv),
      additionalData: new TextEncoder().encode(meta.aad)
    }, key, bytesFromBase64Url(ciphertext)));
    if (meta.compressedSha256 && await sha256Hex(compressed) !== meta.compressedSha256) {
      throw new Error('Compressed import integrity check failed');
    }
    if (typeof DecompressionStream !== 'function') throw new Error('Gzip decompression unavailable');
    const stream = new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'));
    const plaintext = new Uint8Array(await new Response(stream).arrayBuffer());
    if (meta.plaintextSha256 && await sha256Hex(plaintext) !== meta.plaintextSha256) {
      throw new Error('Import integrity check failed');
    }
    return JSON.parse(new TextDecoder().decode(plaintext));
  }

  function openLatestDate(date, message) {
    const mode = document.querySelector('#mode');
    if (mode) mode.value = 'date';
    if (typeof switchMode === 'function') switchMode();
    const dateInput = document.querySelector('#reportDate');
    if (dateInput) dateInput.value = date;
    if (typeof loadDate === 'function') loadDate();
    const status = document.querySelector('#status');
    if (status) status.textContent = message;
  }

  function ensureServicesLink() {
    if (document.querySelector('#servicesAnalyticsLink')) return;
    const topbar = document.querySelector('.topbar');
    if (!topbar) return;
    const link = document.createElement('a');
    link.id = 'servicesAnalyticsLink';
    link.className = 'btn';
    link.href = './services.html';
    link.textContent = 'Аналитика услуг';
    const logout = document.querySelector('#logoutBtn');
    if (logout && logout.parentNode === topbar) topbar.insertBefore(link, logout);
    else topbar.appendChild(link);
  }

  async function applySecureImport() {
    if (running) return;
    const importKey = sessionStorage.getItem(IMPORT_SESSION_KEY);
    if (!importKey || sessionStorage.getItem(SESSION_KEY) !== '1') return;
    running = true;
    try {
      if (localStorage.getItem(IMPORT_MARKER) === '1') {
        sessionStorage.removeItem(IMPORT_SESSION_KEY);
        openLatestDate('2026-08-30', 'Данные по 30.08.2026 уже были загружены ранее.');
        return;
      }
      const {meta, ciphertext} = await readPackage();
      const payload = await decryptPackage(importKey, meta, ciphertext);
      if (!payload || !payload.data || typeof payload.data !== 'object' || Array.isArray(payload.data)) {
        throw new Error('Invalid decrypted payload');
      }
      const entries = Object.entries(payload.data).filter(([date, record]) =>
        /^\d{4}-\d{2}-\d{2}$/.test(date) && record && typeof record === 'object' && !Array.isArray(record)
      );
      if (!entries.length) throw new Error('No dated records');
      if (entries.some(([date]) => date === '2026-08-26' || date > '2026-08-30')) {
        throw new Error('Import date guard failed');
      }
      const store = loadStore();
      entries.forEach(([date, record]) => {
        const base = blankRecord(date);
        store[date] = {
          ...base,
          ...record,
          date,
          dentists: {...base.dentists, ...(record.dentists || {})},
          clinicDocs: {...base.clinicDocs, ...(record.clinicDocs || {})}
        };
      });
      saveStore(store);
      localStorage.setItem(IMPORT_MARKER, '1');
      sessionStorage.removeItem(IMPORT_SESSION_KEY);
      const dates = entries.map(([date]) => date).sort();
      const latest = dates[dates.length - 1];
      openLatestDate(latest, `Импортировано записей: ${entries.length}. Данные по ${new Date(latest + 'T12:00:00').toLocaleDateString('ru-RU')} загружены.`);
      alert(`Данные загружены. Импортировано записей: ${entries.length}. 26.08 сохранено без изменений, 31.08 не загружалось.`);
    } catch (error) {
      console.error('Secure report import failed', error);
      const status = document.querySelector('#status');
      if (status) status.textContent = 'Автоматическая загрузка не выполнена. Ссылка импорта недействительна или устарела.';
      alert('Не удалось автоматически загрузить данные.');
    } finally {
      running = false;
    }
  }

  async function waitForAuthenticatedImport() {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (sessionStorage.getItem(SESSION_KEY) === '1') {
        for (let seedAttempt = 0; seedAttempt < 30; seedAttempt += 1) {
          if (localStorage.getItem(SEED_MARKER)) break;
          await new Promise(resolve => setTimeout(resolve, 100));
        }
        await applySecureImport();
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }

  captureImportKey();
  ensureServicesLink();
  const form = document.querySelector('#loginForm');
  if (form) form.addEventListener('submit', () => setTimeout(waitForAuthenticatedImport, 100));
  if (sessionStorage.getItem(IMPORT_SESSION_KEY)) waitForAuthenticatedImport();
})();