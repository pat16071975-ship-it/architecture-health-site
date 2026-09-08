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
            node('audit-fot-ratio', 'ФОТ / выручка ОПиУ', 3, ratioValues, [], 'detail', 'percent'),
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
        const materialsValues = auditedValues(year, economics.opu?.materials || {});
        if (materialsValues.some(value => value !== null)) {
          base.children = [
            node(
              'audit-materials-accrued',
              'Материалы по ОПиУ — начислено',
              2,
              materialsValues,
              [],
              'subcategory'
            ),
            ...(base.children || [])
          ];
        }
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
