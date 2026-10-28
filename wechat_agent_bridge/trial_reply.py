"""Explicit reply trials, separate from verified sender / mention semantics."""
from dataclasses import replace

PREFIX = 'M1测试：'
REPLY_PREFIX = '【M1 AI】'
TRIGGER_MODES = ('prefix', 'all_new_text')


class LoadedTail:
    """Only an append after the previous tail can be a new event.

    History loads, lost anchors, list replacement and restart establish a new
    baseline. This does not infer who sent a row or whether it mentions anyone.
    """
    def __init__(self):
        self.tail = None
        self.list_id = None
        self.seen = set()

    def page(self, value, page, *, older=False):
        if older:
            return page
        ids = [m.id for m in page.messages]
        listing = value.get('list_runtime_id')
        append_at = ids.index(self.tail) + 1 if self.list_id == listing and self.tail in ids else len(ids)
        messages = tuple(replace(m, historical=not (i >= append_at and m.id not in self.seen))
                         for i, m in enumerate(page.messages))
        self.seen.update(ids)
        self.tail = ids[-1] if ids else None
        self.list_id = listing
        return replace(page, messages=messages)


def triggers(message, mode='prefix', *, outbound=False):
    eligible = (not message.historical and message.own is not True and not outbound and
            message.kind == 'text' and message.content_status == 'available' and
            message.upload_eligible and bool(message.text.strip()) and
            not message.text.lstrip().startswith(REPLY_PREFIX))
    if not eligible:
        return False
    if mode == 'all_new_text':
        return True
    return mode == 'prefix' and message.text.startswith(PREFIX) and bool(message.text[len(PREFIX):].strip())


def reply_text(text, limit):
    # New echoes are identified by durable outbound digests, not a visible tag.
    text = text.strip()
    while text.startswith(REPLY_PREFIX):
        text = text[len(REPLY_PREFIX):].lstrip()
    return text[:limit]
