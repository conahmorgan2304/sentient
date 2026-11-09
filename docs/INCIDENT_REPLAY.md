# Security incident replay

Open **Sentient → Security → Incident replay** to inspect the observations grouped into a retained incident. The card lists recent incidents, the selected floorplan, a play/pause button and a scrubber, and the event list. Select an incident, then press Play, drag the scrubber, or select an observation. A selected observation lights up cyan. Amber marks an unavailable or unknown camera report recorded at that step. Offsets such as `+12s` show actual elapsed time; playback compresses long gaps.

For example, a gate contact can open at `+0s`, Shed can report a person at `+12s`, and Garden can have a coverage gap at either observation. A dot represents one retained observation. It does not establish where a person travelled or link their identity across cameras. The selected step can show its exact retained Frigate snapshot and clip link. Availability of old media follows Frigate retention. A current camera status is shown separately in the pipeline section.

## Setup

1. Enable **Group security incidents** under **Settings → Safety & security**. Select your contact and person sensors under **Camera security**. Enable **Link security alerts to Frigate events** for exact event media, **Explain camera coverage gaps** for historical gaps, and **Security incident replay** for the replay card.
2. Replay starts with a schematic. To use a floorplan, put a PNG, JPEG or WebP in Home Assistant's `www` directory, then set **Floorplan image path**, for example `/local/sentient-floorplan.png`. Choose an image that covers the outside areas you want to see. **Floorplan marker positions** accepts an entity-to-coordinate mapping. Coordinates are percentages measured from the top left, horizontal first. Unmapped sources stay visible in the event list and under the map.

   ```yaml
   binary_sensor.gate: [18, 72]
   camera.gate: [23, 65]
   camera.garden: [51, 46]
   camera.shed: [78, 29]
   ```

3. The page can be opened directly on the tablet or from the Sentient panel's Ask box with “Show me what happened at the gate”. To open it remotely from Assist, select a **Tablet for voice requests** with the Fully Kiosk integration, use **Sentient with backup**, and make the request from an Assist client that passes an authenticated Home Assistant administrator user ID. The destination tablet must be signed in and allowed to read the evidence. An unidentified speaker can use the tablet controls separately.

Replay uses existing incident evidence. Disabling it hides the replay and stops remote replay requests. Existing evidence retains its usual retention and privacy controls.

## Check the detection pipeline

Enable **Show Frigate pipeline health** in the Camera security settings. The Security page shows broker-reported Frigate availability, per-camera processing rates, the latest event receipt in this Sentient session, and the Home Assistant camera report. If live stats stop arriving for three minutes, the view shows missing fresh stats. A quiet camera can have zero detections and remain healthy. The rates and receipts cannot verify recording. Telemetry lives in memory and is cleared when the feature is switched off or Sentient is restarted.

To test a live installation, make an ordinary visit through the gate and past the shed, then repeat through the garden. Repeat after dark and compare the contact time, Frigate event time, first Sentient alert, and selected clip. Record observations and missed detections against the current FPS before tuning it. If AI description is enabled, it arrives on the incident timeline after the first alert. It does not create a second initial alert.
