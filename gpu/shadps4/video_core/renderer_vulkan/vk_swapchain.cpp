// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <limits>
#include "bbport_settings.h"
#include "common/assert.h"
#include "common/logging/log.h"
#include "core/emulator_settings.h"
#include "imgui/renderer/imgui_core.h"
#include "sdl_window.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_low_latency.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_swapchain.h"
#ifdef _WIN32
#include <cstring>
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <vk_mem_alloc.h>
#endif

namespace Vulkan {

static constexpr vk::SurfaceFormatKHR SURFACE_FORMAT_HDR = {
    .format = vk::Format::eA2B10G10R10UnormPack32,
    .colorSpace = vk::ColorSpaceKHR::eHdr10St2084EXT,
};

static bool TestRefused(); // LBPPC_SWAPCHAIN_REFUSE (below)

// lbppc: presenting without a Vulkan swapchain (Swapchain::StartHostPresent, below).
#ifdef _WIN32
struct Swapchain::HostPresent {
    HWND hwnd{};
    ID3D11Device* device{};
    ID3D11DeviceContext* context{};
    IDXGISwapChain1* chain{};
    UINT chain_flags = 0;
    bool gdi = false;
    // Vulkan side: the images (the swapchain's), one command buffer per image, and the readback buffer.
    std::vector<VkImage> images;
    std::vector<VmaAllocation> image_memory;
    vk::CommandPool pool{};
    std::vector<vk::CommandBuffer> commands;
    vk::Fence fence{};
    VkBuffer buffer{};
    VmaAllocation buffer_memory{};
    void* mapped = nullptr;
    u32 next = 0;

    ~HostPresent() {
        if (chain) chain->Release();
        if (context) context->Release();
        if (device) device->Release();
    }

    // D3D11 on the adapter with the Vulkan device's LUID, and a swapchain on the window: flip model (Windows 10's
    // own), then the older blit model.
    bool StartDxgi(const u8* luid) {
        IDXGIFactory2* factory = nullptr;
        if (FAILED(CreateDXGIFactory1(IID_IDXGIFactory2, reinterpret_cast<void**>(&factory)))) {
            std::printf("Present: no DXGI factory\n");
            return false;
        }
        IDXGIAdapter1* adapter = nullptr;
        for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
            DXGI_ADAPTER_DESC1 desc{};
            adapter->GetDesc1(&desc);
            if (std::memcmp(&desc.AdapterLuid, luid, sizeof(desc.AdapterLuid)) == 0) {
                break;
            }
            adapter->Release();
            adapter = nullptr;
        }
        const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1,
                                            D3D_FEATURE_LEVEL_10_0};
        HRESULT hr = D3D11CreateDevice(adapter, adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE,
                                       nullptr, 0, levels, UINT(std::size(levels)), D3D11_SDK_VERSION, &device,
                                       nullptr, &context);
        if (adapter) {
            adapter->Release();
        }
        if (FAILED(hr)) {
            std::printf("Present: no D3D11 device (0x%08lx)\n", static_cast<unsigned long>(hr));
            factory->Release();
            return false;
        }
        RECT client{};
        GetClientRect(hwnd, &client);
        DXGI_SWAP_CHAIN_DESC1 desc{};
        desc.Width = UINT(std::max<LONG>(client.right - client.left, 1));
        desc.Height = UINT(std::max<LONG>(client.bottom - client.top, 1));
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
        desc.Scaling = DXGI_SCALING_STRETCH;
        desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;
        // The blit model first: Windows always composes it with the desktop. A flip-model swapchain on a window that
        // covers the screen (fullscreen, borderless) is handed to the display directly (independent flip, overlay
        // planes), and on a Radeon PC with Windows 10 the screen then flashed between the game and the desktop
        // behind it every other frame; in a window it was fine. LBPPC_DXGI_MODEL=flip: the flip model first (test).
        struct Kind {
            DXGI_SWAP_EFFECT effect;
            UINT buffers;
            const char* name;
        };
        static constexpr Kind blit_first[] = {{DXGI_SWAP_EFFECT_DISCARD, 1, "blit"},
                                              {DXGI_SWAP_EFFECT_FLIP_DISCARD, 2, "flip discard"},
                                              {DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, 2, "flip sequential"}};
        static constexpr Kind flip_first[] = {{DXGI_SWAP_EFFECT_FLIP_DISCARD, 2, "flip discard"},
                                              {DXGI_SWAP_EFFECT_FLIP_SEQUENTIAL, 2, "flip sequential"},
                                              {DXGI_SWAP_EFFECT_DISCARD, 1, "blit"}};
        const char* model = std::getenv("LBPPC_DXGI_MODEL");
        const bool flip = model && std::string_view{model} == "flip";
        for (const Kind& kind : flip ? flip_first : blit_first) {
            desc.SwapEffect = kind.effect;
            desc.BufferCount = kind.buffers;
            hr = factory->CreateSwapChainForHwnd(device, hwnd, &desc, nullptr, nullptr, &chain);
            if (SUCCEEDED(hr)) {
                std::printf("Present: DXGI swapchain (D3D11, %s), %u x %u\n", kind.name, desc.Width,
                            desc.Height);
                break;
            }
            std::printf("Present: DXGI %s swapchain refused (0x%08lx)\n", kind.name,
                        static_cast<unsigned long>(hr));
            chain = nullptr;
        }
        if (chain) {
            // Alt+Enter and the window stay SDL's (the game's own fullscreen is a borderless window).
            factory->MakeWindowAssociation(hwnd, DXGI_MWA_NO_ALT_ENTER | DXGI_MWA_NO_WINDOW_CHANGES);
        }
        factory->Release();
        return chain != nullptr;
    }

    void Show(const void* pixels, u32 width, u32 height) {
        if (chain) {
            ID3D11Texture2D* back = nullptr;
            HRESULT hr = chain->GetBuffer(0, IID_ID3D11Texture2D, reinterpret_cast<void**>(&back));
            if (SUCCEEDED(hr)) {
                D3D11_TEXTURE2D_DESC desc{};
                back->GetDesc(&desc);
                if (desc.Width == width && desc.Height == height) {
                    context->UpdateSubresource(back, 0, nullptr, pixels, width * 4, 0);
                }
                back->Release();
                hr = chain->Present(1, 0);
            }
            if (FAILED(hr)) {
                std::printf("Present: DXGI stopped presenting (0x%08lx): GDI from now on\n",
                            static_cast<unsigned long>(hr));
                chain->Release();
                chain = nullptr;
                gdi = true;
            }
            return;
        }
        BITMAPINFO info{};
        info.bmiHeader.biSize = sizeof(info.bmiHeader);
        info.bmiHeader.biWidth = LONG(width);
        info.bmiHeader.biHeight = -LONG(height); // top row first
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB; // B, G, R, unused: the images' B8G8R8A8 order
        if (HDC dc = GetDC(hwnd)) {
            SetDIBitsToDevice(dc, 0, 0, width, height, 0, 0, 0, height, pixels, &info, DIB_RGB_COLORS);
            ReleaseDC(hwnd, dc);
        }
    }

    // A new window size: the DXGI buffers follow it (ResizeBuffers with no buffer held).
    void Resize(u32 width, u32 height) {
        if (chain && FAILED(chain->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, chain_flags))) {
            std::printf("Present: DXGI resize to %u x %u failed: GDI from now on\n", width, height);
            chain->Release();
            chain = nullptr;
            gdi = true;
        }
    }
};
#else
struct Swapchain::HostPresent {};
#endif

Swapchain::Swapchain(const Instance& instance_, const Frontend::WindowSDL& window_)
    : instance{instance_}, window{window_}, surface{CreateSurface(instance.GetInstance(), window)} {
    FindPresentFormat();
    FindPresentMode();

    Create(window.GetWidth(), window.GetHeight());
    ImGui::Core::Initialize(instance, window, image_count, surface_format.format);
}

Swapchain::~Swapchain() {
    Destroy();
    host_present.reset();
    if (surface) {
        instance.GetInstance().destroySurfaceKHR(surface);
    }
}

void Swapchain::Create(u32 width_, u32 height_) {
    width = width_;
    height = height_;
    needs_recreation = false;

    Destroy();

    // lbppc: LBPPC_PRESENT=dxgi or gdi (test): present without a Vulkan swapchain from the start.
    if (!host_present) {
        static const char* forced = std::getenv("LBPPC_PRESENT");
        if (forced && (std::string_view{forced} == "dxgi" || std::string_view{forced} == "gdi")) {
            StartHostPresent("LBPPC_PRESENT");
        }
    }
    if (host_present) {
        CreateHostImages();
        return;
    }

    SetSurfaceProperties();

    const std::array queue_family_indices = {
        instance.GetGraphicsQueueFamilyIndex(),
        instance.GetPresentQueueFamilyIndex(),
    };

    const bool exclusive = queue_family_indices[0] == queue_family_indices[1];
    const u32 queue_family_indices_count = exclusive ? 1u : 2u;
    const vk::SharingMode sharing_mode =
        exclusive ? vk::SharingMode::eExclusive : vk::SharingMode::eConcurrent;
    const auto format = needs_hdr ? SURFACE_FORMAT_HDR : surface_format;
    const vk::SwapchainCreateInfoKHR swapchain_info = {
        .pNext = LowLatency::SwapchainCreateNext(),
        .surface = surface,
        .minImageCount = image_count,
        .imageFormat = format.format,
        .imageColorSpace = format.colorSpace,
        .imageExtent = extent,
        .imageArrayLayers = 1,
        .imageUsage = vk::ImageUsageFlagBits::eColorAttachment |
                      vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst,
        .imageSharingMode = sharing_mode,
        .queueFamilyIndexCount = queue_family_indices_count,
        .pQueueFamilyIndices = queue_family_indices.data(),
        .preTransform = transform,
        .compositeAlpha = composite_alpha,
        .presentMode = present_mode,
        .clipped = true,
        .oldSwapchain = nullptr,
    };

    auto [swapchain_result, chain] = TestRefused() ? vk::ResultValue<vk::SwapchainKHR>{vk::Result::eErrorFormatNotSupported, vk::SwapchainKHR{}}
                                                   : instance.GetDevice().createSwapchainKHR(swapchain_info);
    if (swapchain_result != vk::Result::eSuccess) {
        chain = CreateFallback(swapchain_info, swapchain_result);
        if (!chain) {
            // Every Vulkan swapchain refused (AMD's Windows 10 driver): the frames go out another way.
            if (!StartHostPresent("the driver refused every Vulkan swapchain")) {
                UNREACHABLE_MSG("Failed to create swapchain: {}", vk::to_string(swapchain_result));
            }
            CreateHostImages();
            return;
        }
    }
    swapchain = chain;
    LowLatency::OnSwapchain(swapchain);

    SetupImages();
    RefreshSemaphores();
}

// lbppc: AMD's Windows 10 driver refused the swapchain in every report from it (RX 6600 XT to RX
// 7900 XTX, whatever the settings: ErrorFormatNotSupported or ErrorUnknown) where every other
// driver takes the same one. Instead of stopping the game, more conservative swapchains are tried
// in turn, each keeping the changes before it: the fewest images the surface allows, only the
// usages the presenter needs (it clears and blits into the image and ImGui draws on it; nothing
// copies from it), each plain 8-bit sRGB format the surface lists, FIFO, each supported alpha
// mode. The first the driver accepts is kept, and the members follow it.
// LBPPC_SWAPCHAIN_REFUSE=<n>: the first n swapchains are refused as a driver would, to check the
// fallback on any GPU.
static bool TestRefused() {
    static int left = [] {
        const char* value = std::getenv("LBPPC_SWAPCHAIN_REFUSE");
        return value ? std::atoi(value) : 0;
    }();
    return left > 0 && left-- > 0;
}

vk::SwapchainKHR Swapchain::CreateFallback(vk::SwapchainCreateInfoKHR info, vk::Result first) {
    const auto physical = instance.GetPhysicalDevice();
    const auto [caps_result, caps] = physical.getSurfaceCapabilitiesKHR(surface);
    const auto [formats_result, formats] = physical.getSurfaceFormatsKHR(surface);
    std::printf("Swapchain: the driver refused %u x %u, %u images, %s / %s, %s (%s); trying "
                "simpler ones\n",
                info.imageExtent.width, info.imageExtent.height, info.minImageCount,
                vk::to_string(info.imageFormat).c_str(), vk::to_string(info.imageColorSpace).c_str(),
                vk::to_string(info.presentMode).c_str(), vk::to_string(first).c_str());
    vk::Result last = first;
    const auto attempt = [&](const char* what) -> vk::SwapchainKHR {
        auto [result, chain] = TestRefused() ? vk::ResultValue<vk::SwapchainKHR>{vk::Result::eErrorUnknown, vk::SwapchainKHR{}}
                                             : instance.GetDevice().createSwapchainKHR(info);
        if (result == vk::Result::eSuccess) {
            std::printf("Swapchain: created with %s (%u images, %s / %s, %s)\n", what,
                        info.minImageCount, vk::to_string(info.imageFormat).c_str(),
                        vk::to_string(info.imageColorSpace).c_str(),
                        vk::to_string(info.presentMode).c_str());
            if (!needs_hdr) {
                surface_format = {info.imageFormat, info.imageColorSpace};
            }
            present_mode = info.presentMode;
            composite_alpha = info.compositeAlpha;
            return chain;
        }
        last = result;
        return {};
    };
    if (caps_result == vk::Result::eSuccess) {
        u32 fewest = std::max(caps.minImageCount, 2u);
        if (caps.maxImageCount > 0) {
            fewest = std::min(fewest, caps.maxImageCount);
        }
        if (fewest != info.minImageCount) {
            info.minImageCount = fewest;
            if (auto chain = attempt("the fewest images")) return chain;
        }
        if (caps.currentExtent.width != std::numeric_limits<u32>::max() &&
            caps.currentExtent.width && caps.currentExtent.height &&
            caps.currentExtent != info.imageExtent) {
            info.imageExtent = caps.currentExtent;
            extent = caps.currentExtent;
            if (auto chain = attempt("the window's current size")) return chain;
        }
    }
    info.imageUsage = vk::ImageUsageFlagBits::eColorAttachment | vk::ImageUsageFlagBits::eTransferDst;
    if (auto chain = attempt("colour-attachment and copy-in usage only")) return chain;
    if (!needs_hdr && formats_result == vk::Result::eSuccess) {
        for (const auto& f : formats) {
            if (f.colorSpace == vk::ColorSpaceKHR::eSrgbNonlinear &&
                (f.format == vk::Format::eB8G8R8A8Unorm || f.format == vk::Format::eR8G8B8A8Unorm) &&
                (f.format != info.imageFormat || f.colorSpace != info.imageColorSpace)) {
                info.imageFormat = f.format;
                info.imageColorSpace = f.colorSpace;
                if (auto chain = attempt("a plain sRGB format")) return chain;
            }
        }
    }
    if (info.presentMode != vk::PresentModeKHR::eFifo) {
        info.presentMode = vk::PresentModeKHR::eFifo;
        if (auto chain = attempt("V-Sync (FIFO)")) return chain;
    }
    if (caps_result == vk::Result::eSuccess) {
        for (const auto alpha : {vk::CompositeAlphaFlagBitsKHR::eOpaque, vk::CompositeAlphaFlagBitsKHR::eInherit,
                                 vk::CompositeAlphaFlagBitsKHR::ePreMultiplied,
                                 vk::CompositeAlphaFlagBitsKHR::ePostMultiplied}) {
            if ((caps.supportedCompositeAlpha & alpha) && alpha != info.compositeAlpha) {
                info.compositeAlpha = alpha;
                if (auto chain = attempt("another alpha mode")) return chain;
            }
        }
    }
    if (info.imageSharingMode != vk::SharingMode::eExclusive) {
        info.imageSharingMode = vk::SharingMode::eExclusive;
        info.queueFamilyIndexCount = 0;
        info.pQueueFamilyIndices = nullptr;
        if (auto chain = attempt("exclusive sharing")) return chain;
    }
    std::printf("Swapchain: refused: %s (and %s with every simpler configuration)\n",
                vk::to_string(first).c_str(), vk::to_string(last).c_str());
    return {};
}

void Swapchain::Recreate(u32 width_, u32 height_) {
    LOG_DEBUG(Render_Vulkan, "Recreate the swapchain: width={} height={} HDR={}", width_, height_,
              needs_hdr);
    Create(width_, height_);
}

void Swapchain::SetHDR(bool hdr) {
    if (needs_hdr == hdr || host_present) { // lbppc: no HDR without a Vulkan swapchain
        return;
    }

    auto result = instance.GetDevice().waitIdle();
    if (result != vk::Result::eSuccess) {
        LOG_WARNING(ImGui, "Failed to wait for Vulkan device idle on mode change: {}",
                    vk::to_string(result));
    }

    needs_hdr = hdr;
    Recreate(width, height);
    ImGui::Core::OnSurfaceFormatChange(needs_hdr ? SURFACE_FORMAT_HDR.format
                                                 : surface_format.format);
}

bool Swapchain::AcquireNextImage() {
    if (host_present) {
        return AcquireHostImage();
    }
    vk::Device device = instance.GetDevice();
    vk::Result result =
        device.acquireNextImageKHR(swapchain, std::numeric_limits<u64>::max(),
                                   image_acquired[frame_index], VK_NULL_HANDLE, &image_index);

    switch (result) {
    case vk::Result::eSuccess:
        break;
    case vk::Result::eSuboptimalKHR:
    case vk::Result::eErrorSurfaceLostKHR:
    case vk::Result::eErrorOutOfDateKHR:
    case vk::Result::eErrorUnknown:
        needs_recreation = true;
        break;
    default:
        LOG_CRITICAL(Render_Vulkan, "Swapchain acquire returned unknown result {}",
                     vk::to_string(result));
        UNREACHABLE();
        break;
    }

    return !needs_recreation;
}

bool Swapchain::Present(vk::Queue queue, u64 present_id) {
    if (host_present) {
        return PresentHost(queue ? queue : instance.GetPresentQueue());
    }
    const vk::PresentIdKHR present_ids = {
        .swapchainCount = 1,
        .pPresentIds = &present_id,
    };
    const vk::PresentInfoKHR present_info = {
        .pNext = present_id && instance.IsPresentIdEnabled() ? &present_ids : nullptr,
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &present_ready[image_index],
        .swapchainCount = 1,
        .pSwapchains = &swapchain,
        .pImageIndices = &image_index,
    };

    auto result = (queue ? queue : instance.GetPresentQueue()).presentKHR(present_info);
    if (result == vk::Result::eErrorOutOfDateKHR || result == vk::Result::eSuboptimalKHR) {
        needs_recreation = true;
    } else {
        ASSERT_MSG(result == vk::Result::eSuccess, "Swapchain presentation failed: {}",
                   vk::to_string(result));
    }

    frame_index = (frame_index + 1) % image_count;

    return !needs_recreation;
}

void Swapchain::FindPresentFormat() {
    const auto [formats_result, formats] =
        instance.GetPhysicalDevice().getSurfaceFormatsKHR(surface);
    ASSERT_MSG(formats_result == vk::Result::eSuccess, "Failed to query surface formats: {}",
               vk::to_string(formats_result));

    // Check if the device supports HDR formats. Here we care of Rec.2020 PQ only as it is expected
    // game output. Other variants as e.g. linear Rec.2020 will require additional color space
    // rotation
    supports_hdr =
        std::find_if(formats.begin(), formats.end(), [](const vk::SurfaceFormatKHR& format) {
            return format == SURFACE_FORMAT_HDR;
        }) != formats.end();
    // Also make sure that user allowed us to use HDR
    supports_hdr &= EmulatorSettings.IsHdrAllowed();

    // If there is a single undefined surface format, the device doesn't care, so we'll just use
    // RGBA sRGB.
    if (formats[0].format == vk::Format::eUndefined) {
        surface_format.format = vk::Format::eR8G8B8A8Unorm;
        surface_format.colorSpace = vk::ColorSpaceKHR::eSrgbNonlinear;
        return;
    }

    // bbport: on a display Windows runs in HDR the picture is re-encoded for the desktop's sRGB
    // decode (post_process.frag), which leaves the game's 12 darkest 8-bit levels on 4 codes of
    // an 8-bit swapchain. A 10-bit one keeps them apart.
    if (window.IsOnHdrDisplay() && EmulatorSettings.IsHdrGammaEnabled()) {
        const auto it = std::ranges::find(
            formats, vk::SurfaceFormatKHR{vk::Format::eA2B10G10R10UnormPack32,
                                          vk::ColorSpaceKHR::eSrgbNonlinear});
        if (it != formats.end()) {
            surface_format = *it;
            return;
        }
    }

    // lbppc: a plain sRGB colour space first. With VK_EXT_swapchain_colorspace a driver also lists
    // each format with other colour spaces (extended sRGB, HDR10, display-native), and the first
    // match below could be one of those.
    for (const vk::SurfaceFormatKHR& sformat : formats) {
        if ((sformat.format == vk::Format::eR8G8B8A8Unorm ||
             sformat.format == vk::Format::eB8G8R8A8Unorm) &&
            sformat.colorSpace == vk::ColorSpaceKHR::eSrgbNonlinear) {
            surface_format = sformat;
            return;
        }
    }

    // Try to find a suitable format.
    for (const vk::SurfaceFormatKHR& sformat : formats) {
        vk::Format format = sformat.format;
        if (format != vk::Format::eR8G8B8A8Unorm && format != vk::Format::eB8G8R8A8Unorm) {
            continue;
        }

        surface_format.format = format;
        surface_format.colorSpace = sformat.colorSpace;
        return;
    }

    UNREACHABLE_MSG("Unable to find required swapchain format!");
}

void Swapchain::FindPresentMode() {
    const auto [modes_result, modes] =
        instance.GetPhysicalDevice().getSurfacePresentModesKHR(surface);
    if (modes_result != vk::Result::eSuccess) {
        LOG_ERROR(Render, "Failed to query available present modes, falling back to Fifo as "
                          "guaranteed supported option.");
        present_mode = vk::PresentModeKHR::eFifo;
        return;
    }

    // bbport: VRR needs V-Sync (FIFO): with Mailbox the display does not follow the game's
    // frames on every driver. The frame cap keeps it below the refresh rate (GetFrameLimit).
    const auto requested_mode =
        EmulatorSettings.IsVrr() ? std::string{"Fifo"} : EmulatorSettings.GetPresentMode();
    if (EmulatorSettings.IsVrr()) {
        std::printf("VRR: V-Sync (FIFO), frame cap %u FPS (display %u Hz)\n",
                    EmulatorSettings.GetFrameLimit(), BbDisplayRefreshHz());
    }
    if (requested_mode == "Mailbox") {
        present_mode = vk::PresentModeKHR::eMailbox;
    } else if (requested_mode == "Fifo") {
        present_mode = vk::PresentModeKHR::eFifo;
    } else if (requested_mode == "Immediate") {
        present_mode = vk::PresentModeKHR::eImmediate;
    } else {
        LOG_ERROR(Render_Vulkan, "Unknown present mode {}, defaulting to Mailbox.",
                  EmulatorSettings.GetPresentMode());
        present_mode = vk::PresentModeKHR::eMailbox;
    }

    if (std::ranges::find(modes, present_mode) == modes.cend()) {
        // FIFO is guaranteed to be supported by the Vulkan spec.
        constexpr auto fallback = vk::PresentModeKHR::eFifo;
        LOG_WARNING(Render, "Requested present mode {} is not supported, falling back to {}.",
                    vk::to_string(present_mode), vk::to_string(fallback));
        present_mode = fallback;
    }
}

void Swapchain::SetSurfaceProperties() {
    const auto [capabilities_result, capabilities] =
        instance.GetPhysicalDevice().getSurfaceCapabilitiesKHR(surface);
    ASSERT_MSG(capabilities_result == vk::Result::eSuccess,
               "Failed to query surface capabilities: {}", vk::to_string(capabilities_result));

    extent = capabilities.currentExtent;
    if (capabilities.currentExtent.width == std::numeric_limits<u32>::max()) {
        extent.width = std::max(capabilities.minImageExtent.width,
                                std::min(capabilities.maxImageExtent.width, width));
        extent.height = std::max(capabilities.minImageExtent.height,
                                 std::min(capabilities.maxImageExtent.height, height));
    }

    // Select number of images in swap chain, we prefer one buffer in the background to work on
    // (bbport: two with frame generation, which presents several images per game frame).
    image_count = capabilities.minImageCount +
                  (BbSettings::Get().ActiveFrameGen() != BbSettings::FrameGenOff ? 2 : 1);
    if (capabilities.maxImageCount > 0) {
        image_count = std::min(image_count, capabilities.maxImageCount);
    }

    // Prefer identity transform if possible
    transform = vk::SurfaceTransformFlagBitsKHR::eIdentity;
    if (!(capabilities.supportedTransforms & transform)) {
        transform = capabilities.currentTransform;
    }

    // Opaque is not supported everywhere.
    composite_alpha = vk::CompositeAlphaFlagBitsKHR::eOpaque;
    if (!(capabilities.supportedCompositeAlpha & vk::CompositeAlphaFlagBitsKHR::eOpaque)) {
        composite_alpha = vk::CompositeAlphaFlagBitsKHR::eInherit;
    }
}

void Swapchain::Destroy() {
    vk::Device device = instance.GetDevice();
    const auto wait_result = device.waitIdle();
    if (wait_result != vk::Result::eSuccess) {
        LOG_WARNING(Render_Vulkan, "Failed to wait for device to become idle: {}",
                    vk::to_string(wait_result));
    }

    for (auto& image_view : images_view) {
        device.destroyImageView(image_view);
    }
    images_view.clear();
    if (host_present) {
        DestroyHostImages();
    }

    if (swapchain) {
        LowLatency::OnSwapchain({});
        device.destroySwapchainKHR(swapchain);
    }

    for (const auto& sem : image_acquired) {
        device.destroySemaphore(sem);
    }
    for (const auto& sem : present_ready) {
        device.destroySemaphore(sem);
    }

    image_acquired.clear();
    present_ready.clear();
}

void Swapchain::RefreshSemaphores() {
    const vk::Device device = instance.GetDevice();
    image_acquired.resize(image_count);
    present_ready.resize(image_count);

    for (vk::Semaphore& semaphore : image_acquired) {
        auto [semaphore_result, sem] = device.createSemaphore({});
        ASSERT_MSG(semaphore_result == vk::Result::eSuccess,
                   "Failed to create image acquired semaphore: {}",
                   vk::to_string(semaphore_result));
        semaphore = sem;
    }
    for (vk::Semaphore& semaphore : present_ready) {
        auto [semaphore_result, sem] = device.createSemaphore({});
        ASSERT_MSG(semaphore_result == vk::Result::eSuccess,
                   "Failed to create present ready semaphore: {}", vk::to_string(semaphore_result));
        semaphore = sem;
    }

    for (u32 i = 0; i < image_count; ++i) {
        SetObjectName(device, image_acquired[i], "Swapchain Semaphore: image_acquired {}", i);
        SetObjectName(device, present_ready[i], "Swapchain Semaphore: present_ready {}", i);
    }
}

void Swapchain::SetupImages() {
    vk::Device device = instance.GetDevice();
    auto [images_result, imgs] = device.getSwapchainImagesKHR(swapchain);
    ASSERT_MSG(images_result == vk::Result::eSuccess, "Failed to create swapchain images: {}",
               vk::to_string(images_result));
    images = std::move(imgs);
    image_count = static_cast<u32>(images.size());
    images_view.resize(image_count);
    for (u32 i = 0; i < image_count; ++i) {
        if (images_view[i]) {
            device.destroyImageView(images_view[i]);
        }
        auto [im_view_result, im_view] = device.createImageView(vk::ImageViewCreateInfo{
            .image = images[i],
            .viewType = vk::ImageViewType::e2D,
            .format = needs_hdr ? SURFACE_FORMAT_HDR.format : surface_format.format,
            .subresourceRange =
                {
                    .aspectMask = vk::ImageAspectFlagBits::eColor,
                    .levelCount = 1,
                    .layerCount = 1,
                },
        });
        ASSERT_MSG(im_view_result == vk::Result::eSuccess, "Failed to create image view: {}",
                   vk::to_string(im_view_result));
        images_view[i] = im_view;
    }

    for (u32 i = 0; i < image_count; ++i) {
        SetObjectName(device, images[i], "Swapchain Image {}", i);
        SetObjectName(device, images_view[i], "Swapchain ImageView {}", i);
    }
}

// lbppc: presenting without a Vulkan swapchain. AMD's Windows 10 driver refused every swapchain on some PCs
// (vkCreateSwapchainKHR: ErrorUnknown, whatever the settings) while its Vulkan device works, and every DirectX game
// on the same PC presents. When that happens the swapchain's images are made here instead, the presenter draws into
// them as before, and each frame is copied into host memory and shown through a DXGI swapchain (D3D11, on the same
// GPU) on the game's window; if DXGI cannot be set up either, with GDI. The copy costs a few milliseconds a frame at
// most. The game's Vulkan surface is destroyed first, so nothing of Vulkan's presentation stays on the window.
#ifdef _WIN32
bool Swapchain::StartHostPresent(const char* why) {
    auto host = std::make_unique<HostPresent>();
    host->hwnd = static_cast<HWND>(window.GetWindowInfo().render_surface);
    if (!host->hwnd) {
        return false;
    }
    // The window is DXGI's from now on: no Vulkan surface stays on it.
    if (surface) {
        instance.GetInstance().destroySurfaceKHR(surface);
        surface = vk::SurfaceKHR{};
    }
    const auto id = instance.GetPhysicalDevice()
                        .getProperties2<vk::PhysicalDeviceProperties2, vk::PhysicalDeviceIDProperties>()
                        .get<vk::PhysicalDeviceIDProperties>();
    const char* forced = std::getenv("LBPPC_PRESENT");
    const bool gdi_only = forced && std::string_view{forced} == "gdi";
    if (gdi_only || !id.deviceLUIDValid || !host->StartDxgi(id.deviceLUID.data())) {
        host->gdi = true;
    }
    std::printf("Swapchain: no Vulkan swapchain (%s): the frames are shown through %s instead\n", why,
                host->gdi ? "GDI" : "DXGI");
    surface_format = {vk::Format::eB8G8R8A8Unorm, vk::ColorSpaceKHR::eSrgbNonlinear};
    present_mode = host->gdi ? vk::PresentModeKHR::eImmediate : vk::PresentModeKHR::eFifo;
    needs_hdr = false;
    supports_hdr = false;
    swapchain = vk::SwapchainKHR{};
    host_present = std::move(host);
    return true;
}

void Swapchain::CreateHostImages() {
    HostPresent& host = *host_present;
    const vk::Device device = instance.GetDevice();
    RECT client{};
    GetClientRect(host.hwnd, &client);
    extent = vk::Extent2D{u32(std::max<LONG>(client.right - client.left, 1)),
                          u32(std::max<LONG>(client.bottom - client.top, 1))};
    static vk::Extent2D logged{};
    if (logged != extent) {
        std::printf("Present: window %u x %u\n", extent.width, extent.height);
        logged = extent;
    }
    host.Resize(extent.width, extent.height);
    image_count = BbSettings::Get().ActiveFrameGen() != BbSettings::FrameGenOff ? 4 : 3;
    host.next = 0;

    const VkImageCreateInfo image_info{
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D,
        .format = VK_FORMAT_B8G8R8A8_UNORM,
        .extent = {extent.width, extent.height, 1},
        .mipLevels = 1,
        .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT,
        .tiling = VK_IMAGE_TILING_OPTIMAL,
        // As the swapchain's: the presenter clears and blits into it and the overlay draws on it; copied out here.
        .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT |
                 VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    const VmaAllocationCreateInfo image_memory{.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE};
    host.images.resize(image_count);
    host.image_memory.resize(image_count);
    images.resize(image_count);
    for (u32 i = 0; i < image_count; ++i) {
        const VkResult result = vmaCreateImage(instance.GetAllocator(), &image_info, &image_memory,
                                               &host.images[i], &host.image_memory[i], nullptr);
        ASSERT_MSG(result == VK_SUCCESS, "Failed to create a presentation image: {}",
                   vk::to_string(vk::Result(result)));
        images[i] = host.images[i];
    }

    // Tightly packed B8G8R8A8 rows, as GDI and UpdateSubresource take them.
    const VkBufferCreateInfo buffer_info{
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .size = VkDeviceSize(extent.width) * extent.height * 4,
        .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    const VmaAllocationCreateInfo buffer_memory{
        .flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT | VMA_ALLOCATION_CREATE_MAPPED_BIT,
        .usage = VMA_MEMORY_USAGE_AUTO,
    };
    VmaAllocationInfo mapped{};
    const VkResult buffer_result = vmaCreateBuffer(instance.GetAllocator(), &buffer_info, &buffer_memory,
                                                   &host.buffer, &host.buffer_memory, &mapped);
    ASSERT_MSG(buffer_result == VK_SUCCESS && mapped.pMappedData, "Failed to create the presentation buffer: {}",
               vk::to_string(vk::Result(buffer_result)));
    host.mapped = mapped.pMappedData;

    auto [pool_result, pool] = device.createCommandPool(vk::CommandPoolCreateInfo{
        .flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer,
        .queueFamilyIndex = instance.GetPresentQueueFamilyIndex(),
    });
    ASSERT_MSG(pool_result == vk::Result::eSuccess, "Failed to create the presentation command pool");
    host.pool = pool;
    auto [commands_result, commands] = device.allocateCommandBuffers(vk::CommandBufferAllocateInfo{
        .commandPool = host.pool,
        .level = vk::CommandBufferLevel::ePrimary,
        .commandBufferCount = image_count,
    });
    ASSERT_MSG(commands_result == vk::Result::eSuccess, "Failed to allocate presentation commands");
    host.commands = std::move(commands);
    auto [fence_result, fence] = device.createFence({});
    ASSERT_MSG(fence_result == vk::Result::eSuccess, "Failed to create the presentation fence");
    host.fence = fence;

    images_view.resize(image_count);
    for (u32 i = 0; i < image_count; ++i) {
        auto [view_result, view] = device.createImageView(vk::ImageViewCreateInfo{
            .image = images[i],
            .viewType = vk::ImageViewType::e2D,
            .format = vk::Format::eB8G8R8A8Unorm,
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1},
        });
        ASSERT_MSG(view_result == vk::Result::eSuccess, "Failed to create a presentation image view");
        images_view[i] = view;
        SetObjectName(device, images[i], "Presentation image {}", i);
    }
    RefreshSemaphores();
    frame_index = 0;
    image_index = 0;
}

void Swapchain::DestroyHostImages() {
    HostPresent& host = *host_present;
    const vk::Device device = instance.GetDevice();
    for (u32 i = 0; i < host.images.size(); ++i) {
        vmaDestroyImage(instance.GetAllocator(), host.images[i], host.image_memory[i]);
    }
    host.images.clear();
    host.image_memory.clear();
    images.clear();
    if (host.buffer) {
        vmaDestroyBuffer(instance.GetAllocator(), host.buffer, host.buffer_memory);
        host.buffer = VK_NULL_HANDLE;
        host.mapped = nullptr;
    }
    if (host.pool) {
        device.destroyCommandPool(host.pool); // frees the command buffers
        host.pool = vk::CommandPool{};
        host.commands.clear();
    }
    if (host.fence) {
        device.destroyFence(host.fence);
        host.fence = vk::Fence{};
    }
}

// The next image in turn. The copy of the one before it has finished (PresentHost waits for it), so it is free;
// its "acquired" semaphore is signalled by an empty submission, as the presenter waits for it.
bool Swapchain::AcquireHostImage() {
    HostPresent& host = *host_present;
    image_index = host.next;
    host.next = (host.next + 1) % image_count;
    const vk::SubmitInfo signal{
        .signalSemaphoreCount = 1,
        .pSignalSemaphores = &image_acquired[frame_index],
    };
    std::scoped_lock lock{Scheduler::submit_mutex};
    const vk::Result result = instance.GetPresentQueue().submit(signal);
    if (result == vk::Result::eErrorDeviceLost) {
        instance.ReportDeviceLost("starting a frame without a swapchain");
    }
    return result == vk::Result::eSuccess;
}

// Called with Scheduler::submit_mutex held (Presenter::PresentImage). Once the presenter's work on the image is done
// (present_ready), it is copied into the buffer; when the copy is done the frame goes to DXGI or GDI.
bool Swapchain::PresentHost(vk::Queue queue) {
    HostPresent& host = *host_present;
    const vk::Device device = instance.GetDevice();
    const vk::CommandBuffer cmd = host.commands[image_index];
    (void)cmd.reset();
    (void)cmd.begin(vk::CommandBufferBeginInfo{.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
    const vk::ImageMemoryBarrier to_copy{
        .srcAccessMask = vk::AccessFlagBits::eColorAttachmentWrite | vk::AccessFlagBits::eTransferWrite,
        .dstAccessMask = vk::AccessFlagBits::eTransferRead,
        .oldLayout = vk::ImageLayout::ePresentSrcKHR,
        .newLayout = vk::ImageLayout::eTransferSrcOptimal,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = images[image_index],
        .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1},
    };
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eAllCommands, vk::PipelineStageFlagBits::eTransfer, {}, {}, {},
                        to_copy);
    const vk::BufferImageCopy region{
        .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1},
        .imageExtent = {extent.width, extent.height, 1},
    };
    cmd.copyImageToBuffer(images[image_index], vk::ImageLayout::eTransferSrcOptimal, host.buffer, region);
    const vk::BufferMemoryBarrier to_host{
        .srcAccessMask = vk::AccessFlagBits::eTransferWrite,
        .dstAccessMask = vk::AccessFlagBits::eHostRead,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .buffer = host.buffer,
        .size = VK_WHOLE_SIZE,
    };
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, {}, to_host,
                        {});
    (void)cmd.end();
    const vk::PipelineStageFlags wait_stage = vk::PipelineStageFlagBits::eTransfer;
    const vk::SubmitInfo submit{
        .waitSemaphoreCount = 1,
        .pWaitSemaphores = &present_ready[image_index],
        .pWaitDstStageMask = &wait_stage,
        .commandBufferCount = 1,
        .pCommandBuffers = &cmd,
    };
    vk::Result result = queue.submit(submit, host.fence);
    if (result == vk::Result::eSuccess) {
        result = device.waitForFences(host.fence, true, std::numeric_limits<u64>::max());
    }
    if (result == vk::Result::eErrorDeviceLost) {
        instance.ReportDeviceLost("presenting without a swapchain");
    }
    ASSERT_MSG(result == vk::Result::eSuccess, "Presentation copy failed: {}", vk::to_string(result));
    (void)device.resetFences(host.fence);
    vmaInvalidateAllocation(instance.GetAllocator(), host.buffer_memory, 0, VK_WHOLE_SIZE);
    host.Show(host.mapped, extent.width, extent.height);
    if (host.gdi && present_mode != vk::PresentModeKHR::eImmediate) {
        present_mode = vk::PresentModeKHR::eImmediate; // GDI waits for no display
    }
    frame_index = (frame_index + 1) % image_count;
    return true;
}
#else
bool Swapchain::StartHostPresent(const char*) {
    return false;
}
void Swapchain::CreateHostImages() {}
bool Swapchain::AcquireHostImage() {
    return false;
}
bool Swapchain::PresentHost(vk::Queue) {
    return false;
}
void Swapchain::DestroyHostImages() {}
#endif

} // namespace Vulkan
