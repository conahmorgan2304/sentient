# Explaining Sentient's behaviour

Enable **Explain listening and action decisions** under Voice, together with conversation sessions. It is off by default. Exact short English questions such as “why are you still listening?”, “why did you stop listening?” and “did you actually do it?” use existing session evidence directly, without a model call. Speech translation, when enabled separately, may still use its existing AI gateway.

The explanation uses the most recent non-expired session for the same speaker and Home Assistant user. Guest mode does not reveal recorded details. Device results are filtered by current exposure and read access. No extra history is stored; Settings changes and forgetting use existing session clearing. The setting does not enable action recovery or send device commands.

Listening explanations describe the agent's recorded decision, not whether hardware is physically listening. Action explanations describe the last recorded guarded action in that session, not a fresh device check or proof of physical success. Other integrations' actions may be absent. Missing reasons or records are reported as unknown. An explanation does not consume a pending confirmation or clarification; its question is repeated so the user can answer it. It does not start a new general discussion.

The existing `sentient_why` tool handles questions about announcements and things withheld. Its occurrence matching now separates spoken events from suppressed events and includes the original timestamp and event kind. Nearby events still do not prove causation. Specific questions naming the message and time are preferable when several similar events exist. These broader questions continue through the existing model/tool route.

After installation, test a command followed by “did you actually do it?”, and ask “why are you still listening?” while a confirmation is pending. Check that explaining never triggers the action.

In 0.97.1, direct explanation questions preserve existing action-recovery evidence. Repeating a pending question rechecks expiry, guest mode and current device visibility/read access; an invalid question is discarded rather than repeated. Confirmation expiry is rechecked after queuing, permission checks and immediately before each device dispatch.
