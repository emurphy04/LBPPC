/* LBPPC swapchain test (Windows): creates a plain window and a Vulkan swapchain on every GPU, and prints what each
 * step returned. It runs none of the game, so a refusal here is the PC's driver or layers, not LBPPC.
 * Options, to recreate what the game process does before it makes its swapchain:
 *   --low      reserves memory from 0x400000 up, as the game loads LBP3 below 4 GiB (LBPPC_LOW_BASE)
 *   --d3d12    creates a D3D12 device on each GPU first, as the FSR 4 check does
 * Build (MSYS2 CLANG64): clang -O2 tools/swapchain_test.c -lvulkan-1 -ld3d12 -ldxgi -ldxguid -lgdi32 -luser32 -o swapchain-test.exe */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#define VK_USE_PLATFORM_WIN32_KHR
#include <vulkan/vulkan.h>
#include <d3d12.h>
#include <dxgi1_4.h>
#include <stdio.h>
#include <string.h>

static const char *result_name(VkResult r) {
    switch (r) {
    case VK_SUCCESS: return "success";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "ErrorOutOfHostMemory";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "ErrorOutOfDeviceMemory";
    case VK_ERROR_INITIALIZATION_FAILED: return "ErrorInitializationFailed";
    case VK_ERROR_DEVICE_LOST: return "ErrorDeviceLost";
    case VK_ERROR_SURFACE_LOST_KHR: return "ErrorSurfaceLost";
    case VK_ERROR_NATIVE_WINDOW_IN_USE_KHR: return "ErrorNativeWindowInUse";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "ErrorFormatNotSupported";
    case VK_ERROR_UNKNOWN: return "ErrorUnknown";
    default: { static char s[32]; snprintf(s, sizeof(s), "VkResult %d", (int)r); return s; }
    }
}

static void reserve_low(void) {
    /* The game's image (about 21 MB) and the memory it hands the game near it, from 0x400000, plus room around. */
    size_t reserved = 0;
    for (uintptr_t at = 0x400000; at < 0x10000000; at += 0x10000)
        if (VirtualAlloc((void *)at, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE)) reserved += 0x10000;
    printf("Low memory: %zu MiB reserved between 0x400000 and 0x10000000\n", reserved >> 20);
}

static void d3d12_device(const uint8_t luid[8]) {
    IDXGIFactory4 *factory = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory4, (void **)&factory))) { printf("  D3D12: no DXGI factory\n"); return; }
    IDXGIAdapter1 *adapter = NULL;
    HRESULT hr = factory->lpVtbl->EnumAdapterByLuid(factory, *(const LUID *)luid, &IID_IDXGIAdapter1, (void **)&adapter);
    ID3D12Device *device = NULL;
    if (SUCCEEDED(hr)) hr = D3D12CreateDevice((IUnknown *)adapter, D3D_FEATURE_LEVEL_11_0, &IID_ID3D12Device, (void **)&device);
    printf("  D3D12 device on this GPU: %s (0x%08lx)\n", SUCCEEDED(hr) ? "created" : "failed", (unsigned long)hr);
    /* Kept, as the game keeps its probe's libraries loaded. */
    if (adapter) adapter->lpVtbl->Release(adapter);
    factory->lpVtbl->Release(factory);
}

int main(int argc, char **argv) {
    int low = 0, dx12 = 0;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--low")) low = 1;
        if (!strcmp(argv[i], "--d3d12")) dx12 = 1;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("Swapchain test%s%s\n", low ? " --low" : "", dx12 ? " --d3d12" : "");
    if (low) reserve_low();

    WNDCLASSW wc = {.lpfnWndProc = DefWindowProcW, .hInstance = GetModuleHandleW(NULL), .lpszClassName = L"LBPPCSwapchainTest"};
    RegisterClassW(&wc);
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"LBPPC swapchain test", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                100, 100, 1280, 720, NULL, NULL, wc.hInstance, NULL);
    if (!hwnd) { printf("No window (%lu)\n", GetLastError()); return 1; }

    const char *extensions[] = {VK_KHR_SURFACE_EXTENSION_NAME, VK_KHR_WIN32_SURFACE_EXTENSION_NAME};
    VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "shadPS4",
                             .pEngineName = "shadPS4", .apiVersion = VK_API_VERSION_1_3};
    VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
                                .enabledExtensionCount = 2, .ppEnabledExtensionNames = extensions};
    VkInstance instance;
    VkResult r = vkCreateInstance(&ici, NULL, &instance);
    printf("Instance: %s\n", result_name(r));
    if (r) return 1;

    uint32_t count = 0;
    vkEnumeratePhysicalDevices(instance, &count, NULL);
    VkPhysicalDevice gpus[8];
    if (count > 8) count = 8;
    vkEnumeratePhysicalDevices(instance, &count, gpus);
    int failures = 0;
    for (uint32_t g = 0; g < count; ++g) {
        VkPhysicalDeviceIDProperties id = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES};
        VkPhysicalDeviceDriverProperties driver = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &id};
        VkPhysicalDeviceProperties2 props = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &driver};
        vkGetPhysicalDeviceProperties2(gpus[g], &props);
        printf("\nGPU %u: %s (%s, %s)\n", g, props.properties.deviceName, driver.driverName, driver.driverInfo);
        if (dx12 && id.deviceLUIDValid) d3d12_device(id.deviceLUID);

        /* A surface of its own each time, as the game makes one. */
        VkWin32SurfaceCreateInfoKHR sci = {.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR,
                                           .hinstance = GetModuleHandleW(NULL), .hwnd = hwnd};
        VkSurfaceKHR surface;
        r = vkCreateWin32SurfaceKHR(instance, &sci, NULL, &surface);
        printf("  Surface: %s\n", result_name(r));
        if (r) { ++failures; continue; }

        uint32_t families = 0;
        vkGetPhysicalDeviceQueueFamilyProperties(gpus[g], &families, NULL);
        VkQueueFamilyProperties family[16];
        if (families > 16) families = 16;
        vkGetPhysicalDeviceQueueFamilyProperties(gpus[g], &families, family);
        int graphics = -1;
        for (uint32_t f = 0; f < families; ++f) {
            VkBool32 present = VK_FALSE;
            vkGetPhysicalDeviceSurfaceSupportKHR(gpus[g], f, surface, &present);
            printf("  Queue family %u: %s%s, present %s\n", f, family[f].queueFlags & VK_QUEUE_GRAPHICS_BIT ? "graphics" : "",
                   family[f].queueFlags & VK_QUEUE_COMPUTE_BIT ? " compute" : "", present ? "yes" : "no");
            if (graphics < 0 && (family[f].queueFlags & VK_QUEUE_GRAPHICS_BIT)) graphics = (int)f;
        }
        VkSurfaceCapabilitiesKHR caps;
        r = vkGetPhysicalDeviceSurfaceCapabilitiesKHR(gpus[g], surface, &caps);
        printf("  Surface capabilities: %s; %u..%u images, current %u x %u, usage 0x%x\n", result_name(r),
               caps.minImageCount, caps.maxImageCount, caps.currentExtent.width, caps.currentExtent.height,
               caps.supportedUsageFlags);
        uint32_t nformats = 0;
        vkGetPhysicalDeviceSurfaceFormatsKHR(gpus[g], surface, &nformats, NULL);
        VkSurfaceFormatKHR formats[64];
        if (nformats > 64) nformats = 64;
        vkGetPhysicalDeviceSurfaceFormatsKHR(gpus[g], surface, &nformats, formats);
        uint32_t nmodes = 0;
        vkGetPhysicalDeviceSurfacePresentModesKHR(gpus[g], surface, &nmodes, NULL);
        printf("  %u surface formats, %u present modes; first format %d / colour space %d\n", nformats, nmodes,
               nformats ? formats[0].format : -1, nformats ? formats[0].colorSpace : -1);

        float priority = 1.0f;
        VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                       .queueFamilyIndex = (uint32_t)graphics, .queueCount = 1, .pQueuePriorities = &priority};
        const char *device_extensions[] = {VK_KHR_SWAPCHAIN_EXTENSION_NAME};
        VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1,
                                  .pQueueCreateInfos = &qci, .enabledExtensionCount = 1,
                                  .ppEnabledExtensionNames = device_extensions};
        VkDevice device;
        r = vkCreateDevice(gpus[g], &dci, NULL, &device);
        printf("  Device: %s\n", result_name(r));
        if (r) { ++failures; vkDestroySurfaceKHR(instance, surface, NULL); continue; }

        VkSwapchainCreateInfoKHR swci = {
            .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR, .surface = surface,
            .minImageCount = caps.minImageCount < 2 ? 2 : caps.minImageCount,
            .imageFormat = nformats ? formats[0].format : VK_FORMAT_B8G8R8A8_UNORM,
            .imageColorSpace = nformats ? formats[0].colorSpace : VK_COLOR_SPACE_SRGB_NONLINEAR_KHR,
            .imageExtent = caps.currentExtent, .imageArrayLayers = 1,
            .imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT, .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE,
            .preTransform = caps.currentTransform, .compositeAlpha = VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
            .presentMode = VK_PRESENT_MODE_FIFO_KHR, .clipped = VK_TRUE};
        VkSwapchainKHR swapchain;
        r = vkCreateSwapchainKHR(device, &swci, NULL, &swapchain);
        printf("  SWAPCHAIN: %s\n", result_name(r));
        if (r) ++failures;
        else vkDestroySwapchainKHR(device, swapchain, NULL);
        vkDestroyDevice(device, NULL);
        vkDestroySurfaceKHR(instance, surface, NULL);
    }
    vkDestroyInstance(instance, NULL);
    DestroyWindow(hwnd);
    printf("\nResult: %s\n", failures ? "a swapchain was refused" : "every GPU made a swapchain");
    return failures ? 2 : 0;
}
