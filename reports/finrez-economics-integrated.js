(() => {
  let economics = null;
  let economicsInstalled = false;
  let totalColumnInstalled = false;

  const norm = value => String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\(\s*[cс]\s*\)/gi, '')
    .replace(/[^а-яa-z0-9\s-]/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const assistantAssignment = item => {
    if (item?.group !== 'Вспомогательный персонал') return false;
    const text = norm([item.role, item.function, item.department].join(' '));
    return /ассист|стаж/.test(text);
  };

  const doctorDirection = item => {
    if (item?.group !== 'Врачи') return null;
    const text = norm([item.department, item.role, item.function].join(' '));
    if (/(отделение структуры|остеопат|нутрициолог|подиатр|гастроэнтеролог|нейропсихолог|массаж|миофункцион|логопед|медицинская сестра по массажу)/.test(text)) {
      return 'Отделение структуры';
    }
    if (/(функциональная стоматология|ортодонт|стоматолог|ортопед|хирург|гигиенист|отделение терапии|терапия|терапевт)/.test(text)) {
      return 'Стоматология';
    }
    return null;
  };

  const reportGroups = [
    { key: 'dent-doctors', label: 'Стоматология — врачи', filter: item => doctorDirection(item) === 'Стоматология' },
    { key: 'structure-doctors', label: 'Отделение структуры — врачи', filter: item => doctorDirection(item) === 'Отделение структуры' },
    { key: 'assistants', label: 'Ассистенты стоматологов', filter: assistantAssignment },
    { key: 'aux-other', label: 'Остальной вспомогательный персонал', filter: item => item?.group === 'Вспомогательный персонал' && !assistantAssignment(item) },
    { key: 'aup', label: 'АУП', filter: item => item?.group === 'АУП' },
    { key: 'admins', label: 'Администраторы', filter: item => item?.group === 'Администраторы' },
    { key: 'lab', label: 'Лаборатория', filter: item => item?.group === 'Лаборатория' },
    { key: 'marketing', label: 'Штатный маркетинг', filter: item => item?.group === 'Штатный маркетинг' }
  ];

  function auditedMonth(year, index) {
    const month = `${year}-${String(index + 1).padStart(2, '0')}`;
    return (economics?.period || []).includes(month) ? month : null;
  }

  function auditedValues(year, source) {
    return Array.from({ length: 12 }, (_, index) => {
      const month = auditedMonth(year, index);
      return month ? (Number(source?.[month]) || 0) : null;
    });
  }

  function valuesForAssignments(year, assignments) {
    return Array.from({ length: 12 }, (_, index) => {
      const month = auditedMonth(year, index);
      if (!month) return null;
      return assignments.reduce((sum, item) => sum + (Number(item?.months?.[month]) || 0), 0);
    });
  }

  function addValues(left, right) {
    return Array.from({ length: 12 }, (_, index) => {
      const a = left?.[index];
      const b = right?.[index];
      if (a === null && b === null) return null;
      return (Number(a) || 0) + (Number(b) || 0);
    });
  }

  function subtractValues(left, right) {
    return Array.from({ length: 12 }, (_, index) => {
      const a = left?.[index];
      const b = right?.[index];
      if (a === null) return null;
      const value = (Number(a) || 0) - (Number(b) || 0);
      return Math.abs(value) < 0.01 ? 0 : value;
    });
  }

  function hasAnyValue(values) {
    return (values || []).some(value => value !== null && Math.abs(Number(value) || 0) > 0.01);
  }

  function employeeNodes(year, assignments, parentKey) {
    return assignments
      .slice()
      .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'))
      .map((item, index) => node(
        `audit-fot-${parentKey}-${index}`,
        `${item.name} — ${item.role}`,
        4,
        auditedValues(year, item.months || {}),
        [],
        'detail'
      ));
  }

  function payrollReportingNodes(year) {
    const assignments = economics?.payroll?.assignments || [];
    return reportGroups.map((definition, groupIndex) => {
      const selected = assignments.filter(definition.filter);
      if (!selected.length) return null;
      return node(
        `audit-fot-group-${definition.key}-${groupIndex}`,
        definition.label,
        3,
        valuesForAssignments(year, selected),
        employeeNodes(year, selected, definition.key),
        'subcategory'
      );
    }).filter(Boolean);
  }

  function medicalBucket(sourceName) {
    const name = norm(sourceName);
    if (name.includes('услуги лаборатории')) return 'Услуги лаборатории';
    if (name.includes('материалы для зтл')) return 'Материалы для ЗТЛ';
    if (name.includes('стоматологические материалы')) return 'Стоматологические материалы';
    if (name.includes('расходные материалы') || name.includes('стоматологические принадлежности')) return 'Расходные медицинские материалы';
    if (name.includes('компьютерная томография') || name.includes('диагност')) return 'Диагностика / КТ';
    if (name.includes('утилизац') || name.includes('спецодеж') || name.includes('санитар')) return 'Санитарные и медицинские расходы';
    if (name.includes('материал')) return 'Прочие материалы';
    return 'Прочие медицинские расходы';
  }

  function medicalAccrualNodes(year) {
    const lines = (economics?.opu?.expense_lines || []).filter(line => line.category === 'Медицинские расходы');
    const bucketOrder = [
      'Стоматологические материалы',
      'Материалы для ЗТЛ',
      'Расходные медицинские материалы',
      'Услуги лаборатории',
      'Диагностика / КТ',
      'Санитарные и медицинские расходы',
      'Прочие материалы',
      'Прочие медицинские расходы'
    ];
    const grouped = new Map();
    lines.forEach((line, lineIndex) => {
      const bucket = medicalBucket(line.source_name);
      if (!grouped.has(bucket)) grouped.set(bucket, []);
      grouped.get(bucket).push({ line, lineIndex });
    });

    const detailNodes = bucketOrder.map((bucket, bucketIndex) => {
      const items = grouped.get(bucket) || [];
      if (!items.length) return null;
      const values = items.reduce((sum, item) => addValues(sum, auditedValues(year, item.line.months || {})), Array(12).fill(null));
      const children = items.map(({ line, lineIndex }) => node(`audit-med-source-${bucketIndex}-${lineIndex}`, line.source_name, 4, auditedValues(year, line.months || {}), [], 'detail'));
      return node(`audit-med-bucket-${bucketIndex}`, bucket, 3, values, children, 'subcategory');
    }).filter(Boolean);

    const materialsValues = auditedValues(year, economics?.opu?.materials || {});
    const materialBucketNames = new Set(['Стоматологические материалы','Материалы для ЗТЛ','Расходные медицинские материалы','Услуги лаборатории','Прочие материалы']);
    const materialChildren = detailNodes.filter(item => materialBucketNames.has(item.label));
    const materialChildrenTotal = materialChildren.reduce((sum, item) => addValues(sum, item.values), Array(12).fill(null));
    const residual = subtractValues(materialsValues, materialChildrenTotal);
    if (hasAnyValue(residual)) materialChildren.push(node('audit-materials-residual', 'Прочие материалы', 3, residual, [], 'detail'));

    const result = [];
    if (materialsValues.some(value => value !== null)) result.push(node('audit-materials-accrued', 'Материалы — начислено', 2, materialsValues, materialChildren, 'subcategory'));
    const nonMaterialNodes = detailNodes.filter(item => !materialBucketNames.has(item.label));
    if (nonMaterialNodes.length) {
      const values = nonMaterialNodes.reduce((sum, item) => addValues(sum, item.values), Array(12).fill(null));
      result.push(node('audit-medical-other-accrued', 'Прочие медицинские расходы — начислено', 2, values, nonMaterialNodes, 'subcategory'));
    }
    return result;
  }

  function sumPresent(values) {
    let any = false;
    let total = 0;
    (values || []).forEach(value => {
      if (value === null || value === undefined || value === '' || !Number.isFinite(+value)) return;
      any = true;
      total += +value;
    });
    return any ? total : null;
  }

  function totalForRow(row, year) {
    if (row && Object.prototype.hasOwnProperty.call(row, 'yearTotal')) return row.yearTotal;
    if (row?.id === 'execution') {
      let planTotal = 0;
      let factTotal = 0;
      let any = false;
      for (let index = 0; index < 12; index++) {
        const fact = factFor(year, index);
        const plan = planFor(year, index);
        if (fact === null || plan === null) continue;
        factTotal += Number(fact) || 0;
        planTotal += Number(plan) || 0;
        any = true;
      }
      return any && planTotal ? factTotal / planTotal : null;
    }
    if (row?.fmt === 'percent') return null;
    return sumPresent(row?.values);
  }

  function annualShare(row, year, value) {
    if (!row?.shareMode || value === null || value === undefined) return '';
    const all = Array.from({ length: 12 }, (_, index) => operatingExpenses(year, index)).reduce((sum, item) => sum + (Number(item) || 0), 0);
    const allShare = all ? Number(value) / all : null;
    if (row.shareMode === 'all') return `<span class="cell-share"><strong>${sharePercent(allShare)}</strong> всех расходов</span>`;
    const categoryTotal = sumPresent(row.categoryValues);
    const categoryShare = categoryTotal ? Number(value) / categoryTotal : null;
    return `<span class="cell-share"><strong>${sharePercent(allShare)}</strong> всех · <span class="share-category"><strong>${sharePercent(categoryShare)}</strong> категории</span></span>`;
  }

  function installTotalColumn() {
    if (totalColumnInstalled || typeof render !== 'function' || typeof flatten !== 'function') return;
    totalColumnInstalled = true;
    const originalRender = render;
    render = function() {
      originalRender();
      const year = +$('#year').value;
      const rows = flatten(buildRows(year));
      let style = document.getElementById('azFinrezTotalStyle');
      if (!style) {
        style = document.createElement('style');
        style.id = 'azFinrezTotalStyle';
        style.textContent = '.az-finrez-total{background:#f6f0e4!important;font-weight:700}.az-finrez-total-head{background:#eee5d6!important;font-weight:800}';
        document.head.appendChild(style);
      }
      const head = $('#head');
      if (head && !head.querySelector('.az-finrez-total-head')) head.insertAdjacentHTML('beforeend', '<th class="az-finrez-total az-finrez-total-head">Итого</th>');
      const colgroup = document.querySelector('#finrezTable colgroup');
      if (colgroup && !colgroup.querySelector('col[data-az-total]')) {
        const col = document.createElement('col');
        col.className = 'month-col';
        col.dataset.azTotal = '1';
        colgroup.appendChild(col);
      }
      document.querySelectorAll('#body > tr').forEach((tr, index) => {
        if (tr.querySelector('td.az-finrez-total')) return;
        const row = rows[index];
        if (!row) return;
        const value = totalForRow(row, year);
        const formatted = row.fmt === 'percent' ? percent(value) : money(value);
        let cls = 'az-finrez-total';
        if ((row.cls === 'operating' || row.cls === 'net') && value !== null && value !== undefined) cls += Number(value) >= 0 ? ' positive' : ' negative';
        if (formatted === '—') cls += ' muted';
        const share = row.fmt === 'money' ? annualShare(row, year, value) : '';
        tr.insertAdjacentHTML('beforeend', `<td class="${cls}"><span class="cell-main">${formatted}</span>${share}</td>`);
      });
    };
  }

  function installEconomics() {
    if (economicsInstalled || !economics?.available || economics?.control?.status !== 'OK') return;
    if (typeof expenseCategoryNode !== 'function' || typeof node !== 'function' || typeof render !== 'function') return;
    economicsInstalled = true;
    const originalExpenseCategoryNode = expenseCategoryNode;
    expenseCategoryNode = function(year, mainName, index) {
      const base = originalExpenseCategoryNode(year, mainName, index);
      if (year !== 2026) return base;
      if (mainName === 'ФОТ') {
        const accruedValues = auditedValues(year, economics.payroll?.total_by_month || {});
        const revenueValues = auditedValues(year, economics.opu?.revenue_net || {});
        const ratioValues = accruedValues.map((value, monthIndex) => value === null || revenueValues[monthIndex] === null || !revenueValues[monthIndex] ? null : value / revenueValues[monthIndex]);
        const ratioNode = node('audit-fot-ratio', 'ФОТ / выручка', 3, ratioValues, [], 'detail', 'percent');
        const totalFot = sumPresent(accruedValues);
        const totalRevenue = sumPresent(revenueValues);
        ratioNode.yearTotal = totalFot !== null && totalRevenue ? totalFot / totalRevenue : null;
        const accruedNode = node('audit-fot-accrued', 'Начисленный ФОТ по зарплатному реестру', 2, accruedValues, [ratioNode, ...payrollReportingNodes(year)], 'subcategory');
        const paidDetail = node('fot-paid-detail', 'ФОТ по фактическим выплатам — детализация', 2, base.values, base.children || [], 'subcategory');
        base.children = [accruedNode, paidDetail];
        return base;
      }
      if (mainName === 'Медицинские расходы') {
        const paidDetail = node('medical-paid-detail', 'Медицинские расходы по фактическим оплатам — детализация', 2, base.values, base.children || [], 'subcategory');
        base.children = [...medicalAccrualNodes(year), paidDetail];
        return base;
      }
      return base;
    };
  }

  async function load() {
    installTotalColumn();
    if (typeof DATA !== 'undefined' && DATA) render();
    try {
      const response = await fetch('/api/reports/economics-control', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const payload = await response.json();
      economics = payload.data;
      installEconomics();
      if (typeof DATA !== 'undefined' && DATA) render();
    } catch (error) {
      console.error('AZ finrez economics integration failed', error);
    }
  }

  load();
})();