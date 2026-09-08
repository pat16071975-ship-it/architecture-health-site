(() => {
  let economics = null;
  let installed = false;

  const groupOrder = [
    'Врачи',
    'АУП',
    'Администраторы',
    'Вспомогательный персонал',
    'Лаборатория',
    'Штатный маркетинг'
  ];

  const norm = value => String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\s+/g, ' ')
    .trim();

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

  function hasAny(values) {
    return (values || []).some(value => value !== null && Math.abs(Number(value) || 0) > 0.01);
  }

  function assignmentValues(year, assignment) {
    return auditedValues(year, assignment?.months || {});
  }

  function payrollGroupNode(year, group, groupIndex) {
    const groupData = economics?.payroll?.groups?.[group];
    if (!groupData) return null;

    const assignments = (economics?.payroll?.assignments || [])
      .filter(item => item.group === group)
      .slice()
      .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'ru'));

    const people = assignments.map((item, itemIndex) => node(
      `audit-fot-${groupIndex}-${itemIndex}`,
      `${item.name} — ${item.role}`,
      4,
      assignmentValues(year, item),
      [],
      'detail'
    ));

    return node(
      `audit-fot-group-${groupIndex}`,
      group,
      3,
      auditedValues(year, groupData.months || {}),
      people,
      'subcategory'
    );
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
    const lines = (economics?.opu?.expense_lines || [])
      .filter(line => line.category === 'Медицинские расходы');

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
      const values = items.reduce(
        (sum, item) => addValues(sum, auditedValues(year, item.line.months || {})),
        Array(12).fill(null)
      );
      const children = items.map(({ line, lineIndex }) => node(
        `audit-med-source-${bucketIndex}-${lineIndex}`,
        line.source_name,
        4,
        auditedValues(year, line.months || {}),
        [],
        'detail'
      ));
      return node(
        `audit-med-bucket-${bucketIndex}`,
        bucket,
        3,
        values,
        children,
        'subcategory'
      );
    }).filter(Boolean);

    const materialsValues = auditedValues(year, economics?.opu?.materials || {});
    const materialBucketNames = new Set([
      'Стоматологические материалы',
      'Материалы для ЗТЛ',
      'Расходные медицинские материалы',
      'Услуги лаборатории',
      'Прочие материалы'
    ]);
    const materialChildren = detailNodes.filter(item => materialBucketNames.has(item.label));
    const materialChildrenTotal = materialChildren.reduce(
      (sum, item) => addValues(sum, item.values),
      Array(12).fill(null)
    );
    const residual = subtractValues(materialsValues, materialChildrenTotal);
    if (hasAny(residual)) {
      materialChildren.push(node(
        'audit-materials-residual',
        'Прочие материалы',
        3,
        residual,
        [],
        'detail'
      ));
    }

    const nodes = [];
    if (materialsValues.some(value => value !== null)) {
      nodes.push(node(
        'audit-materials-accrued',
        'Материалы — начислено',
        2,
        materialsValues,
        materialChildren,
        'subcategory'
      ));
    }

    const nonMaterialNodes = detailNodes.filter(item => !materialBucketNames.has(item.label));
    if (nonMaterialNodes.length) {
      const values = nonMaterialNodes.reduce(
        (sum, item) => addValues(sum, item.values),
        Array(12).fill(null)
      );
      nodes.push(node(
        'audit-medical-other-accrued',
        'Прочие медицинские расходы — начислено',
        2,
        values,
        nonMaterialNodes,
        'subcategory'
      ));
    }

    return nodes;
  }

  function install() {
    if (installed || !economics?.available || economics?.control?.status !== 'OK') return;
    if (typeof expenseCategoryNode !== 'function' || typeof node !== 'function' || typeof render !== 'function') return;

    installed = true;
    const originalExpenseCategoryNode = expenseCategoryNode;

    expenseCategoryNode = function(year, mainName, index) {
      const base = originalExpenseCategoryNode(year, mainName, index);
      if (year !== 2026) return base;

      if (mainName === 'ФОТ') {
        const accruedValues = auditedValues(year, economics.payroll?.total_by_month || {});
        const revenueValues = auditedValues(year, economics.opu?.revenue_net || {});
        const ratioValues = accruedValues.map((value, i) => {
          if (value === null || revenueValues[i] === null || !revenueValues[i]) return null;
          return value / revenueValues[i];
        });
        const assistantValues = auditedValues(year, economics.opu?.assistant_salary || {});
        const groupNodes = groupOrder
          .map((group, groupIndex) => payrollGroupNode(year, group, groupIndex))
          .filter(Boolean);

        const accruedNode = node(
          'audit-fot-accrued',
          'Начисленный ФОТ по зарплатному реестру',
          2,
          accruedValues,
          [
            node('audit-fot-ratio', 'ФОТ / выручка', 3, ratioValues, [], 'detail', 'percent'),
            node('audit-fot-assistants', 'Ассистенты стоматологов — в составе ФОТ', 3, assistantValues, [], 'detail'),
            ...groupNodes
          ],
          'subcategory'
        );

        const paidDetail = node(
          'fot-paid-detail',
          'ФОТ по фактическим выплатам — детализация',
          2,
          base.values,
          base.children || [],
          'subcategory'
        );

        base.children = [accruedNode, paidDetail];
        return base;
      }

      if (mainName === 'Медицинские расходы') {
        const accruedMedical = medicalAccrualNodes(year);
        const paidDetail = node(
          'medical-paid-detail',
          'Медицинские расходы по фактическим оплатам — детализация',
          2,
          base.values,
          base.children || [],
          'subcategory'
        );
        base.children = [...accruedMedical, paidDetail];
        return base;
      }

      return base;
    };

    if (typeof DATA !== 'undefined' && DATA) render();
  }

  async function load() {
    try {
      const response = await fetch('/api/reports/economics-control', {
        credentials: 'same-origin',
        cache: 'no-store'
      });
      if (!response.ok) return;
      const payload = await response.json();
      economics = payload.data;
      install();
    } catch (error) {
      console.error('AZ finrez economics integration failed', error);
    }
  }

  load();
})();
