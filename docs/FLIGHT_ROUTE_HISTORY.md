# Plane? flight-number route history gate

Plane? uses live deterministic CPA as the primary detector and a second route-history veto layer to reduce false approach alerts.

- History is keyed by the transmitted commercial flight callsign/number (for example `THY1017`), never by aircraft registration or ICAO24.
- Plane? stores bounded local path samples and evaluates up to the previous three UTC days for the same flight callsign.
- If those recent routes disagree materially, Plane? suppresses a new alert instead of guessing.
- If today's observed prefix diverges from a stable recent pattern, Plane? suppresses the alert.
- If matching recent routes consistently turn or stay outside the user's alert radius, Plane? vetoes a straight-line CPA false positive.
- The destination airport is resolved from the public ADSB.lol route endpoint. A plausible arrival that reaches its destination materially before the projected observer CPA is vetoed immediately while descending.
- Route history can only veto a live CPA candidate; it cannot create an approach alert by itself.
- Existing active alerts still require the normal multi-cycle cancellation confirmation, avoiding one-sample flapping.

The history collection is TTL-bounded and indexed by `(callsign, utc_date)`.
