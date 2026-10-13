#pragma once
#include <windows.h>
#include <filesystem>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include "core/contracts.hpp"
namespace vrost {
template<class T> class Com {
    T* p_=nullptr;
public:
    Com()=default;~Com(){reset();}
    Com(const Com&)=delete;Com& operator=(const Com&)=delete;
    Com(Com&& o) noexcept:p_(std::exchange(o.p_,nullptr)){}
    Com& operator=(Com&& o) noexcept {reset();p_=std::exchange(o.p_,nullptr);return *this;}
    void reset(){if(p_)p_->Release();p_=nullptr;}
    T* get() const{return p_;} T* operator->() const{return p_;}
    T** put(){reset();return &p_;} explicit operator bool()const{return p_!=nullptr;}
};
inline void check(HRESULT hr,const char* label) {
    if(FAILED(hr)){std::ostringstream s;s<<label<<" failed (0x"<<std::hex<<static_cast<unsigned long>(hr)<<")";throw std::runtime_error(s.str());}
}
inline std::wstring wide(const std::string& s) {
    if(s.empty())return {};
    int n=MultiByteToWideChar(CP_UTF8,0,s.data(),static_cast<int>(s.size()),nullptr,0);
    std::wstring r(n,L' ');MultiByteToWideChar(CP_UTF8,0,s.data(),static_cast<int>(s.size()),r.data(),n);return r;
}
inline std::string utf8(const std::wstring& s) {
    if(s.empty())return {};
    int n=WideCharToMultiByte(CP_UTF8,0,s.data(),static_cast<int>(s.size()),nullptr,0,nullptr,nullptr);
    std::string r(n,' ');WideCharToMultiByte(CP_UTF8,0,s.data(),static_cast<int>(s.size()),r.data(),n,nullptr,nullptr);return r;
}
inline std::filesystem::path executableDir() {
    wchar_t p[32768]{};DWORD n=GetModuleFileNameW(nullptr,p,32768);
    if(n==0||n>=32768)throw std::runtime_error("Cannot locate application folder");
    return std::filesystem::path(p).parent_path();
}
inline double milliseconds() {
    static const double hz=[] {LARGE_INTEGER f;QueryPerformanceFrequency(&f);return double(f.QuadPart);}();
    LARGE_INTEGER t;QueryPerformanceCounter(&t);return 1000.0*double(t.QuadPart)/hz;
}
}
