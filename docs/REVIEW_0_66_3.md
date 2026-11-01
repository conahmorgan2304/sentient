# Resolution of contributor PR #14

The contributor's head at `0c6ad1333948ee4ba86d08a569fbfc7801d3463d` conflicts with main in README, manifest, speech routing and French translations. Maintainer edits are disabled on the source fork. This replacement branch records both histories while resolving against the existing 0.66.2 implementation.

Accepted changes:
- Single-Echo Alexa Media Player calls use an entity ID string, preserving independent delivery results and retry handling.
- Settings → Voice → Use TTS only on Alexa optionally bypasses unreliable announcement mode. It defaults off so existing announcement/chime behaviour remains available.
- French introductory documentation is preserved in README.fr.md and linked from the English README. The image uses an absolute URL pointing at this repository.

Retained from main:
- All live voice checks, confirmation feedback, settings toggles and reliability fixes.
- Existing English defaults and Home Assistant French translations, including the latest settings labels.
- The real browser `Escape` key identifier. The contributor's literal `Échap` replacement would prevent keyboard cancellation.

The contributor's hard-coded French substitutions across backend messages and the panel are intentionally not imported: they do not select language and would force mixed French/English on every installation. Tests are not changed to accept that regression. A complete panel localisation can be a separate change using the user's selected language.

Version is advanced to 0.66.3, rather than downgraded to the contributor's 0.65.25. The original PR remains unchanged; review this replacement instead.
