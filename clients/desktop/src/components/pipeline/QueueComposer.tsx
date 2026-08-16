import { FilePlus2 } from "lucide-react";
import { useState } from "react";

import { parseIssueRef } from "../../lib/links";
import type { AssignmentTargetAgent } from "../../types";
import type { QueueActionHandler } from "./types";

// The compact assignment row above the board. It parses an owner/repo#123 ref
// or a GitHub issue URL and routes it to the selected agent.
export function QueueComposer({
  onQueueAction,
  busy,
}: {
  onQueueAction: QueueActionHandler;
  busy: boolean;
}) {
  const [value, setValue] = useState("");
  const [targetAgent, setTargetAgent] = useState<AssignmentTargetAgent>("auto");
  const parsed = parseIssueRef(value);
  const invalid = Boolean(value.trim()) && !parsed;

  return (
    <form
      className="alfred-pipeline__assign"
      aria-label="Assign existing GitHub issue"
      onSubmit={async (event) => {
        event.preventDefault();
        if (!parsed || busy) return;
        const ok = await onQueueAction(
          parsed.repo,
          parsed.number,
          "assign",
          targetAgent,
        );
        if (ok !== false) setValue("");
      }}
    >
      <div className="alfred-pipeline__assign-title">
        <FilePlus2 size={16} aria-hidden="true" />
        <span>Assign work</span>
      </div>
      <label
        className="alfred-pipeline__assign-field"
        htmlFor="pipeline-assign-issue"
      >
        <span>GitHub issue</span>
        <input
          id="pipeline-assign-issue"
          value={value}
          onChange={(event) => setValue(event.currentTarget.value)}
          placeholder="owner/repo#123"
          spellCheck={false}
          aria-invalid={invalid}
          aria-describedby={invalid ? "pipeline-assign-error" : undefined}
        />
      </label>
      <label className="alfred-pipeline__assign-field">
        <span>Agent</span>
        <select
          value={targetAgent}
          onChange={(event) =>
            setTargetAgent(event.currentTarget.value as AssignmentTargetAgent)
          }
          aria-label="Assignment target"
        >
          <option value="auto">Auto</option>
          <option value="architect">Architect</option>
          <option value="senior-dev">Senior developer</option>
        </select>
      </label>
      <button
        className="secondary-button"
        type="submit"
        disabled={!parsed || busy}
      >
        <FilePlus2 size={16} aria-hidden="true" />
        <span>{busy ? "Queuing" : "Queue"}</span>
      </button>
      {invalid ? (
        <p id="pipeline-assign-error" className="alfred-pipeline__assign-error">
          Use owner/repo#123 or a GitHub issue URL.
        </p>
      ) : null}
    </form>
  );
}
