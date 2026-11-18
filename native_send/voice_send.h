#pragma once
// Voice factory ABI adapted from helper @ 3e65e4bdaff438dc616f32d412175842fa138763.
// Personal noncommercial use; see LICENSE.helper. Exact 4.2.0.9 RVAs are registry locked.
struct VoiceConfig { TextConfig base; uint32_t duration_ms; };
struct VoiceOptions { uint32_t format, codec; uint64_t timeout; uint8_t flag, padding[7]; SmallString extra; };
static_assert(sizeof(VoiceOptions) == 56, "Unexpected voice options ABI");

static SmallString voice_borrow(const char* data, size_t size) {
    SmallString value{}; value.length = size; value.capacity = size > 15 ? size : 15;
    if (size > 15) *reinterpret_cast<const char**>(value.data) = data;
    else std::memcpy(value.data, data, size);
    return value;
}

static bool valid_silk(const unsigned char* data, size_t size, uint32_t duration) {
    if (size < 13 || size > 8388608 || std::memcmp(data, "\x02#!SILK_V3", 10)) return false;
    size_t at = 10; uint32_t frames = 0;
    while (at < size) {
        if (at + 2 > size) return false;
        uint32_t length = data[at] | (static_cast<uint32_t>(data[at+1]) << 8); at += 2;
        if (!length || length > 32767 || at + length > size || ++frames > 3000) return false;
        at += length;
    }
    return frames && frames * 20 == duration;
}

static void send_voice_on_task(const VoiceConfig& input, const unsigned char* bytes, size_t size, bool probe) {
    const TextConfig& config = input.base;
    SharedMessage context{}, provider{}, manager{}, message{};
    alignas(16) unsigned char result[128]{};
    TestResult report{}; bool submitted = false, completed = false;
    char key_scope[128]{}, key_source[128]{}; uint64_t key_first = 0, key_second = 0;
    SmallString target = voice_borrow(config.target, std::strlen(config.target));
    SmallString payload = voice_borrow(reinterpret_cast<const char*>(bytes), size);
    VoiceOptions options{}; options.format = 4; options.codec = 3; options.timeout = 30000; options.extra.capacity = 15;
    __try {
        report.stage = 1;
        if (!identity_matches(config.base) || !valid_silk(bytes,size,input.duration_ms)) __leave;
        using GetContext = SharedMessage* (__fastcall*)(SharedMessage*);
        using GetService = SharedMessage* (__fastcall*)(void*, SharedMessage*);
        report.stage = 5;
        reinterpret_cast<GetContext>(core_address(core_base, CoreField::context_shared))(&context);
        if (!media_context_matches(context, config)) __leave;
        report.stage = 6;
        reinterpret_cast<GetService>(core_address(core_base, CoreField::context_provider))(context.object, &provider);
        if (!provider.object || !provider.control) __leave;
        report.stage = 7;
        reinterpret_cast<GetService>(core_address(core_base, CoreField::message_manager))(provider.object, &manager);
        if (!manager.object || !manager.control || *static_cast<uintptr_t*>(manager.object) != core_address(core_base,CoreField::manager_vtable)) __leave;
        report.stage = 8;
        using Factory = SharedMessage* (__fastcall*)(SharedMessage*, const SmallString*, const SmallString*, const uint32_t*, const VoiceOptions*);
        reinterpret_cast<Factory>(core_address(core_base,CoreField::voice_factory))(&message,&target,&payload,&input.duration_ms,&options);
        if (!message.object || !message.control || *static_cast<uintptr_t*>(message.object) != core_address(core_base,CoreField::voice_vtable)) __leave;
        report.stage = 9;
        auto object = static_cast<unsigned char*>(message.object);
        *reinterpret_cast<uint32_t*>(object+0x118) = 34;
        if (*reinterpret_cast<uint32_t*>(object+0x118) != 34 || *reinterpret_cast<uint32_t*>(object+0x778) != input.duration_ms) __leave;
        auto audio = reinterpret_cast<const SmallString*>(object+0x758);
        if (audio->length != size || audio->capacity < size || audio->capacity > 16777216) __leave;
        const char* copy = audio->capacity > 15 ? *reinterpret_cast<const char* const*>(audio->data) : audio->data;
        if (std::memcmp(copy,bytes,size)) __leave;
        report.stage = 10;
        auto receiver = reinterpret_cast<const SmallString*>(object+0xb0);
        size_t target_length = std::strlen(config.target);
        if (receiver->length != target_length || receiver->capacity < target_length || receiver->capacity > 1024) __leave;
        const char* recipient = receiver->capacity > 15 ? *reinterpret_cast<const char* const*>(receiver->data) : receiver->data;
        if (std::memcmp(recipient,config.target,target_length) || !media_context_matches(context,config)) __leave;
        if (probe) { completed = true; report.stage = 20; __leave; }
        SharedMessage transferred = message;
        _InterlockedIncrement(reinterpret_cast<volatile LONG*>(static_cast<unsigned char*>(message.control)+8));
        using Send = void* (__fastcall*)(void*, void*, SharedMessage*, uint32_t);
        submitted = true; report.stage = 16;
        reinterpret_cast<Send>(core_address(core_base,CoreField::message_send))(manager.object,result,&transferred,1);
        if (*reinterpret_cast<uint64_t*>(result) != 0) __leave;
        report.stage = 17;
        auto returned = reinterpret_cast<SharedMessage*>(result+40);
        if (!returned->object || !returned->control) __leave;
        report.stage = 18;
        auto key = static_cast<unsigned char*>(returned->object);
        if (key[0x50] == 1) {
            auto scope = reinterpret_cast<const SmallString*>(key);
            auto source = reinterpret_cast<const SmallString*>(key+0x30);
            if (scope->length < sizeof(key_scope) && scope->capacity >= scope->length && scope->capacity < 1024)
                std::memcpy(key_scope,scope->capacity > 15 ? *reinterpret_cast<const char* const*>(scope->data) : scope->data,static_cast<size_t>(scope->length));
            if (source->length < sizeof(key_source) && source->capacity >= source->length && source->capacity < 1024)
                std::memcpy(key_source,source->capacity > 15 ? *reinterpret_cast<const char* const*>(source->data) : source->data,static_cast<size_t>(source->length));
            key_first = *reinterpret_cast<const uint64_t*>(key+0x20);
            key_second = *reinterpret_cast<const uint64_t*>(key+0x28);
        }
        report.stage = 19;
        completed = !std::strcmp(key_scope,config.target) && (key_first >> 32) != 0 &&
            static_cast<uint32_t>(key_first) != 0 && key_second / 1000 == static_cast<uint32_t>(key_first) && std::strlen(key_source) == 36;
    } __except (record_exception(GetExceptionInformation(),&report)) {}
    guarded_release_shared(reinterpret_cast<SharedMessage*>(result+40),&report);
    media_free_string(reinterpret_cast<SmallString*>(result+8),&report);
    guarded_release_shared(&message,&report);
    guarded_release_shared(&manager,&report);
    guarded_release_shared(&provider,&report);
    guarded_release_shared(&context,&report);
    media_report(config,report,completed && !report.exception ? probe ? "probe_ok" : "accepted" : submitted || report.exception ? "unknown" : "failed",34,key_first,key_second,key_source);
}

struct VoiceTaskLease {
    VoiceConfig config; std::vector<unsigned char> audio;
    explicit VoiceTaskLease(const VoiceConfig& value) : config(value) { _InterlockedIncrement(&live_tasks); }
    ~VoiceTaskLease() { _InterlockedDecrement(&live_tasks); }
};

static DWORD schedule_voice(void* parameter, bool probe) {
    if (!parameter || live_tasks || live_completions) return ERROR_BUSY;
    const VoiceConfig config = *static_cast<const VoiceConfig*>(parameter);
    if (config.base.base.size != sizeof(VoiceConfig) || config.base.base.pid != GetCurrentProcessId() || !valid_target(config.base.target) ||
        !config.duration_ms || config.duration_ms > 60000 || config.duration_ms % 20 || (probe && std::strcmp(config.base.target,"filehelper"))) return ERROR_INVALID_PARAMETER;
    HMODULE core = GetModuleHandleW(L"Weixin.dll");
    if (!core || !core_fingerprint(core) || !core_offset(CoreField::voice_factory)) return 4;
    core_base = reinterpret_cast<uintptr_t>(core);
    if (!identity_matches(config.base.base)) return ERROR_NOT_READY;
    try {
        auto lease = std::make_shared<VoiceTaskLease>(config);
        wchar_t path[1024]{};
        size_t length = strnlen_s(config.base.base.text,sizeof(config.base.base.text));
        if (!length || length >= sizeof(config.base.base.text) || MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,config.base.base.text,static_cast<int>(length),path,1023)<=0) return ERROR_INVALID_PARAMETER;
        HANDLE file = CreateFileW(path,GENERIC_READ,FILE_SHARE_READ,nullptr,OPEN_EXISTING,FILE_FLAG_OPEN_REPARSE_POINT,nullptr);
        if (file == INVALID_HANDLE_VALUE) return ERROR_INVALID_PARAMETER;
        std::unique_ptr<void,decltype(&CloseHandle)> file_guard(file,CloseHandle);
        BY_HANDLE_FILE_INFORMATION info{}; DWORD count = 0; bool read = false;
        if (GetFileInformationByHandle(file,&info) && !(info.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT|FILE_ATTRIBUTE_DIRECTORY)) && !info.nFileSizeHigh && info.nFileSizeLow && info.nFileSizeLow <= 8388608) {
            lease->audio.resize(static_cast<size_t>(info.nFileSizeLow)+1);
            read = ReadFile(file,lease->audio.data(),info.nFileSizeLow,&count,nullptr) && count == info.nFileSizeLow;
        }
        file_guard.reset();
        if (!read || !valid_silk(lease->audio.data(),count,config.duration_ms)) return ERROR_INVALID_PARAMETER;
        std::function<void()> task = [lease,probe,count]() {
            _InterlockedIncrement(&active_callbacks);
            send_voice_on_task(lease->config,lease->audio.data(),count,probe);
            _InterlockedDecrement(&active_callbacks);
        };
        return schedule_task(config.base.base,&task);
    } catch (...) { return ERROR_NOT_ENOUGH_MEMORY; }
}
extern "C" __declspec(dllexport) DWORD WINAPI WxSendVoice(void* parameter) { return schedule_voice(parameter,false); }
extern "C" __declspec(dllexport) DWORD WINAPI WxProbeVoice(void* parameter) { return schedule_voice(parameter,true); }
