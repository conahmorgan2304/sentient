# Speaker delivery diagnostics

The Mind panel's Speaker deliveries history now shows the destination service, the stage reached and elapsed milliseconds for each speaker. Switch Speaker delivery history off to clear and stop retaining these records.

`routing` means Sentient was choosing or validating a service. `service_call` means the request was sent but did not finish successfully. `service_completed` means Home Assistant completed the service call. None of these independently proves that sound was audible. TTS services can combine audio generation and delivery, so Sentient does not invent separate measurements for those internal stages. Assist's final spoken agent reply is outside this announcement trace.

An optional Voice setting, **Notify about speaker delivery failures**, creates a Home Assistant notification for failed or uncertain deliveries. It is off by default, uses a single replacement notification to avoid piling up alerts, omits spoken content and is suppressed in guest mode. Switching it off stops new notifications; dismiss existing notifications normally. It never repeats speech. Interrupted/cancelled requests are recorded but do not create a notification during cancellation cleanup.

The existing live voice check separately asks whether you heard the reply. This is user confirmation, not automatic playback detection. A service success without sound should be investigated at the speaker/TTS integration, rather than reported as an AI outage.
