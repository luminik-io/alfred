import { FilePlus2 } from "lucide-react";
import { useState } from "react";

import { parseIssueRef } from "../../lib/links";
import type { AssignmentTargetAgent } from "../../types";
import type { QueueActionHandler } from "./types";

// The "Assign existing GitHub issue" composer above the board. Parses an
// owner/repo#123 ref or a GitHub issue URL and routes it into the queue with the
// chosen target agent. Only rendered when queue mutations are available.
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
        const ok = await onQueueAction(parsed.repo, parsed.number, "assign", targetAgent);
        if (ok !== false) setValue("");
      }}
    >
      <div className="alfred-pipeline__assign-label">
        <FilePlus2 size={16} aria-hidden="true" />
        <span>Assign existing issue</span>
        <small>Paste owner/repo#123 or a GitHub issue URL.</small>
      </div>
      <input
        id="pipeline-assign-issue"
        value={value}
        onChange={(event) => setValue(event.currentTarget.value)}
        placeholder="owner/repo#123"
        spellCheck={false}
        aria-invalid={invalid}
        aria-describedby={invalid ? "pipeline-assign-error" : undefined}
      />
      <select
        value={targetAgent}
        onChange={(event) => setTargetAgent(event.currentTarget.value as AssignmentTargetAgent)}
        aria-label="Assignment target"
      >
        <option value="auto">Smart route</option>
        <option value="architect">Architect</option>
        <option value="senior-dev">Senior-dev</option>
      </select>
      <button className="secondary-button" type="submit" disabled={!parsed || busy}>
        <FilePlus2 size={16} aria-hidden="true" />
        <span>{busy ? "Routing" : "Route"}</span>
      </button>
      {invalid ? (
        <p id="pipeline-assign-error" className="alfred-pipeline__assign-error">
          Use owner/repo#123 or a GitHub issue URL.
        </p>
      ) : null}
    </form>
  );
}
