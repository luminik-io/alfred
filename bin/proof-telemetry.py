#!/usr/bin/env python3
"""No-op stub left so existing ``alfred.proof-telemetry`` scheduler rows exit cleanly."""

MESSAGE = (
    "[PROOF-TELEMETRY-DISABLED] proof telemetry was removed; delete the "
    "alfred.proof-telemetry row from agents.conf and redeploy."
)

if __name__ == "__main__":
    print(MESSAGE)
