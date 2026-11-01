# Voice-request delivery timeouts — 0.66.4

A Sentient announcement tool previously awaited the entire speaker delivery while the primary conversation had a 45-second deadline. The speaker service alone could wait 60 seconds, with additional idle, volume and playback waits. A slow Echo could therefore cancel the primary conversation and trigger backup. This is a reproducible code path, not confirmation of the cause of a particular hardware incident.

The announcement tool now has a 12-second deadline plus bounded volume cleanup. A timeout returns an unknown playback outcome; a Home Assistant delivery error returns a structured speaker failure to the model. Neither is presented as confirmed audible playback. If the primary agent subsequently fails after starting an announcement, the same request is not replayed through backup. Ordinary backup requests are capped at 15 seconds.

Volume preparation and restoration each have a three-second bound. Restoration also runs when cancellation occurs during preparation. Accepted speaker targets are remembered immediately, and interrupted deliveries are recorded with unknown playback outcomes, so a later retry skips already accepted destinations within the duplicate window. Delivery history still obeys its Settings switch.

These are reliability fixes, with no new optional feature or setting. The Alexa TTS-only option remains available under Settings → Voice. They cover the Sentient announcement tool, not arbitrary tools supplied by other integrations. Physical Echo/Alexa playback still requires testing after updating.

To investigate an installed failure, use the timestamped Home Assistant log and Sentient's delivery details to distinguish an unavailable notify service, a disconnected Echo, delivery timeout and model failure. Never infer that the AI process crashed solely from the backup response.
