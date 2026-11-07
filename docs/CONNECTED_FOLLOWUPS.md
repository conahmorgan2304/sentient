# Connected follow-ups and corrections (0.89.0)

Enable **Settings → Voice → Connected follow-ups and corrections** alongside **Conversation sessions**, using the **Sentient with backup** conversation agent. The new option is off by default. It uses your existing conversation provider; no realtime API or extra background model calls are introduced.

Within the same active session:

- “How is the heating?” followed by “Why?” retains the heating subject rather than replacing it with “Why?”.
- “What about the kitchen?” retains the subject but clears the old selected target, so the model must resolve the new room.
- “Make that 20 degrees” refreshes the selected devices' reported readings. With zero or multiple permitted candidates, Sentient asks “Which device should I change?” without calling the model or executing anything. The original request accompanies the next clarification answer.
- “No, I meant the kitchen” retains the previous action's outcome for the existing correction/undo handling. The old target is not silently selected for the correction.

Context is bounded by the existing session timeout and conversation/device/user identity. Device readings are refreshed from Home Assistant and filtered by exposure and user read permissions. They are reported states, not independent physical verification. A context-free “Why?” asks for the subject. A new explicit topic clears prior selected devices.

Listening rules and action confirmations are unchanged. The context does not authorise an action; guarded control remains the option for server-enforced device action validation. Topic interpretation and tool choice still depend on the configured model. Supported short-follow-up patterns are English; other requests continue through the existing model path.

Ending the session, forgetting recent data, or changing Settings clears the temporary follow-up context. Turning the new option off removes its context from session diagnostics and disables its enrichment and deterministic clarifications. It does not erase older conversation history by itself; use the existing privacy operation for that.

Tests replay provider responses and inspect requests, permissions, state refresh, clarification carry-over, cancellation and Settings enforcement. They do not establish microphone quality or guarantee a particular provider's wording. Suggested live checks: heating → why → kitchen; an ambiguous temperature adjustment → kitchen; an action → corrected room; and “no thankyou”.
