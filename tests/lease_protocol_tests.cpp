// Real libwayland socketpair roundtrip. No DRM hardware required: the protocol
// core gets an explicit test FD; production validation has no bypass switch.
#define VROST_LEASE_NO_MAIN
#include "client/platform/linux/lease.cpp"
#include <wayland-client.h>
#include "client/platform/linux/lease_protocol/drm-lease-v1-client.h"
#include <atomic>
#include <thread>
#include <cassert>
struct Client {wp_drm_lease_device_v1* device=nullptr;wp_drm_lease_connector_v1* connector=nullptr;int received=0;bool withdrawn=false,finished=false;uint32_t id=0;};
static const wp_drm_lease_connector_v1_listener conListener={
 [](void*,auto*,const char*){},[](void*,auto*,const char*){},[](void* p,auto*,uint32_t n){static_cast<Client*>(p)->id=n;},[](void*,auto*){},[](void* p,auto*){static_cast<Client*>(p)->withdrawn=true;}};
static const wp_drm_lease_device_v1_listener devListener={
 [](void*,auto*,int fd){close(fd);},[](void* p,auto*,wp_drm_lease_connector_v1* connector){auto* c=static_cast<Client*>(p);c->connector=connector;wp_drm_lease_connector_v1_add_listener(connector,&conListener,p);},[](void*,auto*){},[](void*,auto*){}};
static const wl_registry_listener registryListener={
 [](void* p,wl_registry* registry,uint32_t name,const char* interface,uint32_t){if(!strcmp(interface,"wp_drm_lease_device_v1")){auto* c=static_cast<Client*>(p);c->device=static_cast<wp_drm_lease_device_v1*>(wl_registry_bind(registry,name,&wp_drm_lease_device_v1_interface,1));wp_drm_lease_device_v1_add_listener(c->device,&devListener,p);}},[](void*,auto*,uint32_t){}};
static const wp_drm_lease_v1_listener leaseListener={
 [](void* p,auto*,int fd){auto* c=static_cast<Client*>(p);c->received++;char data[5]{};assert(pread(fd,data,4,0)==4);assert(!strcmp(data,"test"));close(fd);},[](void* p,auto*){static_cast<Client*>(p)->finished=true;}};
int main(){
 using namespace vrost_lease;Server s;s.display=wl_display_create();assert(s.display);s.card="/dev/null";s.identityOpener=[](const std::string& path){return open(path.c_str(),O_RDONLY|O_CLOEXEC);};s.name="DP-2";s.connector=123;
 char file[]="/tmp/vrostbridge-lease-test-XXXXXX";s.leased=mkstemp(file);assert(s.leased>=0);unlink(file);assert(write(s.leased,"test",4)==4);
 assert(wl_global_create(s.display,&wp_drm_lease_device_v1_interface,1,&s,vrost_lease::bind));
 int sockets[2];assert(socketpair(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0,sockets)==0);assert(wl_client_create(s.display,sockets[0]));
 auto* display=wl_display_connect_to_fd(sockets[1]);assert(display);Client c;auto* registry=wl_display_get_registry(display);wl_registry_add_listener(registry,&registryListener,&c);
 std::atomic<bool> stop=false;std::thread server([&]{while(!stop){wl_event_loop_dispatch(wl_display_get_event_loop(s.display),1);wl_display_flush_clients(s.display);}});
 assert(wl_display_roundtrip(display)>=0);assert(wl_display_roundtrip(display)>=0);assert(c.device&&c.connector&&c.id==123);
 auto* first=wp_drm_lease_device_v1_create_lease_request(c.device);wp_drm_lease_request_v1_request_connector(first,c.connector);auto* second=wp_drm_lease_device_v1_create_lease_request(c.device);wp_drm_lease_request_v1_request_connector(second,c.connector);
 // Requests remain valid after device.release and only one may consume the FD.
 wp_drm_lease_device_v1_release(c.device);
 auto* lease=wp_drm_lease_request_v1_submit(first);wp_drm_lease_v1_add_listener(lease,&leaseListener,&c);assert(wl_display_roundtrip(display)>=0);assert(c.received==1&&c.withdrawn);
 auto* denied=wp_drm_lease_request_v1_submit(second);wp_drm_lease_v1_add_listener(denied,&leaseListener,&c);assert(wl_display_roundtrip(display)>=0);assert(c.received==1&&c.finished);
 wp_drm_lease_v1_destroy(denied);wp_drm_lease_v1_destroy(lease);wp_drm_lease_connector_v1_destroy(c.connector);wl_registry_destroy(registry);wl_display_flush(display);wl_display_disconnect(display);
 stop=true;server.join();wl_display_destroy_clients(s.display);wl_display_destroy(s.display);close(s.leased);std::cout<<"Wayland lease roundtrip, FD transfer, release lifetime and single-grant checks passed\n";
}
