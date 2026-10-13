package transfers

// Added by the VROSTBridge project (October 2026, VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md in
// the go-bsb-cams folder. Part of go-bsb-cams as modified by the VROSTBridge project; this file is
// licensed like the module it extends (Apache-2.0, github.com/kevmo314/go-uvc).
//
// ClaimFrameReader asks the device for its maximum values (GET_MAX) and commits them, frame
// interval included. For dwFrameInterval the maximum is the longest interval, the lowest frame
// rate the device offers. ClaimFrameReaderAt makes the same requests in the same order, but asks
// for a given frame interval (and says it is fixed, bmHint bit 0), and gives each request a
// timeout instead of waiting forever.

/*
#cgo LDFLAGS: -lusb-1.0
#include <libusb-1.0/libusb.h>
#include <stdlib.h>
*/
import "C"
import (
	"fmt"
	"time"
	"unsafe"

	"github.com/kevmo314/go-uvc/pkg/descriptors"
	"github.com/kevmo314/go-uvc/pkg/requests"
)

// ControlTimeout is how long one probe or commit request may take.
var ControlTimeout = 2 * time.Second

func (si *StreamingInterface) control(requestType requests.RequestType, request requests.RequestCode,
	selector VideoStreamingInterfaceControlSelector, buf unsafe.Pointer, size int) error {
	ifnum := si.iface.altsetting.bInterfaceNumber
	if ret := C.libusb_control_transfer(
		si.handle,
		C.uint8_t(requestType),
		C.uint8_t(request),
		C.uint16_t(selector<<8),
		C.uint16_t(ifnum),
		(*C.uchar)(buf),
		C.uint16_t(size),
		C.uint(ControlTimeout/time.Millisecond),
	); ret < 0 {
		return fmt.Errorf("libusb_control_transfer failed: %s", C.GoString(C.libusb_error_name(ret)))
	}
	return nil
}

// ClaimFrameReaderAt claims the streaming interface and starts streaming the given format and
// frame at the given frame interval. It returns the reader; Negotiated() on it has the values the
// device agreed to (its frame interval may differ from the one asked for).
func (si *StreamingInterface) ClaimFrameReaderAt(formatIndex, frameIndex uint8, interval time.Duration) (*FrameReader, error) {
	ifnum := si.iface.altsetting.bInterfaceNumber
	C.libusb_detach_kernel_driver(si.handle, C.int(ifnum))
	if ret := C.libusb_claim_interface(si.handle, C.int(ifnum)); ret < 0 {
		return nil, fmt.Errorf("libusb_claim_interface failed: %s", C.GoString(C.libusb_error_name(ret)))
	}
	vpcc := &descriptors.VideoProbeCommitControl{}
	const size = 48
	buf := C.malloc(C.size_t(size))
	defer C.free(buf)
	bytes := unsafe.Slice((*byte)(buf), size)
	release := func(err error) (*FrameReader, error) {
		C.libusb_release_interface(si.handle, C.int(ifnum))
		return nil, err
	}

	// The device's bounds, as ClaimFrameReader starts.
	if err := si.control(requests.RequestTypeVideoInterfaceGetRequest, requests.RequestCodeGetMax,
		VideoStreamingInterfaceControlSelectorProbeControl, buf, size); err != nil {
		return release(err)
	}
	if err := vpcc.UnmarshalBinary(bytes); err != nil {
		return release(err)
	}
	vpcc.FormatIndex = formatIndex
	vpcc.FrameIndex = frameIndex
	if interval > 0 {
		vpcc.FrameInterval = interval
		vpcc.HintBitmask |= 1 // dwFrameInterval is fixed: keep it, adjust the rest
	}
	if err := vpcc.MarshalInto(bytes); err != nil {
		return release(err)
	}
	if err := si.control(requests.RequestTypeVideoInterfaceSetRequest, requests.RequestCodeSetCur,
		VideoStreamingInterfaceControlSelectorProbeControl, buf, size); err != nil {
		return release(err)
	}
	// The negotiated values.
	if err := si.control(requests.RequestTypeVideoInterfaceGetRequest, requests.RequestCodeGetCur,
		VideoStreamingInterfaceControlSelectorProbeControl, buf, size); err != nil {
		return release(err)
	}
	if err := si.control(requests.RequestTypeVideoInterfaceSetRequest, requests.RequestCodeSetCur,
		VideoStreamingInterfaceControlSelectorCommitControl, buf, size); err != nil {
		return release(err)
	}
	if err := vpcc.UnmarshalBinary(bytes); err != nil {
		return release(err)
	}
	inputs := si.InputHeaderDescriptors()
	if len(inputs) == 0 {
		return release(fmt.Errorf("no input header descriptors found"))
	}
	reader, err := si.NewFrameReader(inputs[0].EndpointAddress, vpcc)
	if err != nil {
		return release(err)
	}
	return reader, nil
}
