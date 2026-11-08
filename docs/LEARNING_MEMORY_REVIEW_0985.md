# Learning and memory review — 0.98.5

This release improves the existing memory, overnight reflection, curiosity and weekly tidy features. Reflection and curiosity retain their current Settings controls; these reliability fixes introduce no new autonomous action policy, external API or background reasoning loop.

## Preserve what was actually learned

Automatic memory deduplication previously used a high word-overlap score. Similar sentences could overwrite a different temperature, device, negation or relationship. It now reuses only wording-equivalent memories within the same category, normalising Unicode, case, punctuation and English articles while retaining word order and meaningful words/numbers. Distinct or conflicting claims stay separate for explicit editing or the existing cautious memory tidy; this is not semantic contradiction resolution.

Equivalent repeated inferences no longer rewrite a pinned/explicit fact or refresh its original age. An explicit source can upgrade the provenance of an equivalent inferred fact. Facts and corrections remain separate, so a lesson cannot silently turn into an ordinary fact. Unicode word recall now covers accented, Cyrillic, Arabic and other word scripts; this is keyword recall, not universal semantic search or language-specific segmentation.

A full store evicts an unpinned item without sorting the retained list out of order. If every slot is pinned it refuses a new item rather than discarding a pinned memory. Empty, punctuation-only and English-stopword-only forget queries cannot fall back to deleting an unrelated recent memory.

## Learn from the latest conversation

Reflection reads the latest eight exchanges from each retained conversation, including late corrections, rather than its first eight. Existing memories returned again by the model are not counted as new learning. Non-text fields/items are discarded rather than converted into bogus facts; the existing English privacy heuristic also covers lessons. This heuristic and the model's extraction instructions are not a guarantee of semantic correctness or complete multilingual private-data detection.

Only one reflection runs at a time. Repeating a manual reflection on unchanged material and important settings on the same local day reuses its result, while the scheduled pass avoids a second run that day. A changed input/provider/persona/rule can be reflected on by another explicit check. Failed or quota-blocked work does not poison this cache. A digest of the input is stored, not another raw copy of the conversation.

Disabled reflection, guest mode and unload stop optional work. Results returning after a settings, forgetting, guest-mode generation or lifecycle change cannot add memories, lessons, queued thoughts, curiosity questions or timeline notes. Already-submitted AI work cannot be recalled.

## Tidy only the current memory snapshot

Weekly/manual tidy runs once at a time and rechecks its input after the AI responds. If a memory was edited, pinned, added or removed, or the privacy/settings/lifecycle changed, the old proposal is discarded. Routine recall-hit changes do not invalidate it.

Pinned memory text cannot be rewritten by a merge. Repeated IDs cannot merge a memory with itself; correction categories cannot be merged into general facts; and a combined fact is not deleted by a remove instruction in the same reply. The existing removal allowance remains in force. Memory listings are ordered by creation date for the existing newer-fact reasoning; the AI still needs to judge the content conservatively.

## Forget derived content too

“Forget recent” also clears reflection results, queued curiosity questions, asked-question records and queued follow-you messages in the selected window. Older records and pinned memories retain their existing treatment. Curiosity serialises its questions and rechecks the exact queued item before recording a successful delivery, preventing a late reply from restoring a dismissed/forgotten question or popping a different one. Its automatic daily allowance is enforced for direct background calls too; the existing explicit test can bypass that allowance.

Live testing should include a final spoken correction followed by reflection, editing/pinning a memory while tidy is delayed, and forgetting while reflection or a curiosity question is in flight. These changes improve dependable reflective learning; they do not provide consciousness, unrestricted capabilities or permission for new device actions.

## Validation

All **1,176 Python tests** pass, including **58 new regression cases** in `test_learning_memory_reliability.py`. All **6 JavaScript tests** pass. Python compilation, frontend syntax, patch whitespace and English translation parity were checked. Live Home Assistant hardware and real provider delays/outages remain to be tested after installation.
