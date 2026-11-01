# Sentient update checker

Version 0.67.0 adds an optional checker under **Sentient → Settings → Updates → Check for Sentient updates**. It is off by default. The same switch is available in the integration's native options form.

When enabled, Sentient checks its public GitHub repository's latest stable release immediately, on startup and every six hours. This is a small HTTP request with a ten-second limit and no AI calls. Disabling the switch cancels pending work, removes the timer and clears the check results. It never downloads or installs an integration, changes HACS settings, or restarts Home Assistant.

In the sidebar's Mind section, administrators see the version loaded by Home Assistant, the latest stable release, check time and any error. The loaded version can differ from files just downloaded by HACS until Home Assistant has restarted. Prereleases are not offered. A newer local/development version is not presented as needing a downgrade.

- **Check now** requests a check; checks within one minute share cached results. GitHub rate limits cause a one-hour backoff. An unavailable connection reports a failed check, not “up to date”.
- **Release notes** opens the repository's release page.
- **Refresh panel** reloads the current browser page. Use it after installing an update and completing any required Home Assistant restart. Save unfinished form edits first. It does not refresh the integration's Python code or install anything.

Install available versions through HACS as usual. No automatic browser reload interrupts a conversation, form or kiosk. Normal live sensor updates continue independently of this optional checker.

Validation includes disabled/no-network operation, numeric version comparison, invalid and prerelease responses, manual caching, rate-limit backoff, cancellation on disable, Settings/API round trips, and frontend pending/error behaviour. These tests use mocked release responses; the checker has not been exercised on the user's installed Home Assistant.
