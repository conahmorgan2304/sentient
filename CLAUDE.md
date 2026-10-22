# Sentient — working notes

Home Assistant custom integration (domain `sentient`). An evolving AI "brain" shared by every voice assistant and automation in a house.

## Principles
- **Generic engine, house-specific settings.** Never hard-code entity ids, names, rooms or times. Everything about a particular house belongs in the config/options flow, with sensible defaults.
- **Use the AI the user already has.** Talk to models only through Home Assistant: the LLM API for conversation agents, `ai_task` for background jobs. No bundled API keys or SDKs.
- **Cheap by design.** Plain rules run before any model call. Use the smallest capable model and never poll an LLM on a timer when an event will do.
- **One shared brain.** Memory, watches and speech rules live here once and are exposed to every agent through the `sentient` LLM API.
- **Never name the product after the original inspiration.** Persona and name come from the user's setup.

## Layout
- `custom_components/sentient/` — `memory.py` (store), `speech.py` (router and rules), `llm_api.py` (tools and prompt), `config_flow.py`, `sensor.py`, `__init__.py` (runtime and services)
- `tests/` — pytest with `pytest-homeassistant-custom-component`; run `pytest -q`
- `docs/ROADMAP.md` — stages; tick items off as they land

## Conventions
- British English in user-facing strings.
- Keep `strings.json` and `translations/en.json` identical.
- Bump `manifest.json` version for every release; HACS uses GitHub releases/tags.
- Releases: bumping `version` in manifest.json and pushing to main is enough — `.github/workflows/release.yml` creates the `v<version>` tag and GitHub release that HACS reads.
