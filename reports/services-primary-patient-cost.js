(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const COST_KEY = 'az-clinic-primary-cost-v1';
  const IMPORT_KEY = 'az-primary-cost-import-key';
  const LAB_DIRECTION = 'Лаборатория';
  const META_URL = './services-primary-cost.meta.json?v=20260903-1';
  let observer = null;
  let scheduled = false;

  const importPromise = importSecureCost();

  frame.addEventListener('load', async () => {
    await importPromise;
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    installStyle(d);

    const schedule = () => {
      if (scheduled) return;
      scheduled = true;
      setTimeout(() => {
        scheduled = false;
        refresh(w, d);
      }, 30);
    };

    if (observer) observer.disconnect();
    const content = d.getElementById('content');
    if (content) {
      observer = new MutationObserver(() => {
        if (!d.querySelector('#content tr.az-primary-patient-cost')) schedule();
      });
      observer.observe(content, { childList: true, subtree: true });
    }

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      d.getElementById(id)?.addEventListener('change', schedule);
    });
    d.querySelectorAll('[data-tab]').forEach(btn => btn.addEventListener('click', schedule));

    schedule();
  });

  async function importSecureCost() {
    let keyText = sessionStorage.getItem(IMPORT_KEY);
    if (!keyText) {
      const h = location.hash || '';
      if (h.startsWith('#azservices=')) keyText = h.slice(12);
    }
    if (!keyText) return false;

    try {
      const meta = await fetch(META_URL, { cache:'no-store' }).then(r => {
        if (!r.ok) throw Error('meta');
        return r.json();
      });
      const cipherText = await fetch(meta.part + '?v=20260903-1', { cache:'no-store' }).then(r => {
        if (!r.ok) throw Error('part');
        return r.text();
      });
      const cleanCipher = cipherText.trim();

      if (meta.ciphertextSha256 && await sha256(cleanCipher) !== meta.ciphertextSha256) {
        throw Error('cipher hash');
      }

      const key = await crypto.subtle.importKey(
        'raw',
        b64u(keyText),
        { name:'AES-GCM' },
        false,
        ['decrypt']
      );
      const compressed = new Uint8Array(await crypto.subtle.decrypt({
        name:'AES-GCM',
        iv:b64u(meta.iv),
        additionalData:new TextEncoder().encode(meta.aad),
        tagLength:128
      }, key, b64u(cleanCipher)));

      if (meta.compressedSha256 && await sha256Bytes(compressed) !== meta.compressedSha256) {
        throw Error('compressed hash');
      }

      const ds = new DecompressionStream('gzip');
      const plain = new Uint8Array(
        await new Response(new Blob([compressed]).stream().pipeThrough(ds)).arrayBuffer()
      );
      if (meta.plaintextSha256 && await sha256Bytes(plain) !== meta.plaintextSha256) {
        throw Error('plain hash');
      }

      const parsed = JSON.parse(new TextDecoder().decode(plain));
      localStorage.setItem(COST_KEY, JSON.stringify(parsed));
      sessionStorage.removeItem(IMPORT_KEY);
      return true;
    } catch (e) {
      console.error('Primary-patient-cost import failed', e);
      return false;
    }
  }

  function installStyle(d) {
    if (d.getElementById('azPrimaryPatientCostStyle')) return;
    const s = d.createElement('style');
    s.id = 'azPrimaryPatientCostStyle';
    s.textContent = `
      .table tr.az-primary-patient-cost td{background:#f3eee4!important;font-weight:700}
      .table tr.az-primary-patient-cost td:first-child{background:#f3eee4!important}
      .table tr.az-primary-patient-cost td{border-top:2px solid #bda77f!important}
      .az-primary-cost-dash{color:#aaa!important;font-weight:400!important}
      .az-primary-cost-note{display:block;margin-top:2px;font-size:8.5px;color:#7f7568;font-weight:500}
    `;
    d.head.appendChild(s);
  }

  function refresh(w, d) {
    d.querySelectorAll('#content tr.az-primary-patient-cost').forEach(r => r.remove());

    const direction = d.getElementById('direction')?.value;
    if (!direction || direction === LAB_DIRECTION) return;

    const summaryTab = !!d.querySelector('[data-tab="summary"].active');
    const doctorTab = !!d.querySelector('[data-tab="doctor"].active');
    if (!summaryTab && !doctorTab) return;

    const serviceData = getJson(w.localStorage, DATA_KEY);
    const costData = getJson(w.localStorage, COST_KEY);
    if (!serviceData || !costData) return;

    const table = d.querySelector('#content .table');
    const tbody = table?.querySelector('tbody');
    const totalRow = tbody?.querySelector('tr.total-row');
    if (!tbody || !totalRow) return;

    const groups = getGroups(w, serviceData, d);
    const focus = d.getElementById('focusPeriod')?.value || 'all';
    const grand = weightedCost(costData, availableIndexes(costData));
    const anchor = lastEconomicsRow(tbody, doctorTab) || totalRow;

    const row = d.createElement('tr');
    row.className = 'az-primary-patient-cost';

    let html = `<td>Общая стоимость первичного пациента клиники<span class="az-primary-cost-note">Расходы клиники без ФОТ врачей и прямых расходов лаборатории • январь–июль</span></td>`;
    groups.forEach(g => {
      const value = groupCost(costData, g.idx);
      html += `<td class="az-primary-cost-dash ${focus===g.id?'focus':''}">—</td>`;
      html += `<td class="${focus===g.id?'focus':''}">${value == null ? '—' : money(value)}</td>`;
    });
    html += '<td class="total-col az-primary-cost-dash">—</td>';
    html += `<td class="total-col">${grand == null ? '—' : money(grand)}</td>`;
    html += '<td class="az-primary-cost-dash">—</td>';

    row.innerHTML = html;
    anchor.insertAdjacentElement('afterend', row);
  }

  function lastEconomicsRow(tbody, doctorTab) {
    const selector = doctorTab ? 'tr.az-inline-econ' : 'tr.az-summary-econ';
    const rows = tbody.querySelectorAll(selector);
    return rows.length ? rows[rows.length - 1] : null;
  }

  function groupCost(costData, idx) {
    if (!Array.isArray(idx) || !idx.length) return null;
    const max = Number(costData.availableThroughMonthIndex);
    if (idx.some(i => i > max)) return null;
    return weightedCost(costData, idx);
  }

  function availableIndexes(costData) {
    const max = Math.min(
      Number(costData.availableThroughMonthIndex),
      (costData.numerator?.length || 0) - 1,
      (costData.primaryPatients?.length || 0) - 1
    );
    if (!Number.isFinite(max) || max < 0) return [];
    return Array.from({ length:max + 1 }, (_, i) => i);
  }

  function weightedCost(costData, idx) {
    let numerator = 0;
    let primary = 0;
    idx.forEach(i => {
      numerator += +(costData.numerator?.[i] || 0);
      primary += +(costData.primaryPatients?.[i] || 0);
    });
    return primary ? numerator / primary : null;
  }

  function getGroups(w, data, d) {
    try {
      if (typeof w.availableGroups === 'function') return w.availableGroups();
    } catch {}

    const n = data?.months?.length || 0;
    const grouping = d.getElementById('grouping')?.value || 'month';
    let defs = [];
    if (grouping === 'month') {
      defs = (data.months || []).map((label,i) => ({ id:'m'+i, label, idx:[i], expected:1 }));
    }
    if (grouping === 'quarter') {
      defs = [
        {id:'q1',label:'I квартал',idx:[0,1,2],expected:3},
        {id:'q2',label:'II квартал',idx:[3,4,5],expected:3},
        {id:'q3',label:'III квартал',idx:[6,7,8],expected:3},
        {id:'q4',label:'IV квартал',idx:[9,10,11],expected:3}
      ];
    }
    if (grouping === 'half') {
      defs = [
        {id:'h1',label:'I полугодие',idx:[0,1,2,3,4,5],expected:6},
        {id:'h2',label:'II полугодие',idx:[6,7,8,9,10,11],expected:6}
      ];
    }
    return defs
      .map(g => ({ ...g, idx:g.idx.filter(i => i < n) }))
      .filter(g => g.idx.length);
  }

  function getJson(storage, key) {
    try { return JSON.parse(storage.getItem(key) || 'null'); }
    catch { return null; }
  }

  function b64u(s) {
    s = String(s || '').replace(/-/g,'+').replace(/_/g,'/');
    while (s.length % 4) s += '=';
    return Uint8Array.from(atob(s), c => c.charCodeAt(0));
  }

  async function sha256(text) {
    return sha256Bytes(new TextEncoder().encode(text));
  }

  async function sha256Bytes(bytes) {
    const h = await crypto.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(h)].map(x => x.toString(16).padStart(2,'0')).join('');
  }

  function money(n) {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits:0 }).format(n || 0) + ' ₽';
  }
})();
