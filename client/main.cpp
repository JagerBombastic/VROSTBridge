#include "client/renderer.hpp"
#include "client/gaze_probe.hpp"
#include <shlobj.h>
#include <shellapi.h>
#include <fstream>
#include <iomanip>
#include <memory>
#include <algorithm>
#include <ctime>

using namespace vrost;
namespace {
enum { Start=100, Preview, Stop, Delay, Tagged, Stall, Save, Folder, LeftHaptic, RightHaptic, Notes, Gaze };
class App {
public:
    HWND window=nullptr,mirror=nullptr,status=nullptr,notes=nullptr,delayBox=nullptr,taggedBox=nullptr;
    HFONT font=nullptr;
    Runtime runtime;
    GazeProbe gaze;
    std::unique_ptr<Renderer> renderer;
    bool active=false,preview=false,tracking=false,focus=false,tagged=true;
    int delay=0;
    std::uint64_t tick=0,epoch=1,sequence=0,presented=0,repeated=0,recoveryTicks=0,lastSequence=0;
    double startTime=0,pauseUntil=0,lastUi=0,frameAge=0,maxAge=0;
    std::string message="Ready. Connect a headset, start SteamVR, then select Start headset test.";
    std::string hardwareReport;
    std::filesystem::path reports,reportPath;
    std::string stopped="Not started";

    HWND control(const wchar_t* cls,const wchar_t* text,DWORD style,int id,int x,int y,int w,int h){
        HWND c=CreateWindowExW(0,cls,text,WS_CHILD|WS_VISIBLE|style,x,y,w,h,window,reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),GetModuleHandleW(nullptr),nullptr);
        SendMessageW(c,WM_SETFONT,reinterpret_cast<WPARAM>(font),TRUE);return c;
    }
    void initialise(HWND hwnd){
        window=hwnd;font=CreateFontW(-16,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,OUT_DEFAULT_PRECIS,CLIP_DEFAULT_PRECIS,CLEARTYPE_QUALITY,DEFAULT_PITCH,L"Segoe UI");
        control(L"STATIC",L"VROSTBridge 0.1  |  Local headset qualification",0,0,18,14,750,25);
        control(L"BUTTON",L"Start headset test",BS_PUSHBUTTON|WS_TABSTOP,Start,18,48,150,32);
        control(L"BUTTON",L"Desktop preview",BS_PUSHBUTTON|WS_TABSTOP,Preview,178,48,150,32);
        control(L"BUTTON",L"Stop",BS_PUSHBUTTON|WS_TABSTOP,Stop,338,48,70,32);
        control(L"BUTTON",L"Save report",BS_PUSHBUTTON|WS_TABSTOP,Save,418,48,110,32);
        control(L"BUTTON",L"Open reports",BS_PUSHBUTTON|WS_TABSTOP,Folder,538,48,115,32);
        control(L"BUTTON",L"Probe gaze",BS_AUTOCHECKBOX|WS_TABSTOP,Gaze,663,48,125,32);
        control(L"STATIC",L"Frame delay",0,0,18,99,90,24);
        delayBox=control(L"COMBOBOX",L"",CBS_DROPDOWNLIST|WS_TABSTOP|WS_VSCROLL,Delay,108,92,95,160);
        for(const wchar_t* text:{L"0 ms",L"11 ms",L"22 ms",L"33 ms"})SendMessageW(delayBox,CB_ADDSTRING,0,reinterpret_cast<LPARAM>(text));
        SendMessageW(delayBox,CB_SETCURSEL,0,0);
        taggedBox=control(L"BUTTON",L"Use source pose",BS_AUTOCHECKBOX|WS_TABSTOP,Tagged,218,93,165,28);
        SendMessageW(taggedBox,BM_SETCHECK,BST_CHECKED,0);
        control(L"BUTTON",L"500 ms stall test",BS_PUSHBUTTON|WS_TABSTOP,Stall,393,91,145,30);
        control(L"BUTTON",L"Left haptic",BS_PUSHBUTTON|WS_TABSTOP,LeftHaptic,548,91,105,30);
        control(L"BUTTON",L"Right haptic",BS_PUSHBUTTON|WS_TABSTOP,RightHaptic,663,91,110,30);
        control(L"STATIC",L"Hardware notes (headset PC model / dock / cable / selected headset mode)",0,0,18,134,800,22);
        notes=control(L"EDIT",L"",WS_BORDER|ES_AUTOHSCROLL|WS_TABSTOP,Notes,18,160,755,27);
        SendMessageW(notes,EM_SETLIMITTEXT,1000,0);
        status=control(L"STATIC",L"",0,0,18,198,780,148);
        control(L"STATIC",L"Left-eye source mirror  |  Red = left hand, blue = right  |  Esc stops the test",0,0,18,351,790,23);
        mirror=control(L"STATIC",L"",SS_BLACKRECT,0,18,379,755,253);
        PWSTR path=nullptr;
        check(SHGetKnownFolderPath(FOLDERID_LocalAppData,0,nullptr,&path),"Find local report folder");
        reports=std::filesystem::path(path)/L"VROSTBridge"/L"reports";CoTaskMemFree(path);std::filesystem::create_directories(reports);
        refreshUi();
    }
    void resetHistory(){++epoch;if(renderer)for(auto& m:renderer->meta)m.valid=false;lastSequence=0;}
    void start(bool desktop){
        stop();preview=desktop;hardwareReport.clear();stopped="Running";
        try{
            if(!preview)runtime.start();
            renderer=std::make_unique<Renderer>();
            const uint32_t rw=preview?1024:runtime.recommendedW,rh=preview?1024:runtime.recommendedH;
            if(!rw||!rh)throw std::runtime_error("SteamVR returned an empty render target");
            float scale=std::min(.5f,1280.f/float(std::max(rw,rh)));
            uint32_t width=preview?1024:std::max(256u,uint32_t(float(rw)*scale));
            uint32_t height=preview?1024:std::max(256u,uint32_t(float(rh)*scale));
            renderer->start(mirror,preview?0:runtime.adapter,width,height);
            tick=0;sequence=0;presented=0;repeated=0;recoveryTicks=0;lastSequence=0;maxAge=0;frameAge=0;
            startTime=milliseconds();pauseUntil=0;resetHistory();
            hardwareReport=preview?"Desktop preview only. No headset has been tested.\n":runtime.report();
            hardwareReport+="D3D11 adapter: "+renderer->adapterName+"\nActual test eye target: "+std::to_string(width)+" x "+std::to_string(height)+" (reduced resolution)\n";
            SYSTEMTIME time;GetLocalTime(&time);wchar_t name[100];swprintf(name,100,L"session_%04u%02u%02u_%02u%02u%02u_%lu.txt",time.wYear,time.wMonth,time.wDay,time.wHour,time.wMinute,time.wSecond,GetCurrentProcessId());reportPath=reports/name;
            active=true;message=preview?"Desktop preview active. This does not qualify DP/USB, tracking or reprojection.":"Headset test active. Begin with 0 ms; verify the scene and system menu before delay tests.";
            saveReport();
        }catch(const std::exception& e){message=e.what();stopped="Start failed";renderer.reset();runtime.stop();saveReport();}
        refreshUi();
    }
    void stop(){
        if(active){active=false;stopped="Stopped by user or runtime";saveReport();}
        if(runtime.compositor)runtime.compositor->ClearLastSubmittedFrame();
        runtime.stop();renderer.reset();gaze.close();tracking=false;focus=false;
    }
    void saveReport(){
        if(reports.empty())return;
        if(reportPath.empty())reportPath=reports/L"startup_diagnostics.txt";
        wchar_t userNotes[1001]{};GetWindowTextW(notes,userNotes,1001);
        std::ostringstream out;
        out<<"VROSTBridge 0.1.0 - local qualification report\n";
        out<<"Status: "<<stopped<<"\nMessage: "<<message<<"\nMode: "<<(preview?"Desktop preview":"SteamVR headset")<<"\nHardware notes: "<<utf8(userNotes)<<"\n\n"<<hardwareReport;
        out<<"\nSelected delay ms: "<<delay<<"\nUse source pose: "<<(tagged?"yes":"no - mismatch control experiment")<<"\nConfiguration epoch: "<<epoch<<"\nLoop ticks: "<<tick<<"\nScene submit ticks: "<<presented<<"\nRepeated source submissions: "<<repeated<<"\nNeutral recovery ticks: "<<recoveryTicks<<"\nMaximum selected source age ms: "<<maxAge<<"\n";
        out<<"Age is measured from local pose query to submission selection. It is not sensor-to-photon latency.\nCounters are application counters, not compositor or physical display measurements.\n\n";
        out<<"Manual hardware result: NOT RECORDED - confirm in the hardware checklist.\nDP/DSC operation: NOT automatically certified.\nALVR networking / video decode: NOT implemented in this milestone.\nBeyond gaze: optional read-only local probe; host integration NOT implemented.\nGaze values and controller action states are shown live but are not stored here.\n";
        out<<"\nOpenVR source commit: 0924064316de3effbcd1acf1e309182a2deb1c05\nPlan: engineering revision 0.2\n";
        auto temporary=reportPath;temporary+=L".tmp";
        std::ofstream file(temporary,std::ios::binary);if(!file)throw std::runtime_error("Cannot write diagnostics report");file<<out.str();file.close();if(!file)throw std::runtime_error("Cannot finish diagnostics report");
        if(!MoveFileExW(temporary.c_str(),reportPath.c_str(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH))throw std::runtime_error("Cannot replace diagnostics report");
    }
    void refreshUi(){
        std::ostringstream s;s<<message<<"\r\n";
        if(active){
            s<<(preview?"Preview":runtime.model)<<" | "<<renderer->adapterName<<" | "<<renderer->width<<" x "<<renderer->height<<" per eye\r\n";
            s<<std::fixed<<std::setprecision(1)<<"Source age "<<frameAge<<" ms | "<<(tracking?"Tracking valid":"Tracking unavailable")<<" | "<<(focus?"App focused":"Local recovery")<<" | scene/repeat/recovery "<<presented<<"/"<<repeated<<"/"<<recoveryTicks<<"\r\n";
            if(!preview){s<<runtime.inputStatus<<"\r\n";for(int h=0;h<2;++h){s<<(h==0?"L ":"R ")<<(runtime.controllers[h].valid?"tracked ":"missing ");for(const char* n:{"trigger","grip","stick","stick_y","a","b"}){auto it=runtime.controllers[h].values.find(n);if(it!=runtime.controllers[h].values.end())s<<n<<":"<<it->second<<" ";}s<<"\r\n";}}
            s<<gaze.status<<"\r\n";
        }else s<<"This first milestone tests local display and tracking. Network streaming and gaze follow next.\r\nReports are saved locally; use Open reports to retrieve them.";
        SetWindowTextW(status,wide(s.str()).c_str());
    }
    void frame(){
        if(!active)return;
        try{
            ++tick;Pose pose;bool changed=false,quit=false;
            if(preview){pose.m[1][3]=1.6f;tracking=true;focus=true;}
            else tracking=runtime.sample(pose,focus,changed,quit);
            if(quit){message="SteamVR requested exit.";stop();refreshUi();return;}
            if(changed){message="Tracking space or eye geometry changed. Restart the test to re-query headset geometry.";stop();refreshUi();return;}
            const double now=milliseconds();renderer->poll();gaze.poll(now);
            if(!tracking||!focus)resetHistory();
            if(tracking&&focus&&now>=pauseUntil){
                int slot=-1;
                for(int n=0;n<kFrames;++n){int i=int((tick+uint64_t(n))%kFrames);if(!renderer->frames[i].pending&&(renderer->frames[i].lastSubmit==0||tick>renderer->frames[i].lastSubmit+3)){slot=i;break;}}
                if(slot>=0){
                    auto& m=renderer->meta[slot];m.valid=true;m.ready=false;m.sequence=++sequence;m.epoch=epoch;m.captureMs=now;m.sourcePose=pose;
                    std::array<Pose,2> eyes=runtime.eyeToHead;std::array<Fov,2> fov=runtime.fov;
                    if(preview){eyes[0].m[0][3]=-.032f;eyes[1].m[0][3]=.032f;}
                    renderer->render(slot,pose,eyes,fov,runtime.controllers,now-startTime);
                }
            }
            int selected=selectFrame(renderer->meta,now,double(delay),epoch);
            if(viewState(tracking,focus,selected)!=ViewState::Scene)selected=-1;
            if(selected>=0){
                const auto& m=renderer->meta[selected];frameAge=now-m.captureMs;maxAge=std::max(maxAge,frameAge);
                if(!preview)renderer->submit(runtime,selected,tagged,tick);else renderer->frames[selected].lastSubmit=tick;
                ++presented;if(lastSequence==m.sequence)++repeated;lastSequence=m.sequence;
            }else{++recoveryTicks;frameAge=0;if(!preview)renderer->recovery(runtime);}
            renderer->presentMirror(selected);
            if(now-lastUi>200){refreshUi();lastUi=now;}
        }catch(const std::exception& e){message=e.what();stopped="Runtime error";active=false;try{saveReport();}catch(...){}stop();refreshUi();}
    }
    void command(int id,int notification){
        try{
            switch(id){
            case Start:start(false);break;case Preview:start(true);break;
            case Stop:message="Test stopped. The latest report has been saved.";stop();refreshUi();break;
            case Delay:if(notification==CBN_SELCHANGE){const int values[]={0,11,22,33};auto i=SendMessageW(delayBox,CB_GETCURSEL,0,0);delay=values[std::clamp<int>(int(i),0,3)];resetHistory();}break;
            case Tagged:tagged=SendMessageW(taggedBox,BM_GETCHECK,0,0)==BST_CHECKED;resetHistory();break;
            case Gaze:gaze.enabled=SendMessageW(GetDlgItem(window,Gaze),BM_GETCHECK,0,0)==BST_CHECKED;if(!gaze.enabled)gaze.close();break;
            case Stall:pauseUntil=milliseconds()+500;message="Stall injected: the scene should become neutral after 100 ms, then recover.";break;
            case Save:saveReport();message="Report saved. Use Open reports to retrieve it.";refreshUi();break;
            case Folder:ShellExecuteW(window,L"open",reports.c_str(),nullptr,nullptr,SW_SHOWNORMAL);break;
            case LeftHaptic:if(active&&!preview&&focus)runtime.haptic(0);break;
            case RightHaptic:if(active&&!preview&&focus)runtime.haptic(1);break;
            }
        }catch(const std::exception& e){message=e.what();refreshUi();}
    }
};
App* app=nullptr;
LRESULT CALLBACK procedure(HWND hwnd,UINT msg,WPARAM w,LPARAM l){
    if(app){
        if(msg==WM_COMMAND){app->command(LOWORD(w),HIWORD(w));return 0;}
        if(msg==WM_CLOSE){try{app->stop();}catch(...){}DestroyWindow(hwnd);return 0;}
        if(msg==WM_DESTROY){PostQuitMessage(0);return 0;}
        if(msg==WM_GETMINMAXINFO){auto* limits=reinterpret_cast<MINMAXINFO*>(l);limits->ptMinTrackSize.x=830;limits->ptMinTrackSize.y=600;return 0;}
        if(msg==WM_SIZE&&app->mirror){int width=LOWORD(l),height=HIWORD(l);MoveWindow(app->mirror,18,379,std::max(1,width-36),std::max(1,height-397),TRUE);MoveWindow(app->status,18,198,std::max(1,width-36),148,TRUE);}
    }
    return DefWindowProcW(hwnd,msg,w,l);
}
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,PWSTR,int show){
    // Per-system DPI avoids scaling a fixed-size window beyond a handheld screen.
    SetProcessDPIAware();CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
    int result=0;
    try{
        App state;app=&state;
        WNDCLASSW cls{};cls.lpfnWndProc=procedure;cls.hInstance=instance;cls.hCursor=LoadCursorW(nullptr,IDC_ARROW);cls.hbrBackground=reinterpret_cast<HBRUSH>(COLOR_WINDOW+1);cls.lpszClassName=L"VROSTBridgeWindow";
        if(!RegisterClassW(&cls))throw std::runtime_error("Cannot register application window");
        HWND hwnd=CreateWindowW(cls.lpszClassName,L"VROSTBridge - Headset Qualification",WS_OVERLAPPEDWINDOW,CW_USEDEFAULT,CW_USEDEFAULT,840,720,nullptr,nullptr,instance,nullptr);
        if(!hwnd)throw std::runtime_error("Cannot create application window");
        state.initialise(hwnd);ShowWindow(hwnd,show);
        MSG msg{};bool running=true;
        while(running){
            while(PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){
                if(msg.message==WM_QUIT){running=false;break;}
                if(msg.message==WM_KEYDOWN&&msg.wParam==VK_ESCAPE){state.command(Stop,0);continue;}
                if(!IsDialogMessageW(hwnd,&msg)){TranslateMessage(&msg);DispatchMessageW(&msg);}
            }
            if(!running)break;
            if(state.active){state.frame();if(state.preview)MsgWaitForMultipleObjects(0,nullptr,FALSE,8,QS_ALLINPUT);}
            else MsgWaitForMultipleObjects(0,nullptr,FALSE,100,QS_ALLINPUT);
        }
        state.stop();DeleteObject(state.font);app=nullptr;
    }catch(const std::exception& e){MessageBoxW(nullptr,wide(e.what()).c_str(),L"VROSTBridge",MB_OK|MB_ICONERROR);result=1;}
    CoUninitialize();return result;
}
