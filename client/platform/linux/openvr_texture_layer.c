/* VROSTBridge Vulkan layer for SteamVR overlay textures (part of VROSTBridge, MIT licence).
 *
 * SteamVR's client library (vrclient.so) takes an OpenVR overlay's Vulkan texture through the calling program's own
 * VkDevice: to share the picture with SteamVR's compositor it calls external memory and semaphore commands on that
 * device, such as vkGetSemaphoreFdKHR. Mesa's drivers give NULL for a command whose extension the program did not
 * enable, and SteamVR calls it anyway. Baballonia 1.1.1.0rc6's calibration, a Godot 4.5 program, enables
 * VK_KHR_external_memory_fd but not VK_KHR_external_semaphore_fd: started as a SteamVR overlay it crashed inside
 * IVROverlay::SetOverlayTexture (a call to address 0 in vrclient.so) as soon as it showed its first picture. Windows
 * drivers return such commands anyway, which is why the same program works there.
 *
 * VROSTBridge's calibration launcher enables this layer for that one program, and only when it starts as a SteamVR
 * overlay (VK_ADD_LAYER_PATH and VK_INSTANCE_LAYERS in its environment; nothing is installed for other programs).
 * When the program creates its Vulkan instance and device, the layer adds the external memory, semaphore and fence
 * extensions SteamVR uses, and what they build on, where the driver offers them and the program did not ask for
 * them, and switches on the timeline semaphore and synchronization2 features with them. It changes nothing else and
 * calls nothing on its own. If the driver refuses the longer list, the program's own list is used. For each device
 * it writes one line to stderr (Baballonia's log): what it added, and which commands SteamVR calls are still missing.
 *
 * The list matches what rcannood/steamvr-room-setup-on-fedora found SteamVR Room Setup needs on Fedora's Mesa for the
 * same reason (Room Setup enables none of them); this layer is VROSTBridge's own code. */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#define EXPORT __attribute__((visibility("default")))
#define COUNT(array) (sizeof(array) / sizeof *(array))
#define TRACKED 64
#define PREFIX "VROSTBridge Vulkan layer: "

static const char *const INSTANCE_EXTENSIONS[] = {
    "VK_KHR_get_physical_device_properties2", "VK_KHR_external_memory_capabilities",
    "VK_KHR_external_semaphore_capabilities", "VK_KHR_external_fence_capabilities",
};

static const char *const DEVICE_EXTENSIONS[] = {
    "VK_KHR_external_memory",           "VK_KHR_external_memory_fd",       "VK_KHR_external_semaphore",
    "VK_KHR_external_semaphore_fd",     "VK_KHR_external_fence",           "VK_KHR_external_fence_fd",
    "VK_KHR_dedicated_allocation",      "VK_KHR_get_memory_requirements2", "VK_KHR_bind_memory2",
    "VK_KHR_image_format_list",         "VK_EXT_external_memory_dma_buf",  "VK_EXT_queue_family_foreign",
    "VK_EXT_image_drm_format_modifier", "VK_KHR_timeline_semaphore",       "VK_KHR_synchronization2",
};

/* What SteamVR's client library calls on the program's device to share a texture with its compositor. Mesa gives the
 * KHR names of the timeline commands only with their extension enabled, even on a Vulkan 1.2 device. */
static const char *const STEAMVR_COMMANDS[] = {
    "vkGetMemoryFdKHR",           "vkGetSemaphoreFdKHR", "vkImportSemaphoreFdKHR",
    "vkWaitSemaphores",           "vkWaitSemaphoresKHR", "vkGetSemaphoreCounterValue",
    "vkGetSemaphoreCounterValueKHR",
};

struct instance_entry {
    void *key;
    VkInstance handle;
    PFN_vkGetInstanceProcAddr next;
    int features2; /* 2: vkGetPhysicalDeviceFeatures2 may be called (Vulkan 1.1), 1: its KHR form, 0: neither */
};

struct device_entry {
    void *key;
    PFN_vkGetDeviceProcAddr next;
};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static struct instance_entry instances[TRACKED];
static struct device_entry devices[TRACKED];

/* Dispatchable handles start with the loader's dispatch pointer: one key for an instance and its physical devices,
 * and one for a device, whichever layer passes them on. */
static void *dispatch_key(const void *object) { return object ? *(void *const *)object : NULL; }

static void remember_instance(VkInstance handle, PFN_vkGetInstanceProcAddr next, int features2) {
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++)
        if (!instances[i].key) {
            instances[i] = (struct instance_entry){dispatch_key(handle), handle, next, features2};
            break;
        }
    pthread_mutex_unlock(&lock);
}

/* The instance with this key; with one instance only, that one (another layer may have wrapped the handle). */
static struct instance_entry find_instance(void *key) {
    struct instance_entry found = {0}, only = {0};
    int count = 0;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++) {
        if (!instances[i].key) continue;
        count++;
        only = instances[i];
        if (instances[i].key == key) found = instances[i];
    }
    pthread_mutex_unlock(&lock);
    return found.key ? found : (count == 1 ? only : found);
}

static void forget_instance(void *key) {
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++)
        if (instances[i].key == key) instances[i] = (struct instance_entry){0};
    pthread_mutex_unlock(&lock);
}

static void remember_device(VkDevice handle, PFN_vkGetDeviceProcAddr next) {
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++)
        if (!devices[i].key) {
            devices[i] = (struct device_entry){dispatch_key(handle), next};
            break;
        }
    pthread_mutex_unlock(&lock);
}

static PFN_vkGetDeviceProcAddr find_device(void *key) {
    PFN_vkGetDeviceProcAddr next = NULL;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++)
        if (devices[i].key == key) next = devices[i].next;
    pthread_mutex_unlock(&lock);
    return next;
}

static void forget_device(void *key) {
    pthread_mutex_lock(&lock);
    for (int i = 0; i < TRACKED; i++)
        if (devices[i].key == key) devices[i] = (struct device_entry){0};
    pthread_mutex_unlock(&lock);
}

static int listed(const char *name, uint32_t count, const char *const *names) {
    for (uint32_t i = 0; i < count; i++)
        if (names[i] && !strcmp(names[i], name)) return 1;
    return 0;
}

static int offered(const char *name, uint32_t count, const VkExtensionProperties *offers) {
    for (uint32_t i = 0; i < count; i++)
        if (!strcmp(offers[i].extensionName, name)) return 1;
    return 0;
}

static void append(char *text, size_t size, const char *separator, const char *item) {
    size_t used = strlen(text);
    if (used + strlen(separator) + strlen(item) + 1 > size) return;
    snprintf(text + used, size - used, "%s%s", used ? separator : "", item);
}

/* The program's extensions followed by the wanted ones it lacks (with offers, only those the driver offers); the
 * added names go to *added. NULL when nothing is added or memory runs out: the program's own list is used then. */
static const char **extended(uint32_t count, const char *const *names, const char *const *wanted, size_t wanted_count,
                             const VkExtensionProperties *offers, uint32_t offer_count, uint32_t *out_count, char *added,
                             size_t added_size) {
    added[0] = '\0';
    const char **list = calloc((size_t)count + wanted_count, sizeof *list);
    if (!list) return NULL;
    uint32_t n = 0;
    for (uint32_t i = 0; i < count; i++) list[n++] = names[i];
    for (size_t i = 0; i < wanted_count; i++) {
        if (listed(wanted[i], count, names) || (offers && !offered(wanted[i], offer_count, offers))) continue;
        list[n++] = wanted[i];
        append(added, added_size, ", ", wanted[i]);
    }
    if (n == count) {
        free(list);
        return NULL;
    }
    *out_count = n;
    return list;
}

static int chained(const void *next, VkStructureType type) {
    for (const VkBaseInStructure *item = next; item; item = item->pNext)
        if (item->sType == type) return 1;
    return 0;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vrost_GetInstanceProcAddr(VkInstance instance, const char *name);
EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vrost_GetDeviceProcAddr(VkDevice device, const char *name);

static VKAPI_ATTR VkResult VKAPI_CALL layer_CreateInstance(const VkInstanceCreateInfo *info,
                                                          const VkAllocationCallbacks *allocator, VkInstance *instance) {
    VkLayerInstanceCreateInfo *link = (VkLayerInstanceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerInstanceCreateInfo *)link->pNext;
    if (!link || !link->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr next = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    VkLayerInstanceLink *below = link->u.pLayerInfo->pNext;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)next(VK_NULL_HANDLE, "vkCreateInstance");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    link->u.pLayerInfo = below;

    /* All four are core in Vulkan 1.1. The loader passes each driver only the instance extensions it offers. */
    uint32_t count = 0;
    char added[512];
    const char **names = extended(info->enabledExtensionCount, info->ppEnabledExtensionNames, INSTANCE_EXTENSIONS,
                                  COUNT(INSTANCE_EXTENSIONS), NULL, 0, &count, added, sizeof added);
    uint32_t version = info->pApplicationInfo ? info->pApplicationInfo->apiVersion : 0;
    int core = VK_API_VERSION_MAJOR(version) > 1 || (VK_API_VERSION_MAJOR(version) == 1 && VK_API_VERSION_MINOR(version) >= 1);
    int properties2 = listed("VK_KHR_get_physical_device_properties2", info->enabledExtensionCount,
                             info->ppEnabledExtensionNames);
    VkResult result;
    if (names) {
        VkInstanceCreateInfo changed = *info;
        changed.enabledExtensionCount = count;
        changed.ppEnabledExtensionNames = names;
        result = create(&changed, allocator, instance);
        if (result == VK_SUCCESS) {
            properties2 = listed("VK_KHR_get_physical_device_properties2", count, names);
        } else {
            fprintf(stderr, PREFIX "the instance with %s added was refused (%d); the program's own list is used.\n", added,
                    (int)result);
            link->u.pLayerInfo = below;
            result = create(info, allocator, instance);
        }
        free(names);
    } else {
        result = create(info, allocator, instance);
    }
    if (result == VK_SUCCESS) remember_instance(*instance, next, core ? 2 : properties2);
    return result;
}

static VKAPI_ATTR void VKAPI_CALL layer_DestroyInstance(VkInstance instance, const VkAllocationCallbacks *allocator) {
    struct instance_entry entry = find_instance(dispatch_key(instance));
    PFN_vkDestroyInstance destroy = entry.next ? (PFN_vkDestroyInstance)entry.next(instance, "vkDestroyInstance") : NULL;
    forget_instance(dispatch_key(instance));
    if (destroy) destroy(instance, allocator);
}

/* One line per device: what was added, and whether every command SteamVR calls is now there. */
static void report(VkDevice device, PFN_vkGetDeviceProcAddr next, const char *added, const char *features, int refused) {
    char missing[512] = "";
    for (size_t i = 0; i < COUNT(STEAMVR_COMMANDS); i++)
        if (!next(device, STEAMVR_COMMANDS[i])) append(missing, sizeof missing, ", ", STEAMVR_COMMANDS[i]);
    char what[1536] = "";
    if (refused)
        snprintf(what, sizeof what, "the driver refused the added extensions, so the program's Vulkan device has only its own");
    else if (added[0] || features[0])
        snprintf(what, sizeof what, "added %s%s%s%s to the program's Vulkan device for SteamVR's overlay textures", added,
                 added[0] && features[0] ? " and " : "", features[0] ? "the features " : "", features);
    else
        snprintf(what, sizeof what, "the program's Vulkan device already had what SteamVR's overlay textures use");
    if (missing[0])
        fprintf(stderr, PREFIX "%s. Still missing (this driver does not offer them): %s.\n", what, missing);
    else
        fprintf(stderr, PREFIX "%s. The commands SteamVR calls on it are all there.\n", what);
}

static VKAPI_ATTR VkResult VKAPI_CALL layer_CreateDevice(VkPhysicalDevice gpu, const VkDeviceCreateInfo *info,
                                                        const VkAllocationCallbacks *allocator, VkDevice *device) {
    VkLayerDeviceCreateInfo *link = (VkLayerDeviceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerDeviceCreateInfo *)link->pNext;
    if (!link || !link->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr next_instance = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr next_device = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    VkLayerDeviceLink *below = link->u.pLayerInfo->pNext;
    struct instance_entry owner = find_instance(dispatch_key(gpu));
    VkInstance instance = owner.handle;
    PFN_vkCreateDevice create = (PFN_vkCreateDevice)next_instance(instance, "vkCreateDevice");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    link->u.pLayerInfo = below;

    /* Without the driver's own list nothing is added: an extension it does not offer fails device creation. */
    PFN_vkEnumerateDeviceExtensionProperties enumerate =
        (PFN_vkEnumerateDeviceExtensionProperties)next_instance(instance, "vkEnumerateDeviceExtensionProperties");
    uint32_t offer_count = 0;
    VkExtensionProperties *offers = NULL;
    if (enumerate && enumerate(gpu, NULL, &offer_count, NULL) == VK_SUCCESS && offer_count) {
        offers = calloc(offer_count, sizeof *offers);
        if (offers && enumerate(gpu, NULL, &offer_count, offers) != VK_SUCCESS) {
            free(offers);
            offers = NULL;
        }
    }
    uint32_t count = 0;
    char added[1024] = "";
    const char **names = offers ? extended(info->enabledExtensionCount, info->ppEnabledExtensionNames, DEVICE_EXTENSIONS,
                                           COUNT(DEVICE_EXTENSIONS), offers, offer_count, &count, added, sizeof added)
                                : NULL;
    free(offers);
    uint32_t extension_count = names ? count : info->enabledExtensionCount;
    const char *const *extension_names = names ? names : info->ppEnabledExtensionNames;

    /* SteamVR waits on timeline semaphores and may use synchronization2. With their extension enabled (or core in the
     * device's version) their features are switched on too, where the driver supports them and the program did not
     * set them itself, alone or in its Vulkan 1.2 or 1.3 feature structure. */
    VkPhysicalDeviceTimelineSemaphoreFeatures timeline = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
    VkPhysicalDeviceSynchronization2Features sync2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES};
    int want_timeline = listed("VK_KHR_timeline_semaphore", extension_count, extension_names) &&
                        !chained(info->pNext, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES) &&
                        !chained(info->pNext, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES);
    int want_sync2 = listed("VK_KHR_synchronization2", extension_count, extension_names) &&
                     !chained(info->pNext, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES) &&
                     !chained(info->pNext, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES);
    if (want_timeline || want_sync2) {
        /* Only as the program itself may call it: Vulkan 1.1, or the KHR form with its instance extension. */
        PFN_vkGetPhysicalDeviceFeatures2 features2 = NULL;
        if (owner.features2 == 2)
            features2 = (PFN_vkGetPhysicalDeviceFeatures2)next_instance(instance, "vkGetPhysicalDeviceFeatures2");
        if (!features2 && owner.features2)
            features2 = (PFN_vkGetPhysicalDeviceFeatures2)next_instance(instance, "vkGetPhysicalDeviceFeatures2KHR");
        VkPhysicalDeviceTimelineSemaphoreFeatures has_timeline = {
            VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_TIMELINE_SEMAPHORE_FEATURES};
        VkPhysicalDeviceSynchronization2Features has_sync2 = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SYNCHRONIZATION_2_FEATURES,
                                                              &has_timeline};
        VkPhysicalDeviceFeatures2 supported = {VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, &has_sync2};
        if (features2) features2(gpu, &supported);
        want_timeline = want_timeline && features2 && has_timeline.timelineSemaphore;
        want_sync2 = want_sync2 && features2 && has_sync2.synchronization2;
    }
    char features[128] = "";
    const void *chain = info->pNext;
    if (want_timeline) {
        timeline.pNext = (void *)chain;
        timeline.timelineSemaphore = VK_TRUE;
        chain = &timeline;
        append(features, sizeof features, " and ", "timelineSemaphore");
    }
    if (want_sync2) {
        sync2.pNext = (void *)chain;
        sync2.synchronization2 = VK_TRUE;
        chain = &sync2;
        append(features, sizeof features, " and ", "synchronization2");
    }

    VkResult result;
    int refused = 0;
    if (names || chain != info->pNext) {
        VkDeviceCreateInfo changed = *info;
        changed.pNext = chain;
        changed.enabledExtensionCount = extension_count;
        changed.ppEnabledExtensionNames = extension_names;
        result = create(gpu, &changed, allocator, device);
        if (result != VK_SUCCESS) {
            fprintf(stderr, PREFIX "the device with %s%s%s added was refused (%d); the program's own list is used.\n",
                    added, added[0] && features[0] ? " and the features " : features[0] ? "the features " : "", features,
                    (int)result);
            refused = 1;
            link->u.pLayerInfo = below;
            result = create(gpu, info, allocator, device);
        }
    } else {
        result = create(gpu, info, allocator, device);
    }
    free(names);
    if (result == VK_SUCCESS) {
        remember_device(*device, next_device);
        report(*device, next_device, added, features, refused);
    }
    return result;
}

static VKAPI_ATTR void VKAPI_CALL layer_DestroyDevice(VkDevice device, const VkAllocationCallbacks *allocator) {
    PFN_vkGetDeviceProcAddr next = find_device(dispatch_key(device));
    PFN_vkDestroyDevice destroy = next ? (PFN_vkDestroyDevice)next(device, "vkDestroyDevice") : NULL;
    forget_device(dispatch_key(device));
    if (destroy) destroy(device, allocator);
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vrost_GetDeviceProcAddr(VkDevice device, const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)vrost_GetDeviceProcAddr;
    if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)layer_DestroyDevice;
    PFN_vkGetDeviceProcAddr next = find_device(dispatch_key(device));
    return next ? next(device, name) : NULL;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vrost_GetInstanceProcAddr(VkInstance instance, const char *name) {
    if (!name) return NULL;
    if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)vrost_GetInstanceProcAddr;
    if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)layer_CreateInstance;
    if (!strcmp(name, "vkDestroyInstance")) return (PFN_vkVoidFunction)layer_DestroyInstance;
    if (!strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)layer_CreateDevice;
    if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)vrost_GetDeviceProcAddr;
    if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)layer_DestroyDevice;
    if (!instance) return NULL;
    struct instance_entry entry = find_instance(dispatch_key(instance));
    return entry.next ? entry.next(instance, name) : NULL;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *version) {
    if (!version || version->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT) return VK_ERROR_INITIALIZATION_FAILED;
    if (version->loaderLayerInterfaceVersion >= 2) {
        version->pfnGetInstanceProcAddr = vrost_GetInstanceProcAddr;
        version->pfnGetDeviceProcAddr = vrost_GetDeviceProcAddr;
        version->pfnGetPhysicalDeviceProcAddr = NULL;
        version->loaderLayerInterfaceVersion = 2;
    }
    return VK_SUCCESS;
}
