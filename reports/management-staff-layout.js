(() => {
  const DIRECTIONS = [
    { key:'dentists', label:'Стоматология' },
    { key:'clinicDocs', label:'Клиника' }
  ];
  const expandedHidden = new Set();
  let saving = false;

  function sourceNames(kind) {
    if (kind === 'dentists') return typeof dentists !== 'undefined' ? dentists.slice() : [];
    if (kind === 'clinicDocs') return typeof clinicDocs !== 'undefined' ? clinicDocs.slice() : [];
    return [];
  }

  function currentLayout() {
    try {
      if (MANAGEMENT_STAFF_LAYOUT &&
          MANAGEMENT_STAFF_LAYOUT.version === 1 &&
          MANAGEMENT_STAFF_LAYOUT.directions &&
          typeof MANAGEMENT_STAFF_LAYOUT.directions === 'object') {
        return MANAGEMENT_STAFF_LAYOUT;
      }
    } catch {}
    return { version:1, directions:{} };
  }

  function normalize(raw) {
    const directions = {};
    DIRECTIONS.forEach(({key}) => {
      const source = sourceNames(key);
      const allowed = new Set(source);
      const config = raw?.directions?.[key] || {};
      const rawOrder = Array.isArray(config.order) ? config.order : [];
      const rawHidden = Array.isArray(config.hidden) ? config.hidden : [];

      const order = [];
      const seen = new Set();
      rawOrder.forEach(name => {
        if (typeof name !== 'string' || !allowed.has(name) || seen.has(name)) return;
        seen.add(name);
        order.push(name);
      });

      const hiddenSet = new Set(
        rawHidden.filter(name => typeof name === 'string' && allowed.has(name))
      );
      const visible = order.filter(name => !hiddenSet.has(name));
      const hidden = order.filter(name => hiddenSet.has(name));

      source.forEach(name => {
        if (seen.has(name)) return;
        seen.add(name);
        visible.push(name);
      });

      directions[key] = {
        order:[...visible, ...hidden],
        hidden:hidden.slice()
      };
    });
    return { version:1, directions };
  }

  function setLayout(layout) {
    try { MANAGEMENT_STAFF_LAYOUT = layout; } catch {}
  }

  function entries(kind) {
    const config = normalize(currentLayout()).directions[kind] || {order:[], hidden:[]};
    const hidden = new Set(config.hidden);
    return config.order.map(name => ({ name, hidden:hidden.has(name) }));
  }

  window.azManagementStaffEntries = kind => entries(kind);
  window.azManagementStaffNames = (kind, includeHidden = false) => {
    const list = entries(kind);
    return (includeHidden ? list : list.filter(item => !item.hidden)).map(item => item.name);
  };
  window.azManagementStaffHidden = kind => entries(kind).filter(item => item.hidden).map(item => item.name);

  function canEdit() {
    try { return !!CAN_EDIT_MANAGEMENT_STAFF_LAYOUT; }
    catch { return false; }
  }

  function installStyle() {
    if (document.getElementById('azManagementStaffStyle')) return;
    const style = document.createElement('style');
    style.id = 'azManagementStaffStyle';
    style.textContent = `
      .az-management-staff-hidden-row{display:none}
      .az-management-staff-hidden-row.az-management-staff-hidden-revealed{display:table-row}
      .az-management-staff-hidden-row td{background:#f1f1ee!important;color:#7c817c!important}
      .az-management-staff-row-controls{display:inline-flex;align-items:center;gap:3px;margin-right:7px;vertical-align:middle}
      .az-management-staff-action,.az-management-hidden-toggle{
        appearance:none;border:1px solid #cfc5b5;border-radius:5px;background:#fff;color:#4d574f;
        min-width:22px;height:22px;padding:0 4px;font:700 12px/20px Montserrat,Arial,sans-serif;
        cursor:pointer;vertical-align:middle
      }
      .az-management-staff-action:hover,.az-management-hidden-toggle:hover{background:#f3efe8}
      .az-management-staff-action:disabled{opacity:.28;cursor:default}
      .az-management-staff-action[data-action="hide"]{color:#8a403b}
      .az-management-staff-action[data-action="restore"]{color:#315a40}
      .az-management-hidden-toggle-row td{
        background:#f0eee9!important;color:#6e746e!important;text-align:left!important;
        font-size:10px!important;font-weight:700!important
      }
      .az-management-hidden-toggle-wrap{display:inline-flex;align-items:center;gap:7px}
      .az-management-staff-saving .az-management-staff-action,
      .az-management-staff-saving .az-management-hidden-toggle{pointer-events:none;opacity:.45}
    `;
    document.head.appendChild(style);
  }

  function rowInfo(row) {
    const kind = row?.dataset?.managementStaffKind || '';
    const name = row?.dataset?.managementStaffName || '';
    if (!kind || !name) return null;
    return {kind, name, hidden:row.classList.contains('az-management-staff-hidden-row')};
  }

  function makeAction(kind, name, action, label, disabled = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'az-management-staff-action';
    button.dataset.kind = kind;
    button.dataset.name = name;
    button.dataset.action = action;
    button.textContent = label;
    button.disabled = !!disabled;
    button.title =
      action === 'up' ? 'Выше' :
      action === 'down' ? 'Ниже' :
      action === 'hide' ? 'Скрыть строку' :
      action === 'restore' ? 'Вернуть строку' : '';
    return button;
  }

  function decorateRow(row, index, total) {
    const info = rowInfo(row);
    if (!info) return;

    row.classList.toggle(
      'az-management-staff-hidden-revealed',
      info.hidden && canEdit() && expandedHidden.has(info.kind)
    );

    const first = row.cells?.[0];
    if (!first) return;
    first.querySelector('.az-management-staff-row-controls')?.remove();

    if (!canEdit()) return;
    if (info.hidden && !expandedHidden.has(info.kind)) return;

    const controls = document.createElement('span');
    controls.className = 'az-management-staff-row-controls';

    controls.appendChild(makeAction(info.kind, info.name, 'up', '↑', index === 0));
    controls.appendChild(
      makeAction(
        info.kind,
        info.name,
        info.hidden ? 'restore' : 'hide',
        info.hidden ? '+' : '−'
      )
    );
    controls.appendChild(makeAction(info.kind, info.name, 'down', '↓', index === total - 1));

    first.prepend(controls);
  }

  function decorateBody(body) {
    if (!body) return;
    body.querySelectorAll('tr.az-management-hidden-toggle-row').forEach(row => row.remove());

    DIRECTIONS.forEach(({key}) => {
      const rows = [...body.querySelectorAll(
        `tr[data-management-staff-kind="${key}"]`
      )];

      if (!rows.length) return;

      const visibleRows = rows.filter(row => !row.classList.contains('az-management-staff-hidden-row'));
      const hiddenRows = rows.filter(row => row.classList.contains('az-management-staff-hidden-row'));

      visibleRows.forEach((row, index) => decorateRow(row, index, visibleRows.length));
      hiddenRows.forEach((row, index) => decorateRow(row, index, hiddenRows.length));

      if (!canEdit() || !hiddenRows.length) return;

      const toggleRow = document.createElement('tr');
      toggleRow.className = 'az-management-hidden-toggle-row';
      toggleRow.dataset.kind = key;

      const td = document.createElement('td');
      td.colSpan = rows[0].cells?.length || 1;

      const wrap = document.createElement('span');
      wrap.className = 'az-management-hidden-toggle-wrap';

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'az-management-hidden-toggle';
      toggle.dataset.kind = key;
      toggle.textContent = expandedHidden.has(key) ? '−' : '+';
      toggle.title = expandedHidden.has(key) ? 'Свернуть скрытые строки' : 'Показать скрытые строки';

      const label = document.createElement('span');
      label.textContent = `Скрытые сотрудники (${hiddenRows.length})`;

      wrap.append(toggle, label);
      td.appendChild(wrap);
      toggleRow.appendChild(td);

      const firstHidden = hiddenRows[0];
      firstHidden.parentNode.insertBefore(toggleRow, firstHidden);
    });
  }

  function decorateTables() {
    installStyle();
    decorateBody(document.getElementById('periodCompareBody'));
    decorateBody(document.getElementById('dateCompareBody'));
  }

  function configParts(layout, kind) {
    const cfg = layout.directions[kind];
    const hiddenSet = new Set(cfg.hidden || []);
    const visible = cfg.order.filter(name => !hiddenSet.has(name));
    const hidden = cfg.order.filter(name => hiddenSet.has(name));
    return {cfg, visible, hidden};
  }

  function mutateLayout(kind, name, action) {
    const next = normalize(currentLayout());
    const {cfg, visible, hidden} = configParts(next, kind);

    if (action === 'up' || action === 'down') {
      const target = cfg.hidden.includes(name) ? hidden : visible;
      const index = target.indexOf(name);
      const offset = action === 'up' ? -1 : 1;
      const nextIndex = index + offset;
      if (index < 0 || nextIndex < 0 || nextIndex >= target.length) return null;
      [target[index], target[nextIndex]] = [target[nextIndex], target[index]];
    } else if (action === 'hide') {
      const index = visible.indexOf(name);
      if (index < 0) return null;
      visible.splice(index, 1);
      if (!hidden.includes(name)) hidden.push(name);
      expandedHidden.add(kind);
    } else if (action === 'restore') {
      const index = hidden.indexOf(name);
      if (index < 0) return null;
      hidden.splice(index, 1);
      if (!visible.includes(name)) visible.push(name);
      if (!hidden.length) expandedHidden.delete(kind);
    } else {
      return null;
    }

    cfg.order = [...visible, ...hidden];
    cfg.hidden = hidden.slice();
    return next;
  }

  async function saveMutation(kind, name, action) {
    if (saving || !canEdit()) return;
    const next = mutateLayout(kind, name, action);
    if (!next) return;

    saving = true;
    document.body.classList.add('az-management-staff-saving');

    try {
      const response = await fetch('/api/reports/management-staff-layout', {
        method:'PUT',
        credentials:'same-origin',
        headers:{
          'Content-Type':'application/json',
          'X-CSRF-Token':REPORT_CSRF
        },
        body:JSON.stringify({layout:next})
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || ('HTTP ' + response.status));

      setLayout(normalize(payload.layout));
      refreshViews();
    } catch (error) {
      console.error('AZ management staff layout save failed', error);
      alert('Не удалось сохранить порядок персонала. Изменение не применено.');
    } finally {
      saving = false;
      document.body.classList.remove('az-management-staff-saving');
      setTimeout(decorateTables, 0);
    }
  }

  function refreshViews() {
    if (typeof renderStructure === 'function') renderStructure();
    if (typeof window.azRefreshManagementStaffLists === 'function') {
      window.azRefreshManagementStaffLists();
    }

    const mode = document.getElementById('mode')?.value;
    if (mode && mode !== 'date') {
      if (typeof window.renderPeriod === 'function') window.renderPeriod();
      return;
    }

    const view = document.getElementById('dateViewMode');
    if (view && ['compare','monthly'].includes(view.value)) {
      view.dispatchEvent(new Event('change', {bubbles:true}));
      return;
    }

    document.getElementById('reportDate')?.dispatchEvent(
      new Event('change', {bubbles:true})
    );
  }

  document.addEventListener('click', event => {
    const action = event.target.closest('.az-management-staff-action');
    if (action) {
      event.preventDefault();
      event.stopPropagation();
      saveMutation(action.dataset.kind, action.dataset.name, action.dataset.action);
      return;
    }

    const toggle = event.target.closest('.az-management-hidden-toggle');
    if (toggle) {
      event.preventDefault();
      event.stopPropagation();
      const kind = toggle.dataset.kind;
      if (expandedHidden.has(kind)) expandedHidden.delete(kind);
      else expandedHidden.add(kind);
      decorateTables();
    }
  });

  window.addEventListener('az-management-staff-layout-loaded', () => {
    installStyle();
    setTimeout(() => {
      refreshViews();
      decorateTables();
    }, 0);
  });

  window.addEventListener('az-management-staff-table-rendered', () => {
    setTimeout(decorateTables, 0);
  });

  document.addEventListener('DOMContentLoaded', () => {
    installStyle();
    setTimeout(decorateTables, 0);
  });
})();