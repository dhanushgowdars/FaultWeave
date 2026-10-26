/* Gateway readout and in-page engineer session. Scenario actions live separately. */
(function (root) {
  'use strict';
  const MAX_AGE_MS = 20000;
  const ENDPOINT = '/api/v1/command-center/snapshot';
  const FROZEN_MODEL = '7bbf6c2b0ba57d40a45d1ac633c3b50ecfe9ed1b2ea6354d460a32fd120da0be';

  function normalizeSnapshot(payload, now = Date.now()) {
    if (!payload || payload.schema_version !== 1 ||
        payload.incident_feed?.source_model !== 'earlier_gateway_frozen_model' ||
        !payload.telemetry || !payload.candidate04 ||
        !['NOT_CONNECTED', 'AWAITING_TELEMETRY', 'AWAITING_PREDICTION', 'STALE', 'UNAVAILABLE', 'PREDICTED', 'RECENT_PREDICTION', 'HISTORICAL'].includes(payload.candidate04.status)) {
      throw new Error('Unexpected gateway contract');
    }
    let state = payload.telemetry.status;
    const allowed = ['UNOBSERVED', 'INSUFFICIENT', 'STALE', 'CLOCK_SKEW', 'READY'];
    if (!allowed.includes(state)) throw new Error('Unexpected telemetry state');
    const last = Date.parse(payload.telemetry.last_event_at || '');
    const generated = Date.parse(payload.generated_at || '');
    if (!Number.isFinite(generated) || generated > now + 5000 || now - generated > MAX_AGE_MS) {
      state = 'STALE';
    } else if (state === 'READY' && (!Number.isFinite(last) || last > now + 5000 || now - last > MAX_AGE_MS)) {
      state = 'STALE';
    }
    if (state === 'READY' && !(Number(payload.telemetry.request_count) > 0 && Number(payload.telemetry.event_count) > 0)) {
      state = 'INSUFFICIENT';
    }
    const current = state === 'READY' && payload.incident_feed.status === 'CURRENT'
      ? payload.incident_feed.current : null;
    let candidate = payload.candidate04;
    if (candidate.status === 'PREDICTED' || candidate.status === 'RECENT_PREDICTION') {
      const end = Date.parse(candidate.window_ended_at || '');
      if (!Number.isFinite(generated) || generated > now + 5000 || now - generated > MAX_AGE_MS) {
        candidate = { status: 'STALE', detail: 'The readout is too old to show a V2 prediction.' };
      } else if (!Number.isFinite(end) || end > now + 5000 || now - end > MAX_AGE_MS ||
          candidate.source !== 'isolated_v2_research' ||
          candidate.deployment_status !== 'RESEARCH_ONLY_NOT_DEPLOYED' ||
          !(candidate.request_count >= 70) || !(candidate.event_count >= 70) ||
          candidate.model_sha256 !== FROZEN_MODEL ||
          !/^[0-9a-f]{64}$/i.test(candidate.receipt_sha256 || '') ||
          typeof candidate.prediction !== 'string') {
        candidate = { status: 'UNAVAILABLE', detail: 'The V2 prediction receipt could not be verified.' };
      }
    } else if (candidate.status === 'HISTORICAL') {
      const saved = candidate.last_saved || {};
      const end = Date.parse(saved.window_ended_at || '');
      if (!Number.isFinite(generated) || generated > now + 5000 || now - generated > MAX_AGE_MS ||
          !Number.isFinite(end) || end > now + 5000 ||
          candidate.source !== 'isolated_v2_research' ||
          candidate.deployment_status !== 'RESEARCH_ONLY_NOT_DEPLOYED' ||
          !(saved.request_count >= 70) || !(saved.event_count >= 70) ||
          saved.model_sha256 !== FROZEN_MODEL ||
          !/^[0-9a-f]{64}$/i.test(saved.receipt_sha256 || '') ||
          typeof saved.prediction !== 'string') {
        candidate = { status: 'UNAVAILABLE', detail: 'Saved V2 research receipt could not be verified.' };
      }
    }
    return {
      state,
      detail: state === 'STALE' ? 'Telemetry or gateway response is too old to claim a live result.' : String(payload.telemetry.detail || ''),
      ageSeconds: Number.isFinite(last) ? Math.max(0, Math.floor((now - last) / 1000)) : null,
      eventCount: state === 'READY' ? payload.telemetry.event_count : null,
      requestCount: state === 'READY' ? payload.telemetry.request_count : null,
      current,
      incidentCurrentVerified: state === 'READY' && payload.incident_feed.status === 'CURRENT',
      incidentCount: payload.incident_feed.count,
      candidateStatus: candidate.status,
      candidate
    };
  }

  function candidatePresentation(candidate) {
    if (candidate.status === 'HISTORICAL') {
      const saved = candidate.last_saved;
      return { title: `Saved result: ${saved.prediction}`,
        detail: `Historical research only · window ended ${saved.window_ended_at} · ${saved.request_count} client requests · receipt ${saved.receipt_sha256.slice(0, 12)}… · no current health or incident claim.` };
    }
    if (candidate.status === 'PREDICTED' || candidate.status === 'RECENT_PREDICTION') {
      const current = candidate.status === 'PREDICTED' ? 'Current V2 window' : 'Recent V2 receipt; window has advanced';
      return { title: candidate.prediction,
        detail: `${current} · frozen research model · ${candidate.request_count} client requests · receipt ${candidate.receipt_sha256.slice(0, 12)}… · no origin or recovery claim.` };
    }
    const labels = {
      AWAITING_TELEMETRY: 'Awaiting V2 telemetry', AWAITING_PREDICTION: 'Awaiting V2 prediction',
      STALE: 'V2 telemetry stale', UNAVAILABLE: 'V2 unavailable', NOT_CONNECTED: 'Not connected'
    };
    return { title: labels[candidate.status],
      detail: String(candidate.detail || candidate.reason || 'No verified V2 research prediction is available.') };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { normalizeSnapshot, candidatePresentation, ENDPOINT };
  if (!root.document) return;
  const get = id => root.document.getElementById(id);
  const tokenInput = get('liveEngineerToken');
  if (!tokenInput) return;
  const usernameInput = get('liveEngineerName');
  const passwordInput = get('liveEngineerPassword');
  const connectButton = get('connectLiveReadout');
  const disconnectButton = get('disconnectLiveReadout');
  let token = '';
  let timer = null;
  let pending = null;
  let caseController = null;
  let selectedCaseId = null;
  function authHeaders() { return token === 'COOKIE' ? {} : { Authorization: `Bearer ${token}` }; }
  function connected() {
    connectButton.disabled = true;
    disconnectButton.disabled = false;
    clearClaims('Connecting', 'Checking the read-only gateway…');
    refresh();
    refreshCases();
    if (timer) root.clearInterval(timer);
    timer = root.setInterval(refresh, 10000);
    root.dispatchEvent?.(new Event('faultweave:connected'));
  }

  function setText(id, value) { get(id).textContent = value; }
  function clearClaims(state, message) {
    setText('liveReadoutBadge', state);
    setText('liveTelemetryState', state);
    setText('liveTelemetryDetail', message);
    setText('liveIncidentState', 'Unverified');
    setText('liveIncidentDetail', 'No current incident claim is available.');
    setText('liveCandidateState', 'Not connected');
    setText('liveCandidateDetail', 'Candidate 04 remains a separate research shadow service.');
    setText('commandStatusPill', '● Live status unverified');
    setText('liveReadoutMessage', message);
    setText('researchWindowsStatus', 'No current V2 claim. Connect with engineer access to inspect saved research receipts.');
    get('researchWindowsList').replaceChildren();
    setText('researchAcceptanceStatus', 'No verified saved fault-run report has been loaded.');
    get('researchAcceptanceList').replaceChildren();
    setText('acceptanceMatrixStatus', 'No verified isolated acceptance coverage has been loaded.');
    get('acceptanceMatrix').replaceChildren();
  }
  function renderResearchHistory(candidate) {
    const container = get('researchWindowsList');
    container.replaceChildren();
    const allRows = Array.isArray(candidate.recent_receipts) ? candidate.recent_receipts
      : candidate.status === 'HISTORICAL' ? [candidate.last_saved] : [];
    const rows = allRows.slice(0, 3);
    if (!['HISTORICAL', 'PREDICTED', 'RECENT_PREDICTION'].includes(candidate.status) || !rows.length) {
      setText('researchWindowsStatus', 'No separately verified saved V2 window is available. This view does not request inference.');
      return;
    }
    for (const row of rows) {
      if (!row || row.model_sha256 !== FROZEN_MODEL ||
          !/^[0-9a-f]{64}$/i.test(row.receipt_sha256 || '') ||
          (row.feature_vector_sha256 && !/^[0-9a-f]{64}$/i.test(row.feature_vector_sha256)) ||
          !(row.request_count >= 70 && row.event_count >= 70) ||
          !Number.isFinite(Date.parse(row.window_ended_at || '')) ||
          Date.parse(row.window_ended_at) > Date.now() + 5000 ||
          (row.window_started_at && Math.abs(Date.parse(row.window_ended_at) - Date.parse(row.window_started_at) - 60000) > 1)) {
        container.replaceChildren();
        setText('researchWindowsStatus', 'A saved receipt failed display verification; research history is unavailable.');
        return;
      }
      const article = item('article');
      article.append(item('span', 'mini-pill pill-blue', 'Saved research receipt'),
        item('strong', '', row.prediction || 'Prediction unavailable'),
        item('small', '', `Window ended ${row.window_ended_at} · ${row.request_count} independent client requests · ${row.event_count} service events`),
        item('small', 'citation', `Receipt SHA256 ${row.receipt_sha256}`));
      if (row.feature_vector_sha256) article.append(item('small', 'citation', `65-feature fingerprint ${row.feature_vector_sha256}`));
      container.append(article);
    }
    setText('researchWindowsStatus', `${container.children.length} newest verified frozen-model receipt(s)${allRows.length > rows.length ? ` of ${allRows.length} saved` : ''}. ${candidate.status === 'HISTORICAL' ? 'Historical only; no current health or fault claim.' : 'Check the current window status on Overview; older receipts are historical.'}`);
  }
  function renderAcceptance(candidate) {
    const container = get('researchAcceptanceList');
    container.replaceChildren();
    const rows = candidate.acceptance_runs;
    if (!Array.isArray(rows) || !rows.length) {
      setText('researchAcceptanceStatus', candidate.acceptance_detail ||
        'No verified isolated known-fault acceptance report is saved yet.');
      return;
    }
    for (const row of rows.slice(0, 5)) {
      if (!row || !/^v2observer-[0-9a-f]{16}$/.test(row.run_id || '') ||
          !['PASS', 'MISMATCH'].includes(row.status) ||
          row.scope !== 'isolated_known_fault_research_only' ||
          row.root_cause_confirmed !== false ||
          !Number.isInteger(row.observed_failed_client_requests) ||
          !Number.isInteger(row.failed_service_event_count) ||
          row.recovery_completed_requests !== 8 ||
          !Array.isArray(row.model_predictions) || !row.model_predictions.length ||
          !Array.isArray(row.receipt_sha256) ||
          row.receipt_sha256.length !== row.model_predictions.length ||
          !row.receipt_sha256.every(sha => /^[0-9a-f]{64}$/.test(sha)) ||
          !/^[0-9a-f]{64}$/.test(row.report_sha256 || '')) {
        container.replaceChildren();
        setText('researchAcceptanceStatus', 'A saved fault-run report failed display validation.');
        return;
      }
      const article = item('article');
      article.append(item('span', 'mini-pill pill-blue', 'Isolated lab run'),
        item('strong', '', `${row.status} · payment service unavailable`),
        item('small', '', `${row.observed_failed_client_requests}/113 client requests failed · ${row.failed_service_event_count} correlated failure events · Candidate 04: ${row.model_predictions.join(', ')}`),
        item('small', '', `${row.recovery_completed_requests}/8 separate recovery requests completed · ${row.run_id}`),
        item('small', 'citation', `Report SHA256 ${row.report_sha256} · receipt ${row.receipt_sha256.join(', ')}`));
      const evidence = Array.isArray(row.first_failure_evidence) ? row.first_failure_evidence : [];
      for (const event of evidence.slice(0, 3)) {
        if (!event || !/^[0-9a-f]{64}$/.test(event.event_sha256 || '')) continue;
        article.append(item('small', 'citation',
          `${event.timestamp || 'Time unavailable'} · ${event.service || 'service unknown'} → ${event.downstream_service || 'dependency unconfirmed'} · event SHA256 ${event.event_sha256}`));
      }
      container.append(article);
    }
    setText('researchAcceptanceStatus', `${container.children.length} saved isolated lab report(s). The model result is measured; dependency root cause is unconfirmed.`);
  }
  function renderAcceptanceMatrix(candidate) {
    const container = get('acceptanceMatrix');
    container.replaceChildren();
    const rows = candidate.acceptance_matrix;
    if (!Array.isArray(rows) || rows.length !== 9) {
      setText('acceptanceMatrixStatus', 'The nine-family acceptance matrix is unavailable or incomplete.');
      return;
    }
    const families = new Set();
    let completed = 0;
    for (const row of rows) {
      if (!row || typeof row.family !== 'string' || families.has(row.family) ||
          !['PASS', 'MISMATCH', 'NOT_RUN'].includes(row.status) ||
          row.scope !== 'isolated_live_acceptance') {
        container.replaceChildren();
        setText('acceptanceMatrixStatus', 'The acceptance matrix failed display validation.');
        return;
      }
      families.add(row.family);
      const article = item('article');
      const badgeClass = row.status === 'PASS' ? 'pill-green' : row.status === 'MISMATCH' ? 'pill-red' : 'pill-blue';
      article.append(item('span', `mini-pill ${badgeClass}`, row.status),
        item('strong', '', row.family.replaceAll('_', ' ')));
      if (row.status === 'NOT_RUN') {
        article.append(item('small', '', 'No isolated live acceptance report is saved for this family.'));
      } else {
        if (!/^v2observer-[0-9a-f]{16}$/.test(row.run_id || '') ||
            !/^[0-9a-f]{64}$/.test(row.report_sha256 || '') ||
            !Array.isArray(row.model_predictions) || !row.model_predictions.length) {
          container.replaceChildren();
          setText('acceptanceMatrixStatus', 'A completed matrix entry failed evidence validation.');
          return;
        }
        completed += 1;
        article.append(item('small', '', `Target ${row.fault_target || 'unverified'} · Candidate 04: ${row.model_predictions.join(', ')}`),
          item('small', 'citation', `${row.run_id} · report ${row.report_sha256}`));
      }
      container.append(article);
    }
    setText('acceptanceMatrixStatus', `${completed}/9 trained families have a verified isolated live acceptance report. Dataset evaluation remains separate.`);
  }
  function render(snapshot) {
    const data = normalizeSnapshot(snapshot);
    setText('liveReadoutBadge', data.state === 'READY' ? 'Fresh gateway data' : data.state);
    setText('liveTelemetryState', data.state === 'READY' ? 'Fresh 60-second window' : data.state);
    setText('liveTelemetryDetail', data.state === 'READY'
      ? `${data.requestCount} independently observed requests · ${data.eventCount} events · latest event ${data.ageSeconds}s ago`
      : data.detail);
    setText('commandStatusPill', data.state === 'READY' ? '● Gateway telemetry fresh' : '● Live status unverified');
    if (data.current) {
      setText('liveIncidentState', `Incident ${data.current.incident_id || 'active'}`);
      setText('liveIncidentDetail', `Earlier model suggestion: ${data.current.fault_family || 'undetermined'}. Provisional origin: ${data.current.probable_origin || 'undetermined'}. Check case evidence before acting.`);
    } else {
      setText('liveIncidentState', data.incidentCurrentVerified ? 'No active incident in gateway memory' : 'Unverified');
      setText('liveIncidentDetail', data.incidentCurrentVerified
        ? `${data.incidentCount} recent in-memory incident records; this is not proof that all services are healthy.`
        : 'The incident feed cannot be treated as current without fresh telemetry.');
    }
    const shown = candidatePresentation(data.candidate);
    setText('liveCandidateState', shown.title);
    setText('liveCandidateDetail', shown.detail);
    renderResearchHistory(data.candidate);
    renderAcceptance(data.candidate);
    renderAcceptanceMatrix(data.candidate);
    setText('liveReadoutMessage', data.state === 'READY'
      ? 'Read-only gateway observation. Transfer evidence, the earlier gateway model and Candidate 04 remain separate evidence channels.'
      : data.detail);
  }

  function item(tag, className, value) {
    const element = root.document.createElement(tag);
    if (className) element.className = className;
    if (value !== undefined && value !== null) element.textContent = String(value);
    return element;
  }
  function fact(parent, label, value) {
    const wrap = item('div');
    wrap.append(item('span', '', label), item('strong', '', value ?? 'Not established'));
    parent.append(wrap);
  }
  function money(minor, currency) {
    if (!Number.isSafeInteger(minor) || minor < 0 || currency !== 'INR') return 'Amount unverified';
    return `₹${new Intl.NumberFormat('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(minor / 100)}`;
  }
  function clearCases(message) {
    if (caseController) caseController.abort();
    caseController = null;
    selectedCaseId = null;
    get('realCaseList').replaceChildren();
    get('realCaseDetail').replaceChildren();
    get('liveInvestigationDetail').replaceChildren();
    setText('realCasesStatus', message);
    setText('liveInvestigationSummary', 'No real backend case is selected. Connect on Overview to load saved evidence.');
  }
  async function caseGet(path) {
    if (!token) throw new Error('Sign in as an engineer on Overview.');
    if (caseController) caseController.abort();
    const controller = new AbortController();
    caseController = controller;
    const timeout = root.setTimeout(() => controller.abort(), 5000);
    try {
      const response = await root.fetch(path, {
        method: 'GET', headers: authHeaders(),
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal
      });
      if (response.status === 401 || response.status === 403) {
        disconnect('Engineer access expired or was rejected. Get a new temporary token.');
        throw new Error('Engineer access expired. Reconnect on Overview.');
      }
      if (response.status === 404) throw new Error('Saved case route or selected case was not found. Restart the updated local preview.');
      if (!response.ok) throw new Error('Saved case evidence is currently unavailable.');
      return await response.json();
    } finally {
      root.clearTimeout(timeout);
      if (caseController === controller) caseController = null;
    }
  }
  function renderCaseDetail(record) {
    if (record.schema_version !== 1 || record.source !== 'persisted_transfer_evidence' ||
        record.transfer_id !== selectedCaseId || !Array.isArray(record.timeline)) {
      throw new Error('Saved case contract could not be verified.');
    }
    const target = get('realCaseDetail');
    target.replaceChildren();
    target.append(item('h3', '', `Transfer ${record.transfer_id}`));
    target.append(item('p', 'real-case-muted',
      `Persisted workflow evidence · ${record.created_at || 'timestamp unavailable'} · Candidate 04 is separate research; no per-transfer Candidate 04 inference is claimed.`));
    const facts = item('div', 'real-case-facts');
    fact(facts, 'Workflow state', record.state);
    fact(facts, 'Funds status', record.funds_status);
    fact(facts, 'Transfer amount', money(record.amount_minor, record.currency));
    fact(facts, 'Recorded failures', record.case_history_counts?.failures);
    fact(facts, 'Engineer reviews', record.case_history_counts?.reviews);
    fact(facts, 'Recoveries', record.case_history_counts?.recoveries);
    target.append(facts);
    const localization = record.localization;
    const first = localization?.first_failure;
    if (!localization || !['OBSERVED_FAILURE_ROOT_CAUSE_UNCONFIRMED', 'NO_FAILED_EVENT_RECORDED'].includes(localization.status) ||
        localization.root_cause_confirmed !== false ||
        (first && (first.status !== 'FAILED' || !record.timeline.some(row =>
          row.event_id === first.event_id && row.status === 'FAILED' && row.service === first.service))) ||
        (localization.status === 'OBSERVED_FAILURE_ROOT_CAUSE_UNCONFIRMED') !== !!first ||
        !Array.isArray(localization.cited_failure_event_ids) ||
        !localization.cited_failure_event_ids.every(id => record.timeline.some(row => row.event_id === id && row.status === 'FAILED'))) {
      throw new Error('Saved investigation citations could not be verified.');
    }
    setText('liveInvestigationSummary', first
      ? `Real case ${record.transfer_id}: first saved failure ${first.service || 'service unknown'} / ${first.step || 'step unknown'} at ${first.timestamp}; evidence ${first.event_id}. Funds: ${record.funds_status || 'unverified'}. Root cause remains unconfirmed.`
      : `Real case ${record.transfer_id}: ${record.state || 'state unverified'}; no failed event is recorded in this case timeline. Funds: ${record.funds_status || 'unverified'}.`);
    const investigation = get('liveInvestigationDetail');
    investigation.replaceChildren();
    const investigationFacts = item('div', 'real-case-facts');
    fact(investigationFacts, 'Transfer', record.transfer_id);
    fact(investigationFacts, 'Workflow state', record.state);
    fact(investigationFacts, 'Funds status', record.funds_status);
    fact(investigationFacts, 'Amount', money(record.amount_minor, record.currency));
    fact(investigationFacts, 'First failed service', first?.service || 'No saved failure');
    fact(investigationFacts, 'First failed step', first?.step || 'No saved failure');
    investigation.append(investigationFacts);
    investigation.append(item('h4', '', 'Evidence-backed conclusion'));
    investigation.append(item('p', 'real-case-note', first
      ? `The first observed failed event is ${first.event_id} at ${first.timestamp}. It records ${first.service || 'an unknown service'} / ${first.step || 'an unknown step'}. Root cause remains unconfirmed.`
      : 'The persisted timeline contains no failed event, so this view makes no fault-location claim.'));
    if (localization.cited_failure_event_ids.length) investigation.append(item('p', 'citation',
      `Cited failed events: ${localization.cited_failure_event_ids.join(', ')}`));
    const savedAssistant = record.saved_assistant;
    investigation.append(item('h4', '', 'Saved grounded explanation'));
    if (!savedAssistant) {
      investigation.append(item('p', 'real-case-muted', 'No saved assistant explanation is linked to this case. This read-only page did not call an AI provider.'));
    } else {
      investigation.append(item('p', 'real-case-note', savedAssistant.executive_summary || savedAssistant.headline || 'Saved explanation has no summary.'));
      for (const label of ['first_failure', 'funds_state', 'model_assessment', 'probable_cause']) {
        const statement = savedAssistant.statements?.[label];
        if (!statement) continue;
        investigation.append(item('p', 'real-case-muted',
          `${label.replaceAll('_', ' ')} (${statement.kind || 'unverified'}): ${statement.text || 'No statement'} · cites ${statement.evidence_event_ids?.join(', ') || 'no event ID'}`));
      }
    }
    target.append(item('h4', '', 'Evidence-backed fault location'));
    target.append(item('p', 'real-case-note', first
      ? `First observed failed step: ${first.service || 'unknown service'} · ${first.step || 'unknown step'} · event ${first.event_id}. This identifies where this transfer first recorded failure; the underlying dependency root cause is unconfirmed.`
      : 'No failed step is recorded in this transfer. There is no fault location to claim.'));
    if (localization.cited_failure_event_ids.length) target.append(item('p', 'citation',
      `Persisted investigation cites failed events: ${localization.cited_failure_event_ids.join(', ')}`));
    if (localization.reported_failure_service) target.append(item('p', 'real-case-muted',
      `Backend investigation also reports ${localization.reported_failure_service} as an observed failure service. Check the cited timeline before identifying a dependency cause.`));
    target.append(item('h4', '', 'Persisted service timeline'));
    if (!record.timeline.length) target.append(item('p', 'real-case-muted', 'No service events were returned for this case.'));
    else {
      const table = item('table', 'real-case-events');
      const body = item('tbody');
      for (const event of record.timeline) {
        const row = item('tr');
        row.dataset.result = String(event.status || 'UNKNOWN');
        row.append(item('td', '', event.timestamp || 'Time unknown'));
        const explanation = item('td');
        explanation.append(item('strong', '', `${event.service || 'Unknown service'} · ${event.step || 'unspecified step'}`),
          item('p', 'real-case-muted', event.detail || 'No saved detail'));
        row.append(explanation, item('td', '', event.status || 'UNKNOWN'),
          item('td', 'citation', event.event_id));
        body.append(row);
      }
      table.append(body);
      target.append(table);
    }
    target.append(item('h4', '', 'Financial evidence'));
    const entries = record.journal_entries || [];
    const movements = entries.map(row => `${row.direction || 'Entry'} ${money(row.amount_minor, row.currency)}`);
    target.append(item('p', 'real-case-note', movements.length
      ? `Saved journal entries: ${movements.join(' · ')}. Review the event IDs and financial record before asserting a recovery.`
      : 'No journal entries were returned. This view does not claim that money moved or that recovery succeeded.'));
    const assistant = record.saved_assistant;
    target.append(item('h4', '', 'Saved explanation · earlier model snapshot'));
    if (!assistant) {
      target.append(item('p', 'real-case-muted', 'No previously saved assistant result is linked to this transfer. No AI provider was called by this read-only view.'));
    } else {
      const laterWorkflow = record.state === 'COMPLETED' ||
        Number(record.case_history_counts?.reviews) > 0 ||
        Number(record.case_history_counts?.recoveries) > 0;
      const explanation = laterWorkflow ? item('details', 'real-case-saved-history') : target;
      target.append(item('p', 'real-case-note', laterWorkflow
        ? `Historical analysis only. Current saved state: ${record.state || 'unverified'}; funds: ${record.funds_status || 'unverified'}. Later review or recovery activity is recorded above. Statements about awaiting review or funds in the earlier explanation may be outdated.`
        : 'Saved analysis of this case; use the persisted state and timeline above for its latest recorded outcome.'));
      if (laterWorkflow) {
        explanation.append(item('summary', '', 'Show earlier saved explanation'));
        target.append(explanation);
      }
      explanation.append(item('p', 'real-case-note',
        `${assistant.status || 'Saved analysis'} · earlier frozen model · ${assistant.mode || 'mode unverified'} · ${assistant.headline || ''}`));
      explanation.append(item('p', '', assistant.executive_summary || 'No saved summary.'));
      for (const label of ['first_failure', 'funds_state', 'model_assessment', 'probable_cause']) {
        const statement = assistant.statements?.[label];
        if (!statement) continue;
        explanation.append(item('p', 'real-case-muted',
          `${label.replaceAll('_', ' ')} (${statement.kind}): ${statement.text || 'No statement'} · cites ${statement.evidence_event_ids?.join(', ') || 'no event ID'}`));
      }
      if (assistant.limitations?.length) explanation.append(item('p', 'real-case-muted', `Limitations: ${assistant.limitations.join(' · ')}`));
    }
  }
  async function openCase(id) {
    selectedCaseId = id;
    get('realCaseList').querySelectorAll('button').forEach(button =>
      button.setAttribute('aria-pressed', button.dataset.caseId === id ? 'true' : 'false'));
    setText('realCasesStatus', 'Loading saved evidence…');
    try {
      const record = await caseGet(`/api/v1/command-center/cases/${encodeURIComponent(id)}`);
      if (token && selectedCaseId === id) {
        renderCaseDetail(record);
        setText('realCasesStatus', `Verified read-only evidence for ${id}.`);
      }
    } catch (error) {
      if (token) setText('realCasesStatus', error.message || 'Saved evidence unavailable.');
    }
  }
  async function refreshCases() {
    if (!token) {
      setText('realCasesStatus', 'Sign in as an engineer on Overview to read saved cases.');
      return;
    }
    setText('realCasesStatus', 'Reading saved backend cases…');
    try {
      const data = await caseGet('/api/v1/command-center/cases');
      if (!token) return;
      if (data.schema_version !== 1 || data.source !== 'persisted_transfer_evidence' || !Array.isArray(data.items)) {
        throw new Error('Saved case listing could not be verified.');
      }
      const list = get('realCaseList');
      list.replaceChildren();
      if (!data.items.length) {
        get('realCaseDetail').replaceChildren();
        setText('realCasesStatus', 'No saved transfers are visible to this engineer. No live case is being claimed.');
        return;
      }
      const visible = data.items.slice(0, 8);
      for (const row of visible) {
        if (!/^[0-9a-f-]{36}$/i.test(row.id || '')) continue;
        const button = item('button');
        button.type = 'button';
        button.dataset.caseId = row.id;
        button.setAttribute('aria-pressed', 'false');
        button.append(item('span', 'mini-pill pill-blue', row.state || 'UNKNOWN'),
          item('strong', '', money(row.amount_minor, row.currency)),
          item('small', '', `${row.funds_status || 'funds unverified'} · ${row.created_at || 'time unknown'}`),
          item('small', '', `ID ${row.id}`));
        button.addEventListener('click', () => openCase(row.id));
        list.append(button);
      }
      setText('realCasesStatus', `Showing the ${list.children.length} newest saved transfer(s) of ${data.total ?? data.items.length}. Select a case for persisted evidence.`);
      if (list.firstElementChild) await openCase(list.firstElementChild.dataset.caseId);
    } catch (error) {
      if (token) setText('realCasesStatus', error.message || 'Saved cases unavailable.');
    }
  }
  get('refreshRealCases').addEventListener('click', refreshCases);

  async function refresh() {
    if (!token || pending) return;
    pending = new AbortController();
    const controller = pending;
    const timeout = root.setTimeout(() => controller.abort(), 4000);
    try {
      const response = await root.fetch(ENDPOINT, {
        method: 'GET', headers: authHeaders(),
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal
      });
      if (!response.ok) {
        if ([401, 403, 422].includes(response.status)) {
          disconnect('Engineer access was rejected. Check the temporary session.');
          return;
        }
        throw new Error('Gateway unavailable');
      }
      render(await response.json());
    } catch {
      if (token) clearClaims('Unavailable', 'The read-only gateway is unreachable or returned an invalid response.');
    } finally {
      root.clearTimeout(timeout);
      if (pending === controller) pending = null;
    }
  }
  function disconnect(message = 'Disconnected. No live status is being checked.') {
    if (token === 'COOKIE') root.fetch('/api/v1/command-center/logout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      credentials: 'same-origin', cache: 'no-store', redirect: 'error'
    }).catch(() => {});
    token = '';
    if (timer) root.clearInterval(timer);
    timer = null;
    if (pending) pending.abort();
    pending = null;
    tokenInput.value = '';
    if (passwordInput) passwordInput.value = '';
    connectButton.disabled = false;
    disconnectButton.disabled = true;
    clearClaims('Disconnected', message);
    clearCases('Disconnected. Sign in as an engineer on Overview to read saved cases.');
    root.dispatchEvent?.(new Event('faultweave:disconnected'));
  }
  connectButton.addEventListener('click', async () => {
    const supplied = tokenInput.value.trim();
    if (supplied) {
      token = supplied;
      tokenInput.value = '';
      connected();
      return;
    }
    connectButton.disabled = true;
    const password = passwordInput?.value || '';
    if (passwordInput) passwordInput.value = '';
    if (!password) {
      connectButton.disabled = false;
      clearClaims('Access required', 'Enter the engineer password on Overview. No terminal token command is needed.');
      return;
    }
    try {
      const response = await root.fetch('/api/v1/command-center/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: usernameInput?.value.trim() || 'engineer', password }),
        credentials: 'same-origin', cache: 'no-store', redirect: 'error'
      });
      if (!response.ok) throw new Error('Engineer login was rejected. Check the local credentials.');
      token = 'COOKIE';
      connected();
    } catch (error) {
      connectButton.disabled = false;
      clearClaims('Access required', error.message || 'Engineer login unavailable.');
    }
  });
  disconnectButton.addEventListener('click', () => disconnect());
  root.addEventListener?.('faultweave:case-updated', () => {
    if (token) refreshCases();
  });
  root.addEventListener?.('faultweave:open-case', async event => {
    if (!token || !/^[0-9a-f-]{36}$/i.test(event.caseId || '')) return;
    await refreshCases();
    await openCase(event.caseId);
  });
  if (root.location?.hostname === '127.0.0.1') {
    root.fetch('/api/v1/command-center/session', {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error'
    }).then(response => { if (response.ok && !token) { token = 'COOKIE'; connected(); } }).catch(() => {});
  }
})(typeof window === 'undefined' ? globalThis : window);
