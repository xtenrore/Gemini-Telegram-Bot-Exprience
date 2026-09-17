# Aircraft alert reliability guards

The production worker installs `app.worker.reliability` before the monitor binds its provider, trajectory, and route-history functions.

The guard layer is intentionally bounded and deterministic:

- a fully blank primary ADS-B cycle may hedge to OpenSky when credentials are available;
- aircraft from a recent successful cycle can bridge a feed gap for at most 28 seconds, after which the existing stale-position rules take over;
- a fresh aircraft physically observed inside the configured alert radius is treated as an alert-worthy nearby pass even when the first usable point arrives just after geometric CPA;
- remote flight-route enrichment refreshes in the background so network timeouts cannot block the five-second monitor loop;
- locally stored recent flight-number history remains available immediately as a false-alert veto before the aircraft reaches the radius;
- a fresh direct observation inside the user's radius cannot be vetoed by historical routing.

AI is not part of these decisions. The deterministic CPA/radius engine remains authoritative.
