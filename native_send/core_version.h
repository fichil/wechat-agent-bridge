#pragma once
// Canonical RVAs name the verified 4.1.15.13 targets. Select only a complete,
// hash-verified build, then resolve every call, vtable check and hook cleanup.
struct CoreBuild {
    LONG id;
    uint64_t bytes;
    uint32_t timestamp, image_size;
    unsigned char sha256[32];
};
static constexpr CoreBuild core_builds[] = {
{2,204305968ull,0x6ad083c8,0xc34f000,{0xe0,0x42,0x8a,0x7b,0x69,0x18,0x53,0xde,0x49,0x65,0x32,0x52,0x3c,0x2c,0x09,0xaa,0x09,0x5f,0x90,0x94,0x1c,0x37,0xc9,0xf4,0xcf,0xb3,0xf1,0x37,0xf3,0xc6,0x65,0x56}},
{2,204305968ull,0x6ad083c8,0xc34f000,{0x93,0x2d,0xd6,0x70,0x0f,0x22,0xe3,0x2f,0xae,0xb9,0xb0,0xcc,0x95,0x81,0xa1,0xf6,0x01,0xe6,0x0a,0xce,0x15,0x10,0x67,0x68,0x0d,0x3f,0x65,0x96,0x49,0x6f,0xab,0x99}}
};
static volatile LONG core_profile_id = 0;
static bool core_size_supported(uint64_t bytes) {
    for (const auto& build: core_builds) if (build.bytes == bytes) return true;
    return false;
}
static bool core_select_build(uint64_t bytes, const unsigned char* digest,
                              uint32_t timestamp, uint32_t image_size) {
    for (const auto& build: core_builds) {
        if (build.bytes == bytes && build.timestamp == timestamp && build.image_size == image_size &&
                std::equal(build.sha256, build.sha256+32, digest)) {
            InterlockedExchange(&core_profile_id, build.id);
            return true;
        }
    }
    return false;
}
static uintptr_t core_rva(uintptr_t legacy) {
 if (InterlockedCompareExchange(&core_profile_id,0,0) != 2) return 0;
 switch (legacy) {
 case 0x45d10: return 0x45d10;
 case 0x47310: return 0x47310;
 case 0x2fb010: return 0x30f450;
 case 0x54b02f0: return 0x55f0380;
 case 0x74ab89c: return 0x754bcdc;
 case 0x74ab8e0: return 0x754bd20;
 default: return 0;
 }
}
static uintptr_t core_address(uintptr_t base, uintptr_t legacy) {
 const auto rva=core_rva(legacy);
 if (!rva) RaiseException(ERROR_INVALID_DATA,0,0,nullptr);
 return base+rva;
}

