#pragma once
#include "client/device/view_geometry.hpp"
#include <iomanip>
#include <sstream>

namespace vrost {
using RawProjection = std::array<float,4>; // OpenVR left, right, top, bottom.

inline VrostView openvrView(const Pose& eyeToHead,const RawProjection& raw) {
  // OpenVR's names are reversed vertically: raw "top" is the -Y edge,
  // raw "bottom" is +Y. ALVR uses signed +Y up / -Y down angles.
  // Do not negate them: that mirrors an asymmetric vertical frustum.
  // Valve docs/Driver_API_Documentation.md, IVRDisplayComponent::GetProjectionRaw.
  return {quaternion(eyeToHead),std::atan(raw[0]),std::atan(raw[1]),
          std::atan(raw[3]),std::atan(raw[2])};
}

inline std::string viewGeometryDiagnostic(const ViewGeometry::Snapshot& snapshot,
                                         const std::array<RawProjection,2>& raw) {
  std::ostringstream out;
  out<<std::setprecision(9)<<"VIEW_GEOMETRY {\"schema\":1,\"revision\":"<<snapshot.revision
     <<",\"convention\":\"openvr_raw_to_alvr_v2\",\"eyes\":[";
  for(int eye=0;eye<2;++eye) {
    const auto& v=snapshot.views[eye];const auto& q=v.pose.orientation;
    if(eye)out<<',';
    out<<"{\"eye\":\""<<(eye?"right":"left")<<"\",\"raw_lrtb\":["
       <<raw[eye][0]<<','<<raw[eye][1]<<','<<raw[eye][2]<<','<<raw[eye][3]
       <<"],\"fov_lrud_rad\":["<<v.left<<','<<v.right<<','<<v.up<<','<<v.down
       <<"],\"eye_to_head_position_m\":["<<v.pose.position[0]<<','<<v.pose.position[1]<<','<<v.pose.position[2]
       <<"],\"eye_to_head_orientation_xyzw\":["<<q.x<<','<<q.y<<','<<q.z<<','<<q.w<<"]}";
  }
  out<<"]}";
  return out.str(); // Static optical geometry only; no world poses or gaze.
}
}
