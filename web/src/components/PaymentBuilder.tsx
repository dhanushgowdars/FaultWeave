import { useMemo, useState } from "react";
import { CreditCard, Landmark, RefreshCw, Send, Smartphone } from "lucide-react";

import type { DemoScenario, PaymentChannel, PaymentFormValue } from "../api/types";
import { formatMoney } from "../lib/presentation";

const samples = [
  ["Dhanush R S", "Ananya Rao", "FW-DEMO-ANANYA", 125000, "UPI", "Project demonstration"],
  ["Rahul Kumar", "Meera Nair", "FW-DEMO-MEERA", 250000, "DEBIT_CARD", "Course payment"],
  ["Priya Sharma", "Arjun Patel", "FW-DEMO-ARJUN", 87500, "NET_BANKING", "Shared expenses"],
] as const;

export function PaymentBuilder({
  scenarios,
  disabled,
  onSubmit,
}: {
  scenarios: DemoScenario[];
  disabled: boolean;
  onSubmit: (value: PaymentFormValue) => void;
}) {
  const [scenarioId, setScenarioId] = useState("ledger-service-unavailable");
  const [senderName, setSenderName] = useState("Dhanush R S");
  const [sourceAccount, setSourceAccount] = useState("FW-DEMO-001");
  const [recipientName, setRecipientName] = useState("Ananya Rao");
  const [destinationAccount, setDestinationAccount] = useState("FW-DEMO-ANANYA");
  const [amountRupees, setAmountRupees] = useState(1000);
  const [channel, setChannel] = useState<PaymentChannel>("UPI");
  const [purpose, setPurpose] = useState("Project demonstration");

  const selectedScenario = useMemo(
    () => scenarios.find((item) => item.scenario_id === scenarioId),
    [scenarioId, scenarios],
  );

  function randomize() {
    const sample = samples[Math.floor(Math.random() * samples.length)];
    setSenderName(sample[0]);
    setRecipientName(sample[1]);
    setDestinationAccount(sample[2]);
    setAmountRupees(sample[3] / 100);
    setChannel(sample[4]);
    setPurpose(sample[5]);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    onSubmit({
      scenario_id: scenarioId,
      source_account_number: sourceAccount.trim(),
      destination_account_number: destinationAccount.trim(),
      amount_minor: Math.round(amountRupees * 100),
      currency: "INR",
      sender_name: senderName.trim(),
      recipient_name: recipientName.trim(),
      payment_channel: channel,
      purpose: purpose.trim(),
    });
  }

  return (
    <form className="payment-builder card" onSubmit={submit}>
      <div className="card-header">
        <div><span className="eyebrow">Demo payment</span><h2>Create one real transfer</h2></div>
        <button type="button" className="button ghost compact" onClick={randomize} disabled={disabled}>
          <RefreshCw size={15} /> Random autofill
        </button>
      </div>
      <div className="form-grid">
        <label>Sender name<input value={senderName} onChange={(event) => setSenderName(event.target.value)} required /></label>
        <label>Sender account<input value={sourceAccount} onChange={(event) => setSourceAccount(event.target.value)} required pattern="FW-[A-Z0-9-]+" /></label>
        <label>Recipient name<input value={recipientName} onChange={(event) => setRecipientName(event.target.value)} required /></label>
        <label>Recipient account<input value={destinationAccount} onChange={(event) => setDestinationAccount(event.target.value.toUpperCase())} required pattern="FW-[A-Z0-9-]+" /></label>
        <label>Amount (INR)<div className="money-input"><span>₹</span><input type="number" min="1" max="1000000" step="0.01" value={amountRupees} onChange={(event) => setAmountRupees(Number(event.target.value))} required /></div></label>
        <label>Payment purpose<input value={purpose} onChange={(event) => setPurpose(event.target.value)} required /></label>
      </div>
      <fieldset>
        <legend>Payment channel</legend>
        <div className="channel-grid">
          {([
            ["UPI", Smartphone, "Simulated UPI metadata"],
            ["DEBIT_CARD", CreditCard, "Simulated card metadata"],
            ["NET_BANKING", Landmark, "Simulated bank metadata"],
          ] as const).map(([value, Icon, copy]) => (
            <button type="button" key={value} className={channel === value ? "channel active" : "channel"} onClick={() => setChannel(value)}>
              <Icon size={20} /><span><strong>{value === "DEBIT_CARD" ? "Debit card" : value === "NET_BANKING" ? "Net banking" : "UPI"}</strong><small>{copy}</small></span>
            </button>
          ))}
        </div>
        <p className="field-help">The channel is stored only as demo metadata. FaultWeave does not claim a real UPI, card or bank-network connection.</p>
      </fieldset>
      <label>Scenario<select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>{scenarios.map((scenario) => <option value={scenario.scenario_id} key={scenario.scenario_id}>{scenario.title}</option>)}</select></label>
      {selectedScenario && <div className="scenario-note"><strong>{selectedScenario.recommended ? "Recommended demonstration" : "Secondary demonstration"}</strong><p>{selectedScenario.summary}</p><span>Expected money evidence: {selectedScenario.expected_funds_status}</span></div>}
      <div className="payment-total"><div><span>Payment amount</span><strong>{formatMoney(Math.round(amountRupees * 100))}</strong></div><button className="button primary" disabled={disabled || scenarios.length === 0}><Send size={17} />{disabled ? "Workflow running…" : "Start real workflow"}</button></div>
    </form>
  );
}
