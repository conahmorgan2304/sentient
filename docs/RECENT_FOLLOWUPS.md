# Short follow-ups after a fresh wake word

Enable **Short follow-ups after a fresh wake word** in Voice settings, together with conversation sessions and contextual follow-ups. It is off by default.

After a completed command, a fresh wake-word request such as “actually, I meant the hallway”, “make it warmer” or “why?” can use the last completed subject from the same speaker and Home Assistant user. The existing session timeout applies (60 seconds by default). This does not identify a person by voice.

Only the subject, selected entities and previous action evidence are copied into a new session. Pending confirmations, undo permissions, listening state and provider conversation history are not copied. Device exposure and read permissions are checked before copying; normal guarded control and fresh confirmation rules still apply. A correction after a recorded action requires a fresh confirmation, and never silently reverses the earlier action. Current readings are refreshed by the existing follow-up path.

“The other one” asks which device or room you mean. A bare “yes” never restores an old task. An unrelated command starts normally. Active, waiting, running, expired or ended sessions are not restored. Explicit endings clear earlier same-owner context; Settings changes and forgetting use the existing session clearing path. Guest mode cannot restore this context.

Completed commands still close without another anything-else question. Information questions retain the existing discussion policy. This feature adds no classification AI call; the normal model answers a resolved follow-up. Recognition currently uses English phrase rules, not universal language recognition.

Test on a real speaker after installation: issue a light command, wake again and correct the room, then verify the confirmation before any new action. Also try “the other one”, a bare “yes”, and “no thank you” followed by a new wake word.
