// Adapted from Prometheus-first/helper @ 3e65e4bdaff438dc616f32d412175842fa138763.
// Personal noncommercial use; see LICENSE.helper and NOTICE.md.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <bcrypt.h>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <cstdio>
#include <functional>
#include <memory>
#include <iterator>
#include "core_version.h"
struct SharedMessage { void* object; void* control; };
struct SmallString { char data[16]; uint64_t length; uint64_t capacity; };
struct TestConfig {
 uint32_t size, pid;
 uint64_t created;
 wchar_t log_path[MAX_PATH];
 char text[1024];
 char account[128];
 char token[40];
};
struct TestResult { DWORD stage, exception; uint64_t exception_pc, fault_address, access_kind; };
static uintptr_t core_base = 0;
static volatile LONG active_callbacks = 0, live_completions = 0, live_tasks = 0;

static int record_exception(EXCEPTION_POINTERS* exception, TestResult* result) {
    result->exception = exception->ExceptionRecord->ExceptionCode;
    result->exception_pc = reinterpret_cast<uintptr_t>(exception->ExceptionRecord->ExceptionAddress);
    if (exception->ExceptionRecord->NumberParameters >= 2) {
        result->access_kind = exception->ExceptionRecord->ExceptionInformation[0];
        result->fault_address = exception->ExceptionRecord->ExceptionInformation[1];
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

static bool core_image_identity(HMODULE core, uint32_t* timestamp, uint32_t* image_size) {
    __try {
        const auto bytes = reinterpret_cast<const unsigned char*>(core);
        const auto dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(bytes);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < 64 || dos->e_lfanew > 1048576) return false;
        const auto nt = reinterpret_cast<const IMAGE_NT_HEADERS64*>(bytes + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
                nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) return false;
        *timestamp = nt->FileHeader.TimeDateStamp;
        *image_size = nt->OptionalHeader.SizeOfImage;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}

static bool core_fingerprint(HMODULE core) {
    uint32_t timestamp = 0, image_size = 0;
    if (!core_image_identity(core, &timestamp, &image_size)) return false;
    wchar_t path[32768]{};
    if (!GetModuleFileNameW(core, path, static_cast<DWORD>(std::size(path)))) return false;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size{};
    bool valid = GetFileSizeEx(file, &size) && core_size_supported(static_cast<uint64_t>(size.QuadPart));
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (valid && BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0) {
        // Windows allocates the hash object when pbHashObject is NULL on supported Windows.
        if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) >= 0) {
            unsigned char buffer[65536], digest[32];
            DWORD count = 0;
            while (valid) {
                valid = ReadFile(file, buffer, sizeof(buffer), &count, nullptr) != FALSE;
                if (!valid || !count) break;
                valid = BCryptHashData(hash, buffer, count, 0) >= 0;
            }
            valid = valid && BCryptFinishHash(hash, digest, sizeof(digest), 0) >= 0 &&
                core_select_build(static_cast<uint64_t>(size.QuadPart), digest, timestamp, image_size);
            BCryptDestroyHash(hash);
        } else valid = false;
        BCryptCloseAlgorithmProvider(algorithm, 0);
    } else valid = false;
    CloseHandle(file);
    return valid;
}

extern "C" __declspec(dllexport) DWORD WINAPI WxInspectCoreVersion(void*) {
    const auto core = GetModuleHandleW(L"Weixin.dll");
    if (!core || !core_fingerprint(core)) return 0;
    return static_cast<DWORD>(InterlockedCompareExchange(&core_profile_id, 0, 0));
}

static void release_shared(SharedMessage& message) {
    if (!message.control) return;
    auto control = static_cast<unsigned char*>(message.control);
    if (_InterlockedDecrement(reinterpret_cast<volatile LONG*>(control + 8)) == 0) {
        using VirtualFunction = void(__fastcall*)(void*);
        auto table = *reinterpret_cast<VirtualFunction**>(control);
        table[0](control);
        if (_InterlockedDecrement(reinterpret_cast<volatile LONG*>(control + 12)) == 0) {
            table = *reinterpret_cast<VirtualFunction**>(control);
            table[1](control);
        }
    }
    message = {};
}

static void guarded_release_shared(SharedMessage* message, TestResult* report) {
    __try {
        release_shared(*message);
    } __except (record_exception(GetExceptionInformation(), report)) {
    }
}

static bool set_inline(SmallString* string, const char* text) {
    const size_t length = std::strlen(text);
    if (length > 15 || string->capacity != 15 || string->length != 0) return false;
    std::memcpy(string->data, text, length + 1);
    string->length = length;
    return true;
}

static bool set_native_string(SmallString* string, const char* text) {
    const size_t length = std::strlen(text);
    if (length < 16) return set_inline(string, text);
    if (length > 1023 || string->capacity != 15 || string->length != 0) return false;
    using Allocate = void* (__fastcall*)(size_t);
    char* buffer = static_cast<char*>(reinterpret_cast<Allocate>(core_address(core_base, CoreField::allocate))(length + 1));
    if (!buffer) return false;
    std::memcpy(buffer, text, length + 1);
    std::memcpy(string->data, &buffer, sizeof(buffer));
    string->length = string->capacity = length;
    return true;
}

static void free_native_string(SmallString* string) {
    if (string->capacity > 15) {
        void* allocation = *reinterpret_cast<void**>(string->data);
        using Free = void (__fastcall*)(void*, size_t);
        reinterpret_cast<Free>(core_address(core_base, CoreField::free))(allocation, string->capacity + 1);
    }
    *string = {};
    string->capacity = 15;
}
static void write_json_string(FILE* log, const char* text) {
    std::fputc('"', log);
    for (const unsigned char* current = reinterpret_cast<const unsigned char*>(text); *current; ++current) {
        if (*current == '"' || *current == '\\') { std::fputc('\\', log); std::fputc(*current, log); }
        else if (*current < 32) std::fprintf(log, "\\u%04x", *current);
        else std::fputc(*current, log);
    }
    std::fputc('"', log);
}

static void report_status(const TestConfig& config, const char* status) {
 FILE* f = nullptr;
 if (_wfopen_s(&f, config.log_path, L"wb") == 0 && f) {
  std::fputs("{\"status\":", f); write_json_string(f, status);
  std::fputs(",\"token\":", f); write_json_string(f, config.token);
  std::fputs("}", f); std::fclose(f);
 }
}
static void write_report(const TestConfig& config, const TestResult&, const char*) { report_status(config, "unknown"); }
static bool identity_matches(const TestConfig& config) {
 __try {
  FILETIME c{}, e{}, k{}, u{};
  if (config.pid != GetCurrentProcessId() || !GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u) ||
      ((static_cast<uint64_t>(c.dwHighDateTime)<<32)|c.dwLowDateTime) != config.created) return false;
  const auto app = *reinterpret_cast<uintptr_t*>(core_address(core_base, CoreField::application));
  if (!app || *reinterpret_cast<uintptr_t*>(app) != core_address(core_base, CoreField::app_vtable)) return false;
  if (*reinterpret_cast<uintptr_t*>(core_address(core_base, CoreField::app_vtable) + core_offset(CoreField::app_get_slot)) != core_address(core_base, CoreField::app_get)) return false;
  const auto context = *reinterpret_cast<uintptr_t*>(app + core_offset(CoreField::app_context));
  const unsigned char context_code[] = {0x48, 0x8d, 0x41, static_cast<unsigned char>(core_offset(CoreField::account_string)), 0xc3};
  if (!context || *reinterpret_cast<uintptr_t*>(context) != core_address(core_base, CoreField::context_vtable) ||
      *reinterpret_cast<uintptr_t*>(core_address(core_base, CoreField::context_vtable) + core_offset(CoreField::context_get_slot)) != core_address(core_base, CoreField::context_get) ||
      std::memcmp(reinterpret_cast<void*>(core_address(core_base, CoreField::context_get)), context_code, sizeof(context_code))) return false;
  const auto s = reinterpret_cast<SmallString*>(context + core_offset(CoreField::account_string));
  const size_t length = strnlen_s(config.account, 128);
  if (!length || length >= 128 || s->length != length || s->capacity < length ||
      !(s->capacity == 15 || (s->capacity >= 16 && s->capacity <= 1048576))) return false;
  const char* body = s->capacity == 15 ? s->data : *reinterpret_cast<char**>(s->data);
  return body && !std::memcmp(body, config.account, length);
 } __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
static DWORD schedule_task(const TestConfig& config, std::function<void()>* callable) {
    static_assert(sizeof(std::function<void()>) == 64, "Unexpected function ABI");
    struct Location { const char* function; const char* file; uint32_t line; uint32_t pad; void* caller; };
    static_assert(sizeof(Location) == 32, "Unexpected source location ABI");
    Location location{"wxbot.selftest", "selftest_bridge.cpp", 1, 0, nullptr};
    SharedMessage scheduler{}, future{};
    TestResult error{};
    __try {
        __try {
            // 0x45d10 reads the process-wide application pointer; 0x47310 copies
            // its owned scheduler. These helpers do not require a task-local TLS.
            using App = void* (__fastcall*)();
            using Scheduler = SharedMessage* (__fastcall*)(void*, SharedMessage*);
            void* app = reinterpret_cast<App>(core_address(core_base, CoreField::app_entry))();
            if (!app) return ERROR_NOT_READY;
            reinterpret_cast<Scheduler>(core_address(core_base, CoreField::scheduler_get))(app, &scheduler);
            if (!scheduler.object || !scheduler.control) return ERROR_NOT_READY;
            using Schedule = SharedMessage* (__fastcall*)(void*, SharedMessage*, Location*, std::function<void()>*, uint32_t);
            reinterpret_cast<Schedule>(core_address(core_base, CoreField::schedule))(scheduler.object, &future, &location, callable, 1);
        } __finally {
            guarded_release_shared(&future, &error);
            guarded_release_shared(&scheduler, &error);
        }
    } __except (record_exception(GetExceptionInformation(), &error)) {
    }
    if (error.exception) {
        write_report(config, error, "WXNATIVE_TEST_3");
        return 5;
    }
    return 0;
}

struct TextConfig { TestConfig base; char target[128]; };
static_assert(sizeof(TextConfig) == 1856, "Unexpected text config ABI");
static bool valid_target(const char* target) {
    size_t length = strnlen_s(target, 128);
    if (!length || length >= 128) return false;
    for (size_t index = 0; index < length; ++index) {
        unsigned char ch = target[index];
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
              (ch >= '0' && ch <= '9') || ch == '_' || ch == '.' || ch == '@' || ch == '-')) return false;
    }
    return true;
}

struct TextCompletion {
    volatile LONG references;
    uint32_t padding;
    void (__fastcall* invoke)(TextCompletion*, const unsigned char*);
    void (__fastcall* destroy)(TextCompletion*);
    TextConfig config;
    char members[1024];
};

static void __fastcall text_completion_invoke(TextCompletion* self, const unsigned char* result) {
 _InterlockedIncrement(&active_callbacks);
 __try { report_status(self->config.base, result && result[0] == 1 ? "sent" : "unknown"); }
 __finally { _InterlockedDecrement(&active_callbacks); }
}
static void __fastcall text_completion_destroy(TextCompletion* self) {
    delete self;
    _InterlockedDecrement(&live_completions);
}

static DWORD send_text_on_task(const TextConfig* config) {
    if (!config || live_completions != 0) return ERROR_BUSY;
    struct TextRequest { SmallString receiver; SmallString body; } request{};
    request.receiver.capacity = request.body.capacity = 15;
    SmallString description{};
    description.capacity = 15;
    if (!set_native_string(&request.receiver, config->target) ||
        !set_native_string(&request.body, config->base.text) ||
        !set_inline(&description, "wxbot.text")) {
        free_native_string(&request.receiver);
        free_native_string(&request.body);
        return ERROR_INVALID_PARAMETER;
    }
    TextCompletion* completion = new TextCompletion{1, 0, text_completion_invoke, text_completion_destroy, *config, {}};
    _InterlockedIncrement(&live_completions);
    TestResult report{};
    if (!identity_matches(config->base)) {
        text_completion_destroy(completion);
        free_native_string(&request.receiver); free_native_string(&request.body);
        report_status(config->base, "failed"); return ERROR_NOT_READY;
    }
    __try {
        using Builder = void (__fastcall*)(void*, TextRequest*, SmallString*, TextCompletion**);
        reinterpret_cast<Builder>(core_address(core_base, CoreField::text_builder))(nullptr, &request, &description, &completion);
        report.stage = 20;
    } __except (record_exception(GetExceptionInformation(), &report)) {
        write_report(config->base, report, "WXBOT_TEXT_SEND_EXCEPTION");
    }
    if (completion) text_completion_destroy(completion);
    // 好友和群 ID 通常超过 SSO 的 15 字节，必须释放两份请求字符串。
    free_native_string(&request.receiver);
    free_native_string(&request.body);
    return report.exception ? 5 : 0;
}
struct TextTaskLease {
    TextConfig config;
    explicit TextTaskLease(const TextConfig& value) : config(value) { _InterlockedIncrement(&live_tasks); }
    ~TextTaskLease() { _InterlockedDecrement(&live_tasks); }
};

extern "C" __declspec(dllexport) DWORD WINAPI WxSendText(void* parameter) {
    if (!parameter || live_completions != 0 || live_tasks != 0) return ERROR_BUSY;
    const TextConfig config = *static_cast<const TextConfig*>(parameter);
    if (config.base.size != sizeof(TextConfig) || config.base.pid != GetCurrentProcessId() ||
        !valid_target(config.target)) return ERROR_INVALID_PARAMETER;
    size_t length = strnlen_s(config.base.text, sizeof(config.base.text));
    if (!length || length >= sizeof(config.base.text) ||
        !MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, config.base.text, static_cast<int>(length), nullptr, 0))
        return ERROR_INVALID_PARAMETER;
    HMODULE core = GetModuleHandleW(L"Weixin.dll");
    if (!core || !core_fingerprint(core)) return 4;
    core_base = reinterpret_cast<uintptr_t>(core);
    if (!identity_matches(config.base)) return ERROR_NOT_READY;
    auto lease = std::make_shared<TextTaskLease>(config);
    std::function<void()> task = [lease]() {
        _InterlockedIncrement(&active_callbacks);
        __try { send_text_on_task(&lease->config); }
        __finally { _InterlockedDecrement(&active_callbacks); }
    };
    return schedule_task(config.base, &task);
}
extern "C" __declspec(dllexport) DWORD WINAPI WxWaitForTasks(void*) {
 return active_callbacks || live_completions || live_tasks ? ERROR_BUSY : 0;
}
BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
 if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(module);
 return TRUE;
}

