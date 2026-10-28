#include "thread_hook_contract.h"
#include <stdio.h>
#include <wchar.h>
#ifdef BRIDGE_BOUND_INSPECTOR
#include "bound_send_inspector.h"
#endif

static bool Identity(const Contract *s, HWND hwnd) {
    DWORD pid=0; DWORD tid=GetWindowThreadProcessId(hwnd,&pid);
    return s->target_pid == GetCurrentProcessId() && s->target_created == Created(GetCurrentProcess()) &&
           s->target_tid == GetCurrentThreadId() && tid == s->target_tid && pid == s->target_pid &&
           uint64_t(hwnd) == s->hwnd && Alive(s->controller_pid,s->controller_created) &&
           Alive(s->owner_pid,s->owner_created);
}

static void Receive(Contract *s, HWND hwnd) {
    InterlockedIncrement(&s->entries);
    if (InterlockedCompareExchange(&s->phase,Entered,Ready) != Ready) return;
    InterlockedIncrement(&s->active);
    LONG status=BadIdentity;
    if (s->request <= s->consumed) status=Duplicate;
    else {
        InterlockedExchange(&s->consumed,s->request);
        InterlockedExchange(&s->callback_arrived,1);
        while (s->block_before_dispatch && !s->release_callback && !s->stop &&
               GetTickCount64()<s->deadline && Identity(s,hwnd)) Sleep(2);
        if (!Identity(s,hwnd)) status=BadIdentity;
        else if (s->stop) status=Cancelled;
        else if (GetTickCount64()>=s->deadline) status=Expired;
        else {
            wchar_t name[96]{};
            GetClassNameW(hwnd,name,96);
            auto invoke=reinterpret_cast<LONG(*)(HWND,uint64_t,LONG)>(
                GetProcAddress(GetModuleHandleW(nullptr),"BridgeSyntheticInvoke"));
            if (!wcscmp(name,BRIDGE_CLASS) && invoke) status=invoke(hwnd,s->nonce,s->expected_generation);
#ifdef BRIDGE_BOUND_INSPECTOR
            else status=InspectBoundSend(s,hwnd);
#else
            else status=UnsupportedTarget;
#endif
        }
    }
    InterlockedExchange(&s->status,status);
    InterlockedDecrement(&s->active);
    InterlockedExchange(&s->phase,status == Done ? Returned : Denied);
}

extern "C" __declspec(dllexport) LRESULT CALLBACK BridgeHook(int code, WPARAM wp, LPARAM lp) {
    if (code == HC_ACTION && lp) {
        const CWPSTRUCT *event=reinterpret_cast<const CWPSTRUCT*>(lp);
        if (event->message == RegisterWindowMessageW(BRIDGE_MESSAGE)) {
            wchar_t mapping_name[96]{};
            swprintf_s(mapping_name,L"Local\\BridgeM1ThreadHook-%lu-%016llx",
                       DWORD(event->wParam),uint64_t(event->lParam));
            HANDLE mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,mapping_name);
            if (mapping) {
                auto s=static_cast<Contract*>(MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(Contract)));
                if (ValidContract(s) && s->nonce == uint64_t(event->lParam) &&
                    s->controller_pid == DWORD(event->wParam)) Receive(s,event->hwnd);
                if (s) UnmapViewOfFile(s);
                CloseHandle(mapping);
            }
        }
    }
    return CallNextHookEx(nullptr,code,wp,lp);
}

BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
