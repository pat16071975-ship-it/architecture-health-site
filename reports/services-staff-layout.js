(() => {
  const frame = document.getElementById('serviceFrame');
  if (!frame) return;

  const DATA_KEY = 'az-service-analytics-v1';
  const LAYOUT_KEY = 'az-service-staff-layout-v1';
  let apiState = { canEdit:false, csrf:'' };
  let observer = null;
  let scheduled = false;
  let applying = false;
  let draft = null;

  frame.addEventListener('load', async () => {
    const w = frame.contentWindow;
    const d = frame.contentDocument;
    if (!w || !d) return;

    installStyles(d);
    installHelpers(w);
    bindRefresh(w, d);

    await loadLayout(w);
    installHelpers(w);
    installAdminUi(w, d);
    rerender(w, d);
  });

  function getData(w) {
    try { return JSON.parse(w.localStorage.getItem(DATA_KEY) || 'null'); }
    catch { return null; }
  }

  function getLayout(w) {
    try {
      const parsed = JSON.parse(w.localStorage.getItem(LAYOUT_KEY) || 'null');
      return parsed && parsed.version === 1 && parsed.directions && typeof parsed.directions === 'object'
        ? parsed
        : { version:1, directions:{} };
    } catch {
      return { version:1, directions:{} };
    }
  }

  function sourceNames(w, direction) {
    return Object.keys(getData(w)?.directions?.[direction]?.doctors || {});
  }

  function orderedNames(w, direction, includeHidden = false) {
    const source = sourceNames(w, direction);
    const allowed = new Set(source);
    const config = getLayout(w)?.directions?.[direction] || {};
    const stored = Array.isArray(config.order) ? config.order : [];
    const hidden = new Set(Array.isArray(config.hidden) ? config.hidden : []);
    const result = [];
    const seen = new Set();

    stored.forEach(name => {
      if (!allowed.has(name) || seen.has(name)) return;
      seen.add(name);
      result.push(name);
    });
    source.forEach(name => {
      if (seen.has(name)) return;
      seen.add(name);
      result.push(name);
    });

    return includeHidden ? result : result.filter(name => !hidden.has(name));
  }

  function hiddenNames(w, direction) {
    const source = new Set(sourceNames(w, direction));
    const config = getLayout(w)?.directions?.[direction] || {};
    return new Set(
      (Array.isArray(config.hidden) ? config.hidden : [])
        .filter(name => source.has(name))
    );
  }

  function installHelpers(w) {
    w.AZServiceStaffLayout = {
      names(direction, includeHidden = false) {
        return orderedNames(w, direction, includeHidden);
      },
      hidden(direction) {
        return [...hiddenNames(w, direction)];
      },
      isHidden(direction, name) {
        return hiddenNames(w, direction).has(name);
      }
    };
  }

  async function loadLayout(w) {
    try {
      const response = await fetch('/api/reports/services-staff-layout', {
        credentials:'same-origin',
        cache:'no-store'
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const payload = await response.json();
      apiState = {
        canEdit: !!payload.can_edit,
        csrf: String(payload.csrf || '')
      };
      if (payload.layout) {
        w.localStorage.setItem(LAYOUT_KEY, JSON.stringify(payload.layout));
      }
    } catch (error) {
      console.error('AZ service staff layout load failed', error);
    }
  }

  function bindRefresh(w, d) {
    if (observer) observer.disconnect();
    observer = new MutationObserver(schedule);
    observer.observe(d.body, { childList:true, subtree:true });

    ['direction','grouping','focusPeriod','doctor','category'].forEach(id => {
      d.getElementById(id)?.addEventListener('change', schedule);
    });
    d.addEventListener('click', event => {
      if (event.target.closest('[data-tab],[data-az-tab],#azPayrollTopOpen,#azPayOpen')) {
        schedule();
      }
    });
  }

  function schedule() {
    if (scheduled || applying) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      const w = frame.contentWindow;
      const d = frame.contentDocument;
      if (w && d) applyRows(w, d);
    }, 30);
  }

  function rowName(row, source) {
    const direct = String(row?.dataset?.doctor || '').trim();
    if (source.has(direct)) return direct;
    const first = row?.cells?.[0];
    if (!first) return '';
    const text = String(first.innerText || first.textContent || '').trim();
    for (const name of source) {
      if (text === name || text.startsWith(name + '\n')) return name;
    }
    return '';
  }

  function applyRows(w, d) {
    if (applying) return;
    const direction = d.getElementById('direction')?.value;
    if (!direction) return;
    const all = orderedNames(w, direction, true);
    if (!all.length) return;

    applying = true;
    try {
      const source = new Set(all);
      const hidden = hiddenNames(w, direction);
      const rank = new Map(all.map((name, index) => [name, index]));

      d.querySelectorAll('table tbody').forEach(tbody => {
        const rows = [...tbody.children].filter(node => node.tagName === 'TR');
        const staffRows = rows
          .map((row, index) => ({ row, index, name:rowName(row, source) }))
          .filter(item => item.name);

        if (!staffRows.length) return;

        staffRows.forEach(item => {
          item.row.classList.toggle('az-staff-layout-hidden', hidden.has(item.name));
        });

        if (staffRows.length < 2) return;

        const sorted = [...staffRows].sort((a, b) => {
          return (rank.get(a.name) ?? 100000) - (rank.get(b.name) ?? 100000);
        });
        const placeholders = staffRows.map(item => {
          const mark = d.createComment('az-staff-row');
          item.row.replaceWith(mark);
          return mark;
        });
        sorted.forEach((item, index) => placeholders[index].replaceWith(item.row));
      });
    } finally {
      applying = false;
    }
  }

  function rerender(w, d) {
    try {
      if (typeof w.renderFilters === 'function') w.renderFilters();
      if (typeof w.render === 'function') w.render();
    } catch (error) {
      console.error('AZ staff layout rerender failed', error);
    }
    const direction = d.getElementById('direction');
    if (direction) {
      direction.dispatchEvent(new w.Event('change', { bubbles:true }));
    }
    schedule();
    window.dispatchEvent(new CustomEvent('az-service-staff-layout-changed'));
  }

  function installStyles(d) {
    if (d.getElementById('azStaffLayoutStyle')) return;
    const style = d.createElement('style');
    style.id = 'azStaffLayoutStyle';
    style.textContent = `
      .az-staff-layout-hidden{display:none!important}
      .az-staff-layout-backdrop{position:fixed;inset:0;z-index:18000;display:grid;place-items:center;padding:18px;background:rgba(34,40,36,.52);backdrop-filter:blur(3px)}
      .az-staff-layout-hide{display:none!important}
      .az-staff-layout-modal{width:min(860px,97vw);max-height:92vh;display:flex;flex-direction:column;overflow:hidden;border:1px solid #d8cdbb;border-radius:16px;background:#fffdf8;box-shadow:0 26px 80px rgba(34,32,27,.28)}
      .az-staff-layout-head{display:flex;justify-content:space-between;gap:14px;padding:15px 17px;border-bottom:1px solid #e6ded1}
      .az-staff-layout-head h3{margin:0;font-size:22px}.az-staff-layout-head small{display:block;margin-top:4px;color:#6d746c;font-size:10px}
      .az-staff-layout-x{border:0;background:transparent;color:#596158;font-size:26px;cursor:pointer}
      .az-staff-layout-controls{padding:11px 17px;border-bottom:1px solid #eee7dc;background:#fbf8f2}.az-staff-layout-controls select{min-width:260px;padding:8px 10px;border:1px solid #d8cdbb;border-radius:8px;background:#fff}
      .az-staff-layout-body{overflow:auto;padding:13px 17px}.az-staff-layout-columns{display:grid;grid-template-columns:1.3fr 1fr;gap:14px}
      .az-staff-layout-box{border:1px solid #e2d9ca;border-radius:11px;background:#fff;overflow:hidden}.az-staff-layout-box h4{margin:0;padding:9px 11px;background:#faf6ef;font-size:12px}
      .az-staff-layout-list{display:grid;gap:6px;padding:9px;min-height:52px}
      .az-staff-layout-row{display:grid;grid-template-columns:26px minmax(0,1fr) auto;gap:7px;align-items:center;padding:7px 8px;border:1px solid #e3dacd;border-radius:8px;background:#fffdf8}
      .az-staff-layout-row[draggable="true"]{cursor:grab}.az-staff-layout-row.az-dragging{opacity:.45}
      .az-staff-layout-handle{font-size:16px;color:#8a8174;text-align:center}.az-staff-layout-name{font-size:11px;font-weight:700}
      .az-staff-layout-actions{display:flex;gap:5px}.az-staff-layout-actions button{border:1px solid #d5c9b8;border-radius:7px;background:#fff;padding:5px 7px;font:600 10px Montserrat;cursor:pointer}
      .az-staff-layout-actions .danger{color:#8a403b}.az-staff-layout-empty{padding:11px;color:#7a817a;font-size:10px}
      .az-staff-layout-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 17px 14px;border-top:1px solid #e6ded1}.az-staff-layout-status{font-size:10px;color:#607067}
      @media(max-width:700px){.az-staff-layout-columns{grid-template-columns:1fr}.az-staff-layout-modal{width:100vw;height:100dvh;max-height:100dvh;border-radius:0}.az-staff-layout-controls select{width:100%;min-width:0}}
    `;
    d.head.appendChild(style);
  }

  function normalizeDraft(w, raw) {
    const data = getData(w);
    const directions = {};
    Object.keys(data?.directions || {}).forEach(direction => {
      const source = Object.keys(data.directions?.[direction]?.doctors || {});
      const allowed = new Set(source);
      const config = raw?.directions?.[direction] || {};
      const order = [];
      const seen = new Set();
      (Array.isArray(config.order) ? config.order : []).forEach(name => {
        if (!allowed.has(name) || seen.has(name)) return;
        seen.add(name);
        order.push(name);
      });
      source.forEach(name => {
        if (!seen.has(name)) {
          seen.add(name);
          order.push(name);
        }
      });
      const hiddenSet = new Set(
        (Array.isArray(config.hidden) ? config.hidden : [])
          .filter(name => allowed.has(name))
      );
      directions[direction] = {
        order,
        hidden: order.filter(name => hiddenSet.has(name))
      };
    });
    return { version:1, directions };
  }

  function installAdminUi(w, d) {
    if (!apiState.canEdit) return;
    const top = d.querySelector('.top-actions');
    if (!top) return;

    let button = d.getElementById('azStaffLayoutOpen');
    if (!button) {
      button = d.createElement('button');
      button.id = 'azStaffLayoutOpen';
      button.type = 'button';
      button.className = 'btn';
      button.textContent = 'Настроить персонал';
      top.prepend(button);
      button.addEventListener('click', () => openEditor(w, d));
    }

    if (d.getElementById('azStaffLayoutEditor')) return;
    const modal = d.createElement('div');
    modal.id = 'azStaffLayoutEditor';
    modal.className = 'az-staff-layout-backdrop az-staff-layout-hide';
    modal.innerHTML = `
      <div class="az-staff-layout-modal" role="dialog" aria-modal="true" aria-labelledby="azStaffLayoutTitle">
        <div class="az-staff-layout-head">
          <div><h3 id="azStaffLayoutTitle">Порядок и видимость персонала</h3><small>Настройка общая для всех пользователей. Скрытие не удаляет данные и не меняет итоги.</small></div>
          <button type="button" class="az-staff-layout-x" id="azStaffLayoutX" aria-label="Закрыть">×</button>
        </div>
        <div class="az-staff-layout-controls"><label>Направление<br><select id="azStaffLayoutDirection"></select></label></div>
        <div class="az-staff-layout-body">
          <div class="az-staff-layout-columns">
            <div class="az-staff-layout-box"><h4>Показываются — перетаскивайте вверх/вниз</h4><div id="azStaffVisible" class="az-staff-layout-list"></div></div>
            <div class="az-staff-layout-box"><h4>Скрытые</h4><div id="azStaffHidden" class="az-staff-layout-list"></div></div>
          </div>
        </div>
        <div class="az-staff-layout-foot"><div id="azStaffLayoutStatus" class="az-staff-layout-status"></div><div><button type="button" class="btn" id="azStaffLayoutCancel">Отмена</button> <button type="button" class="btn primary" id="azStaffLayoutSave">Сохранить для всех</button></div></div>
      </div>
    `;
    d.body.appendChild(modal);

    modal.addEventListener('click', event => {
      if (event.target === modal) closeEditor(d);
    });
    d.getElementById('azStaffLayoutX').onclick = () => closeEditor(d);
    d.getElementById('azStaffLayoutCancel').onclick = () => closeEditor(d);
    d.getElementById('azStaffLayoutSave').onclick = () => saveEditor(w, d);
    d.getElementById('azStaffLayoutDirection').addEventListener('change', () => renderEditor(w, d));
    d.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !modal.classList.contains('az-staff-layout-hide')) closeEditor(d);
    });
  }

  function openEditor(w, d) {
    draft = normalizeDraft(w, getLayout(w));
    const select = d.getElementById('azStaffLayoutDirection');
    const directions = Object.keys(draft.directions);
    const current = d.getElementById('direction')?.value;
    select.innerHTML = directions.map(direction => `<option>${escapeHtml(direction)}</option>`).join('');
    if (directions.includes(current)) select.value = current;
    d.getElementById('azStaffLayoutStatus').textContent = '';
    d.getElementById('azStaffLayoutEditor').classList.remove('az-staff-layout-hide');
    renderEditor(w, d);
  }

  function closeEditor(d) {
    d.getElementById('azStaffLayoutEditor')?.classList.add('az-staff-layout-hide');
    draft = null;
  }

  function currentDraftConfig(d) {
    const direction = d.getElementById('azStaffLayoutDirection')?.value;
    return [direction, draft?.directions?.[direction]];
  }

  function renderEditor(w, d) {
    if (!draft) return;
    const [direction, config] = currentDraftConfig(d);
    if (!direction || !config) return;

    const hidden = new Set(config.hidden || []);
    const visibleNames = config.order.filter(name => !hidden.has(name));
    const hiddenNamesList = config.order.filter(name => hidden.has(name));

    const visible = d.getElementById('azStaffVisible');
    const hiddenHost = d.getElementById('azStaffHidden');

    visible.innerHTML = visibleNames.length ? visibleNames.map((name, index) => `
      <div class="az-staff-layout-row" draggable="true" data-name="${escapeAttr(name)}">
        <span class="az-staff-layout-handle">↕</span>
        <span class="az-staff-layout-name">${escapeHtml(name)}</span>
        <span class="az-staff-layout-actions">
          <button type="button" data-up="${escapeAttr(name)}" title="Выше">↑</button>
          <button type="button" data-down="${escapeAttr(name)}" title="Ниже">↓</button>
          <button type="button" class="danger" data-hide="${escapeAttr(name)}">Скрыть</button>
        </span>
      </div>
    `).join('') : '<div class="az-staff-layout-empty">Все строки этого направления скрыты.</div>';

    hiddenHost.innerHTML = hiddenNamesList.length ? hiddenNamesList.map(name => `
      <div class="az-staff-layout-row" data-name="${escapeAttr(name)}">
        <span class="az-staff-layout-handle">—</span>
        <span class="az-staff-layout-name">${escapeHtml(name)}</span>
        <span class="az-staff-layout-actions"><button type="button" data-show="${escapeAttr(name)}">Вернуть</button></span>
      </div>
    `).join('') : '<div class="az-staff-layout-empty">Скрытых строк нет.</div>';

    visible.querySelectorAll('[data-hide]').forEach(button => button.onclick = () => {
      const name = button.dataset.hide;
      config.hidden = [...new Set([...(config.hidden || []), name])];
      config.order = config.order.filter(item => item !== name).concat(name);
      renderEditor(w, d);
    });
    hiddenHost.querySelectorAll('[data-show]').forEach(button => button.onclick = () => {
      const name = button.dataset.show;
      config.hidden = (config.hidden || []).filter(item => item !== name);
      config.order = config.order.filter(item => item !== name).concat(name);
      renderEditor(w, d);
    });
    visible.querySelectorAll('[data-up]').forEach(button => button.onclick = () => moveVisible(config, button.dataset.up, -1, w, d));
    visible.querySelectorAll('[data-down]').forEach(button => button.onclick = () => moveVisible(config, button.dataset.down, 1, w, d));

    let dragged = '';
    visible.querySelectorAll('.az-staff-layout-row[draggable="true"]').forEach(row => {
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
        event.dataTransfer.dropEffect = 'move';
      });
      row.addEventListener('drop', event => {
        event.preventDefault();
        const target = row.dataset.name || '';
        if (!dragged || !target || dragged === target) return;
        reorderVisible(config, dragged, target);
        renderEditor(w, d);
      });
    });
  }

  function visibleOrder(config) {
    const hidden = new Set(config.hidden || []);
    return config.order.filter(name => !hidden.has(name));
  }

  function rebuildOrder(config, visible) {
    const hidden = new Set(config.hidden || []);
    const hiddenOrder = config.order.filter(name => hidden.has(name));
    config.order = [...visible, ...hiddenOrder];
  }

  function moveVisible(config, name, delta, w, d) {
    const visible = visibleOrder(config);
    const index = visible.indexOf(name);
    const next = index + delta;
    if (index < 0 || next < 0 || next >= visible.length) return;
    [visible[index], visible[next]] = [visible[next], visible[index]];
    rebuildOrder(config, visible);
    renderEditor(w, d);
  }

  function reorderVisible(config, source, target) {
    const visible = visibleOrder(config);
    const from = visible.indexOf(source);
    const to = visible.indexOf(target);
    if (from < 0 || to < 0) return;
    visible.splice(from, 1);
    visible.splice(to, 0, source);
    rebuildOrder(config, visible);
  }

  async function saveEditor(w, d) {
    if (!draft || !apiState.canEdit) return;
    const status = d.getElementById('azStaffLayoutStatus');
    const save = d.getElementById('azStaffLayoutSave');
    save.disabled = true;
    status.textContent = 'Сохраняю…';
    try {
      const response = await fetch('/api/reports/services-staff-layout', {
        method:'PUT',
        credentials:'same-origin',
        headers:{
          'Content-Type':'application/json',
          'X-CSRF-Token':apiState.csrf
        },
        body:JSON.stringify({ layout:draft })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.message || ('HTTP ' + response.status));
      w.localStorage.setItem(LAYOUT_KEY, JSON.stringify(payload.layout));
      draft = normalizeDraft(w, payload.layout);
      status.textContent = 'Сохранено для всех пользователей.';
      setTimeout(() => {
        closeEditor(d);
        installHelpers(w);
        rerender(w, d);
      }, 250);
    } catch (error) {
      console.error(error);
      status.textContent = 'Не удалось сохранить настройку.';
    } finally {
      save.disabled = false;
    }
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[char]));
  }

  function escapeAttr(value) {
    return escapeHtml(String(value ?? ''));
  }
})();