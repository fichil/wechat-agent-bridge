# Attribution

The account configuration layout, WCDB Config.Cipher object layout/XOR mask,
and SQLCipher page AES/HMAC algorithms in `wechat_account_probe/memory.py`
and `wechat_account_probe/sqlcipher.py` are adapted from:

- Project: wechatauto-replica, copyright its contributors
- Source: https://github.com/fanyuantaier/wechatauto-replica
- Revision: 6e603974bbac3bda93d88bbd6995510ae2547f5c
- File: wechatauto/db.py
- License: Apache License 2.0, reproduced in LICENSE

Local changes: only self-account/self-avatar rows are queried; no original package imports,
master-key reading, account-directory heuristic, plaintext/key cache, message
features or UI activation. Process binding, sequential bounded workers, memory-only
snapshots and strict WAL checksums/commit boundaries are local implementations.
Key candidates are reused only inside the current PID's short-lived worker to
validate the contact and head-image databases; no key cache is persisted.
Upstream runtime/support claims are not local acceptance evidence.

The `SessionTable` summary-query field selection in `wechat_conversations/reader.py`
also references `WeChatDB.get_sessions` in upstream `wechatauto/db.py` (main,
consulted 2026-10-28), under the same Apache-2.0 license. The new module is separate
from the exact-self probe: local additions include account-bound worker lifetime,
current-version container classification, contact/avatar lookups scoped to session
identifiers, immutable pagination, JSON APIs, and per-account exclusion settings.
It does not import or run upstream code or create plaintext/key caches.

Message-table naming, shard-local Name2Id sender mapping, and the V1/V2 image
container/key derivation in `wechat_messages/` reference `wechatauto/db.py` and
`wechatauto/media.py` at revision 6e603974bbac3bda93d88bbd6995510ae2547f5c (2026-10-28).
Local changes: bounded read-only memory caches, authenticated WAL updates, typed
JSON jobs, opaque keyset cursors and references, explicit unknown identities,
existing-transcript-only parsing, bounded image validation and no UI/network or
disk-output fallbacks. The upstream package is not imported.

The existing voice transcript field layout was additionally cross-checked against
https://github.com/ChisBread/wechat-ai/blob/2b21ead0d395fd258f806a525f3b2f3c439f4527/src/wechat_ai_bot/weixin/parser/audio_parser.py
The local bounded protobuf wire parser is independently implemented; no generated
protobuf files or runtime automation code from that project are bundled.
