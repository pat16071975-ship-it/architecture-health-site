(() => {
  const DATA_KEY = 'az-patient-transitions-v1';
  const IMPORT_KEY = 'az-transitions-import-key';
  const META_URL = './transitions-data.meta.json?v=20260904-1';

  function captureKey() {
    const h = location.hash || '';
    if (h.startsWith('#azservices=')) {
      sessionStorage.setItem(IMPORT_KEY, h.slice(12));
      history.replaceState(null, '', location.pathname + location.search);
    }
  }

  function readLocal() {
    try { return JSON.parse(localStorage.getItem(DATA_KEY) || 'null'); }
    catch { return null; }
  }

  function b64u(s) {
    s = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  }

  async function sha256Bytes(bytes) {
    const h = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(h)].map(x => x.toString(16).padStart(2, '0')).join('');
  }

  async function sha256Text(text) {
    return sha256Bytes(new TextEncoder().encode(text));
  }

  function unpackLevel(rawLevel) {
    const entities = rawLevel?.[0] || [];
    const compactCohorts = rawLevel?.[1] || {};
    const cohorts = {};

    Object.entries(compactCohorts).forEach(([cohortId, records]) => {
      const sources = {};
      (records || []).forEach(record => {
        const source = entities[record[0]];
        const targets = {};
        (record[8] || []).forEach(target => {
          targets[entities[target[0]]] = {
            count30: target[1],
            count60: target[2],
            count90: target[3],
            countAll: target[4],
            avgDays: target[5],
            medianDays: target[6]
          };
        });
        sources[source] = {
          cohort: record[1],
          any30: record[2],
          any60: record[3],
          any90: record[4],
          anyAll: record[5],
          medianFirstOtherDays: record[6],
          anchorMax: record[7],
          targets
        };
      });
      cohorts[cohortId] = sources;
    });

    return { entities, cohorts };
  }

  function unpack(raw) {
    if (!raw || raw.v !== 2 || !raw.l) return raw;
    const q = raw.q || [];
    return {
      id: raw.i,
      version: raw.v,
      generatedAt: raw.g,
      dataStart: raw.s,
      dataEnd: raw.d,
      months: (raw.m || []).map(x => ({ id: x[0], label: x[1] })),
      levels: {
        direction: unpackLevel(raw.l.direction),
        doctor: unpackLevel(raw.l.doctor)
      },
      quality: {
        completedAppointmentRows: q[0] || 0,
        patientDoctorDateEvents: q[1] || 0,
        uniquePatients: q[2] || 0,
        clinicalExecutors: q[3] || 0,
        directionCount: q[4] || 0,
        excludedObviousTestPatients: q[5] || 0,
        excludedObviousTestEvents: q[6] || 0
      }
    };
  }

  async function importSecure() {
    captureKey();
    const current = readLocal();
    const keyText = sessionStorage.getItem(IMPORT_KEY);
    if (!keyText) return current;

    try {
      const meta = await fetch(META_URL, { cache: 'no-store' }).then(r => {
        if (!r.ok) throw new Error('Не загружены метаданные переходов');
        return r.json();
      });
      const cipherText = (await Promise.all((meta.parts || []).map((part, index) =>
        fetch(part + '?v=20260904-1-' + index, { cache: 'no-store' }).then(r => {
          if (!r.ok) throw new Error('Не загружена часть пакета переходов');
          return r.text();
        })
      ))).map(x => x.trim()).join('');

      if (!cipherText) throw new Error('Пакет переходов пуст');
      if (meta.ciphertextSha256 && await sha256Text(cipherText) !== meta.ciphertextSha256) {
        throw new Error('Нарушена целостность шифротекста');
      }

      const key = await crypto.subtle.importKey(
        'raw',
        b64u(keyText),
        { name: 'AES-GCM' },
        false,
        ['decrypt']
      );
      const compressed = new Uint8Array(await crypto.subtle.decrypt({
        name: 'AES-GCM',
        iv: b64u(meta.iv),
        additionalData: new TextEncoder().encode(meta.aad),
        tagLength: 128
      }, key, b64u(cipherText)));

      if (meta.compressedSha256 && await sha256Bytes(compressed) !== meta.compressedSha256) {
        throw new Error('Нарушена целостность сжатых данных');
      }

      const ds = new DecompressionStream('gzip');
      const plain = new Uint8Array(
        await new Response(new Blob([compressed]).stream().pipeThrough(ds)).arrayBuffer()
      );

      if (meta.plaintextSha256 && await sha256Bytes(plain) !== meta.plaintextSha256) {
        throw new Error('Нарушена целостность данных');
      }

      const parsed = unpack(JSON.parse(new TextDecoder().decode(plain)));
      localStorage.setItem(DATA_KEY, JSON.stringify(parsed));
      sessionStorage.removeItem(IMPORT_KEY);
      window.dispatchEvent(new CustomEvent('az-transitions-ready', { detail: parsed }));
      return parsed;
    } catch (error) {
      console.error('Transitions import failed', error);
      window.dispatchEvent(new CustomEvent('az-transitions-error', { detail: String(error?.message || error) }));
      return current;
    }
  }

  const ready = importSecure();
  window.AZTransitionsData = {
    key: DATA_KEY,
    ready,
    get: readLocal,
    captureKey
  };
})();
