// The Vulkan layer for SteamVR overlay textures (client/platform/linux/openvr_texture_layer.c, 1.0.0.2), loaded the way
// VROSTBridge's calibration launcher loads it (VK_ADD_LAYER_PATH and VK_INSTANCE_LAYERS, set by CTest). The instance
// and device are made as Baballonia's Godot 4.5 calibration makes them: Vulkan 1.2, its usual extensions with
// VK_KHR_external_memory_fd but no semaphore or timeline extension, pEnabledFeatures and a pNext chain with
// VkPhysicalDeviceVulkan11Features. Every command SteamVR calls whose extension the driver offers must then resolve,
// and a timeline semaphore must work. Skips (77) without a Vulkan device or when the layer is not loaded.
#include <vulkan/vulkan.h>

#include <cstdio>
#include <cstring>
#include <vector>

static const char *const LAYER = "VK_LAYER_VROSTBRIDGE_openvr_textures";

static bool offered(const std::vector<VkExtensionProperties> &offers, const char *name) {
    for (const auto &offer : offers)
        if (!std::strcmp(offer.extensionName, name)) return true;
    return false;
}

int main() {
    uint32_t layer_count = 0;
    vkEnumerateInstanceLayerProperties(&layer_count, nullptr);
    std::vector<VkLayerProperties> layers(layer_count);
    vkEnumerateInstanceLayerProperties(&layer_count, layers.data());
    bool found = false;
    for (const auto &layer : layers) found = found || !std::strcmp(layer.layerName, LAYER);
    if (!found) {
        std::printf("SKIP: %s is not on VK_ADD_LAYER_PATH\n", LAYER);
        return 77;
    }

    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO, nullptr, "BabbleCalibration", 1, "Godot Engine", 1,
                          VK_API_VERSION_1_2};
    VkInstanceCreateInfo instance_info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, nullptr, 0, &app, 0, nullptr, 0, nullptr};
    VkInstance instance;
    if (vkCreateInstance(&instance_info, nullptr, &instance) != VK_SUCCESS) {
        std::printf("SKIP: no Vulkan instance\n");
        return 77;
    }
    uint32_t gpu_count = 1;
    VkPhysicalDevice gpu = VK_NULL_HANDLE;
    if (vkEnumeratePhysicalDevices(instance, &gpu_count, &gpu) < 0 || !gpu_count) {
        vkDestroyInstance(instance, nullptr);
        std::printf("SKIP: no Vulkan device\n");
        return 77;
    }
    uint32_t offer_count = 0;
    vkEnumerateDeviceExtensionProperties(gpu, nullptr, &offer_count, nullptr);
    std::vector<VkExtensionProperties> offers(offer_count);
    vkEnumerateDeviceExtensionProperties(gpu, nullptr, &offer_count, offers.data());

    // Godot 4.5's own list (RenderingDeviceDriverVulkan::_initialize_device_extensions), where offered.
    std::vector<const char *> names;
    for (const char *name : {"VK_KHR_swapchain", "VK_KHR_multiview", "VK_KHR_create_renderpass2", "VK_KHR_shader_float16_int8",
                             "VK_KHR_16bit_storage", "VK_KHR_image_format_list", "VK_KHR_maintenance2",
                             "VK_EXT_pipeline_creation_cache_control", "VK_KHR_buffer_device_address",
                             "VK_KHR_external_memory_fd"})
        if (offered(offers, name)) names.push_back(name);

    VkPhysicalDeviceShaderFloat16Int8Features has_float16{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES};
    VkPhysicalDeviceVulkan11Features has_vulkan11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &has_float16};
    VkPhysicalDeviceFeatures2 supported{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &has_vulkan11};
    vkGetPhysicalDeviceFeatures2(gpu, &supported);
    VkPhysicalDeviceShaderFloat16Int8Features float16{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT16_INT8_FEATURES};
    float16.shaderFloat16 = has_float16.shaderFloat16;
    float16.shaderInt8 = has_float16.shaderInt8;
    VkPhysicalDeviceVulkan11Features vulkan11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, &float16};
    vulkan11.multiview = has_vulkan11.multiview;
    vulkan11.storageBuffer16BitAccess = has_vulkan11.storageBuffer16BitAccess;
    VkPhysicalDeviceFeatures features{};
    float priority = 1.0f;
    VkDeviceQueueCreateInfo queue{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, nullptr, 0, 0, 1, &priority};
    VkDeviceCreateInfo device_info{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, &vulkan11, 0, 1, &queue, 0, nullptr,
                                   static_cast<uint32_t>(names.size()), names.data(), &features};
    VkDevice device;
    if (vkCreateDevice(gpu, &device_info, nullptr, &device) != VK_SUCCESS) {
        vkDestroyInstance(instance, nullptr);
        std::printf("FAIL: vkCreateDevice with the layer\n");
        return 1;
    }

    int failures = 0;
    struct Command {
        const char *name, *extension;
    };
    const Command commands[] = {
        {"vkGetMemoryFdKHR", "VK_KHR_external_memory_fd"},
        {"vkGetSemaphoreFdKHR", "VK_KHR_external_semaphore_fd"},
        {"vkImportSemaphoreFdKHR", "VK_KHR_external_semaphore_fd"},
        {"vkGetFenceFdKHR", "VK_KHR_external_fence_fd"},
        {"vkWaitSemaphoresKHR", "VK_KHR_timeline_semaphore"},
        {"vkGetSemaphoreCounterValueKHR", "VK_KHR_timeline_semaphore"},
        {"vkQueueSubmit2KHR", "VK_KHR_synchronization2"},
    };
    for (const auto &command : commands) {
        bool there = vkGetDeviceProcAddr(device, command.name) != nullptr;
        bool expected = offered(offers, command.extension);
        std::printf("%-32s %-10s (%s %s)\n", command.name, there ? "there" : "missing", command.extension,
                    expected ? "offered" : "not offered");
        if (expected && !there) failures++;
    }

    // A timeline semaphore, signalled and waited on through the KHR names, as SteamVR synchronises with its compositor.
    auto signal = reinterpret_cast<PFN_vkSignalSemaphoreKHR>(vkGetDeviceProcAddr(device, "vkSignalSemaphoreKHR"));
    auto wait = reinterpret_cast<PFN_vkWaitSemaphoresKHR>(vkGetDeviceProcAddr(device, "vkWaitSemaphoresKHR"));
    if (offered(offers, "VK_KHR_timeline_semaphore")) {
        VkSemaphoreTypeCreateInfo type{VK_STRUCTURE_TYPE_SEMAPHORE_TYPE_CREATE_INFO, nullptr, VK_SEMAPHORE_TYPE_TIMELINE, 0};
        VkSemaphoreCreateInfo semaphore_info{VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO, &type, 0};
        VkSemaphore semaphore;
        if (!signal || !wait || vkCreateSemaphore(device, &semaphore_info, nullptr, &semaphore) != VK_SUCCESS) {
            std::printf("FAIL: no timeline semaphore\n");
            failures++;
        } else {
            uint64_t value = 3;
            VkSemaphoreSignalInfo signal_info{VK_STRUCTURE_TYPE_SEMAPHORE_SIGNAL_INFO, nullptr, semaphore, value};
            VkSemaphoreWaitInfo wait_info{VK_STRUCTURE_TYPE_SEMAPHORE_WAIT_INFO, nullptr, 0, 1, &semaphore, &value};
            VkResult signalled = signal(device, &signal_info), waited = wait(device, &wait_info, 100000000ull);
            std::printf("timeline semaphore: signal %d, wait %d\n", signalled, waited);
            if (signalled != VK_SUCCESS || waited != VK_SUCCESS) failures++;
            vkDestroySemaphore(device, semaphore, nullptr);
        }
    }
    vkDestroyDevice(device, nullptr);
    vkDestroyInstance(instance, nullptr);
    std::printf(failures ? "FAIL: %d\n" : "PASS\n", failures);
    return failures ? 1 : 0;
}
