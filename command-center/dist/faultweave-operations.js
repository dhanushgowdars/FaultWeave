/* Real backend workflow reader and explicit engineer actions. */
(function (root) {
  'use strict';
  if (!root.document) return;
  const doc = root.document;
  const byId = id => doc.getElementById(id);
  if (!byId('realDemoStatus')) return;
  const endpoint = '/api/v1/demo/sessions';
  const actions = ['realPrepare', 'realArm', 'realExecute', 'realBlindRun', 'realAnalyze', 'realReveal', 'realApprove', 'realRecover'];
  let current = null;
  let busy = false;
  let connected = false;

  function element(tag, value, className) {
    const node = doc.createElement(tag);
    if (value !== undefined) node.textContent = String(value);
    if (className) node.className = className;
    return node;
  }
  function status(value) { byId('realDemoStatus').textContent = value; }
  function controls() {
    const state = current?.state;
    const blind = current?.mode === 'BLIND_RANDOM';
    const ready = current?.analysis_status === 'READY';
    const allowed = {
      realPrepare: connected,
      realArm: connected && !busy && !blind && state === 'PREPARED' && !!current?.scenario?.fault_id,
      realExecute: connected && !busy && !blind && (state === 'FAULT_ARMED' || state === 'PREPARED' && !current?.scenario?.fault_id),
      realBlindRun: connected && !busy && blind && state === 'PREPARED',
      realAnalyze: connected && !busy && ['AWAITING_REVIEW', 'APPROVED', 'RECOVERED', 'COMPLETED'].includes(state),
      realReveal: connected && !busy && blind && ready && !current?.challenge?.revealed_at,
      realApprove: connected && !busy && state === 'AWAITING_REVIEW' && ready,
      realRecover: connected && !busy && state === 'APPROVED'
    };
    for (const id of actions) byId(id).disabled = busy || !allowed[id];
    byId('realOpenCase').disabled = !connected || !current?.transfer_id;
  }
  async function api(path, payload) {
    const response = await root.fetch(path, {
      method: payload === undefined ? 'GET' : 'POST',
      headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      credentials: 'same-origin', cache: 'no-store', redirect: 'error'
    });
    let result;
    try { result = await response.json(); } catch { throw new Error('The gateway returned an invalid response.'); }
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        connected = false; controls();
        throw new Error('Engineer session expired. Sign in again on Overview.');
      }
      throw new Error(typeof result.detail === 'string' ? result.detail : `Backend rejected the action (HTTP ${response.status}).`);
    }
    return result;
  }
  function render(session) {
    if (!session || !/^demo-[a-zA-Z0-9]+$/.test(session.session_id || '')) throw new Error('Invalid saved session identity.');
    current = session;
    byId('realSessionSelect').value = session.session_id;
    controls();
    const evidence = session.snapshots?.evidence_after_recovery || session.snapshots?.latest_evidence || session.snapshots?.evidence_after_execution;
    const facts = byId('realDemoOutcome');
    facts.replaceChildren();
    for (const [label, value] of [
      ['Session', session.session_id], ['Workflow', session.state || 'Unverified'],
      ['Transfer', session.transfer_id || 'Not executed'],
      ['Funds', evidence?.funds_status || 'No saved financial result'],
      ['Earlier model analysis', session.analysis_status || 'Not requested'],
      ['Recovery', session.verification?.status || 'Not verified']
    ]) {
      const box = element('div'); box.append(element('span', label), element('strong', value)); facts.append(box);
    }
    const events = byId('realDemoEvents'); events.replaceChildren();
    const rows = Array.isArray(session.events) ? session.events : [];
    events.append(element('h4', `Saved workflow history · ${rows.length} event(s)`));
    for (const row of rows.slice(-30)) {
      events.append(element('p', `${row.timestamp || row.at || 'Time unavailable'} · ${row.action || 'Action'} · ${row.status || 'Status unverified'} · ${row.detail || ''}`, 'real-case-muted'));
    }
    const explanation = byId('realDemoExplanation'); explanation.replaceChildren();
    const assistant = session.snapshots?.assistant;
    if (assistant?.analysis) {
      const analysis = assistant.analysis;
      explanation.append(element('h4', 'Saved grounded explanation · earlier model'),
        element('p', session.state === 'RECOVERED'
          ? 'Historical analysis from before verified recovery. Current workflow and funds are shown above.'
          : 'Saved analysis of this transfer. Use its cited events and current workflow state before acting.', 'real-case-note'),
        element('p', analysis.headline || 'No headline'), element('p', analysis.executive_summary || 'No saved summary'));
      for (const key of ['first_failure', 'funds_state', 'model_assessment', 'probable_cause']) {
        const statement = analysis[key];
        if (statement) explanation.append(element('p', `${key.replaceAll('_', ' ')} (${statement.kind || 'unverified'}): ${statement.text || ''} · cites ${(statement.evidence_event_ids || []).join(', ')}`, 'real-case-muted'));
      }
      if (analysis.limitations?.length) explanation.append(element('p', `Limitations: ${analysis.limitations.join(' · ')}`, 'real-case-muted'));
    }
    status(`Saved session ${session.session_id} · ${session.state || 'state unavailable'}. ${session.transfer_id ? 'Open Case evidence for the persisted service timeline.' : 'No transfer has executed.'}`);
  }
  async function listSessions(selected) {
    const data = await api(endpoint + '?limit=25');
    if (!Array.isArray(data.items)) throw new Error('Saved session list has an unexpected format.');
    const select = byId('realSessionSelect'); select.replaceChildren(element('option', 'Choose or prepare a session'));
    select.firstElementChild.value = '';
    for (const item of data.items) {
      if (!/^demo-[a-zA-Z0-9]+$/.test(item.session_id || '')) continue;
      const option = element('option', `${item.session_id} · ${item.state || 'state unavailable'} · ${item.scenario?.title || 'scenario sealed'}`);
      option.value = item.session_id; select.append(option);
    }
    if (selected) select.value = selected;
  }
  async function loadSession(id) {
    if (!/^demo-[a-zA-Z0-9]+$/.test(id)) throw new Error('Invalid session ID.');
    render(await api(`${endpoint}/${id}`));
  }
  async function initialize() {
    try {
      const state = await api('/api/v1/command-center/session');
      if (!state.connected) return;
      connected = true; controls();
      const data = await api('/api/v1/demo/scenarios');
      if (!Array.isArray(data.items)) throw new Error('Scenario contract unavailable.');
      const choices = byId('realScenarioSelect'); choices.replaceChildren();
      for (const scenario of data.items) {
        const option = element('option', scenario.title || scenario.scenario_id);
        option.value = scenario.scenario_id; choices.append(option);
      }
      const blind = element('option', 'Sealed random challenge (long research workload)');
      blind.value = 'BLIND_RANDOM'; choices.append(blind);
      await listSessions(current?.session_id);
      if (current?.session_id) await loadSession(current.session_id);
      else status('Engineer session connected. Choose a saved session or prepare one. No fault or transaction has run.');
    } catch (error) {
      if (error.message?.includes('session expired')) status('Sign in on Overview to load real saved sessions.');
      else status(error.message || 'Backend workflow controls unavailable.');
    }
  }
  function amountMinor() {
    const value = byId('realAmount').value.trim();
    if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error('Enter an INR amount with at most two decimal places.');
    const [whole, fraction = ''] = value.split('.');
    const minor = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
    if (!Number.isSafeInteger(minor) || minor <= 0 || minor > 100_000_000) throw new Error('Amount must be between ₹0.01 and ₹10,00,000.');
    return minor;
  }
  async function action(name, path, payload, confirmation) {
    if (busy || !connected) return;
    if (confirmation && !root.confirm(confirmation)) return;
    busy = true; controls(); status(`${name} requested; waiting for the original gateway. Do not click again.`);
    try {
      const result = await api(path, payload);
      const session = result.session || result;
      if (!session?.session_id) throw new Error('Action returned no saved demo session. Check history before retrying.');
      await listSessions(session.session_id);
      await loadSession(session.session_id);
      if (session.transfer_id) root.dispatchEvent?.(new Event('faultweave:case-updated'));
    } catch (error) {
      if (current?.session_id) await loadSession(current.session_id).catch(() => {});
      status(`${name}: ${error.message || 'Unknown error'}. Check saved session state before retrying.`);
    } finally { busy = false; controls(); }
  }
  byId('realSessionSelect').addEventListener('change', async event => {
    if (event.target.value) await loadSession(event.target.value).catch(error => status(error.message));
    else { current = null; controls(); }
  });
  byId('realOpenCase').addEventListener('click', () => {
    if (!connected || !current?.transfer_id) return;
    const event = new Event('faultweave:open-case');
    event.caseId = current.transfer_id;
    root.dispatchEvent?.(event);
    doc.querySelector('.nav-btn[data-page="investigation"]')?.click();
  });
  byId('realPrepare').addEventListener('click', () => {
    try {
      const scenario = byId('realScenarioSelect').value;
      if (!scenario) throw new Error('Select a real backend scenario.');
      const destination = byId('realRecipientAccount').value.trim();
      const body = {
        mode: scenario === 'BLIND_RANDOM' ? 'BLIND_RANDOM' : 'CONTROLLED',
        scenario_id: scenario === 'BLIND_RANDOM' ? null : scenario,
        source_account_number: byId('realSenderAccount').value.trim(),
        amount_minor: amountMinor(), currency: 'INR',
        sender_name: byId('realSenderName').value.trim() || 'Command Center sender',
        recipient_name: byId('realRecipientName').value.trim() || 'Command Center recipient',
        payment_channel: 'UPI', purpose: 'FaultWeave local engineer demonstration'
      };
      if (destination) body.destination_account_number = destination;
      action('Prepare session', endpoint, body);
    } catch (error) { status(error.message); }
  });
  const act = (id, label, command, body = {}) => byId(id).addEventListener('click', () => {
    if (!current) return;
    const confirmations = {
      arm: 'Arm the selected controlled fault for this session? No transfer runs until Execute is clicked.',
      execute: 'Execute this backend transfer once? This writes account and transfer records for the controlled session.',
      run: 'Run the sealed research challenge? This generates a sustained workload and a controlled saved case.',
      recover: 'Resume the same reviewed transfer and verify balances and journal records?'
    };
    action(label, `${endpoint}/${current.session_id}/${command}`, body, confirmations[command]);
  });
  act('realArm', 'Arm fault', 'arm');
  act('realExecute', 'Execute transfer', 'execute');
  act('realBlindRun', 'Run sealed challenge', 'run');
  act('realAnalyze', 'Analyze saved evidence', 'analyze');
  act('realReveal', 'Reveal sealed scenario', 'reveal');
  byId('realApprove').addEventListener('click', () => {
    if (!current) return;
    const note = byId('realReviewNote').value.trim();
    if (note.length < 10) { status('Write a review note of at least 10 characters before approval.'); return; }
    action('Approve recovery', `${endpoint}/${current.session_id}/approve`, { note },
      'Approve idempotent recovery of this reviewed transfer? This approval alone does not resume it.');
  });
  act('realRecover', 'Recover and verify', 'recover');
  root.addEventListener('faultweave:connected', initialize);
  root.addEventListener('faultweave:disconnected', () => {
    connected = false; current = null; controls();
    status('Engineer session disconnected. Sign in on Overview.');
    byId('realDemoOutcome').replaceChildren(); byId('realDemoEvents').replaceChildren(); byId('realDemoExplanation').replaceChildren();
  });
  controls();
  if (root.location?.hostname === '127.0.0.1') initialize();
})(typeof window === 'undefined' ? globalThis : window);
