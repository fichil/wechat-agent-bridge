"""Synthetic address maps. Tests must never open a client process or its DLL."""
import struct
import unittest
from types import SimpleNamespace

from wechat_agent_bridge.adapters.runtime_accessibility import BoundedMemory, interface_for_id, connections, CACHE_RVA, ExactProfile
from wechat_agent_bridge.console_models import ConsoleError


class Map:
    def __init__(self): self.values = {}; self.identity = True
    def put(self, address, value, fmt='<Q'): self.values[address] = struct.pack(fmt, value)
    def read(self, address, count): return self.values[address][:count]
    def check(self):
        if not self.identity: raise ConsoleError("runtime_process_identity_changed")
    def port(self): return BoundedMemory(self.read, self.check)


class RuntimeTests(unittest.TestCase):
    def cache(self):
        m = Map(); base = 0x180000000
        cache, table, array, node, iface = 0x10000,0x20000,0x30000,0x40000,0x50000
        uid = 0x80000002
        for a,v in ((base+CACHE_RVA,cache),(cache+16,table),(table+8,array),(array,node),
                    (node,table),(node+16,iface)):m.put(a,v)
        for a,v in ((table+0x14,1),(table+0x20,1),(table+0x24,0),(node+8,uid),(node+12,uid)):m.put(a,v,'<I')
        return m,base,uid,iface

    def test_exact_id_lookup_and_signed_runtime_id(self):
        m,base,uid,iface=self.cache()
        self.assertEqual(interface_for_id(m.port(),base,[42,uid-0x100000000]),iface)

    def test_stale_id_is_not_another_object(self):
        m,base,uid,_=self.cache()
        with self.assertRaisesRegex(ConsoleError,'stale'):
            interface_for_id(m.port(),base,[42,uid+1])

    def test_hash_chain_cycle(self):
        m,base,uid,_=self.cache();m.put(0x40000,0x40000)
        with self.assertRaisesRegex(ConsoleError,'cycle'):
            interface_for_id(m.port(),base,[42,uid+1])

    def test_runtime_id_validation(self):
        m,base,_,_=self.cache()
        for value in ([],[42],['1',2],[1,7],[1,True],[1,0xffffffff]):
            with self.subTest(value=value),self.assertRaises(ConsoleError): interface_for_id(m.port(),base,value)

    def test_object_changed_between_snapshot_and_recheck(self):
        m=Map();m.put(0x10000,5);port=m.port()
        port.number(0x10000);m.put(0x10000,6)
        with self.assertRaisesRegex(ConsoleError,'object_changed'):port.recheck()

    def test_pid_reuse_fails_every_read(self):
        m=Map();m.put(0x10000,5);port=m.port();m.identity=False
        with self.assertRaisesRegex(ConsoleError,'identity_changed'):port.number(0x10000)

    def test_bounds_and_short_reads(self):
        m=Map();m.put(0x10000,5);port=BoundedMemory(m.read,m.check,max_bytes=8)
        for address,count in ((0,8),(0x10000,4097),(0x800000000000,8)):
            with self.assertRaises(ConsoleError):port.read(address,count)
        port.read(0x10000,8)
        with self.assertRaises(ConsoleError):port.read(0x10000,1)
        port=BoundedMemory(lambda a,n:b'',lambda:None)
        with self.assertRaisesRegex(ConsoleError,'short_read'):port.read(0x10000,1)

    def graph(self):
        m=Map();obj=0x10000;private=0x20000;data=0x30000;vector=0x40000;node=0x50000
        for a,v in ((obj+8,private),(private+0x40,data),(data+8,vector),(vector+0x10,0),
                    (vector+0x20,node),(node+0x10,0),(node+0x20,obj),(node+0x28,0),(node+0x38,0)):
            m.put(a,v)
        m.put(vector+8,1,'<I')
        for off in (0x50,0x52,0x58):m.put(node+off,0,'<H')
        return m,obj,node

    def test_connection_metadata_does_not_read_arguments(self):
        m,obj,node=self.graph()
        result=connections(m.port(),obj,0x180000000,0x10000000,None)
        self.assertEqual(result[0]['receiver_class'],'disconnected')
        self.assertEqual(result[0]['signal_index'],0)
        self.assertNotIn('arguments',result[0])

    def test_connection_sender_mismatch(self):
        m,obj,node=self.graph();m.put(node+0x20,0x90000)
        with self.assertRaisesRegex(ConsoleError,'sender_mismatch'):connections(m.port(),obj,0,1,None)

    def test_connection_cycle(self):
        m,obj,node=self.graph();m.put(node+0x10,node)
        with self.assertRaisesRegex(ConsoleError,'cycle'):connections(m.port(),obj,0,1,None)

    def test_signal_vector_limit(self):
        m,obj,node=self.graph();m.put(0x40008,257,'<I')
        with self.assertRaisesRegex(ConsoleError,'vector_limit'):connections(m.port(),obj,0,1,None)

    def test_slot_object_skips_unused_method_fields(self):
        m,obj,node=self.graph();base=0x180000000
        m.put(node+0x58,8,'<H');m.put(node+0x38,0x60000)
        m.put(0x60008,base+0x1234)
        del m.values[node+0x50];del m.values[node+0x52]
        row=connections(m.port(),obj,base,0x10000000,None)[0]
        self.assertIsNone(row['method_offset'])
        self.assertIsNone(row['method_relative'])
        self.assertEqual(row['callback_rva'],0x1234)

    def test_known_member_wrapper_reads_only_its_pointer_and_adjustment(self):
        m,obj,node=self.graph();base=0x180000000
        m.put(node+0x58,8,'<H');m.put(node+0x38,0x60000)
        m.put(0x60008,base+0x1a2df0);m.put(0x60010,base+0x1234);m.put(0x60018,0,'<i')
        row=connections(m.port(),obj,base,0x10000000,None)[0]
        self.assertEqual(row['member_target_rva'],0x1234)
        self.assertEqual(row['receiver_adjustment'],0)

    def test_wrong_file_hash_never_parses(self):
        with self.assertRaisesRegex(ConsoleError,'fingerprint'):ExactProfile(b'synthetic_wrong_dll')
