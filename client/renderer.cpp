#include "client/renderer.hpp"
#include <d3dcompiler.h>
#include <algorithm>
#include <cstring>
namespace vrost {
static const char* shader=R"HLSL(
cbuffer Parameters : register(b0) {
    float4 row0; float4 row1; float4 row2;
    float4 fov; float4 hand0; float4 hand1; float4 settings;
};
struct Vertex { float4 position : SV_Position; float2 uv : TEXCOORD0; };
Vertex vs(uint id : SV_VertexID) {
    Vertex o; o.uv=float2((id<<1)&2,id&2);
    o.position=float4(o.uv.x*2-1,1-o.uv.y*2,0,1); return o;
}
float hitSphere(float3 ro,float3 rd,float3 centre,float radius) {
    float3 oc=ro-centre; float b=dot(oc,rd);float c=dot(oc,oc)-radius*radius;
    float discriminant=b*b-c;if(discriminant<0)return 10000;
    float t=-b-sqrt(discriminant);return t>0.03?t:10000;
}
float4 ps(Vertex i) : SV_Target {
    float3 local=normalize(float3(lerp(fov.x,fov.y,i.uv.x),-lerp(fov.z,fov.w,i.uv.y),-1));
    float3 rd=normalize(float3(dot(row0.xyz,local),dot(row1.xyz,local),dot(row2.xyz,local)));
    float3 ro=float3(row0.w,row1.w,row2.w);
    float3 colour=float3(.022,.032,.05); float nearest=10000;
    if(rd.y<-.001) {
        float t=-ro.y/rd.y;
        if(t>.03&&t<25) {
            nearest=t;float3 p=ro+rd*t;
            float2 cell=abs(frac(p.xz+.5)-.5);
            float grid=1-step(.018,min(cell.x,cell.y));
            colour=lerp(float3(.035,.045,.06),float3(.20,.23,.27),grid)*exp(-t*.045);
            if(abs(p.x)<.025)colour=float3(.12,.24,.5);
            if(abs(p.z)<.025)colour=float3(.5,.15,.10);
        }
    }
    [unroll]for(int n=0;n<5;++n) {
        float3 centre=float3((n-2)*.65,1.4,-2.5);
        float t=hitSphere(ro,rd,centre,.14);
        if(t<nearest) {nearest=t;float3 normal=normalize(ro+rd*t-centre);
            float3 base=n==0?float3(.7,.07,.06):n==4?float3(.05,.35,.75):float3(.15,.6,.25);
            colour=base*(.25+.75*max(0,dot(normal,normalize(float3(-.5,1,1)))));
        }
    }
    float3 moving=float3(sin(settings.x)*.45,.8,-1.6);
    float t=hitSphere(ro,rd,moving,.075);
    if(t<nearest){nearest=t;colour=float3(.65,.4,.04);}
    [unroll]for(int h=0;h<2;++h) {
        float4 hand=h==0?hand0:hand1;
        if(hand.w>=0) {
            float ht=hitSphere(ro,rd,hand.xyz,.035+.02*hand.w);
            if(ht<nearest){nearest=ht;colour=h==0?float3(.65,.08,.07):float3(.06,.3,.8);}
        }
    }
    // Tiny corner marks identify the source eye in the desktop mirror / headset.
    if(i.uv.x<.025&&i.uv.y<.08)colour=settings.y<.5?float3(.65,.08,.07):float3(.06,.3,.8);
    return float4(pow(saturate(colour),1.0/2.2),1);
}
Texture2D source : register(t0); SamplerState linearSampler : register(s0);
float4 mirror(Vertex i):SV_Target {return source.Sample(linearSampler,i.uv);}
)HLSL";
static Com<ID3DBlob> compile(const char* entry,const char* target){
    Com<ID3DBlob> blob,error;
    HRESULT hr=D3DCompile(shader,std::strlen(shader),"VROSTBridge scene",nullptr,nullptr,entry,target,D3DCOMPILE_ENABLE_STRICTNESS|D3DCOMPILE_OPTIMIZATION_LEVEL3,0,blob.put(),error.put());
    if(FAILED(hr))throw std::runtime_error(error?std::string(static_cast<const char*>(error->GetBufferPointer()),error->GetBufferSize()):"Shader compilation failed");
    return blob;
}
void Renderer::surface(Surface& out){
    D3D11_TEXTURE2D_DESC desc{};desc.Width=width;desc.Height=height;desc.MipLevels=1;desc.ArraySize=1;
    desc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;desc.Usage=D3D11_USAGE_DEFAULT;
    desc.BindFlags=D3D11_BIND_RENDER_TARGET|D3D11_BIND_SHADER_RESOURCE;
    check(device_->CreateTexture2D(&desc,nullptr,out.texture.put()),"Create eye texture");
    check(device_->CreateRenderTargetView(out.texture.get(),nullptr,out.target.put()),"Create eye target");
    check(device_->CreateShaderResourceView(out.texture.get(),nullptr,out.view.put()),"Create eye view");
}
void Renderer::start(HWND mirrorWindow,int adapter,uint32_t eyeWidth,uint32_t eyeHeight){
    mirrorWindow_=mirrorWindow;width=eyeWidth;height=eyeHeight;
    Com<IDXGIFactory1> factory;check(CreateDXGIFactory1(__uuidof(IDXGIFactory1),reinterpret_cast<void**>(factory.put())),"Create DXGI factory");
    Com<IDXGIAdapter1> gpu;check(factory->EnumAdapters1(static_cast<UINT>(std::max(0,adapter)),gpu.put()),"Select headset GPU");
    DXGI_ADAPTER_DESC1 info{};check(gpu->GetDesc1(&info),"Read GPU description");adapterName=utf8(info.Description);
    D3D_FEATURE_LEVEL level{};const D3D_FEATURE_LEVEL wanted[]={D3D_FEATURE_LEVEL_11_0};
    check(D3D11CreateDevice(gpu.get(),D3D_DRIVER_TYPE_UNKNOWN,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,wanted,1,D3D11_SDK_VERSION,device_.put(),&level,context_.put()),"Create D3D11 device");
    DXGI_SWAP_CHAIN_DESC desc{};desc.BufferDesc.Width=900;desc.BufferDesc.Height=400;
    desc.BufferDesc.Format=DXGI_FORMAT_R8G8B8A8_UNORM;desc.SampleDesc.Count=1;desc.BufferUsage=DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.BufferCount=2;desc.OutputWindow=mirrorWindow;desc.Windowed=TRUE;desc.SwapEffect=DXGI_SWAP_EFFECT_DISCARD;
    check(factory->CreateSwapChain(device_.get(),&desc,swapchain_.put()),"Create desktop mirror");
    factory->MakeWindowAssociation(mirrorWindow,DXGI_MWA_NO_ALT_ENTER);
    Com<ID3D11Texture2D> back;check(swapchain_->GetBuffer(0,__uuidof(ID3D11Texture2D),reinterpret_cast<void**>(back.put())),"Get mirror texture");
    check(device_->CreateRenderTargetView(back.get(),nullptr,mirrorTarget_.put()),"Create mirror view");
    auto vs=compile("vs","vs_5_0"),ps=compile("ps","ps_5_0"),ms=compile("mirror","ps_5_0");
    check(device_->CreateVertexShader(vs->GetBufferPointer(),vs->GetBufferSize(),nullptr,vertex_.put()),"Create vertex shader");
    check(device_->CreatePixelShader(ps->GetBufferPointer(),ps->GetBufferSize(),nullptr,scene_.put()),"Create scene shader");
    check(device_->CreatePixelShader(ms->GetBufferPointer(),ms->GetBufferSize(),nullptr,mirror_.put()),"Create mirror shader");
    D3D11_BUFFER_DESC buffer{};buffer.ByteWidth=7*16;buffer.Usage=D3D11_USAGE_DEFAULT;buffer.BindFlags=D3D11_BIND_CONSTANT_BUFFER;
    check(device_->CreateBuffer(&buffer,nullptr,constants_.put()),"Create scene constants");
    D3D11_SAMPLER_DESC samp{};samp.Filter=D3D11_FILTER_MIN_MAG_MIP_LINEAR;samp.AddressU=samp.AddressV=samp.AddressW=D3D11_TEXTURE_ADDRESS_CLAMP;samp.MaxLOD=D3D11_FLOAT32_MAX;
    check(device_->CreateSamplerState(&samp,sampler_.put()),"Create mirror sampler");
    for(auto& frame:frames){for(auto& eye:frame.eyes)surface(eye);D3D11_QUERY_DESC q{D3D11_QUERY_EVENT,0};check(device_->CreateQuery(&q,frame.completed.put()),"Create GPU completion marker");}
    const float dark[]={.025f,.03f,.04f,1};
    for(auto& eye:recovery_){surface(eye);context_->ClearRenderTargetView(eye.target.get(),dark);}
    context_->Flush();
}
void Renderer::render(int slot,const Pose& pose,const std::array<Pose,2>& eyes,const std::array<Fov,2>& fov,const std::array<Controller,2>& controllers,double time){
    ID3D11ShaderResourceView* none=nullptr;context_->PSSetShaderResources(0,1,&none);
    context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    context_->VSSetShader(vertex_.get(),nullptr,0);context_->PSSetShader(scene_.get(),nullptr,0);
    D3D11_VIEWPORT viewport{0,0,float(width),float(height),0,1};context_->RSSetViewports(1,&viewport);
    for(int e=0;e<2;++e){
        float constants[28]{};auto eye=compose(pose,eyes[e]);std::memcpy(constants,eye.m,sizeof(eye.m));
        constants[12]=fov[e].left;constants[13]=fov[e].right;constants[14]=fov[e].top;constants[15]=fov[e].bottom;
        for(int h=0;h<2;++h){const auto& hand=controllers[h];int at=16+h*4;constants[at]=hand.pose.m[0][3];constants[at+1]=hand.pose.m[1][3];constants[at+2]=hand.pose.m[2][3];auto it=hand.values.find("trigger");constants[at+3]=hand.valid?(it==hand.values.end()?0:it->second):-1;}
        constants[24]=float(time*.001);constants[25]=float(e);
        context_->UpdateSubresource(constants_.get(),0,nullptr,constants,0,0);auto cb=constants_.get();context_->PSSetConstantBuffers(0,1,&cb);
        auto rt=frames[slot].eyes[e].target.get();context_->OMSetRenderTargets(1,&rt,nullptr);context_->Draw(3,0);
    }
    context_->OMSetRenderTargets(0,nullptr,nullptr);
    context_->End(frames[slot].completed.get());frames[slot].pending=true;meta[slot].ready=false;context_->Flush();
}
void Renderer::poll(){for(int i=0;i<kFrames;++i)if(frames[i].pending){
    BOOL completed=FALSE;auto hr=context_->GetData(frames[i].completed.get(),&completed,sizeof(completed),D3D11_ASYNC_GETDATA_DONOTFLUSH);
    if(FAILED(hr))check(hr,"Poll GPU completion");
    if(hr==S_OK&&completed){frames[i].pending=false;meta[i].ready=true;}
}}
void Renderer::presentMirror(int slot){
    const float dark[]={.015f,.018f,.025f,1};context_->ClearRenderTargetView(mirrorTarget_.get(),dark);
    auto rt=mirrorTarget_.get();context_->OMSetRenderTargets(1,&rt,nullptr);
    RECT client{};GetClientRect(mirrorWindow_,&client);
    const float cw=float(std::max<LONG>(1,client.right)),ch=float(std::max<LONG>(1,client.bottom));
    const float displayW=std::min(cw,ch*float(width)/float(height));
    const float w=displayW*900.f/cw,h=(displayW*float(height)/float(width))*400.f/ch;
    D3D11_VIEWPORT vp{(900-w)*.5f,(400-h)*.5f,w,h,0,1};context_->RSSetViewports(1,&vp);
    auto view=slot>=0?frames[slot].eyes[0].view.get():recovery_[0].view.get();context_->PSSetShaderResources(0,1,&view);
    auto sampler=sampler_.get();context_->PSSetSamplers(0,1,&sampler);
    context_->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);context_->VSSetShader(vertex_.get(),nullptr,0);context_->PSSetShader(mirror_.get(),nullptr,0);context_->Draw(3,0);
    ID3D11ShaderResourceView* none=nullptr;context_->PSSetShaderResources(0,1,&none);context_->OMSetRenderTargets(0,nullptr,nullptr);
    HRESULT result=swapchain_->Present(0,DXGI_PRESENT_DO_NOT_WAIT);
    if(result!=DXGI_ERROR_WAS_STILL_DRAWING)check(result,"Present mirror");
}
void Renderer::submit(Runtime& runtime,int slot,bool tagged,std::uint64_t tick){
    for(int e=0;e<2;++e){
        vr::VRTextureWithPose_t tex{};tex.handle=frames[slot].eyes[e].texture.get();tex.eType=vr::TextureType_DirectX;tex.eColorSpace=vr::ColorSpace_Gamma;
        std::memcpy(tex.mDeviceToAbsoluteTracking.m,meta[slot].sourcePose.m,sizeof(meta[slot].sourcePose.m));
        auto error=runtime.compositor->Submit(static_cast<vr::EVREye>(e),&tex,nullptr,tagged?vr::Submit_TextureWithPose:vr::Submit_Default);
        if(error!=vr::VRCompositorError_None&&error!=vr::VRCompositorError_DoNotHaveFocus)throw std::runtime_error("SteamVR Submit error "+std::to_string(int(error)));
    }
    frames[slot].lastSubmit=tick;context_->Flush();
}
void Renderer::recovery(Runtime& runtime){
    for(int e=0;e<2;++e){vr::Texture_t tex{recovery_[e].texture.get(),vr::TextureType_DirectX,vr::ColorSpace_Gamma};auto error=runtime.compositor->Submit(static_cast<vr::EVREye>(e),&tex);if(error!=vr::VRCompositorError_None&&error!=vr::VRCompositorError_DoNotHaveFocus)throw std::runtime_error("Recovery Submit error "+std::to_string(int(error)));}
    context_->Flush();
}
}
