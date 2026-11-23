// Entity 360: the customer view after Ask. The React page (app/src/Entity360.jsx) builds one report on request.
// Only Financial Services has it; another dataset shows a short note.
function renderEntity() {
  if (locationView.dataset !== 'financial-services')
    return heading(stageTitle('entity'), t('{name} · Customer view', {name: dataset().name})) +
      `<p class="note" data-entity-unavailable>${t('The customer view is not built for this dataset. It exists for Financial Services only.')}</p>`;
  return heading(stageTitle('entity'), t('{name} · One customer, from the tables, the documents and the concepts. Built on request.', {name: dataset().name})) +
    '<div id="pf-root"></div>';
}
