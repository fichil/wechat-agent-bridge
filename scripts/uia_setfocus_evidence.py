"""Pure, bounded evidence for the fingerprinted UIA SetFocus ETW entry/exit.

No native calls or payload reads. A runner supplies scalar headers and a lazy
reader of one validated x64 stack. ETL bookkeeping is the runner's responsibility.
Each phase needs a fresh instance; successful parsing does not prove cleanup,
WeChat compatibility, or a top-level window activation.
"""
import struct
import uuid

PROVIDER = uuid.UUID('820a42d8-38c4-465d-b64e-d7d56ea1d612').bytes_le
DLL_SHA256 = 'be5037bf46e61566d3e5a83a6c628cebe9035771e4ae741a7b9aa27b5696d989'
ENTRY = bytes.fromhex('4f01001104014f010000010000000040')
EXIT = bytes.fromhex('5001001104024f010000010000000040')
# Nearest frame first, matching exact disk call-return positions, not time proximity.
CHAINS = {335: (0xDD1D8, 0x229928, 0x2268FD, 0x2269E1, 0x1C674B),
          336: (0xDD1D8, 0x22663C, 0x2299DF, 0x2268FD, 0x2269E1, 0x1C674B)}


def descriptor(event):
    return struct.pack('<HBBBBHQ', *(event[key] for key in
        ('id', 'version', 'channel', 'level', 'opcode', 'task_id')), int(event['keyword'], 16))


def schema_table(events):
    """Accept the complete parsed manifest, retaining the enabled keyword/level.

    The caller must bind the manifest and loaded DLL to DLL_SHA256. Fields are
    inspected only in metadata; actual non-target payloads are never accessed.
    """
    if not isinstance(events, (list, tuple)) or not 2 <= len(events) <= 512:
        raise ValueError('manifest_size_invalid')
    result = {}
    for event in events:
        if event.get('provider') != 'Microsoft-Windows-UIAutomationCore':
            raise ValueError('manifest_provider_invalid')
        raw = descriptor(event)
        keyword = int(event['keyword'], 16)
        if event['level'] <= 4 and (keyword == 0 or keyword & 0x10000):
            if raw in result:
                raise ValueError('manifest_descriptor_duplicate')
            result[raw] = {'id': event['id'], 'payload_empty': event.get('fields') == []}
    for raw in (ENTRY, EXIT):
        if raw not in result or not result[raw]['payload_empty']:
            raise ValueError('setfocus_manifest_mismatch')
    return result


class SetFocusEvidence:
    def __init__(self, pid, tid, schemas, modules, *, require_stack=True,
                 max_events=512, max_pairs=8):
        if any(type(value) is not int or not 0 < value < 2**32 for value in (pid, tid)):
            raise ValueError('source_identity_invalid')
        if (type(max_events) is not int or not 1 <= max_events <= 4096 or
                type(max_pairs) is not int or not 1 <= max_pairs <= 32 or type(require_stack) is not bool):
            raise ValueError('evidence_bound_invalid')
        if not isinstance(schemas, dict) or not 2 <= len(schemas) <= 512:
            raise ValueError('schema_table_invalid')
        for raw, item in schemas.items():
            if (type(raw) is not bytes or len(raw) != 16 or not isinstance(item, dict) or
                    struct.unpack_from('<H', raw)[0] != item.get('id')):
                raise ValueError('schema_table_invalid')
        if any(schemas.get(raw) != {'id': event_id, 'payload_empty': True}
               for raw, event_id in ((ENTRY, 335), (EXIT, 336))):
            raise ValueError('setfocus_manifest_mismatch')
        if not isinstance(modules, (list, tuple)) or not 1 <= len(modules) <= 256:
            raise ValueError('module_table_invalid')
        self.modules = []
        for row in modules:
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise ValueError('module_table_invalid')
            base, size, name = row
            if (type(base) is not int or type(size) is not int or not 0 < base < 2**64 or
                    not 0 < size <= 2**32 or base + size > 2**64 or not isinstance(name, str) or
                    not 1 <= len(name) <= 128 or any(char in name for char in '/\\\0')):
                raise ValueError('module_table_invalid')
            self.modules.append((base, size, name.lower()))
        ordered = sorted(self.modules)
        if (any(a[0] + a[1] > b[0] for a, b in zip(ordered, ordered[1:])) or
                sum(name == 'uiautomationcore.dll' for _, _, name in ordered) != 1):
            raise ValueError('module_table_ambiguous')
        self.pid, self.tid = pid, tid
        self.schemas = {raw: dict(item) for raw, item in schemas.items()}
        self.require_stack, self.max_events, self.max_pairs = require_stack, max_events, max_pairs
        self.total = self.other = self.other_threads = self.entries = self.exits = self.pairs = 0
        self.pending, self.failed, self.sealed = False, False, False
        self.last_time, self.other_counts, self.other_thread_counts = -1, {}, {}
        self.records, self.reasons = [], {}

    def _reject(self, reason):
        self.failed = True
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        return False

    def _frames(self, reader, event_id):
        addresses = reader()
        if not isinstance(addresses, (list, tuple)) or not 1 <= len(addresses) <= 64:
            raise ValueError('stack_size_invalid')
        frames = []
        for address in addresses:
            if type(address) is not int or not 0 <= address < 2**64:
                raise ValueError('stack_address_invalid')
            if address == 0:
                continue
            matches = [(name, address - base) for base, size, name in self.modules
                       if base <= address < base + size]
            if len(matches) != 1:
                raise ValueError('stack_module_unknown')
            name, rva = matches[0]
            frames.append({'module': name, 'rva': rva})
        position = 0
        chain = CHAINS[event_id]
        for frame in frames:
            if (frame['module'] == 'uiautomationcore.dll' and position < len(chain) and
                    frame['rva'] == chain[position]):
                position += 1
        if position != len(chain):
            raise ValueError('synchronous_call_chain_missing')
        return frames

    def consume(self, header, stack_reader=None):
        """Header: provider(bytes), pid/tid, descriptor(bytes), flags, data_length, time.

        stack_reader must check extended item count/type/size before returning one
        x64 stack's addresses. It is never called for rejected/other events.
        Known non-target events on other threads of the same process are counted
        separately; they never establish the selected MTA's negative control.
        """
        if self.sealed:
            return self._reject('event_after_finish')
        if self.failed:
            return False
        self.total += 1
        if self.total > self.max_events:
            return self._reject('event_limit_exceeded')
        try:
            if (not isinstance(header, dict) or header.get('provider') != PROVIDER or
                    type(header.get('pid')) is not int or header['pid'] != self.pid or
                    type(header.get('tid')) is not int or not 0 < header['tid'] < 2**32):
                return self._reject('event_source_mismatch')
            raw = header.get('descriptor')
            if type(raw) is not bytes or raw not in self.schemas:
                return self._reject('event_schema_unknown')
            flags, length, tick = (header.get(key) for key in ('flags', 'data_length', 'time'))
            if (type(flags) is not int or not 0 <= flags <= 0xffff or flags & 0x20 or not flags & 0x40 or
                    type(length) is not int or not 0 <= length <= 0xffff or
                    type(tick) is not int or not 0 <= tick < 2**64 or
                    (header['tid'] == self.tid and tick < self.last_time)):
                return self._reject('event_header_invalid')
            if header['tid'] == self.tid:
                self.last_time = tick
            event_id = self.schemas[raw]['id']
            if raw not in (ENTRY, EXIT):
                if self.schemas[raw].get('payload_empty') is True and length != 0:
                    return self._reject('empty_event_payload_mismatch')
                key = str(event_id)
                if header['tid'] == self.tid:
                    self.other += 1
                    self.other_counts[key] = self.other_counts.get(key, 0) + 1
                else:
                    self.other_threads += 1
                    self.other_thread_counts[key] = self.other_thread_counts.get(key, 0) + 1
                return True
            if header['tid'] != self.tid:
                return self._reject('setfocus_thread_mismatch')
            if length != 0:
                return self._reject('setfocus_payload_unexpected')
            if ((raw == ENTRY and self.pending) or (raw == EXIT and not self.pending)):
                return self._reject('setfocus_pair_order_invalid')
            if raw == ENTRY and self.entries >= self.max_pairs:
                return self._reject('setfocus_pair_limit_exceeded')
            frames = []
            if self.require_stack:
                if not callable(stack_reader):
                    return self._reject('stack_missing')
                try:
                    frames = self._frames(stack_reader, event_id)
                except ValueError as exc:
                    reason = str(exc)
                    allowed = ('stack_size_invalid', 'stack_address_invalid', 'stack_module_unknown',
                               'synchronous_call_chain_missing')
                    return self._reject(reason if reason in allowed else 'stack_reader_failed')
                except Exception:
                    return self._reject('stack_reader_failed')
            self.records.append({'event_id': event_id, 'time': tick, 'frames': frames})
            if raw == ENTRY:
                self.entries += 1
                self.pending = True
            else:
                self.exits += 1
                self.pairs += 1
                self.pending = False
            return True
        except Exception:
            return self._reject('event_invalid')

    def finish(self, *, expect_focus, observation_complete, events_lost=0):
        self.sealed = True
        valid_window = (observation_complete is True and type(events_lost) is int and events_lost == 0)
        positive = self.entries == self.exits == self.pairs and self.pairs >= 1
        negative = self.entries == self.exits == self.pairs == 0 and self.other > 0
        passed = bool(type(expect_focus) is bool and valid_window and not self.failed and not self.pending and
                      (positive if expect_focus else negative))
        return {'passed': passed, 'observation_complete': valid_window, 'require_stack': self.require_stack,
                'synchronous_chain_verified': passed and expect_focus and self.require_stack,
                'entries': self.entries, 'exits': self.exits, 'pairs': self.pairs,
                'other_events': self.other, 'total_events': self.total,
                'other_event_counts': dict(self.other_counts),
                'other_thread_events': self.other_threads,
                'other_thread_event_counts': dict(self.other_thread_counts),
                'reasons': dict(self.reasons),
                'records': [{'event_id': row['event_id'], 'time': row['time'],
                             'frames': [dict(frame) for frame in row['frames']]} for row in self.records],
                'live_client_verified': False, 'foreground_activation_verified': False}
