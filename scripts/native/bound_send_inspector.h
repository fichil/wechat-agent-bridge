#pragma once
#include <bcrypt.h>

// Current exact build only. This entry never invokes Qt, writes text, or sends.
// The transport owner receives no arbitrary function pointer or write address.
struct BoundInspection {
    uint64_t magic, nonce, module_base, module_size;
    DWORD version, bytes, uid[3], last_input;
    uint64_t widget[3], foreground;
    DWORD clipboard, mode;
    volatile LONG checked, selected, gui_thread, reason;
    volatile LONG operation_started, operation_returned, exception_code;
};
static_assert(sizeof(BoundInspection)==128,"bound inspection ABI");
static const uint64_t BOUND_MAGIC=0x4252494447454931ULL;

template<class T> static bool ReadAt(uint64_t address,T &out) {
    if (address < 0x10000 || address > 0x7fffffffffffULL-sizeof(T)) return false;
    SIZE_T n=0;
    return ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(address),&out,sizeof(T),&n) && n==sizeof(T);
}
static bool SameCode(uint64_t base,DWORD rva,DWORD bytes,const char *expected) {
    if (bytes>1024) return false;
    unsigned char data[1024]{},digest[32]{}; SIZE_T n=0;
    if (!ReadProcessMemory(GetCurrentProcess(),reinterpret_cast<void*>(base+rva),data,bytes,&n) || n!=bytes) return false;
    BCRYPT_ALG_HANDLE algorithm=nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return false;
    bool okay=BCryptHash(algorithm,nullptr,0,data,bytes,digest,32)>=0;
    BCryptCloseAlgorithmProvider(algorithm,0);
    static const char hex[]="0123456789abcdef";
    for (int i=0;i<32 && okay;++i) okay=expected[i*2]==hex[digest[i]>>4] && expected[i*2+1]==hex[digest[i]&15];
    return okay;
}
static bool CachedInterface(uint64_t base,DWORD uid,uint64_t &interface_pointer) {
    uint64_t cache=0,table=0,array=0,node=0; DWORD size=0,buckets=0,seed=0;
    if (uid<0x80000000 || uid==0xffffffff || !ReadAt(base+0xBBB13D0,cache) ||
        !ReadAt(cache+0x10,table) || !ReadAt(table+0x14,size) || !ReadAt(table+0x20,buckets) ||
        !ReadAt(table+0x24,seed) || !size || size>1000000 || !buckets || buckets>1000000 ||
        !ReadAt(table+8,array) || !ReadAt(array+8*((uid^seed)%buckets),node)) return false;
    uint64_t seen[128]{};
    for (unsigned i=0;node!=table && i<128;++i) {
        for (unsigned j=0;j<i;++j) if (seen[j]==node) return false;
        seen[i]=node; DWORD hash=0,key=0;
        if (!ReadAt(node+8,hash) || !ReadAt(node+12,key)) return false;
        if (hash==(uid^seed) && key==uid) return ReadAt(node+16,interface_pointer) && interface_pointer;
        if (!ReadAt(node,node)) return false;
    }
    return false;
}
static bool BoundObject(const BoundInspection *b,int index,uint64_t &object) {
    static const DWORD tables[]={0x908F258,0x908CD08,0x908DD58};
    uint64_t iface=0,table=0,weak=0,control=0; LONG strong=0;
    if (!CachedInterface(b->module_base,b->uid[index],iface) || !ReadAt(iface,table) ||
        table!=b->module_base+tables[index] || !ReadAt(iface+8,weak) || !ReadAt(weak,control) ||
        !control || !ReadAt(control+4,strong) || strong==0 || !ReadAt(weak+8,object) || object!=b->widget[index]) return false;
    uint64_t vtable=0;
    if (!ReadAt(object,vtable) || vtable<b->module_base || vtable>=b->module_base+b->module_size) return false;
    if (index==1 && vtable!=b->module_base+0x93A6928) return false;
    if (index==2 && vtable!=b->module_base+0x9B64A88) return false;
    return true;
}
static bool SendConnection(const BoundInspection *b,uint64_t sender) {
    uint64_t p=0,data=0,vector=0,node=0; DWORD count=0;
    if (!ReadAt(sender+8,p) || !ReadAt(p+0x40,data) || !ReadAt(data+8,vector) || !ReadAt(vector+8,count) ||
        count<8 || count>256 || !ReadAt(vector+0x20+16*7,node) || !node) return false;
    uint64_t actual_sender=0,receiver=0,slot=0,callback=0,capture=0,next=0,vt=0,all=0; WORD flags=0;
    return ReadAt(vector+0x10,all) && all==0 && ReadAt(node+0x20,actual_sender) && actual_sender==sender &&
        ReadAt(node+0x28,receiver) && receiver && ReadAt(receiver,vt) && vt==b->module_base+0x9354998 &&
        ReadAt(node+0x58,flags) && (flags&15)==8 && ReadAt(node+0x38,slot) &&
        ReadAt(slot+8,callback) && callback==b->module_base+0x1C231B0 && ReadAt(slot+0x10,capture) && capture==receiver &&
        ReadAt(node+0x10,next) && next==0;
}
static bool InputEnvironment(const BoundInspection *b,HWND hwnd) {
    LASTINPUTINFO input{sizeof(LASTINPUTINFO),0};
    return GetLastInputInfo(&input) && input.dwTime==b->last_input &&
        uint64_t(GetForegroundWindow())==b->foreground && GetForegroundWindow()!=hwnd &&
        GetClipboardSequenceNumber()==b->clipboard;
}
static bool SelectedRow(uint64_t row) {
    unsigned char selected=0;
    return ReadAt(row+0x210,selected) && selected==1;
}
static LONG InspectBoundSend(Contract *s,HWND hwnd) {
    wchar_t name[112]{};
    swprintf_s(name,L"Local\\BridgeM1ThreadHook-%lu-%016llx-Bound",s->controller_pid,s->nonce);
    HANDLE map=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,name);
    if (!map) return UnsupportedTarget;
    auto b=static_cast<BoundInspection*>(MapViewOfFile(map,FILE_MAP_ALL_ACCESS,0,0,sizeof(BoundInspection)));
    LONG status=BadProtocol;
    bool allowed_mode=b && b->mode==0;
#ifdef BRIDGE_BOUND_SEND
    allowed_mode=allowed_mode || (b && b->mode==1);
#endif
    if (b && b->magic==BOUND_MAGIC && b->nonce==s->nonce && b->version==1 && b->bytes==sizeof(BoundInspection) && allowed_mode) {
        uint64_t input=0,send=0,row=0;
        b->gui_thread=LONG(GetCurrentThreadId()); b->checked=1;
        if (b->module_base!=uint64_t(GetModuleHandleW(L"Weixin.dll")) || b->module_size!=204795904) b->reason=1;
        else if (!InputEnvironment(b,hwnd)) b->reason=2;
        else if (!SameCode(b->module_base,0x9D0F50,145,"6afb645c3f38d0e6917f5f5519fc20b64047fe0aab1e4f8a7610264851297619") ||
                 !SameCode(b->module_base,0x9D2080,150,"a10c8fbde544f55bd957aa98df413d9122943150d494a810265b3ebea765e13e") ||
                 !SameCode(b->module_base,0x1C231B0,72,"2b7ffaf70eeb8f8246d906d965c2026e727894d30834da0d65318320c13d8465") ||
                 !SameCode(b->module_base,0x911110,853,"f545567651645d2e8b463327217bda3d2ebc9a1976064ed15583f82c4e7eb891")) b->reason=3;
        else if (!BoundObject(b,0,input) || !BoundObject(b,1,send) || !BoundObject(b,2,row)) b->reason=4;
        else if (!SelectedRow(row)) b->reason=5;
        else if (!SendConnection(b,send)) b->reason=6;
        else if (s->stop || GetTickCount64()>=s->deadline || !Alive(s->controller_pid,s->controller_created) ||
                 !Alive(s->owner_pid,s->owner_created) || !InputEnvironment(b,hwnd)) b->reason=7;
        else {
            b->selected=1; b->reason=0; status=Done;
#ifdef BRIDGE_BOUND_SEND
            if (b->mode==1) {
                // Emit the one reviewed XOutlineButton::clicked signal. Qt owns
                // connection lifetime/dispatch; never call a captured lambda.
                InterlockedExchange(&b->operation_started,1);
                __try {
                    using Metacall=void(*)(void*,int,int,void**);
                    auto emit=reinterpret_cast<Metacall>(b->module_base+0x911110);
                    emit(reinterpret_cast<void*>(send),0,0,nullptr);
                    InterlockedExchange(&b->operation_returned,1);
                } __except(EXCEPTION_EXECUTE_HANDLER) {
                    b->exception_code=LONG(GetExceptionCode()); b->reason=8; status=WrongObject;
                }
            }
#endif
        }
        if (status!=Done) status=WrongObject;
    }
    if (b) UnmapViewOfFile(b);
    CloseHandle(map); return status;
}
