"""Bounded group working memory; compaction never has reply/edit permission."""
from .conversations import ConversationError
from .diagnostics import log_context
from . import secret_protection

MEMORY_TARGET = 3000
MEMORY_LIMIT = 4000
MEMORY_INSTRUCTIONS = (
    'Group working memory must be a concise summary, targeting 3000 Unicode characters and never exceeding 4000. '
    'This limit overrides all older memory-size instructions, including those in character cards. '
    'Keep durable relationships, preferences, commitments, unresolved tasks and the current topic. '
    'Merge repetitions and replace obsolete details; do not append a message-by-message diary. '
    'The complete chat archive remains local. Media placeholders have NOT been visually inspected; never invent their contents.'
)


def compact_for_run(execution, aid, cid, rid, profile, encrypted_key, mode, phase, memory):
    if len(memory) <= MEMORY_LIMIT:
        return memory
    saved = execution.store.begin_memory_compaction(aid, cid, rid, phase, memory)
    if saved is not None:
        return saved
    envelope = dict(request_id=rid + '-memory-' + phase, instructions=MEMORY_INSTRUCTIONS,
                    character_card={}, memory=memory, messages=[], memory_max_chars=MEMORY_LIMIT,
                    processing_type='memory_backfill', role_card_edit_allowed=False,
                    reply_allowed=False, reply_required=False, reply_target_ids=[], trigger_messages=[])
    try:
        with log_context(execution.store, account_id=aid, conversation_id=cid, task_id=rid,
                         operation='memory_compaction', processing_type='memory_backfill', trigger='auto'):
            result = execution.provider.generate(profile, secret_protection.reveal(encrypted_key), envelope, mode)
        if (not result['memory'].strip() or len(result['memory']) > MEMORY_LIMIT
                or result['reply'].strip() or result.get('role_card_update', '').strip()):
            raise ValueError('invalid compaction')
        execution.store.complete_memory_compaction(aid, cid, rid, phase, result['memory'])
        return result['memory']
    except Exception:
        raise ConversationError('ai_memory_compaction_failed',
                                '记忆压缩未成功，本轮已暂停；原记忆和待处理消息保留，不会自动重复压缩。', 422) from None
