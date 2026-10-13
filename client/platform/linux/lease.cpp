// VROSTBridge: expose one gamescope-owned DRM lease through the standard
// Wayland leasing protocol. We never sublease or drop DRM master on that FD.
#include <wayland-server.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include "lease_protocol/drm-lease-v1-server.h"
#include "core/headset_edid.hpp"
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <sys/prctl.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>

namespace vrost_lease {
static int openCard(const std::string& path){int fd=open(path.c_str(),O_RDWR|O_CLOEXEC);if(fd>=0 && drmIsMaster(fd)!=0){close(fd);return -1;}return fd;}
struct Server { int (*identityOpener)(const std::string&)=openCard; wl_display* display=nullptr; int leased=-1; std::string card,name; uint32_t connector=0; bool consumed=false; std::vector<wl_resource*> connectors; };
struct Request { Server* server; bool selected=false; };
static void destroy(wl_client*,wl_resource* r){wl_resource_destroy(r);}
static void connectorGone(wl_resource* r){auto* s=static_cast<Server*>(wl_resource_get_user_data(r));auto& v=s->connectors;v.erase(std::remove(v.begin(),v.end(),r),v.end());}
static const struct wp_drm_lease_connector_v1_interface connectorImpl={destroy};
static void leaseGone(wl_resource*) { std::cout<<"LEASE_RELEASED (SteamVR must exit before another session)"<<std::endl; }
static const struct wp_drm_lease_v1_interface leaseImpl={destroy};
static void requestGone(wl_resource* r){delete static_cast<Request*>(wl_resource_get_user_data(r));}
static void selectConnector(wl_client*,wl_resource* r,wl_resource* connector){
    auto* request=static_cast<Request*>(wl_resource_get_user_data(r));
    if(!wl_resource_instance_of(connector,&wp_drm_lease_connector_v1_interface,&connectorImpl) || wl_resource_get_user_data(connector)!=request->server){wl_resource_post_error(r,WP_DRM_LEASE_REQUEST_V1_ERROR_WRONG_DEVICE,"Connector is not on this device");return;}
    if(request->selected){wl_resource_post_error(r,WP_DRM_LEASE_REQUEST_V1_ERROR_DUPLICATE_CONNECTOR,"Connector already requested");return;}
    request->selected=true;
}
static void submit(wl_client* client,wl_resource* r,uint32_t id){
    auto* q=static_cast<Request*>(wl_resource_get_user_data(r));auto* s=q->server;
    if(!q->selected){wl_resource_post_error(r,WP_DRM_LEASE_REQUEST_V1_ERROR_EMPTY_LEASE,"No connector requested");return;}
    auto* lease=wl_resource_create(client,&wp_drm_lease_v1_interface,1,id);
    if(!lease){wl_client_post_no_memory(client);return;}
    wl_resource_set_implementation(lease,&leaseImpl,s,leaseGone);
    if(s->consumed){wp_drm_lease_v1_send_finished(lease);}else{
        s->consumed=true;
        for(auto* c:s->connectors)wp_drm_lease_connector_v1_send_withdrawn(c);
        wp_drm_lease_v1_send_lease_fd(lease,s->leased);
        std::cout<<"LEASE_GRANTED connector="<<s->connector<<std::endl;
    }
    wl_resource_destroy(r);
}
static const struct wp_drm_lease_request_v1_interface requestImpl={selectConnector,submit};
static void createRequest(wl_client* c,wl_resource* r,uint32_t id){
    auto* n=wl_resource_create(c,&wp_drm_lease_request_v1_interface,1,id);
    if(!n){wl_client_post_no_memory(c);return;}
    auto* q=new Request{static_cast<Server*>(wl_resource_get_user_data(r)),false};
    wl_resource_set_implementation(n,&requestImpl,q,requestGone);
}
static void release(wl_client*,wl_resource* r){wp_drm_lease_device_v1_send_released(r);wl_resource_destroy(r);}
static const struct wp_drm_lease_device_v1_interface deviceImpl={createRequest,release};
// 1.0.0.10: which program reached this display and whether it took the lease, so a Game Mode
// start that fails says whether SteamVR's compositor ever came here (the 1.0.0.9 test could not tell).
static std::string program(pid_t pid){
    std::string name;int fd=open(("/proc/"+std::to_string(pid)+"/comm").c_str(),O_RDONLY|O_CLOEXEC);
    if(fd>=0){char text[64]{};auto n=read(fd,text,sizeof(text)-1);close(fd);if(n>0)name.assign(text,size_t(n));}
    while(!name.empty()&&(name.back()=='\n'||name.back()=='\r'))name.pop_back();
    for(auto& ch:name)if(ch<' '||ch=='"'||ch=='\\')ch='?';
    return name.empty()?"unknown":name;
}
static void clientCreated(wl_listener*,void* data){
    pid_t pid=0;uid_t uid=0;wl_client_get_credentials(static_cast<wl_client*>(data),&pid,&uid,nullptr);
    std::cout<<"LEASE_CLIENT connected pid="<<pid<<" program="<<program(pid)<<std::endl;
}
static wl_listener clientListener{{nullptr,nullptr},clientCreated};
static void bind(wl_client* c,void* data,uint32_t,uint32_t id){
    pid_t pid=0;uid_t uid;wl_client_get_credentials(c,&pid,&uid,nullptr);
    if(uid!=getuid()){wl_client_destroy(c);return;}
    std::cout<<"LEASE_CLIENT bound pid="<<pid<<" program="<<program(pid)<<std::endl;
    auto* s=static_cast<Server*>(data);auto* r=wl_resource_create(c,&wp_drm_lease_device_v1_interface,1,id);
    if(!r){wl_client_post_no_memory(c);return;}
    wl_resource_set_implementation(r,&deviceImpl,s,nullptr);
    // This is a fresh NON-master card FD for device identity, not the lease FD.
    int fd=s->identityOpener(s->card);
    if(fd<0){wl_resource_post_error(r,0,"Could not open non-master DRM device");return;}
    wp_drm_lease_device_v1_send_drm_fd(r,fd);close(fd);
    if(!s->consumed){
        auto* connector=wl_resource_create(c,&wp_drm_lease_connector_v1_interface,1,0);
        if(!connector){wl_client_post_no_memory(c);return;}
        wl_resource_set_implementation(connector,&connectorImpl,s,connectorGone);s->connectors.push_back(connector);
        wp_drm_lease_device_v1_send_connector(r,connector);
        wp_drm_lease_connector_v1_send_name(connector,s->name.c_str());
        wp_drm_lease_connector_v1_send_description(connector,"VROSTBridge headset display");
        wp_drm_lease_connector_v1_send_connector_id(connector,s->connector);
        wp_drm_lease_connector_v1_send_done(connector);
    }
    wp_drm_lease_device_v1_send_done(r);
}
static std::runtime_error failure(const std::string& s){return std::runtime_error(s+": "+strerror(errno));}
static void privateDirectory(const std::string& p){struct stat st{};if(lstat(p.c_str(),&st) || !S_ISDIR(st.st_mode) || st.st_uid!=getuid() || (st.st_mode&077))throw std::runtime_error("Runtime directory is not private");}
static int receiveLease(const std::string& path,int& socketFd){
    if(path.size()>=sizeof(sockaddr_un::sun_path))throw std::runtime_error("Lease socket path too long");
    socketFd=socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC,0);if(socketFd<0)throw failure("socket");
    sockaddr_un addr{};addr.sun_family=AF_UNIX;strcpy(addr.sun_path,path.c_str());
    if(connect(socketFd,reinterpret_cast<sockaddr*>(&addr),sizeof(addr)))throw failure("Connect to gamescope lease");
    ucred credentials{};socklen_t size=sizeof(credentials);
    if(getsockopt(socketFd,SOL_SOCKET,SO_PEERCRED,&credentials,&size) || credentials.uid!=getuid())throw std::runtime_error("Lease socket owner mismatch");
    pollfd p{socketFd,POLLIN,0};if(poll(&p,1,5000)!=1)throw std::runtime_error("Timed out waiting for gamescope lease");
    char tag=0; iovec iov{&tag,1};alignas(cmsghdr) char control[CMSG_SPACE(sizeof(int)*8)]{};msghdr msg{};
    msg.msg_iov=&iov;msg.msg_iovlen=1;msg.msg_control=control;msg.msg_controllen=sizeof(control);
    auto count=recvmsg(socketFd,&msg,MSG_CMSG_CLOEXEC);std::vector<int> received;
    for(auto* c=CMSG_FIRSTHDR(&msg);c;c=CMSG_NXTHDR(&msg,c))if(c->cmsg_level==SOL_SOCKET && c->cmsg_type==SCM_RIGHTS){
        size_t n=(c->cmsg_len-CMSG_LEN(0))/sizeof(int);auto* f=reinterpret_cast<int*>(CMSG_DATA(c));received.insert(received.end(),f,f+n);
    }
    if(count!=1 || tag!='L' || (msg.msg_flags&(MSG_CTRUNC|MSG_TRUNC)) || received.size()!=1){for(int f:received)close(f);throw std::runtime_error("Invalid gamescope lease response");}
    return received.front();
}
static std::vector<unsigned char> unhex(const std::string& hex){
    if(hex.size()<256 || hex.size()>65536 || hex.size()%2)throw std::runtime_error("Invalid headset EDID length");std::vector<unsigned char> bytes;
    for(size_t i=0;i<hex.size();i+=2){auto a=hex[i],b=hex[i+1];auto digit=[](char c){return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;};if(digit(a)<0||digit(b)<0)throw std::runtime_error("Invalid EDID encoding");bytes.push_back(digit(a)*16+digit(b));}return bytes;
}
static void validate(Server& s,const std::string& hex){
    auto edid=unhex(hex);
    // RC4.3: the kernel's head-mounted display markers, not only two product codes (core/headset_edid.hpp),
    // or the kernel's own non-desktop flag on the leased connector (checked below).
    const bool markedHeadset=vrost::headsetModel(edid.data(),edid.size())!=nullptr;
    struct stat card{},leased{};
    if(stat(s.card.c_str(),&card)||fstat(s.leased,&leased)||!S_ISCHR(card.st_mode)||!S_ISCHR(leased.st_mode)||card.st_rdev!=leased.st_rdev)throw std::runtime_error("Lease DRM card mismatch");
    if(drmSetClientCap(s.leased,DRM_CLIENT_CAP_UNIVERSAL_PLANES,1))throw failure("Universal planes");
    auto* res=drmModeGetResources(s.leased);bool valid=res&&res->count_connectors==1&&res->connectors[0]==s.connector&&res->count_crtcs==1;
    if(res)drmModeFreeResources(res);if(!valid)throw std::runtime_error("Lease resources do not match the headset");
    auto* con=drmModeGetConnector(s.leased,s.connector);valid=con&&con->connection==DRM_MODE_CONNECTED&&con->connector_type==DRM_MODE_CONNECTOR_DisplayPort;
    bool nonDesktop=false;
    if(con){valid=valid&&(s.name=="DP-"+std::to_string(con->connector_type_id));bool matched=false;
        for(int i=0;i<con->count_props;i++){auto* prop=drmModeGetProperty(s.leased,con->props[i]);if(prop){if(!strcmp(prop->name,"EDID")){auto* blob=drmModeGetPropertyBlob(s.leased,con->prop_values[i]);if(blob){matched=blob->length==edid.size()&&!memcmp(blob->data,edid.data(),edid.size());drmModeFreePropertyBlob(blob);}}
            else if(!strcmp(prop->name,"non-desktop"))nonDesktop=con->prop_values[i]!=0;drmModeFreeProperty(prop);}}
        valid=valid&&matched;drmModeFreeConnector(con);
    }
    if(!valid)throw std::runtime_error("The leased connector or EDID changed");
    if(!markedHeadset&&!nonDesktop)throw std::runtime_error("Display is not a recognized headset");
    auto* planes=drmModeGetPlaneResources(s.leased);valid=planes&&planes->count_planes>=1;if(planes)drmModeFreePlaneResources(planes);if(!valid)throw std::runtime_error("Lease has no display plane");
}
// RC4.3: one JSON line per connected connector of every card, read without probing the outputs.
// The kernel's "non-desktop" flag is what compositors use to offer a display for leasing; it also
// covers headsets only the kernel's quirk list knows. Read-only: no DRM master is needed.
static void listConnectors(std::ostream& out){
    for(int index=0;index<16;++index){
        const std::string card="/dev/dri/card"+std::to_string(index);
        int fd=open(card.c_str(),O_RDONLY|O_CLOEXEC);if(fd<0)continue;
        // The first opener of a card no compositor holds becomes its DRM master; give that up at once
        // so a compositor starting at this moment (a Desktop/Game Mode switch) is never refused it.
        if(drmIsMaster(fd))drmDropMaster(fd);
        if(auto* res=drmModeGetResources(fd)){
            for(int i=0;i<res->count_connectors;i++){
                auto* con=drmModeGetConnectorCurrent(fd,res->connectors[i]);if(!con)continue;
                if(con->connection==DRM_MODE_CONNECTED){
                    bool nonDesktop=false;std::string edid;
                    for(int p=0;p<con->count_props;p++){auto* prop=drmModeGetProperty(fd,con->props[p]);if(!prop)continue;
                        if(!strcmp(prop->name,"non-desktop"))nonDesktop=con->prop_values[p]!=0;
                        else if(!strcmp(prop->name,"EDID")&&con->prop_values[p]){if(auto* blob=drmModeGetPropertyBlob(fd,uint32_t(con->prop_values[p]))){
                            static const char digits[]="0123456789abcdef";auto* bytes=static_cast<const unsigned char*>(blob->data);
                            for(uint32_t b=0;b<blob->length&&b<32768;b++){edid+=digits[bytes[b]>>4];edid+=digits[bytes[b]&15];}
                            drmModeFreePropertyBlob(blob);}}
                        drmModeFreeProperty(prop);}
                    const char* type=drmModeGetConnectorTypeName(con->connector_type);
                    std::string name=std::string(type?type:"Unknown")+"-"+std::to_string(con->connector_type_id);
                    if(name.find_first_of("\"\\\n\r")==std::string::npos)
                        out<<"{\"card\":\""<<card<<"\",\"name\":\""<<name<<"\",\"connector_id\":"<<con->connector_id
                           <<",\"non_desktop\":"<<(nonDesktop?1:0)<<",\"edid_hex\":\""<<edid<<"\"}\n";
                }
                drmModeFreeConnector(con);
            }
            drmModeFreeResources(res);
        }
        close(fd);
    }
    out.flush();
}
static int signalStop(int,void* data){wl_display_terminate(static_cast<wl_display*>(data));return 0;}
static int upstream(int fd,uint32_t mask,void* data){
    char byte;auto result=recv(fd,&byte,1,MSG_DONTWAIT);
    if((mask&(WL_EVENT_HANGUP|WL_EVENT_ERROR)) || result==0 || result>0 || (result<0&&errno!=EAGAIN&&errno!=EINTR)){
        std::cerr<<"Gamescope lease connection closed"<<std::endl;wl_display_terminate(static_cast<wl_display*>(data));
    }return 0;
}
}
#ifndef VROST_LEASE_NO_MAIN
int main(int argc,char** argv){
    using namespace vrost_lease;
    if(argc==2&&std::string(argv[1])=="--list-connectors"){listConnectors(std::cout);return 0;}
    Server server;int upstreamFd=-1;std::string path;int result=1;
    try{
        std::string socket,hex;for(int i=1;i<argc;i+=2){if(i+1==argc)throw std::runtime_error("Missing option value");std::string a=argv[i],v=argv[i+1];
            if(a=="--gamescope-socket")socket=v;else if(a=="--connector")server.name=v;else if(a=="--card")server.card=v;else if(a=="--connector-id"){size_t used;auto n=std::stoul(v,&used);if(used!=v.size()||n>UINT32_MAX||!n)throw std::runtime_error("Invalid connector ID");server.connector=n;}else if(a=="--edid-hex")hex=v;else throw std::runtime_error("Unknown lease option");}
        if(socket.empty()||server.card.empty()||!server.connector)throw std::runtime_error("Missing lease parameters");
        auto parent=getppid();prctl(PR_SET_PDEATHSIG,SIGTERM);if(getppid()!=parent)throw std::runtime_error("Companion exited");
        const char* env=getenv("XDG_RUNTIME_DIR");if(!env||env[0]!='/')throw std::runtime_error("No graphical runtime directory");privateDirectory(env);
        server.leased=receiveLease(socket,upstreamFd);validate(server,hex);
        // 1.0.0.10: the socket sits in the runtime directory itself under a plain name, as Wayland
        // displays normally do (WAYLAND_DISPLAY=vrostbridge-headset-PID), not as a path in a folder of
        // its own: libraries and container runtimes that only look up names find it. The runtime
        // directory is private to this user (checked above), and the socket is the user's only.
        const std::string name="vrostbridge-headset-"+std::to_string(getpid());
        const std::string where=std::string(env)+"/"+name;if(where.size()>=sizeof(sockaddr_un::sun_path))throw std::runtime_error("Wayland path too long");
        // A socket of this user's left by an earlier helper with the same process number is replaced; anything else is not touched.
        {struct stat old{};if(!lstat(where.c_str(),&old)){if(!S_ISSOCK(old.st_mode)||old.st_uid!=getuid())throw std::runtime_error("Wayland name already taken");unlink(where.c_str());}}
        int fd=::socket(AF_UNIX,SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK,0);if(fd<0)throw failure("Wayland socket");
        sockaddr_un addr{};addr.sun_family=AF_UNIX;strcpy(addr.sun_path,where.c_str());
        if(::bind(fd,reinterpret_cast<sockaddr*>(&addr),sizeof(addr))){close(fd);throw failure("Open private Wayland socket");}
        path=where;  // ours from here on: removed at exit
        if(chmod(path.c_str(),0600)||listen(fd,8)){close(fd);throw failure("Open private Wayland socket");}
        server.display=wl_display_create();if(!server.display){close(fd);throw std::runtime_error("Wayland initialization failed");}
        if(wl_display_add_socket_fd(server.display,fd)){close(fd);throw failure("Register Wayland socket");}
        wl_display_add_client_created_listener(server.display,&clientListener);
        if(!wl_global_create(server.display,&wp_drm_lease_device_v1_interface,1,&server,vrost_lease::bind))throw std::runtime_error("Cannot advertise DRM lease");
        auto* loop=wl_display_get_event_loop(server.display);auto* term=wl_event_loop_add_signal(loop,SIGTERM,signalStop,server.display);auto* interrupt=wl_event_loop_add_signal(loop,SIGINT,signalStop,server.display);auto* peer=wl_event_loop_add_fd(loop,upstreamFd,WL_EVENT_READABLE,upstream,server.display);
        if(!term||!interrupt||!peer)throw std::runtime_error("Cannot monitor display lifetime");
        // Paths are restricted below to a JSON-safe runtime path.
        if(path.find_first_of("\"\\\n\r")!=std::string::npos)throw std::runtime_error("Unsupported runtime path");
        std::cout<<"LEASE_READY {\"wayland_display\":\""<<name<<"\",\"path\":\""<<path<<"\",\"connector_id\":"<<server.connector<<"}"<<std::endl;
        wl_display_run(server.display);wl_event_source_remove(term);wl_event_source_remove(interrupt);wl_event_source_remove(peer);result=0;
    }catch(const std::exception& e){std::cerr<<"LEASE_ERROR "<<e.what()<<std::endl;}
    if(server.display){wl_display_destroy_clients(server.display);wl_display_destroy(server.display);}
    if(server.leased>=0)close(server.leased);if(upstreamFd>=0)close(upstreamFd);
    if(!path.empty())unlink(path.c_str());return result;
}
#endif
