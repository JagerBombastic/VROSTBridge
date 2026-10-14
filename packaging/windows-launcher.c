/* VROSTBridge native entry: no PowerShell, no C runtime, capture errors before Python loads.
   Built as "VROSTBridge Host.exe" or, with -DVROST_CLIENT -DVROST_VERSION="<version>", as "VROSTBridge Client.exe"
   (packaging/build_windows_launcher.py --role host|client). The host's strings below are its exact earlier ones. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#ifdef VROST_CLIENT
#ifndef VROST_VERSION
#error "Build the client launcher with packaging/build_windows_launcher.py --role client (it passes VROST_VERSION)."
#endif
#define PRODUCT L"VROSTBridge Client"
#define LOG_NAME L"\\client-native.log"
#define ROLE_ARGUMENT L" --role client"
#define ARGUMENT_HELP L"Unsupported launcher argument. Use --background to start without opening the page or --self-test to check installation files."
#define BANNER "\r\n--- VROSTBridge Client native launcher " VROST_VERSION " ---\r\n"
#else
#define PRODUCT L"VROSTBridge Host"
#define LOG_NAME L"\\host-native.log"
#define ROLE_ARGUMENT L" --role host"
#define ARGUMENT_HELP L"Unsupported launcher argument. Use --background for the waiting host service or --self-test to check installation files."
#define BANNER "\r\n--- VROSTBridge Host native launcher 1.0.0.12 ---\r\n"
#endif
void *memset(void *destination, int value, size_t count) { volatile unsigned char *p=destination; while(count--)*p++=(unsigned char)value; return destination; }
static wchar_t root[32768], python[32768], command[32768], logpath[32768], state[32768];
static wchar_t message[4096];
static void fail(const wchar_t *what, DWORD code) {
    wchar_t detail[512] = L"";
    FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, NULL, code, 0, detail, 512, NULL);
    wsprintfW(message,L"%ls\n\nWindows code: %lu\n%ls\n\nStartup log: %ls\nIf a file is missing, rerun the " PRODUCT L" installer to repair it.",what,code,detail,logpath);
    MessageBoxW(NULL,message,PRODUCT L" could not start",MB_OK|MB_ICONERROR);
}
void WINAPI WinMainCRTStartup(void) {
    DWORD result=1;
    DWORD n=GetModuleFileNameW(NULL,root,32768);
    if(!n || n>=32000){fail(L"Cannot locate the installation folder.",GetLastError());ExitProcess(1);}
    wchar_t *slash=root+n; while(slash>root && *slash!=L'\\')--slash; *slash=0;
    if(SHGetFolderPathW(NULL,CSIDL_LOCAL_APPDATA|CSIDL_FLAG_CREATE,NULL,SHGFP_TYPE_CURRENT,state)!=S_OK){fail(L"Cannot locate Local AppData.",GetLastError());ExitProcess(1);}
    lstrcatW(state,L"\\VROSTBridge");CreateDirectoryW(state,NULL);
    lstrcpyW(logpath,state);lstrcatW(logpath,LOG_NAME);
    SECURITY_ATTRIBUTES sa={sizeof(sa),NULL,TRUE};
    HANDLE log=CreateFileW(logpath,FILE_APPEND_DATA,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,NULL);
    if(log==INVALID_HANDLE_VALUE){fail(L"Cannot create the startup log.",GetLastError());ExitProcess(1);}
    HANDLE input=CreateFileW(L"NUL",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,&sa,OPEN_EXISTING,0,NULL);
    lstrcpyW(python,root);lstrcatW(python,L"\\runtime\\python\\python.exe");
    int argc=0;wchar_t **argv=CommandLineToArgvW(GetCommandLineW(),&argc);
    BOOL selftest=argc==2 && lstrcmpW(argv[1],L"--self-test")==0;
    BOOL background=argc==2 && lstrcmpW(argv[1],L"--background")==0;
    if(!argv || argc>2 || (argc==2 && !selftest && !background)) {
        fail(ARGUMENT_HELP,ERROR_INVALID_PARAMETER);
        ExitProcess(ERROR_INVALID_PARAMETER);
    }
    /* Bounded by root<32000 above; two quoted root paths need tighter Win32 commandline bound. */
    if(lstrlenW(root)>15000){fail(L"Installation path is too long.",ERROR_FILENAME_EXCED_RANGE);ExitProcess(1);}
    lstrcpyW(command,L"\""); lstrcatW(command,python); lstrcatW(command,L"\" -u \"");
    lstrcatW(command,root); lstrcatW(command,L"\\launcher\\bootstrap.py\"" ROLE_ARGUMENT);
    if(selftest)lstrcatW(command,L" --self-test");
    if(background)lstrcatW(command,L" --background");
    SetEnvironmentVariableW(L"VROSTBRIDGE_NATIVE_LAUNCHER",L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8",L"1");
    const char start[]=BANNER;DWORD written;
    WriteFile(log,start,sizeof(start)-1,&written,NULL);
    STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESTDHANDLES|STARTF_USESHOWWINDOW;
    si.wShowWindow=SW_HIDE;si.hStdOutput=log;si.hStdError=log;si.hStdInput=input;
    PROCESS_INFORMATION pi={0};
    if(!CreateProcessW(python,command,NULL,NULL,TRUE,CREATE_NO_WINDOW,NULL,root,&si,&pi)) {
        result=GetLastError();fail(L"The bundled Python runtime did not start.",result);
    } else {
        CloseHandle(pi.hThread);WaitForSingleObject(pi.hProcess,INFINITE);GetExitCodeProcess(pi.hProcess,&result);CloseHandle(pi.hProcess);
        if(result!=0 && !selftest)fail(PRODUCT L" stopped during startup or execution. The startup log contains the underlying error.",result);
    }
    if(argv)LocalFree(argv);CloseHandle(input);CloseHandle(log);ExitProcess(result);
}
