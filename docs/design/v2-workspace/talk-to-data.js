const talkThreads = new Map();

function renderTalkToData(available) {
  return `<section class="panel talk-panel" id="talk-to-data" aria-labelledby="talk-title">
    <div class="panel-head"><h2 id="talk-title">${t('Talk to Data')}</h2></div>
    <div class="panel-body">
      <p>${t('Ask about this dataset’s definitions and evidence, or describe the data product you need.')}</p>
      ${available ? `<div class="talk-thread" role="log" aria-label="${html(t('Conversation'))}"></div>
        <form class="talk-form">
          <label for="talk-question">${t('Question or product requirements')}</label>
          <textarea id="talk-question" name="question" rows="3" maxlength="2000" required
            aria-describedby="talk-hint"></textarea>
          <p id="talk-hint" class="helper">${t('Include the fields, measures, or level of detail you need.')}</p>
          <label class="talk-interpretation" hidden><input type="checkbox" name="useMistral"> ${t('Use Mistral to interpret this request')}</label>
          <p class="talk-interpretation-note helper" hidden>${t('Mistral receives the question and candidate definitions.')}</p>
          <div class="action-row">
            <button type="submit" name="mode" value="query" class="button primary">${t('Ask a question')}</button>
            <button type="submit" name="mode" value="proposal" class="button secondary">${t('Draft a data product')}</button>
          </div>
          <p class="talk-status helper" role="status"></p>
        </form>` : `<p class="note">${t('Start the local workspace server to ask questions or draft a data product.')}</p>`}
    </div>
  </section>`;
}

const talkText = value => html(maskIdentifiers(value));
const talkList = values => values?.length ? `<ul>${values.map(value => `<li>${talkText(value)}</li>`).join('')}</ul>` : '';

function renderTalkAnswer(answer, config) {
  const {definitions = [], evidence = [], facts = [], proposal, sparkDraft} = answer;
  const citations = evidence.map(item => [item, config.cite(item)]).filter(([, path]) => path);
  const definitionsMarkup = definitions.length ? `<section><h4>${t('Grounding definitions')}</h4><ul>
    ${definitions.map(item => `<li><strong>${talkText(item.title)}</strong>
      <p>${talkText(item.definition)}</p></li>`).join('')}</ul></section>` : '';
  const evidenceMarkup = citations.length ? `<section><h4>${t('Evidence')}</h4>
    ${citations.map(([item, cite]) => {
      const locator = observationLocator(item);
      return `<blockquote class="quote"><strong>${talkText(item.title)}</strong>
        <p>${talkText(item.quote)}</p>
        <a href="${html(cite)}">${t('Open cited source · {locator}', {locator: talkText(locator)})}</a>
        </blockquote>`;
    }).join('')}</section>` : '';
  const factsMarkup = facts.length ? `<section><h4>${t('Reported facts')}</h4><dl class="kv">${facts.map(item =>
    `<div><dt>${talkText(item.label)}</dt><dd>${talkText(item.value)}
    ${item.source ? `<small>${talkText(item.source)}</small>` : ''}
    ${item.startDate ? `<small>${item.endDate && item.endDate !== item.startDate ? t('Business dates {start} to {end}', {start: talkText(item.startDate), end: talkText(item.endDate)}) : t('Business dates {start}', {start: talkText(item.startDate)})}</small>` : ''}
    </dd></div>`).join('')}</dl></section>` : '';
  const proposalMarkup = proposal ? `<section><h4>${t('Proposed data product')}</h4><strong>${talkText(proposal.title)}</strong>
    <dl class="kv"><div><dt>${t('Source table')}</dt><dd>${talkText(proposal.table)}</dd></div>
    <div><dt>${t('Grain')}</dt><dd>${talkText(proposal.grain)}</dd></div></dl>
    ${proposal.fields?.length ? `<p>${t('Proposed fields')}</p>${talkList(proposal.fields.map(field => typeof field === 'string' ? field : field.name || field.title))}` : ''}
    ${proposal.limitations?.length ? `<p>${t('Open requirements')}</p>${talkList(proposal.limitations)}` : ''}</section>` : '';
  const sparkMarkup = config.canDraft && sparkDraft ? `<section><h4>${t('Spark draft')}</h4>
    <p>${t('Review the bindings and approve the draft before running it in Cloudera Data Engineering.')}</p>
    ${fieldRows([
      [t('Source binding'), sparkDraft.source?.path || sparkDraft.source?.table || sparkDraft.source || t('Review required')],
      [t('Model revision'), sparkDraft.source?.modelRevision || proposal?.modelRevision || t('Unavailable')],
      [t('Target binding'), sparkDraft.target?.description || sparkDraft.target || t('Unbound')]
    ])}
    ${sparkDraft.dryRun ? `<p>${talkText(sparkDraft.dryRun)}</p>` : ''}
    <pre class="ingest-output" tabindex="0" aria-label="${html(t('PySpark draft'))}"><code>${talkText(sparkDraft.code)}</code></pre>
    </section>` : '';
  const label = {grounded:'Grounded response', clarify:'Needs clarification', unsupported:'Insufficient grounding'};
  return `<article class="talk-reply"><h3>${t(label[answer.status] || 'Workspace response')}</h3>
    <p>${talkText(V2I18n.server(answer.summary))}</p>
    ${answer.interpretation ? `<p class="helper">${t(answer.interpretation === 'mistral' ? 'Mistral interpretation' : 'Local interpretation')}</p>` : ''}
    ${factsMarkup}${definitionsMarkup}${evidenceMarkup}${proposalMarkup}
    ${answer.gaps?.length ? `<section><h4>${t('Missing inputs')}</h4>${talkList(answer.gaps)}</section>` : ''}
    ${sparkMarkup}</article>`;
}

function renderStructuredAnswer(answer) {
  const facts = answer.facts || [], citations = answer.citations || [], comparison = answer.comparison;
  const filterText = filters => Object.entries(filters || {}).map(([key, value]) => `${key} = ${value}`).join(', ');
  const label = {answered:'Answered', refused:'Refused', clarify:'Needs clarification'};
  return `<section class="structured-answer" data-structured-answer><h4>${t('Structured answer · {status}', {status: talkText(t(label[answer.status] || answer.status))})}</h4>
    ${answer.questionEnglish ? `<p class="muted" data-understood>${t('Understood as: {english}', {english: talkText(answer.questionEnglish)})}</p>` : ''}
    ${answer.reason ? `<p data-refusal>${talkText(V2I18n.server(answer.reason))}${answer.refusalReason ? ` <small>${t('Reason: {reason}', {reason: talkText(V2I18n.server(answer.refusalReason))})}</small>` : ''}</p>` : ''}
    ${facts.length ? `<h5>${t('SQL facts')}</h5><dl class="kv">${facts.map(item => `<div><dt>${talkText(item.label)}</dt>
      <dd>${talkText(item.value)}<small>${talkText(filterText(item.filters))}</small></dd></div>`).join('')}</dl>` : ''}
    ${comparison ? `<h5>${t('Comparison')}</h5><p data-comparison>${t('Document {document} against SQL {fact}', {document: talkText(comparison.documentValue) + (comparison.unit ? ' ' + talkText(comparison.unit) : ''), fact: talkText(comparison.factValue)})}${comparison.test ? t(comparison.holds ? ' · test {test} · holds' : ' · test {test} · does not hold', {test: talkText(comparison.test)}) : ''}</p>` : ''}
    ${citations.length ? `<h5>${t('Cited clauses')} <small>${t('(at most 4 shown)')}</small></h5>${citations.map(item => `<blockquote class="quote">
      <strong>${talkText(item.title || item.source)}</strong> <small>${talkText(item.role)}</small><p>${talkText(item.span)}</p></blockquote>`).join('')}` : ''}
    ${answer.conclusion !== null && answer.conclusion !== undefined && answer.status === 'answered' ? `<p>${t('Conclusion: {value}', {value: `<strong>${talkText(answer.conclusion)}</strong>`})}</p>` : ''}</section>`;
}

// The structured answer is shown only when it has facts or clauses, or a reason the question could not be answered from them.
const needsStructuredAnswer = answer => answer && (answer.facts?.length || answer.citations?.length || answer.status === 'clarify' ||
  (answer.status === 'refused' && answer.refusalReason && answer.refusalReason !== 'no supported answer'));

function mountTalkToData(root, config) {
  if (!root || !config.available) return;
  if (!talkThreads.has(config.scope)) {
    talkThreads.set(config.scope, {messages:[], draft:'', pending:false, error:''});
  }
  const thread = talkThreads.get(config.scope);
  const form = root.querySelector('form');
  const question = form.elements.question;
  const log = root.querySelector('.talk-thread');
  const status = root.querySelector('.talk-status');
  const interpretation = form.elements.useMistral;
  if (config.canDraft) form.querySelector('[value="proposal"]').textContent = t('Draft data product and Spark');
  config.request('/api/talk-to-data').then(result => {
    interpretation.closest('label').hidden = !result.mistralAvailable;
    form.querySelector('.talk-interpretation-note').hidden = !result.mistralAvailable;
  }).catch(() => { interpretation.checked = false; });
  thread.repaint = () => {
    if (!root.isConnected) return;
    if (question.value !== thread.draft) question.value = thread.draft;
    log.innerHTML = thread.messages.map(({text, answer, structured, asked, mode}, index) => `<div class="talk-turn">
      <article class="talk-question"><h3>${t('Your question')}</h3><p>${talkText(text)}</p></article>
      ${answer ? renderTalkAnswer(answer, config) : ''}${structured ? renderStructuredAnswer(structured) : ''}
      ${answer && mode === 'query' && !asked ? `<button type="button" class="button secondary" data-load-structured="${index}">${t('Check against SQL facts and citations')}</button>` : ''}</div>`).join('');
    form.querySelectorAll('button').forEach(button => button.disabled = thread.pending);
    status.textContent = thread.pending ? t('Checking available definitions and evidence…') : thread.error;
    status.classList.toggle('workspace-error', Boolean(thread.error));
  };
  // The structured answer costs a model call. It is requested when the first response cites passages (the question
  // needs documents), or when the user asks for it with the button under the answer.
  const loadStructured = turn => {
    turn.asked = true;
    return config.request('/api/answer', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({dataset:config.scope, question:turn.text})})
      .then(result => { if (needsStructuredAnswer(result)) { turn.structured = result; } thread.repaint(); }, () => thread.repaint());
  };
  log.addEventListener('click', event => {
    const button = event.target.closest('[data-load-structured]');
    if (button) { button.disabled = true; loadStructured(thread.messages[Number(button.dataset.loadStructured)]); }
  });
  question.value = thread.draft;
  thread.repaint();
  question.addEventListener('input', () => {
    question.setCustomValidity('');
    thread.draft = question.value;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const text = question.value.trim();
    if (thread.pending) return;
    if (!text) {
      question.setCustomValidity(t('Enter a question or product requirements.'));
      question.reportValidity();
      return;
    }
    const mode = event.submitter?.value || 'query';
    const chosenInterpretation = !interpretation.closest('label').hidden && interpretation.checked ? 'mistral' : 'offline';
    interpretation.checked = false;
    const history = thread.messages.filter(turn => turn.answer).slice(-3).flatMap(turn => [
      {role:'user',text:turn.text}, {role:'assistant',text:turn.answer.summary}
    ]);
    const turn = {text, answer:null, mode};
    thread.messages.push(turn);
    thread.pending = true;
    thread.error = '';
    thread.draft = '';
    thread.repaint();
    try {
      turn.answer = await config.request('/api/talk-to-data', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({dataset:config.scope, question:text, mode, history, interpretation:chosenInterpretation})
      });
      if (mode === 'query' && turn.answer.evidence?.length) loadStructured(turn);   // a failed request leaves the block out
    } catch (error) {
      thread.messages.pop();
      thread.error = V2I18n.server(error.message);
      if (!thread.draft) thread.draft = text;
    } finally {
      thread.pending = false;
      thread.repaint();
    }
  });
}
