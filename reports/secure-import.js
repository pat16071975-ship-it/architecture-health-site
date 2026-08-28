(() => {
  const HASH_PARAM = 'azimport';
  const IMPORT_SESSION_KEY = 'az-secure-import-key-v1';
  const IMPORT_MARKER = 'az-secure-imported:az-report-2026-08-26-v1';
  const SEED_MARKER = 'az-management-seed-2026-07';
  const PACKAGE_URL = './secure-import-20260826.json?v=20260828-1';
  const EXPECTED_ID = 'az-report-2026-08-26-v1';
  let running = false;

  const bytesFromBase64Url = value => {
    const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized + '='.repeat((4 - normalized.length % 4) % 4);
    return Uint8Array.from(atob(padded), char => char.charCodeAt(0));
  };

  const hex = bytes => [...new Uint8Array(bytes)]
    .map(value => value.toString(16).padStart(2, '0'))
    .join('');

  function captureImportKey() {
    const hash = location.hash.startsWith('#') ? location.hash.slice(1) : '';
    if (!hash) return;
    const params = new URLSearchParams(hash);
    const key = params.get(HASH_PARAM);
    if (!key) return;
    sessionStorage.setItem(IMPORT_SESSION_KEY, key);
    history.replaceState(null, '', location.pathname + location.search);
  }

  function hasImportedDates(store, dates) {
    return Array.isArray(dates) && dates.length > 0 && dates.every(date => store[date]);
  }

  async function decryptPackage(importKey, encryptedPackage) {
    if (!encryptedPackage || encryptedPackage.id !== EXPECTED_ID) {
      throw new Error('Unexpected import package');
    }
    const rawKey = bytesFromBase64Url(importKey);
    if (rawKey.byteLength !== 32) throw new Error('Invalid import key');
    const key = await crypto.subtle.importKey(
      'raw',
      rawKey,
      {name: 'AES-GCM'},
      false,
      ['decrypt']
    );
    const plaintext = await crypto.subtle.decrypt(
      {
        name: 'AES-GCM',
        iv: bytesFromBase64Url(encryptedPackage.iv),
        additionalData: new TextEncoder().encode(encryptedPackage.aad)
      },
      key,
      bytesFromBase64Url(encryptedPackage.ciphertext)
    );
    if (encryptedPackage.plaintextSha256) {
      const digest = await crypto.subtle.digest('SHA-256', plaintext);
      if (hex(digest) !== encryptedPackage.plaintextSha256) {
        throw new Error('Import integrity check failed');
      }
    }
    return JSON.parse(new TextDecoder().decode(plaintext));
  }

  async function applySecureImport() {
    if (running) return;
    const importKey = sessionStorage.getItem(IMPORT_SESSION_KEY);
    if (!importKey || sessionStorage.getItem(SESSION_KEY) !== '1') return;

    running = true;
    try {
      const response = await fetch(PACKAGE_URL, {cache: 'no-store'});
      if (!response.ok) throw new Error(`Import package HTTP ${response.status}`);
      const encryptedPackage = await response.json();
      const currentStore = loadStore();

      if (
        localStorage.getItem(IMPORT_MARKER) === '1' &&
        hasImportedDates(currentStore, encryptedPackage.recordDates)
      ) {
        sessionStorage.removeItem(IMPORT_SESSION_KEY);
        const existingDate = encryptedPackage.recordDates[encryptedPackage.recordDates.length - 1];
        const dateInput = document.querySelector('#reportDate');
        if (dateInput) dateInput.value = existingDate;
        if (typeof loadDate === 'function') loadDate();
        const status = document.querySelector('#status');
        if (status) status.textContent = 'Данные за 26.08.2026 уже были загружены ранее.';
        return;
      }

      const payload = await decryptPackage(importKey, encryptedPackage);
      if (!payload || !payload.data || typeof payload.data !== 'object' || Array.isArray(payload.data)) {
        throw new Error('Invalid decrypted payload');
      }
      const entries = Object.entries(payload.data).filter(
        ([date, record]) =>
          /^\d{4}-\d{2}-\d{2}$/.test(date) &&
          record &&
          typeof record === 'object' &&
          !Array.isArray(record)
      );
      if (!entries.length) throw new Error('No dated records');

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
      const mode = document.querySelector('#mode');
      if (mode) mode.value = 'date';
      if (typeof switchMode === 'function') switchMode();
      const dateInput = document.querySelector('#reportDate');
      if (dateInput) dateInput.value = latest;
      if (typeof loadDate === 'function') loadDate();

      const status = document.querySelector('#status');
      if (status) {
        status.textContent = `Данные за ${new Date(latest + 'T12:00:00').toLocaleDateString('ru-RU')} загружены автоматически.`;
      }
      alert('Данные за 26.08.2026 загружены в управленческий отчёт.');
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

  const form = document.querySelector('#loginForm');
  if (form) {
    form.addEventListener('submit', () => {
      setTimeout(waitForAuthenticatedImport, 100);
    });
  }

  if (sessionStorage.getItem(IMPORT_SESSION_KEY)) {
    waitForAuthenticatedImport();
  }
})();