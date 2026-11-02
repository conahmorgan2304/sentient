# Asking Sentient through Alexa

Amazon only lets free speech reach Home Assistant through an Alexa **custom skill**. Sentient doesn't install one; it gives the skill a better agent to talk to.

## In Sentient
1. Go to **Settings → Ask through Alexa** and turn on **Answer questions asked through Alexa**.
2. Optional: **Say slow answers on that Echo** (on by default). When the AI takes longer than Alexa will wait, the Echo says "Give me a moment" and the answer is said on that same Echo when it's ready. This needs Alexa Media Player.
3. Make sure **Voice agent** (Settings → Voice backup) is your AI agent, not Sentient's own "with backup" agent.

## The skill (one-off, in Amazon's developer console)
The community [Home Assistant Assist](https://github.com/fabianosan/HomeAssistantAssist) skill works well. Follow its installation guide.

1. Create a free Amazon developer account (the same login as your Alexa app).
2. Create an **Alexa-hosted (Python)** skill and import the repository.
3. In the skill's `config.cfg`:
   - `home_assistant_url`: your external address, e.g. your Nabu Casa URL.
   - `home_assistant_token`: a long-lived access token. Make a separate Home Assistant user for it if you like.
   - `home_assistant_agent_id`: `conversation.<persona>_for_alexa`, Sentient's **For Alexa** agent (Settings → Voice assistants shows its id).
   - `home_assistant_language`: `en-GB`.
   - `home_assistant_room_recognition`: optional. Sentient doesn't need it, because it finds the Echo you spoke to through Alexa Media Player's "last called".
4. Choose the invocation name you want (for example your persona's name), then build and test.

Then: "Alexa, ask Sentinel how's the house?"

## How it behaves
- **The room:** the Echo you spoke to most recently (Alexa Media Player's `last_called`) is taken as the device asking, so "in here" means that room.
- **Short answers:** the voice is told it's talking through an Echo, so it gives one or two spoken sentences and shows nothing on screens.
- **Alexa waits about 8 seconds.** Sentient answers within **Longest wait** (6 s by default). A slower answer is said on the Echo afterwards, or, with that option off, you're asked to try again.
- **Late answers** count as replies to someone who asked, so they aren't held for quiet hours or a hush. Echos set to "alarms and reminders only" still say them.
- **Off:** the agent replies that asking through Alexa is switched off. Nothing else changes.
