#pragma once
#include "client/runtime.hpp"
#include <d3d11.h>
#include <dxgi.h>
namespace vrost {
constexpr int kFrames=12;
struct Surface { Com<ID3D11Texture2D> texture;Com<ID3D11RenderTargetView> target;Com<ID3D11ShaderResourceView> view; };
struct Frame { std::array<Surface,2> eyes;Com<ID3D11Query> completed;std::uint64_t lastSubmit=0;bool pending=false; };
class Renderer {
    Com<ID3D11Device> device_;
    Com<ID3D11DeviceContext> context_;
    Com<IDXGISwapChain> swapchain_;
    Com<ID3D11RenderTargetView> mirrorTarget_;
    Com<ID3D11VertexShader> vertex_;
    Com<ID3D11PixelShader> scene_,mirror_;
    Com<ID3D11Buffer> constants_;
    Com<ID3D11SamplerState> sampler_;
    HWND mirrorWindow_=nullptr;
    Surface recovery_[2];
    void surface(Surface& surface);
public:
    std::array<Frame,kFrames> frames;
    std::array<FrameMeta,kFrames> meta;
    uint32_t width=0,height=0;
    std::string adapterName;
    void start(HWND mirrorWindow,int adapter,uint32_t eyeWidth,uint32_t eyeHeight);
    void render(int slot,const Pose& pose,const std::array<Pose,2>& eyes,const std::array<Fov,2>& fov,const std::array<Controller,2>& controllers,double time);
    void poll();
    void presentMirror(int slot);
    void submit(Runtime& runtime,int slot,bool tagged,std::uint64_t tick);
    void recovery(Runtime& runtime);
};
}
