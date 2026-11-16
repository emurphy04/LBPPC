// SPDX-FileCopyrightText: Copyright 2025-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <thread>
#include <unordered_map>
#include <unordered_set>

#include <miniz.h>

#include "common/serdes.h"
#include "core/emulator_settings.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/frontend/fetch_shader.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/recompiler.h"
#include "shader_recompiler/specialization.h"
#include "video_core/cache_storage.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_pipeline_cache.h"
#include "video_core/renderer_vulkan/vk_shader_util.h"
#include "video_core/shader_build.h"

namespace Serialization {
/* You should increment versions below once corresponding serialization scheme is changed. */
// bbport: above every version a release has used (1.9.2: 11, 8, 6), never back to an old one: the
// GPU rollback to 1.8.2's code took them back to 7, 7, 5, and a cache another build wrote into
// the same folder then passed as this build's.
// Projection jitter needs recompiled vertex shaders, including for existing caches.
static constexpr u32 ShaderBinaryVersion = 14u; // lbppc: 14, geometry shaders split over invocations
static constexpr u32 ShaderMetaVersion = 9u;
static constexpr u32 PipelineKeyVersion = 7u;

// bbport: shader binaries carry no version of their own, so their names do: a build with another
// version writes its binaries beside these instead of over them (where a shader's metadata
// passed its version check and then loaded the other build's binary).
static std::string ShaderBinaryName(u64 pgm_hash, size_t perm_idx) {
    return fmt::format("{:#018x}_{}_v{}", pgm_hash, perm_idx, ShaderBinaryVersion);
}
} // namespace Serialization

namespace Vulkan {

void RegisterPipelineData(const ComputePipelineKey& key,
                          ComputePipeline::SerializationSupport& sdata) {
    if (!Storage::DataBase::Instance().IsOpened()) {
        return;
    }

    Serialization::Archive ar{};
    Serialization::Writer pldata{ar};

    pldata.Write(Serialization::PipelineKeyVersion);
    pldata.Write(u32{1}); // compute

    key.Serialize(ar);
    sdata.Serialize(ar);

    Storage::DataBase::Instance().Save(Storage::BlobType::PipelineKey,
                                       fmt::format("c_{:#018x}", key.value), ar.TakeOff());
}

void RegisterPipelineData(const GraphicsPipelineKey& key, u64 hash,
                          GraphicsPipeline::SerializationSupport& sdata) {
    if (!Storage::DataBase::Instance().IsOpened()) {
        return;
    }

    Serialization::Archive ar{};
    Serialization::Writer pldata{ar};

    pldata.Write(Serialization::PipelineKeyVersion);
    pldata.Write(u32{0}); // graphics

    key.Serialize(ar);
    sdata.Serialize(ar);

    Storage::DataBase::Instance().Save(Storage::BlobType::PipelineKey,
                                       fmt::format("g_{:#018x}", hash), ar.TakeOff());
}

void RegisterShaderMeta(const Shader::Info& info,
                        const std::optional<Shader::Gcn::FetchShaderData>& fetch_shader_data,
                        const Shader::StageSpecialization& spec, size_t perm_hash,
                        size_t perm_idx) {
    if (!Storage::DataBase::Instance().IsOpened()) {
        return;
    }

    Serialization::Archive ar;
    Serialization::Writer meta{ar};

    meta.Write(Serialization::ShaderMetaVersion);
    meta.Write(Serialization::ShaderBinaryVersion);

    meta.Write(perm_hash);
    meta.Write(perm_idx);

    spec.Serialize(ar);
    info.Serialize(ar);

    Storage::DataBase::Instance().Save(Storage::BlobType::ShaderMeta,
                                       fmt::format("{:#018x}", perm_hash), ar.TakeOff());
}

void RegisterShaderBinary(std::vector<u32>&& spv, u64 pgm_hash, size_t perm_idx) {
    if (!Storage::DataBase::Instance().IsOpened()) {
        return;
    }

    Storage::DataBase::Instance().Save(Storage::BlobType::ShaderBinary,
                                       Serialization::ShaderBinaryName(pgm_hash, perm_idx),
                                       std::move(spv));
}

void RegisterAttribSharps(const Shader::Info& info,
                          const std::optional<Shader::Gcn::FetchShaderData>& fetch_shader_data,
                          size_t perm_hash) {
    if (!Storage::DataBase::Instance().IsOpened() || !fetch_shader_data ||
        fetch_shader_data->attributes.empty()) {
        return;
    }
    // u32 count, then count x (u32 key: sgpr_base << 8 | dword_offset, 16 bytes of V#).
    std::vector<u8> blob(4);
    u32 count = 0;
    for (const auto& attrib : fetch_shader_data->attributes) {
        const auto sharp = attrib.RawSharp(info);
        const u32 key = u32(attrib.sgpr_base) << 8 | attrib.dword_offset;
        const size_t at = blob.size();
        blob.resize(at + 4 + sizeof(sharp));
        std::memcpy(&blob[at], &key, 4);
        std::memcpy(&blob[at + 4], &sharp, sizeof(sharp));
        ++count;
    }
    std::memcpy(blob.data(), &count, 4);
    Storage::DataBase::Instance().Save(Storage::BlobType::ShaderMeta,
                                       fmt::format("{:#018x}_vs", perm_hash), std::move(blob));
}

bool LoadShaderMeta(Serialization::Archive& ar, Shader::Info& info,
                    std::optional<Shader::Gcn::FetchShaderData>& fetch_shader_data,
                    Shader::StageSpecialization& spec, size_t& perm_idx) {
    Serialization::Reader meta{ar};

    u32 meta_version{};
    meta.Read(meta_version);
    if (meta_version != Serialization::ShaderMetaVersion) {
        return false;
    }

    u32 binary_version{};
    meta.Read(binary_version);
    if (binary_version != Serialization::ShaderBinaryVersion) {
        return false;
    }

    u64 perm_hash_ar{};
    meta.Read(perm_hash_ar);
    meta.Read(perm_idx);

    spec.Deserialize(ar);
    info.Deserialize(ar);

    // Motion vertex shaders embed session-local buffer device addresses. They must be
    // recompiled for the current allocation, never loaded from a previous process.
    if (info.hw_stage == Shader::HwStage::Vertex && spec.runtime_info.hw.vs.motion_vectors) {
        return false;
    }

    // lbppc: the stages of a pipeline are loaded in turn into one fetch shader slot, and the
    // geometry stage comes after the vertex stage: it replaced the vertex shader's fetch shader
    // with none. Preloaded geometry shader pipelines then bound no vertex buffers, and drew
    // their vertices from garbage (LBP3: dark lines across every level, only with a warm cache).
    if (spec.fetch_shader_data) {
        fetch_shader_data = spec.fetch_shader_data;
    }
    return true;
}

void ComputePipelineKey::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer key{ar};
    key.Write(value);
}

bool ComputePipelineKey::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader key{ar};
    key.Read(value);
    return true;
}

void ComputePipeline::SerializationSupport::Serialize(Serialization::Archive& ar) const {
    // Nothing here yet
    return;
}

bool ComputePipeline::SerializationSupport::Deserialize(Serialization::Archive& ar) {
    // Nothing here yet
    return true;
}

struct PipelineCache::PreloadJob {
    bool compute{};
    ComputePipelineKey compute_key{};
    GraphicsPipelineKey graphics_key{};
    std::array<const Shader::Info*, MaxShaderStages> infos{};
    std::array<Shader::RuntimeInfo, MaxShaderStages> runtime_infos{};
    std::array<vk::ShaderModule, MaxShaderStages> modules{};
    std::optional<Shader::Gcn::FetchShaderData> fetch_shader{};
    GraphicsPipeline::SerializationSupport graphics_sdata{};
    ComputePipeline::SerializationSupport compute_sdata{};
    std::unique_ptr<GraphicsPipeline> graphics;
    std::unique_ptr<ComputePipeline> compute_pipeline;
};

bool PipelineCache::LoadComputePipeline(Serialization::Archive& ar) {
    compute_key.Deserialize(ar);

    ComputePipeline::SerializationSupport sdata{};
    sdata.Deserialize(ar);

    std::vector<u8> meta_blob;
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderMeta,
                                       fmt::format("{:#018x}", compute_key.value), meta_blob);
    if (meta_blob.empty()) {
        return false;
    }

    Serialization::Archive meta_ar{std::move(meta_blob)};

    if (!LoadPipelineStage(meta_ar, 0)) {
        return false;
    }

    const auto [it, is_new] = compute_pipelines.try_emplace(compute_key);
    ASSERT(is_new);

    if (preload_jobs) {
        auto& job = preload_jobs->emplace_back();
        job.compute = true;
        job.compute_key = compute_key;
        job.infos = sel.infos;
        job.modules = sel.modules;
        sel.infos.fill(nullptr);
        sel.modules.fill(nullptr);
        return true;
    }

    it.value() =
        std::make_unique<ComputePipeline>(instance, scheduler, desc_heap, profile, *pipeline_cache,
                                          compute_key, *sel.infos[0], sel.modules[0], sdata, true);

    sel.infos.fill(nullptr);
    sel.modules.fill(nullptr);

    // bbport: a cached pipeline the driver cannot create (a new driver, or one saved by a build
    // that stopped on it) stopped every start here. It is compiled again when the game uses it.
    if (!it.value()->IsValid()) {
        compute_pipelines.erase(compute_key);
        return false;
    }
    return true;
}

void GraphicsPipelineKey::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer key{ar};

    key.Write(this, sizeof(*this));
}

bool GraphicsPipelineKey::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader key{ar};

    key.Read(this, sizeof(*this));
    return true;
}

void GraphicsPipeline::SerializationSupport::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer sdata{ar};

    sdata.Write(&vertex_attributes, sizeof(vertex_attributes));
    sdata.Write(&vertex_bindings, sizeof(vertex_bindings));
    sdata.Write(&divisors, sizeof(divisors));
    sdata.Write(multisampling);
    sdata.Write(tcs);
    sdata.Write(tes);
}

bool GraphicsPipeline::SerializationSupport::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader sdata{ar};

    sdata.Read(&vertex_attributes, sizeof(vertex_attributes));
    sdata.Read(&vertex_bindings, sizeof(vertex_bindings));
    sdata.Read(&divisors, sizeof(divisors));
    sdata.Read(multisampling);
    sdata.Read(tcs);
    sdata.Read(tes);
    return true;
}

bool PipelineCache::LoadGraphicsPipeline(Serialization::Archive& ar) {
    // bbport: a pipeline that failed to load (a stale stage) returned with the stages it had
    // already loaded still selected, and the next one kept them for the stages it does not have:
    // a shadow pipeline (no fragment shader) was built with another pipeline's fragment shader,
    // and cast striped shadows (Chalice Dungeons, NVIDIA, a cache with stale entries).
    sel.infos.fill(nullptr);
    sel.modules.fill(nullptr);
    sel.fetch_shader.reset();
    sel.graphics_key.Deserialize(ar);

    // bbport: motion-vector pipelines are never preloaded. Their vertex shader embeds session-
    // local buffer addresses (see LoadShaderMeta), and the shader metadata is stored once per
    // program, not per permutation: the meta found for a motion pipeline's stages could be the
    // plain permutation, so LoadShaderMeta's motion check missed it and the preload built the
    // 8-attachment motion key with plain shaders. The AMD driver refused some of these
    // (ErrorUnknown: the startup crash), and the ones it accepted were registered under the
    // motion key without motion output. Built at first use instead, like the vertex shaders.
    if (sel.graphics_key.motion_vectors) {
        return false;
    }

    GraphicsPipeline::SerializationSupport sdata{};
    sdata.Deserialize(ar);

    for (int stage_idx = 0; stage_idx < MaxShaderStages; ++stage_idx) {
        const auto& hash = sel.graphics_key.stage_hashes[stage_idx];
        if (!hash) {
            continue;
        }

        std::vector<u8> meta_blob;
        Storage::DataBase::Instance().Load(Storage::BlobType::ShaderMeta,
                                           fmt::format("{:#018x}", hash), meta_blob);
        if (meta_blob.empty()) {
            return false;
        }

        Serialization::Archive meta_ar{std::move(meta_blob)};

        if (!LoadPipelineStage(meta_ar, stage_idx)) {
            return false;
        }
    }

    const auto [it, is_new] = graphics_pipelines.try_emplace(sel.graphics_key);
    ASSERT(is_new);

    if (preload_jobs) {
        auto& job = preload_jobs->emplace_back();
        job.graphics_key = sel.graphics_key;
        job.infos = sel.infos;
        job.runtime_infos = sel.runtime_infos;
        job.modules = sel.modules;
        job.fetch_shader = sel.fetch_shader;
        job.graphics_sdata = sdata;
        sel.infos.fill(nullptr);
        sel.modules.fill(nullptr);
        sel.fetch_shader.reset();
        return true;
    }

    it.value() = std::make_unique<GraphicsPipeline>(
        instance, scheduler, desc_heap, profile, sel.graphics_key, *pipeline_cache, sel.infos,
        sel.runtime_infos, sel.fetch_shader, sel.modules, sdata, true);

    sel.infos.fill(nullptr);
    sel.modules.fill(nullptr);
    sel.fetch_shader.reset();

    // bbport: as for compute pipelines, a failed one is left to compile when the game uses it.
    if (!it.value()->IsValid()) {
        graphics_pipelines.erase(sel.graphics_key);
        return false;
    }
    return true;
}

bool PipelineCache::LoadPipelineStage(Serialization::Archive& ar, size_t stage) {
    auto program = std::make_unique<Program>();
    Shader::StageSpecialization spec{};
    spec.info = &program->info;
    size_t perm_idx{};
    if (!LoadShaderMeta(ar, program->info, sel.fetch_shader, spec, perm_idx)) {
        return false;
    }

    std::vector<u32> spv{};
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderBinary,
                                       Serialization::ShaderBinaryName(program->info.pgm_hash, perm_idx),
                                       spv);
    if (spv.empty()) {
        return false;
    }

    // Permutation hash depends on shader variation index. To prevent collisions, we need insert it
    // at the exact position rather than append

    vk::ShaderModule module{};

    auto [it_pgm, new_program] = program_cache.try_emplace(program->info.pgm_hash);
    if (new_program) {
        module = CompileSPV(spv, instance.GetDevice());
        it_pgm.value() = std::move(program);
    } else {
        const auto& it = std::ranges::find(it_pgm.value()->modules, spec, &Program::Module::spec);
        if (it != it_pgm.value()->modules.end()) {
            // A matching permutation is valid only at its original index. A different index means
            // the store holds entries from more than one cache generation, so this pipeline is
            // left to compile at runtime.
            const auto idx = std::distance(it_pgm.value()->modules.begin(), it);
            if (perm_idx != idx) {
                LOG_WARNING(Render_Vulkan,
                            "Cached permutation {} of {}_{:x} conflicts with index {}, skipping "
                            "preload",
                            perm_idx, program->info.hw_stage, program->info.pgm_hash, idx);
                return false;
            }
            module = it->module;
        } else {
            module = CompileSPV(spv, instance.GetDevice());
        }
    }
    it_pgm.value()->InsertPermut(module, std::move(spec), perm_idx);

    sel.infos[stage] = &it_pgm.value()->info;
    sel.modules[stage] = module;

    return true;
}

namespace {

// lbppc: the shader list (tools/shader_list.py writes it), little-endian:
//   "LBPSLST1", u32 meta version, u32 binary version, u32 pipeline key version,
//   u32 n, n x shader location: u64 program hash, str file (in the game folder), u8[20] SHA-1 of
//       the archive resource holding it (all zero: the file itself), u32 offset of its footer
//       (in the unpacked resource or the file),
//   u32 n, n x shader metadata: str name, blob,
//   u32 n, n x pipeline key: str name, blob
// (str: u16 length + bytes; blob: u32 length + bytes). Metadata and keys are cache blobs as this
// build writes them; the shaders' code comes from the player's files.
struct ListReader {
    const u8* p;
    const u8* end;
    bool ok = true;

    template <typename T>
    T Get() {
        T value{};
        if (size_t(end - p) < sizeof(T)) {
            ok = false;
            return value;
        }
        std::memcpy(&value, p, sizeof(T));
        p += sizeof(T);
        return value;
    }
    std::string Str() {
        const u16 n = Get<u16>();
        if (!ok || size_t(end - p) < n) {
            ok = false;
            return {};
        }
        std::string s(reinterpret_cast<const char*>(p), n);
        p += n;
        return s;
    }
    std::vector<u8> Blob() {
        const u32 n = Get<u32>();
        if (!ok || size_t(end - p) < n) {
            ok = false;
            return {};
        }
        std::vector<u8> b(p, p + n);
        p += n;
        return b;
    }
};

struct ShaderLocation {
    std::string file;
    std::array<u8, 20> sha1;
    u32 footer;
};

std::vector<u8> ReadWholeFile(const std::filesystem::path& path) {
    std::ifstream in{path, std::ios::binary};
    if (!in) {
        return {};
    }
    in.seekg(0, std::ios::end);
    std::vector<u8> data(size_t(in.tellg()));
    in.seekg(0);
    in.read(reinterpret_cast<char*>(data.data()), std::streamsize(data.size()));
    return in ? data : std::vector<u8>{};
}

// LittleBigPlanet resources are zlib streams after a small header: every stream found, unpacked
// and joined (as tools/shader_list.py does, so the offsets it records match).
std::vector<u8> UnpackResource(const std::vector<u8>& data) {
    std::vector<u8> out;
    size_t i = 0;
    std::vector<u8> chunk;
    while (i + 2 <= data.size()) {
        const u8* found = nullptr;
        for (size_t j = i; j + 1 < data.size(); ++j) {
            if (data[j] == 0x78 && data[j + 1] == 0xda) {
                found = &data[j];
                i = j;
                break;
            }
        }
        if (!found) {
            break;
        }
        mz_stream s{};
        if (mz_inflateInit(&s) != MZ_OK) {
            break;
        }
        s.next_in = found;
        s.avail_in = mz_uint32(data.size() - i);
        chunk.clear();
        int status = MZ_OK;
        u8 buffer[65536];
        while (status == MZ_OK) {
            s.next_out = buffer;
            s.avail_out = sizeof(buffer);
            status = mz_inflate(&s, MZ_NO_FLUSH);
            chunk.insert(chunk.end(), buffer, buffer + (sizeof(buffer) - s.avail_out));
            if (status == MZ_BUF_ERROR && s.avail_in == 0) {
                break; // the data ends inside the stream: keep what it gave
            }
        }
        const size_t used = s.total_in;
        mz_inflateEnd(&s);
        if (status == MZ_STREAM_END || (status == MZ_BUF_ERROR && s.avail_in == 0)) {
            out.insert(out.end(), chunk.begin(), chunk.end());
            if (status != MZ_STREAM_END) {
                break;
            }
            i += used;
        } else {
            ++i; // not a stream after all
        }
    }
    return out;
}

/// The player's game files, read as the shaders are needed: archive tables, unpacked resources.
class GameFiles {
public:
    /// `update`: the 1.28 update's own folder (empty when it is in the game's): its files replace the game's.
    GameFiles(std::filesystem::path game_, std::filesystem::path update_)
        : game{std::move(game_)}, update{std::move(update_)} {}

    /// The bytes that hold a shader: the unpacked resource, or the whole file. Null if missing.
    const std::vector<u8>* Source(const ShaderLocation& where) {
        const bool raw = std::all_of(where.sha1.begin(), where.sha1.end(), [](u8 b) { return !b; });
        std::string id = where.file;
        if (!raw) {
            id.append(reinterpret_cast<const char*>(where.sha1.data()), where.sha1.size());
        }
        if (const auto it = sources.find(id); it != sources.end()) {
            return it->second.empty() ? nullptr : &it->second;
        }
        auto& slot = sources[id];
        if (raw) {
            slot = ReadWholeFile(Path(where.file));
        } else if (const auto entry = FindResource(where.file, where.sha1)) {
            std::vector<u8> packed(entry->second);
            std::ifstream in{Path(where.file), std::ios::binary};
            in.seekg(std::streamoff(entry->first));
            in.read(reinterpret_cast<char*>(packed.data()), std::streamsize(packed.size()));
            if (in) {
                slot = UnpackResource(packed);
            }
        }
        if (sources.size() > 64 && !raw) {
            Trim(id);
        }
        return slot.empty() ? nullptr : &slot;
    }

private:
    using Sha1 = std::array<u8, 20>;
    struct Sha1Hash {
        size_t operator()(const Sha1& s) const {
            size_t h;
            std::memcpy(&h, s.data(), sizeof(h));
            return h;
        }
    };
    using Table = std::unordered_map<Sha1, std::pair<u64, u32>, Sha1Hash>;

    std::optional<std::pair<u64, u32>> FindResource(const std::string& file, const Sha1& sha1) {
        auto [it, is_new] = tables.try_emplace(file);
        if (is_new) {
            // The table is at the end: n entries of SHA-1, offset and size (big-endian), then n
            // and "FARC".
            std::ifstream in{Path(file), std::ios::binary};
            in.seekg(0, std::ios::end);
            const auto size = u64(in.tellg());
            u8 tail[8];
            if (size >= 8 && in.seekg(std::streamoff(size - 8)) &&
                in.read(reinterpret_cast<char*>(tail), 8) && std::memcmp(tail + 4, "FARC", 4) == 0) {
                const u32 n = u32(tail[0]) << 24 | u32(tail[1]) << 16 | u32(tail[2]) << 8 | tail[3];
                if (u64(n) * 28 + 8 <= size) {
                    std::vector<u8> table(size_t(n) * 28);
                    in.seekg(std::streamoff(size - 8 - table.size()));
                    in.read(reinterpret_cast<char*>(table.data()), std::streamsize(table.size()));
                    const auto be = [](const u8* b) {
                        return u32(b[0]) << 24 | u32(b[1]) << 16 | u32(b[2]) << 8 | b[3];
                    };
                    for (u32 k = 0; in && k < n; ++k) {
                        const u8* e = &table[size_t(k) * 28];
                        Sha1 key;
                        std::memcpy(key.data(), e, 20);
                        it->second[key] = {be(e + 20), be(e + 24)};
                    }
                }
            }
        }
        const auto found = it->second.find(sha1);
        if (found == it->second.end()) {
            return std::nullopt;
        }
        return found->second;
    }

    void Trim(const std::string& keep) {
        // Shaders are listed by resource, so a resource is rarely needed again once passed.
        for (auto it = sources.begin(); it != sources.end();) {
            it = it->first != keep && it->first.size() > 20 ? sources.erase(it) : std::next(it);
        }
    }

    /// Where a game file is read from, as the game reads it: the update's copy, else the game's.
    std::filesystem::path Path(const std::string& file) const {
        std::error_code error;
        if (!update.empty() && std::filesystem::is_regular_file(update / file, error)) {
            return update / file;
        }
        return game / file;
    }

    std::filesystem::path game, update;
    std::unordered_map<std::string, Table> tables;
    std::unordered_map<std::string, std::vector<u8>> sources;
};

/// A shader's code from the bytes holding it: its footer ("OrbShdr", the program hash at +16)
/// at the recorded offset, or found by the hash; the code starts (length/8 - 1 in its first
/// instruction, s_mov_b32 vcc_hi) that far before the footer.
std::vector<u32> ShaderCode(const std::vector<u8>& data, u32 footer_at, u64 hash) {
    const auto footer_ok = [&](size_t at) {
        u64 h;
        return at + 24 <= data.size() && std::memcmp(&data[at], "OrbShdr", 7) == 0 &&
               (std::memcpy(&h, &data[at + 16], 8), h == hash);
    };
    size_t footer = footer_at;
    if (!footer_ok(footer)) {
        static constexpr std::string_view sig{"OrbShdr"};
        footer = SIZE_MAX;
        for (auto it = std::search(data.begin(), data.end(), sig.begin(), sig.end());
             it != data.end(); it = std::search(it + 1, data.end(), sig.begin(), sig.end())) {
            if (footer_ok(size_t(it - data.begin()))) {
                footer = size_t(it - data.begin());
                break;
            }
        }
        if (footer == SIZE_MAX) {
            return {};
        }
    }
    for (size_t length = 8; length <= footer && length <= (4u << 20); length += 8) {
        u32 head[2];
        std::memcpy(head, &data[footer - length], 8);
        if (head[0] == 0xBEEB03FF && (u64(head[1]) + 1) * 8 == length) {
            // The code is the footer's length (as GetParams takes it in play); what follows it,
            // up to the footer, is other data.
            u32 bits;
            std::memcpy(&bits, &data[footer + 8], 4);
            const size_t code_bytes = bits >> 8;
            if (code_bytes == 0 || code_bytes > length || code_bytes % 4) {
                return {};
            }
            std::vector<u32> code(code_bytes / 4);
            std::memcpy(code.data(), &data[footer - length], code_bytes);
            return code;
        }
    }
    return {};
}

/// Whether a translation found the shader's resources as recorded (else the recorded
/// metadata does not describe this translation, and it is left to compile in play).
/// (Counts, not bytes: the resources hold padding, which differs between two translations that
/// give the same SPIR-V: 368 of 368 compared were identical to the ones made in play.)
bool SameResources(const Shader::Info& a, const Shader::Info& b) {
    return a.ud_mask.mask == b.ud_mask.mask && a.buffers.size() == b.buffers.size() &&
           a.images.size() == b.images.size() && a.samplers.size() == b.samplers.size() &&
           a.fmasks.size() == b.fmasks.size() &&
           a.srt_info.flattened_bufsize_dw == b.srt_info.flattened_bufsize_dw;
}

} // namespace

std::vector<u32> PipelineCache::TranslateRecorded(std::span<const u32> code,
                                                 Shader::Info& recorded,
                                                 Shader::StageSpecialization& spec,
                                                 const Shader::Gcn::RecordedSharps* sharps) {
    // Only the stages whose translation reads nothing else from the game's memory (a geometry
    // shader reads its copy shader there, its export stage the ring setup): the rest compile in
    // play as before.
    if (recorded.hw_stage != Shader::HwStage::Vertex &&
        recorded.hw_stage != Shader::HwStage::Fragment &&
        recorded.hw_stage != Shader::HwStage::Compute) {
        return {};
    }
    // A vertex shader reads its attributes' V#s from the game's memory: only as recorded.
    if (spec.fetch_shader_data && !spec.fetch_shader_data->attributes.empty() && !sharps) {
        return {};
    }
    // The user data and resource descriptors as recorded (the flattened buffer starts with the
    // user data); pointers in them are not followed (fetch shader and walk replaced).
    std::array<u32, Shader::ShaderParams::NumShaderUserData> user_data{};
    std::copy_n(recorded.flattened_ud_buf.begin(),
                std::min(recorded.flattened_ud_buf.size(), user_data.size()), user_data.begin());
    const Shader::ShaderParams params{.user_data = user_data, .code = code,
                                      .hash = recorded.pgm_hash};
    Shader::Info info{recorded.hw_stage, recorded.sw_stage, params};
    auto runtime_info = spec.runtime_info;
    auto binding = spec.start;
    Shader::Gcn::g_fetch_shader_override = &spec.fetch_shader_data;
    Shader::Info::offline_flat_buf = &recorded.flattened_ud_buf;
    Shader::Gcn::g_attrib_sharp_override = sharps;
    std::vector<u32> spv;
    {
        const auto program = Shader::TranslateProgram(code, pools, info, runtime_info, profile);
        spv = Shader::Backend::SPIRV::EmitSPIRV(profile, runtime_info, program, binding);
    }
    Shader::Info::offline_flat_buf = nullptr;
    Shader::Gcn::g_fetch_shader_override = nullptr;
    Shader::Gcn::g_attrib_sharp_override = nullptr;
    if (!spv.empty() && !SameResources(info, recorded)) {
        static const bool keep_all = [] {
            const char* env = std::getenv("BB_SHADER_LIST_DEBUG");
            return env && env[0] == '1';
        }();
        std::printf("Shaders: %#018llx translated with other resources than recorded%s\n",
                    static_cast<unsigned long long>(recorded.pgm_hash),
                    keep_all ? " (kept: BB_SHADER_LIST_DEBUG)" : "");
        if (!keep_all) {
            spv.clear();
        }
    }
    return spv;
}

void PipelineCache::InstallShaderList() {
    const char* list_env = std::getenv("BB_SHADER_LIST");
    const char* app0 = std::getenv("BB_APP0");
    if (!list_env || !*list_env || !app0 || !*app0) {
        return;
    }
    const auto list = ReadWholeFile(list_env);
    if (list.size() < 20 || std::memcmp(list.data(), "LBPSLST2", 8) != 0) {
        std::printf("Shaders: no shader list at %s\n", list_env);
        return;
    }
    // Done once per list when everything in it was prepared; its fingerprint is kept beside the
    // cache's profile (a new GPU or driver clears the cache, this mark with it).
    u64 fingerprint = 0xcbf29ce484222325ULL;
    for (const u8 b : list) {
        fingerprint = (fingerprint ^ b) * 0x100000001b3ULL;
    }
    const std::string mark = fmt::format("{:016x}", fingerprint);
    std::vector<u8> installed{};
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderProfile, "shader_list", installed);
    if (std::string(installed.begin(), installed.end()) == mark) {
        return;
    }

    ListReader r{list.data() + 8, list.data() + list.size()};
    const u32 meta_version = r.Get<u32>(), binary_version = r.Get<u32>(),
              key_version = r.Get<u32>();
    if (meta_version != Serialization::ShaderMetaVersion ||
        binary_version != Serialization::ShaderBinaryVersion ||
        key_version != Serialization::PipelineKeyVersion) {
        std::printf("Shaders: the shader list is for another build (versions %u %u %u)\n",
                    meta_version, binary_version, key_version);
        return;
    }
    std::unordered_map<u64, ShaderLocation> where;
    for (u32 n = r.Get<u32>(); r.ok && n--;) {
        const u64 hash = r.Get<u64>();
        ShaderLocation location{.file = r.Str()};
        for (auto& b : location.sha1) {
            b = r.Get<u8>();
        }
        location.footer = r.Get<u32>();
        where.emplace(hash, std::move(location));
    }
    std::vector<std::pair<std::string, std::vector<u8>>> metas, keys;
    for (u32 n = r.Get<u32>(); r.ok && n--;) {
        auto name = r.Str();
        metas.emplace_back(std::move(name), r.Blob());
    }
    for (u32 n = r.Get<u32>(); r.ok && n--;) {
        auto name = r.Str();
        keys.emplace_back(std::move(name), r.Blob());
    }
    for (u32 n = r.Get<u32>(); r.ok && n--;) {
        const u64 perm_hash = r.Get<u64>();
        const auto blob = r.Blob();
        u32 count = 0;
        if (blob.size() >= 4) {
            std::memcpy(&count, blob.data(), 4);
        }
        if (blob.size() != 4 + size_t(count) * 20) {
            continue;
        }
        auto& sharps = list_sharps[perm_hash];
        for (u32 i = 0; i < count; ++i) {
            std::pair<u32, std::array<u32, 4>> entry;
            std::memcpy(&entry.first, &blob[4 + i * 20], 4);
            std::memcpy(entry.second.data(), &blob[8 + i * 20], 16);
            sharps.push_back(entry);
        }
    }
    if (!r.ok) {
        std::printf("Shaders: the shader list is damaged\n");
        return;
    }
    // The code of programs the game makes in memory, kept the first time it used them (beside
    // the cache files, so a rebuilt cache keeps it).
    list_code_dir = Storage::DataBase::Instance().DriverCachePath().parent_path() / "code";

    // Shaders: in list order (grouped by resource), each translated from the player's files.
    const auto start = std::chrono::steady_clock::now();
    const char* app0_update = std::getenv("BB_APP0_UPDATE");
    GameFiles files{app0, app0_update ? app0_update : ""};
    u32 done = 0, translated = 0, present = 0, waiting = 0, different = 0;
    const size_t step = std::max<size_t>(metas.size() / 50, 16);
    std::printf("Shaders: preparing %u of %zu\n", 0u, metas.size());
    std::fflush(stdout);
    for (auto& [name, blob] : metas) {
        if (++done % step == 0) {
            std::printf("Shaders: preparing %u of %zu\n", done, metas.size());
            std::fflush(stdout);
        }
        Shader::Info recorded{};
        std::optional<Shader::Gcn::FetchShaderData> fetch{};
        Shader::StageSpecialization spec{};
        spec.info = &recorded;
        size_t perm_idx{};
        u64 perm_hash{};
        if (blob.size() < 24) {
            continue;
        }
        std::memcpy(&perm_hash, blob.data() + 8, 8); // after the meta and binary versions
        {
            Serialization::Archive ar{std::vector<u8>(blob)};
            if (!LoadShaderMeta(ar, recorded, fetch, spec, perm_idx)) {
                continue; // motion-vector shaders, built in play
            }
        }
        const auto spv_name = Serialization::ShaderBinaryName(recorded.pgm_hash, perm_idx);
        std::vector<u8> have_meta, have_spv;
        Storage::DataBase::Instance().Load(Storage::BlobType::ShaderMeta, name, have_meta);
        Storage::DataBase::Instance().Load(Storage::BlobType::ShaderBinary, spv_name, have_spv);
        if (!have_meta.empty() && have_meta != blob) {
            continue; // the cache has its own permutation by this name: not mixed with the list
        }
        list_metas[recorded.pgm_hash].push_back(blob);
        if (!have_spv.empty()) {
            list_ready.insert(perm_hash); // this cache already has it
            ++present;
            continue;
        }
        const auto location = where.find(recorded.pgm_hash);
        const std::vector<u8>* source =
            location != where.end() ? files.Source(location->second) : nullptr;
        auto code = source ? ShaderCode(*source, location->second.footer, recorded.pgm_hash)
                           : std::vector<u32>{};
        if (code.empty()) {
            const auto kept =
                ReadWholeFile(list_code_dir / fmt::format("{:016x}.bin", recorded.pgm_hash));
            code.resize(kept.size() / 4);
            std::memcpy(code.data(), kept.data(), code.size() * 4);
        }
        if (code.empty()) {
            ++waiting; // made by the game in memory: prepared when it first uses it
            continue;
        }
        const auto sharps = list_sharps.find(perm_hash);
        auto spv = TranslateRecorded(code, recorded, spec,
                                     sharps != list_sharps.end() ? &sharps->second : nullptr);
        if (spv.empty()) {
            ++different;
            continue;
        }
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderMeta, name,
                                           std::vector<u8>(blob));
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderBinary, spv_name,
                                           std::move(spv));
        list_ready.insert(perm_hash);
        ++translated;
    }
    std::printf("Shaders: preparing %zu of %zu\n", metas.size(), metas.size());

    // Pipelines whose every stage is ready go in the cache (the preload builds them); the rest
    // wait for their programs.
    u32 pipelines = 0;
    for (auto& [name, blob] : keys) {
        Serialization::Archive ar{std::vector<u8>(blob)};
        Serialization::Reader pldata{ar};
        u32 version{}, is_compute{};
        pldata.Read(version);
        pldata.Read(is_compute);
        if (version != Serialization::PipelineKeyVersion) {
            continue;
        }
        bool complete = true;
        if (is_compute) {
            ComputePipelineKey key{};
            key.Deserialize(ar);
            complete = list_ready.contains(key.value);
        } else {
            GraphicsPipelineKey key{};
            key.Deserialize(ar);
            for (const auto hash : key.stage_hashes) {
                complete &= !hash || list_ready.contains(hash);
            }
        }
        if (complete) {
            Storage::DataBase::Instance().Save(Storage::BlobType::PipelineKey, name,
                                               std::vector<u8>(blob));
            ++pipelines;
        } else if (!is_compute) {
            list_keys.push_back(std::move(blob));
        }
    }
    if (list_keys.empty() && waiting == 0) {
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderProfile, "shader_list",
                                           std::vector<u8>(mark.begin(), mark.end()));
    }
    Storage::DataBase::Instance().Flush();
    std::printf("Shaders: list prepared in %.1f s: %u shaders translated from the game files, %u "
                "already cached, %u waiting for the game, %u not as recorded; %u of %zu "
                "pipelines ready, %zu waiting\n",
                std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count(),
                translated, present, waiting, different, pipelines, keys.size(), list_keys.size());
    std::fflush(stdout);
}

void PipelineCache::InstallListedProgram(const Shader::ShaderParams& params) {
    const auto it = list_metas.find(params.hash);
    if (it == list_metas.end()) {
        return;
    }
    auto blobs = std::move(it->second);
    list_metas.erase(it);
    const auto start = std::chrono::steady_clock::now();
    // The selection is half made for the draw that met this program: kept as it is.
    const auto infos = sel.infos;
    const auto modules = sel.modules;
    const auto fetch_shader = sel.fetch_shader;
    u32 translated = 0, loaded = 0;
    bool keep_code = false;
    for (auto& blob : blobs) {
        u64 perm_hash{};
        std::memcpy(&perm_hash, blob.data() + 8, 8);
        if (list_ready.contains(perm_hash)) {
            continue;
        }
        Shader::Info recorded{};
        std::optional<Shader::Gcn::FetchShaderData> fetch{};
        Shader::StageSpecialization spec{};
        spec.info = &recorded;
        size_t perm_idx{};
        Serialization::Archive ar{std::vector<u8>(blob)};
        if (!LoadShaderMeta(ar, recorded, fetch, spec, perm_idx)) {
            continue;
        }
        const auto sharps = list_sharps.find(perm_hash);
        auto spv = TranslateRecorded(params.code, recorded, spec,
                                     sharps != list_sharps.end() ? &sharps->second : nullptr);
        if (spv.empty()) {
            continue;
        }
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderMeta,
                                           fmt::format("{:#018x}", perm_hash),
                                           std::vector<u8>(blob));
        Storage::DataBase::Instance().Save(
            Storage::BlobType::ShaderBinary,
            Serialization::ShaderBinaryName(recorded.pgm_hash, perm_idx), std::move(spv));
        list_ready.insert(perm_hash);
        keep_code = true;
        ++translated;
    }
    Storage::DataBase::Instance().Flush();
    {
        // Into the program cache at the recorded indices, as the preload puts them.
        std::unique_lock lk{programs_mutex};
        for (auto& blob : blobs) {
            u64 perm_hash{};
            std::memcpy(&perm_hash, blob.data() + 8, 8);
            if (list_ready.contains(perm_hash)) {
                Serialization::Archive ar{std::vector<u8>(blob)};
                loaded += LoadPipelineStage(ar, 0) ? 1 : 0;
            }
        }
    }
    sel.infos = infos;
    sel.modules = modules;
    sel.fetch_shader = fetch_shader;
    if (keep_code && !list_code_dir.empty()) {
        // For later starts (and a rebuilt cache): this program prepared before play.
        std::error_code ec;
        std::filesystem::create_directories(list_code_dir, ec);
        std::ofstream out{list_code_dir / fmt::format("{:016x}.bin", params.hash),
                          std::ios::binary};
        out.write(reinterpret_cast<const char*>(params.code.data()),
                  std::streamsize(params.code.size_bytes()));
    }
    list_build_pending = loaded > 0;
    std::printf("Shaders: program %#018llx first used: %u of %zu listed permutations translated "
                "from memory, %u in use (%.1f ms)\n",
                static_cast<unsigned long long>(params.hash), translated, blobs.size(), loaded,
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() -
                                                          start)
                    .count());
}

void PipelineCache::BuildListedPipelines() {
    list_build_pending = false;
    const auto start = std::chrono::steady_clock::now();
    const auto saved = sel;
    std::vector<PreloadJob> jobs;
    // A few per draw lookup (each takes ~0.5 ms here): the first use of the game's main vertex
    // shader completed 329 at once, a 185 ms frame.
    constexpr size_t MaxPerLookup = 24;
    bool more = false;
    {
        std::unique_lock lk{programs_mutex};
        preload_jobs = &jobs;
        sel.runtime_infos = {};
        std::erase_if(list_keys, [&](const std::vector<u8>& blob) {
            if (jobs.size() >= MaxPerLookup) {
                more = true;
                return false;
            }
            Serialization::Archive ar{std::vector<u8>(blob)};
            Serialization::Reader pldata{ar};
            u32 version{}, is_compute{};
            pldata.Read(version);
            pldata.Read(is_compute);
            GraphicsPipelineKey key{};
            key.Deserialize(ar);
            for (const auto hash : key.stage_hashes) {
                if (hash && !list_ready.contains(hash)) {
                    return false; // still waiting for a program
                }
            }
            if (graphics_pipelines.contains(key)) {
                return true; // the game got there first
            }
            Serialization::Archive load{std::vector<u8>(blob)};
            Serialization::Reader header{load};
            header.Read(version);
            header.Read(is_compute);
            LoadGraphicsPipeline(load);
            return true;
        });
        preload_jobs = nullptr;
    }
    sel = saved;
    list_build_pending = more;
    if (jobs.empty()) {
        return;
    }
    for (auto& job : jobs) {
        auto pipeline = std::make_unique<GraphicsPipeline>(
            instance, scheduler, desc_heap, profile, job.graphics_key, *pipeline_cache, job.infos,
            job.runtime_infos, job.fetch_shader, job.modules, job.graphics_sdata, true, true);
        GraphicsPipeline* raw = pipeline.get();
        graphics_pipelines[job.graphics_key] = std::move(pipeline);
        async_builds.push_back({job.graphics_key,
                                std::hash<GraphicsPipelineKey>{}(job.graphics_key),
                                job.graphics_sdata, raw});
        SubmitBackgroundBuild(raw);
    }
    std::printf("Shaders: %zu listed pipelines building in the background (%zu still waiting), "
                "set up in %.1f ms\n",
                jobs.size(), list_keys.size(),
                std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start)
                    .count());
}

void PipelineCache::WarmUp() {
    if (!EmulatorSettings.IsPipelineCacheEnabled()) {
        return;
    }

    Storage::DataBase::Instance().Open();

    // bbport: the cache holds what one build's recompiler made, and the versions above were not
    // raised with every change to it. Testers whose beta shared a cache folder with another
    // build got that build's shaders: dark pictures and hundreds of "stale pipelines" in their
    // logs, gone once the cache was cleared. The shader build, a fingerprint of the sources that
    // decide the cache (gpu/CMakeLists.txt), marks the build that wrote it; another build's
    // cache is rebuilt, never loaded.
    const std::string_view build{BB_SHADER_BUILD};
    const auto start_cache = [&] {
        Storage::DataBase::Instance().FinishPreload();
        std::vector<u8> profile_data(sizeof(profile));
        std::memcpy(profile_data.data(), &profile, sizeof(profile));
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderProfile, "profile",
                                           std::move(profile_data));
        Storage::DataBase::Instance().Save(Storage::BlobType::ShaderProfile, "build",
                                           std::vector<u8>(build.begin(), build.end()));
    };

    // Check if cache is compatible
    std::vector<u8> profile_data{};
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderProfile, "profile", profile_data);
    // lbppc: a new cache (or one rebuilt below) no longer starts empty: the shader list fills it
    // and the preload builds it, as it does a cache filled in play.
    bool fresh = false;
    if (profile_data.empty()) {
        std::printf("Pipeline cache: new, for shader build %s\n", BB_SHADER_BUILD);
        start_cache();
        fresh = true;
    }
    if (!fresh && profile_data.size() != sizeof(Shader::Profile)) {
        LOG_WARNING(Render, "Pipeline cache profile has unexpected size ({} != {})",
                    profile_data.size(), sizeof(Shader::Profile));
    }
    Shader::Profile cached_profile{};
    if (profile_data.size() == sizeof(Shader::Profile)) {
        std::memcpy(&cached_profile, profile_data.data(), sizeof(cached_profile));
    }
    if (!fresh && (profile_data.size() != sizeof(Shader::Profile) || cached_profile != profile)) {
        // bbport: upstream closed the cache for the session here, so it was never rewritten
        // and every later session compiled every shader again (stutters on each new area).
        // Start a fresh cache for this build and GPU instead.
        LOG_WARNING(Render, "Pipeline cache isn't compatible with current system: rebuilding it");
        Storage::DataBase::Instance().Clear();
        start_cache();
        fresh = true;
    }
    std::vector<u8> build_data{};
    Storage::DataBase::Instance().Load(Storage::BlobType::ShaderProfile, "build", build_data);
    const std::string cached_build(build_data.begin(), build_data.end());
    if (!fresh && cached_build != build) {
        LOG_WARNING(Render,
                    "Pipeline cache was written by another build ({}): rebuilding it for this one "
                    "(shader build {})",
                    cached_build.empty() ? "one without a shader build" : cached_build.substr(0, 32),
                    build);
        Storage::DataBase::Instance().Clear();
        start_cache();
        fresh = true;
    }
    if (!EmulatorSettings.IsPipelineCacheArchived()) {
        InstallShaderList();
    } else if (fresh) {
        return; // an archive is written, not read, from here on
    }

    u32 num_pipelines{};
    u32 num_total_pipelines{};

    // bbport: the stages are read here, one after the other; the pipelines are then built on
    // all CPU cores (BuildPreloaded), where the driver spends nearly all of the preload's time.
    const auto preload_start = std::chrono::steady_clock::now();
    std::vector<PreloadJob> jobs;
    preload_jobs = &jobs;
    Storage::DataBase::Instance().ForEachBlob(
        Storage::BlobType::PipelineKey, [&](std::vector<u8>&& data) {
            ++num_total_pipelines;

            Serialization::Archive ar{std::move(data)};
            Serialization::Reader pldata{ar};

            u32 version{};
            pldata.Read(version);
            if (version != Serialization::PipelineKeyVersion) {
                return;
            }

            u32 is_compute{};
            pldata.Read(is_compute);

            bool result{};
            if (is_compute) {
                result = LoadComputePipeline(ar);
            } else {
                result = LoadGraphicsPipeline(ar);
            }

            if (result) {
                ++num_pipelines;
            }
        });

    preload_jobs = nullptr;
    num_pipelines -= BuildPreloaded(jobs);
    driver_cache_changes += num_pipelines;
    std::printf("Pipeline cache: preload took %.1f s\n",
                std::chrono::duration<double>(std::chrono::steady_clock::now() - preload_start)
                    .count());

    LOG_INFO(Render, "Preloaded {} pipelines", num_pipelines);
    std::printf("Pipeline cache: %u of %u pipelines preloaded (shader build %s)\n", num_pipelines,
                num_total_pipelines, BB_SHADER_BUILD);
    if (num_total_pipelines > num_pipelines) {
        LOG_WARNING(Render, "{} stale pipelines were found. Consider re-generating the cache",
                    num_total_pipelines - num_pipelines);
    }

    // lbppc: the shader list's permutations of programs the preload loaded go in at their
    // indices now (play would otherwise give those indices to its own); the other programs'
    // wait for the game's first use (InstallListedProgram).
    for (auto it = list_metas.begin(); it != list_metas.end();) {
        if (!program_cache.contains(it->first)) {
            ++it;
            continue;
        }
        for (auto& blob : it->second) {
            u64 perm_hash{};
            std::memcpy(&perm_hash, blob.data() + 8, 8);
            if (list_ready.contains(perm_hash)) {
                Serialization::Archive ar{std::vector<u8>(blob)};
                LoadPipelineStage(ar, 0);
            }
        }
        it = list_metas.erase(it);
    }
    sel.infos.fill(nullptr);
    sel.modules.fill(nullptr);
    sel.fetch_shader.reset();

    Storage::DataBase::Instance().FinishPreload();
}

// The pipelines only use the device while they are built for the preload (no guest memory, no
// command buffers), and the driver's pipeline cache is synchronized by the driver.
u32 PipelineCache::BuildPreloaded(std::vector<PreloadJob>& jobs) {
    if (jobs.empty()) {
        return 0;
    }
    // One core is left to the rest of the start; BB_PRELOAD_THREADS=1 builds them one by one.
    u32 threads = std::clamp(std::thread::hardware_concurrency(), 2u, 17u) - 1;
    if (const char* env = std::getenv("BB_PRELOAD_THREADS"); env && *env) {
        threads = std::clamp<u32>(u32(std::strtoul(env, nullptr, 10)), 1u, 64u);
    }
    threads = std::min<u32>(threads, u32(jobs.size()));

    std::atomic<size_t> next{0};
    // lbppc: the launcher shows "Preparing shaders" from these lines (a fresh install builds
    // hundreds here, seconds to a minute); a few dozen lines at most.
    const size_t step = std::max<size_t>(jobs.size() / 50, 16);
    std::printf("Shaders: building %zu of %zu\n", size_t{0}, jobs.size());
    std::fflush(stdout);
    const auto build = [&] {
        for (size_t i; (i = next.fetch_add(1, std::memory_order_relaxed)) < jobs.size();) {
            if (i && i % step == 0) {
                std::printf("Shaders: building %zu of %zu\n", i, jobs.size());
                std::fflush(stdout);
            }
            auto& job = jobs[i];
            if (job.compute) {
                job.compute_pipeline = std::make_unique<ComputePipeline>(
                    instance, scheduler, desc_heap, profile, *pipeline_cache, job.compute_key,
                    *job.infos[0], job.modules[0], job.compute_sdata, true);
            } else {
                job.graphics = std::make_unique<GraphicsPipeline>(
                    instance, scheduler, desc_heap, profile, job.graphics_key, *pipeline_cache,
                    job.infos, job.runtime_infos, job.fetch_shader, job.modules,
                    job.graphics_sdata, true);
            }
        }
    };
    {
        std::vector<std::jthread> pool;
        for (u32 t = 1; t < threads; ++t) {
            pool.emplace_back(build);
        }
        build();
    }

    // As before: a pipeline the driver could not build is left to compile at first use.
    u32 failed = 0;
    for (auto& job : jobs) {
        if (job.compute) {
            if (job.compute_pipeline && job.compute_pipeline->IsValid()) {
                compute_pipelines[job.compute_key] = std::move(job.compute_pipeline);
            } else {
                compute_pipelines.erase(job.compute_key);
                ++failed;
            }
        } else if (job.graphics && job.graphics->IsValid()) {
            graphics_pipelines[job.graphics_key] = std::move(job.graphics);
        } else {
            graphics_pipelines.erase(job.graphics_key);
            ++failed;
        }
    }
    std::printf("Pipeline cache: %zu pipelines built on %u threads\n", jobs.size(), threads);
    std::printf("Shaders: building %zu of %zu\n", jobs.size(), jobs.size());
    std::fflush(stdout);
    return failed;
}

void PipelineCache::Sync() {
    Storage::DataBase::Instance().Close();
}

} // namespace Vulkan

namespace Shader {

void Info::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer info{ar};

    info.Write(this, sizeof(InfoPersistent));
    info.Write(flattened_ud_buf);
    srt_info.Serialize(ar);
}

bool Info::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader info{ar};

    info.Read(this, sizeof(Shader::InfoPersistent));
    info.Read(flattened_ud_buf);

    return srt_info.Deserialize(ar);
}

void Gcn::FetchShaderData::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer fetch{ar};
    ar.Grow(6 + attributes.size() * sizeof(VertexAttribute));

    fetch.Write(size);
    fetch.Write(vertex_offset_sgpr);
    fetch.Write(instance_offset_sgpr);
    fetch.Write(attributes);
}

bool Gcn::FetchShaderData::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader fetch{ar};

    fetch.Read(size);
    fetch.Read(vertex_offset_sgpr);
    fetch.Read(instance_offset_sgpr);
    fetch.Read(attributes);

    return true;
}

void PersistentSrtInfo::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer srt{ar};

    srt.Write(this, sizeof(*this));
    if (walker_func_size) {
        srt.Write(reinterpret_cast<void*>(walker_func), walker_func_size);
    }
}

bool PersistentSrtInfo::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader srt{ar};

    srt.Read(this, sizeof(*this));

    if (walker_func_size) {
        walker_func = RegisterWalkerCode(ar.CurrPtr(), walker_func_size);
        ar.Advance(walker_func_size);
    }

    return true;
}

void StageSpecialization::Serialize(Serialization::Archive& ar) const {
    Serialization::Writer spec{ar};

    spec.Write(start);
    spec.Write(runtime_info);

    spec.Write(bitset.to_string());

    if (fetch_shader_data) {
        spec.Write(sizeof(*fetch_shader_data));
        fetch_shader_data->Serialize(ar);
    } else {
        spec.Write(size_t{0});
    }

    spec.Write(vs_attribs);
    spec.Write(buffers);
    spec.Write(images);
    spec.Write(fmasks);
    spec.Write(samplers);
}

bool StageSpecialization::Deserialize(Serialization::Archive& ar) {
    Serialization::Reader spec{ar};

    spec.Read(start);
    spec.Read(runtime_info);

    std::string bits{};
    spec.Read(bits);
    bitset = std::bitset<MaxStageResources>(bits);

    u64 fetch_data_size{};
    spec.Read(fetch_data_size);

    if (fetch_data_size) {
        Gcn::FetchShaderData fetch_data;
        fetch_data.Deserialize(ar);
        fetch_shader_data = fetch_data;
    }

    spec.Read(vs_attribs);
    spec.Read(buffers);
    spec.Read(images);
    spec.Read(fmasks);
    spec.Read(samplers);

    return true;
}

} // namespace Shader
