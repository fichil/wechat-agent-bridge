#include "thread_hook_contract.h"
#include <stdio.h>
#include <wchar.h>
#include "bound_send_inspector.h"

// Only self-allocated fixture bytes. No external process or window arguments.
static unsigned char blocks[24][2048]{};
template<class T> static void Put(uint64_t p,T value) { memcpy(reinterpret_cast<void*>(p),&value,sizeof(value)); }
static uint64_t Block(int i) { return uint64_t(blocks[i]); }
static int checked=0;
static void Expect(bool yes) { if (!yes) ExitProcess(50+DWORD(checked)); ++checked; }

int main(int argc,char**) {
    if (argc!=1) return 2;
    void *image=VirtualAlloc(nullptr,204795904,MEM_RESERVE,PAGE_NOACCESS);
    if (!image) return 3;
    uint64_t base=uint64_t(image);
    if (!VirtualAlloc(reinterpret_cast<void*>(base+0xBBB1000),4096,MEM_COMMIT,PAGE_READWRITE) ||
        !VirtualAlloc(reinterpret_cast<void*>(base+0x2000),4096,MEM_COMMIT,PAGE_READWRITE)) return 4;
    BoundInspection b{}; b.module_base=base; b.module_size=204795904;
    uint64_t cache=Block(0),table=Block(1),array=Block(2);
    Put(base+0xBBB13D0,cache); Put(cache+0x10,table); Put(table+0x14,DWORD(3));
    Put(table+0x20,DWORD(1)); Put(table+0x24,DWORD(0)); Put(table+8,array); Put(array,Block(3));
    const DWORD accessible[]={0x908F258,0x908CD08,0x908DD58};
    const DWORD vtables[]={0x9350000,0x93A6928,0x9B64A88};
    for (int i=0;i<3;++i) {
        b.uid[i]=0x80000001+DWORD(i); b.widget[i]=Block(15+i);
        uint64_t node=Block(3+i),iface=Block(6+i),weak=Block(9+i),control=Block(12+i);
        Put(node,i==2?table:Block(4+i)); Put(node+8,b.uid[i]); Put(node+12,b.uid[i]); Put(node+16,iface);
        Put(iface,base+accessible[i]); Put(iface+8,weak); Put(weak,control); Put(weak+8,b.widget[i]); Put(control+4,LONG(-1));
        Put(b.widget[i],base+vtables[i]);
        uint64_t got=0; Expect(BoundObject(&b,i,got) && got==b.widget[i]);
    }
    Put(b.widget[2]+0x210,(unsigned char)1); Expect(SelectedRow(b.widget[2]));
    Put(b.widget[2]+0x210,(unsigned char)0); Expect(!SelectedRow(b.widget[2]));
    uint64_t got=0; auto uid=b.uid[0]; b.uid[0]=0x80000009; Expect(!BoundObject(&b,0,got)); b.uid[0]=uid;
    Put(Block(12)+4,LONG(0)); Expect(!BoundObject(&b,0,got)); Put(Block(12)+4,LONG(-1));
    Put(Block(6),base+0x9000000); Expect(!BoundObject(&b,0,got)); Put(Block(6),base+accessible[0]);
    b.widget[0]+=8; Expect(!BoundObject(&b,0,got)); b.widget[0]-=8;
    Put(Block(3),Block(3)); Expect(!CachedInterface(base,0x80000005,got)); Put(Block(3),Block(4));
    Put(table+0x20,DWORD(0)); Expect(!CachedInterface(base,uid,got)); Put(table+0x20,DWORD(1));
    Expect(!ReadAt(0x1234,got));
    uint64_t sender=b.widget[1],p=Block(18),data=Block(19),vector=Block(20),node=Block(21),slot=Block(22),receiver=Block(23);
    Put(sender+8,p); Put(p+0x40,data); Put(data+8,vector); Put(vector+8,DWORD(8)); Put(vector+0x90,node);
    Put(node+0x20,sender); Put(node+0x28,receiver); Put(receiver,base+0x9354998); Put(node+0x58,WORD(8));
    Put(node+0x38,slot); Put(slot+8,base+0x1C231B0); Put(slot+0x10,receiver);
    Expect(SendConnection(&b,sender));
    Put(slot+0x10,receiver+8); Expect(!SendConnection(&b,sender)); Put(slot+0x10,receiver);
    Put(node+0x10,node); Expect(!SendConnection(&b,sender)); Put(node+0x10,uint64_t(0));
    Put(vector+0x10,node); Expect(!SendConnection(&b,sender)); Put(vector+0x10,uint64_t(0));
    Put(node+0x58,WORD(10)); Expect(!SendConnection(&b,sender)); Put(node+0x58,WORD(8));
    memcpy(reinterpret_cast<void*>(base+0x2000),"guard",5);
    // Known SHA-256 computed independently in the Python runner.
    const char *digest="a9d4034da07d8ef31db1cd4119b6a4552fdfbd19787e2848e71c8ee3b47703a7";
    Expect(SameCode(base,0x2000,5,digest));
    Put(base+0x2000,(unsigned char)'x'); Expect(!SameCode(base,0x2000,5,digest));
    LASTINPUTINFO input{sizeof(input),0}; Expect(GetLastInputInfo(&input)!=0);
    b.last_input=input.dwTime; b.foreground=uint64_t(GetForegroundWindow()); b.clipboard=GetClipboardSequenceNumber();
    Expect(InputEnvironment(&b,nullptr)); b.last_input^=1; Expect(!InputEnvironment(&b,nullptr));
    Contract s{}; s.controller_pid=GetCurrentProcessId(); s.nonce=GetTickCount64();
    Expect(InspectBoundSend(&s,nullptr)==UnsupportedTarget);
    Expect(VirtualFree(image,0,MEM_RELEASE)!=0);
    printf("{\"passed\":true,\"checks\":%d,\"external_target_accessed\":false}\n",checked);
    return 0;
}
