(() => {
  const DIRECTIONS = [
    { key:'dentists', label:'Стоматология' },
    { key:'clinicDocs', label:'Клиника' }
  ];
  let draft = null;

  function sourceNames(kind) {
    if (kind === 'dentists') return typeof dentists !== 'undefined' ? dentists.slice() : [];
    if (kind === 'clinicDocs') return typeof clinicDocs !== 'undefined' ? clinicDocs.slice() : [];
    return [];
  }

  function currentLayout() {
    try {
      if (MANAGEMENT_STAFF_LAYOUT && MANAGEMENT_STAFF_LAYOUT.version === 1 &&
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
      const order = [];
      const seen = new Set();

      (Array.isArray(config.order) ? config.order : []).forEach(name => {
        if (!allowed.has(name) || seen.has(name)) return;
        seen.add(name);
        order.push(name);
      });
      source.forEach(name => {
        if (seen.has(name)) return;
        seen.add(name);
        order.push(name);
      });

      const hiddenSet = new Set(
        (Array.isArray(config.hidden) ? config.hidden : [])
          .filter(name => allowed.has(name))
      );
      directions[key] = {
        order,
        hidden: order.filter(name => hiddenSet.has(name))
      };
    });
    return { version:1, directions };
  }

  window.azManagementStaffNames = function(kind, includeHidden = false) {
    const layout = normalize(currentLayout());
    const config = layout.directions[kind] || { order:[], hidden:[] };
    if (includeHidden) return config.order.slice();
    const hidden = new Set(config.hidden);
    return config.order.filter(name => !hidden.has(name));
  };

  window.azManagementStaffHidden = function(kind) {
    const layout = normalize(currentLayout());
    return (layout.directions[kind]?.hidden || []).slice();
  };

  function installStyle() {
    if (document.getElementById('azManagementStaffStyle')) return;
    const style = document.createElement('style');
    style.id = 'azManagementStaffStyle';
    style.textContent = `
      .az-management-staff-backdrop{position:fixed;inset:0;z-index:15000;display:grid;place-items:center;padding:18px;background:rgba(34,40,36,.52);backdrop-filter:blur(3px)}
      .az-management-staff-hide{display:none!important}
      .az-management-staff-modal{width:min(860px,97vw);max-height:92vh;display:flex;flex-direction:column;overflow:hidden;border:1px solid #d8cdbb;border-radius:16px;background:#fffdf8;box-shadow:0 26px 80px rgba(34,32,27,.28)}
      .az-management-staff-head{display:flex;justify-content:space-between;gap:14px;padding:15px 17px;border-bottom:1px solid #e6ded1}
      .az-management-staff-head h3{margin:0;font-size:22px}.az-management-staff-head small{display:block;margin-top:4px;color:#6d746c;font-size:10px;line-height:1.4}
      .az-management-staff-x{border:0;background:transparent;color:#596158;font-size:26px;cursor:pointer}
      .az-management-staff-controls{padding:11px 17px;border-bottom:1px solid #eee7dc;background:#fbf8f2}.az-management-staff-controls select{min-width:260px;padding:8px 10px;border:1px solid #d8cdbb;border-radius:8px;background:#fff}
      .az-management-staff-body{overflow:auto;padding:13px 17px}.az-management-staff-columns{display:grid;grid-template-columns:1.3fr 1fr;gap:14px}
      .az-management-staff-box{border:1px solid #e2d9ca;border-radius:11px;background:#fff;overflow:hidden}.az-management-staff-box h4{margin:0;padding:9px 11px;background:#faf6ef;font-size:12px}
      .az-management-staff-list{display:grid;gap:6px;padding:9px;min-height:52px}
      .az-management-staff-row{display:grid;grid-template-columns:26px minmax(0,1fr) auto;gap:7px;align-items:center;padding:7px 8px;border:1px solid #e3dacd;border-radius:8px;background:#fffdf8}
      .az-management-staff-row[draggable="true"]{cursor:grab}.az-management-staff-row.az-dragging{opacity:.45}
      .az-management-staff-handle{font-size:16px;color:#8a8174;text-align:center}.az-management-staff-name{font-size:11px;font-weight:700}
      .az-management-staff-actions{display:flex;gap:5px}.az-management-staff-actions button{border:1px solid #d5c9b8;border-radius:7px;background:#fff;padding:5px 7px;font:600 10px Montserrat;cursor:pointer}
      .az-management-staff-actions .danger{color:#8a403b}.az-management-staff-empty{padding:11px;color:#7a817a;font-size:10px}
      .az-management-staff-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 17px 14px;border-top:1px solid #e6ded1}.az-management-staff-status{font-size:10px;color:#607067}
      @media(max-width:700px){.az-management-staff-columns{grid-template-columns:1fr}.az-management-staff-modal{width:100vw;height:100dvh;max-height:100dvh;border-radius:0}.az-management-staff-controls select{width:100%;min-width:0}}
    `;
    document.head.appendChild(style);
  }

  function installEditor() {
    let canEdit = false;
    try { canEdit = !!CAN_EDIT_MANAGEMENT_STAFF_LAYOUT; } catch {}
    if (!canEdit) return;

    const actions = document.querySelector('.toolbar-actions');
    if (!actions) return;

    let open = document.getElementById('azManagementStaffOpen');
    if (!open) {
      open = document.createElement('button');
      open.id = 'azManagementStaffOpen';
      open.type = 'button';
      open.className = 'btn';
      open.textContent = 'Настроить персонал';
      actions.prepend(open);
      open.addEventListener('click', openEditor);
    }

    if (document.getElementById('azManagementStaffEditor')) return;

    const modal = document.createElement('div');
    modal.id = 'azManagementStaffEditor';
    modal.className = 'az-management-staff-backdrop az-management-staff-hide';
    modal.innerHTML = `
      <div class="az-management-staff-modal" role="dialog" aria-modal="true" aria-labelledby="azManagementStaffTitle">
        <div class="az-management-staff-head">
          <div><h3 id="azManagementStaffTitle">Порядок и видимость персонала</h3><small>Настройка Управленческого отчёта общая для всех сотрудников. Скрытие строки не удаляет данные и не меняет итоги направления.</small></div>
          <button type="button" class="az-management-staff-x" id="azManagementStaffX" aria-label="Закрыть">×</button>
        </div>
        <div class="az-management-staff-controls"><label>Направление<br><select id="azManagementStaffDirection"></select></label></div>
        <div class="az-management-staff-body">
          <div class="az-management-staff-columns">
            <div class="az-management-staff-box"><h4>Показываются — перетаскивайте вверх/вниз</h4><div id="azManagementStaffVisible" class="az-management-staff-list"></div></div>
            <div class="az-management-staff-box"><h4>Скрытые</h4><div id="azManagementStaffHidden" class="az-management-staff-list"></div></div>
          </div>
        </div>
        <div class="az-management-staff-foot"><div id="azManagementStaffStatus" class="az-management-staff-status"></div><div><button type="button" class="btn" id="azManagementStaffCancel">Отмена</button> <button type="button" class="btn primary" id="azManagementStaffSave">Сохранить для всех</button></div></div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.addEventListener('click', event => {
      if (event.target === modal) closeEditor();
    });
    document.getElementById('azManagementStaffX').onclick = closeEditor;
    document.getElementById('azManagementStaffCancel').onclick = closeEditor;
    document.getElementById('azManagementStaffSave').onclick = saveEditor;
    document.getElementById('azManagementStaffDirection').addEventListener('change', renderEditor);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !modal.classList.contains('az-management-staff-hide')) closeEditor();
    });
  }

  function openEditor() {
    draft = normalize(currentLayout());
    const select = document.getElementById('azManagementStaffDirection');
    select.innerHTML = DIRECTIONS.map(item => `<option value="${item.key}">${escapeHtml(item.label)}</option>`).join('');
    document.getElementById('azManagementStaffStatus').textContent = '';
    document.getElementById('azManagementStaffEditor').classList.remove('az-management-staff-hide');
    renderEditor();
  }

  function closeEditor() {
    document.getElementById('azManagementStaffEditor')?.classList.add('az-management-staff-hide');
    draft = null;
  }

  function config() {
    const kind = document.getElementById('azManagementStaffDirection')?.value;
    return [kind, draft?.directions?.[kind]];
  }

  function visibleOrder(cfg) {
    const hidden = new Set(cfg.hidden || []);
    return cfg.order.filter(name => !hidden.has(name));
  }

  function rebuildOrder(cfg, visible) {
    const hidden = new Set(cfg.hidden || []);
    cfg.order = [...visible, ...cfg.order.filter(name => hidden.has(name))];
  }

  function move(cfg, name, delta) {
    const visible = visibleOrder(cfg);
    const index = visible.indexOf(name);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= visible.length) return;
    [visible[index], visible[next]] = [visible[next], visible[index]];
    rebuildOrder(cfg, visible);
    renderEditor();
  }

  function reorder(cfg, source, target) {
    const visible = visibleOrder(cfg);
    const from = visible.indexOf(source);
    const to = visible.indexOf(target);
    if (from < 0 || to < 0) return;
    visible.splice(from, 1);
    visible.splice(to, 0, source);
    rebuildOrder(cfg, visible);
  }

  function renderEditor() {
    if (!draft) return;
    const [_kind, cfg] = config();
    if (!cfg) return;

    const hidden = new Set(cfg.hidden || []);
    const visibleNames = cfg.order.filter(name => !hidden.has(name));
    const hiddenNames = cfg.order.filter(name => hidden.has(name));
    const visible = document.getElementById('azManagementStaffVisible');
    const hiddenHost = document.getElementById('azManagementStaffHidden');

    visible.innerHTML = visibleNames.length ? visibleNames.map(name => `
      <div class="az-management-staff-row" draggable="true" data-name="${escapeAttr(name)}">
        <span class="az-management-staff-handle">↕</span>
        <span class="az-management-staff-name">${escapeHtml(name)}</span>
        <span class="az-management-staff-actions">
          <button type="button" data-up="${escapeAttr(name)}" title="Выше">↑</button>
          <button type="button" data-down="${escapeAttr(name)}" title="Ниже">↓</button>
          <button type="button" class="danger" data-hide="${escapeAttr(name)}">Скрыть</button>
        </span>
      </div>
    `).join('') : '<div class="az-management-staff-empty">Все строки этого направления скрыты.</div>';

    hiddenHost.innerHTML = hiddenNames.length ? hiddenNames.map(name => `
      <div class="az-management-staff-row" data-name="${escapeAttr(name)}">
        <span class="az-management-staff-handle">—</span>
        <span class="az-management-staff-name">${escapeHtml(name)}</span>
        <span class="az-management-staff-actions"><button type="button" data-show="${escapeAttr(name)}">Вернуть</button></span>
      </div>
    `).join('') : '<div class="az-management-staff-empty">Скрытых строк нет.</div>';

    visible.querySelectorAll('[data-up]').forEach(button => button.onclick = () => move(cfg, button.dataset.up, -1));
    visible.querySelectorAll('[data-down]').forEach(button => button.onclick = () => move(cfg, button.dataset.down, 1));
    visible.querySelectorAll('[data-hide]').forEach(button => button.onclick = () => {
      const name = button.dataset.hide;
      cfg.hidden = [...new Set([...(cfg.hidden || []), name])];
      cfg.order = cfg.order.filter(item => item !== name).concat(name);
      renderEditor();
    });
    hiddenHost.querySelectorAll('[data-show]').forEach(button => button.onclick = () => {
      const name = button.dataset.show;
      cfg.hidden = (cfg.hidden || []).filter(item => item !== name);
      cfg.order = cfg.order.filter(item => item !== name).concat(name);
      renderEditor();
    });

    let dragged = '';
    visible.querySelectorAll('[draggable="true"]').forEach(row => {
      row.addEventListener('dragstart', event => {
        dragged = row.dataset.name || '';
        row.classList.add('az-dragging');
        event.dataTransfer.effectAllowed = 'move';
      });
      row.addEventListener('dragend', () => {
        dragged = '';
        row.classList.remove('az-dragging');
      });
      row.addEventListener('dragover', event => {
        if (!dragged || dragged === row.dataset.name) return;
        event.preventDefault();
      });
      row.addEventListener('drop', event => {
        event.preventDefault();
        const target = row.dataset.name || '';
        if (!dragged || !target || dragged === target) return;
        reorder(cfg, dragged, target);
        renderEditor();
      });
    });
  }

  async function saveEditor() {
    if (!draft) return;
    let canEdit = false;
    try { canEdit = !!CAN_EDIT_MANAGEMENT_STAFF_LAYOUT; } catch {}
    if (!canEdit) return;

    const status = document.getElementById('azManagementStaffStatus');
    const save = document.getElementById('azManagementStaffSave');
    save.disabled = true;
    status.textContent = 'Сохраняю…';

    try {
      const response = await fetch('/api/reports/management-staff-layout', {
        method:'PUT',
        credentials:'same-origin',
        headers:{
          'Content-Type':'application/json',
          'X-CSRF-Token':REPORT_CSRF
        },
        body:JSON.stringify({ layout:draft })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || ('HTTP ' + response.status));

      MANAGEMENT_STAFF_LAYOUT = payload.layout;
      status.textContent = 'Сохранено для всех сотрудников.';
      closeEditor();
      refreshViews();
    } catch (error) {
      console.error(error);
      status.textContent = 'Не удалось сохранить настройку.';
    } finally {
      save.disabled = false;
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
    if (view) {
      view.dispatchEvent(new Event('change', { bubbles:true }));
    } else {
      document.getElementById('reportDate')?.dispatchEvent(new Event('change', { bubbles:true }));
    }
  }

  function boot() {
    installStyle();
    installEditor();
    refreshViews();
  }

  window.addEventListener('az-management-staff-layout-loaded', boot);
  document.addEventListener('DOMContentLoaded', () => {
    setTimeout(boot, 0);
  });

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[char]));
  }
  function escapeAttr(value) {
    return escapeHtml(String(value ?? ''));
  }
})();