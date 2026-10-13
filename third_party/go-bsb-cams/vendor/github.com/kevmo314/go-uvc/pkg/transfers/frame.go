package transfers

// Modified by the VROSTBridge project (October 2026, VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md
// in the go-bsb-cams folder. Upstream: github.com/kevmo314/go-uvc at 0eb292711e9f, Apache-2.0.
// - ReadFrame puts each frame together in a buffer of its own (two, used in turn) instead of
//   keeping every payload in one shared buffer, and copes with lost data: a frame that lost a
//   packet, carries the UVC error bit or does not fit is dropped and counted (DamagedFrames),
//   and the next frame is read; upstream returned an error and go-bsb-cams then opened the
//   camera again, which paused the eye tracking.
// - Timeouts and a device that is gone are passed on (ErrTimeout, ErrNoDevice). With a timeout set,
//   ReadFrame also returns ErrTimeout after that long without a complete frame while USB data keeps
//   coming (a camera sending only headers, every packet damaged); the frame being put together is
//   kept for the next call. Upstream waited for a frame for as long as data came.
// - A payload whose header length is shorter than the fields its header flags announce is skipped
//   as damaged (upstream sliced past it and panicked), and a payload's data starts after its header
//   length, as UVC defines it.
// - Packets per isochronous transfer follow IsochronousMaxPackets.
// - Close also closes the transfer reader (cancelling its transfers) and returns the interface
//   to its zero-bandwidth setting before releasing it.
// - NewFrameReaderFrom puts frames together from any payload source (used by tests).

/*
#cgo LDFLAGS: -lusb-1.0
#include <libusb-1.0/libusb.h>
#include <stdlib.h>
*/
import "C"
import (
	"errors"
	"fmt"
	"io"
	"time"
	"unsafe"

	"github.com/kevmo314/go-uvc/pkg/descriptors"
)

type FrameReader struct {
	ctx    *C.libusb_context
	handle *C.struct_libusb_device_handle
	iface  *C.struct_libusb_interface
	vpcc   *descriptors.VideoProbeCommitControl
	pr     io.Reader

	// VROSTBridge change: one payload at a time, and the frame being put together in one of
	// two buffers (a returned frame stays valid until the next ReadFrame returns).
	payload  []byte
	frames   [2][]byte
	current  int
	maxFrame int
	inFrame  bool // a frame is being put together
	ended    bool // the last frame ended with an end-of-frame bit
	haveFID  bool
	fid      bool
	damaged  bool // the frame being put together (or the next one) lost data
	iso      bool
	pending  *Frame        // a second frame completed by the same payload, returned next
	wait     time.Duration // the longest one ReadFrame waits for a complete frame (0: no limit)

	// Counters (VROSTBridge change).
	Frames         uint64 // complete frames returned
	DamagedFrames  uint64 // frames dropped because they lost data
	DamagedPackets uint64 // packets or transfers lost
	ErrorPayloads  uint64 // payloads with the UVC error bit
	Overflows      uint64 // frames larger than the frame limit
}

type Frame struct {
	Payloads      []*Payload
	index, offset int

	data []byte // VROSTBridge change: the frame's bytes (ReadFrame no longer fills Payloads)
}

// Bytes returns the frame's data (VROSTBridge addition). It is valid until the next ReadFrame returns.
func (f *Frame) Bytes() []byte {
	return f.data
}

// Read reads the payload datas concatenated together.
func (f *Frame) Read(buf []byte) (int, error) {
	if f.Payloads == nil { // VROSTBridge change: frames from ReadFrame keep their bytes in data
		if f.offset >= len(f.data) {
			return 0, io.EOF
		}
		n := copy(buf, f.data[f.offset:])
		f.offset += n
		return n, nil
	}
	total := 0
	for _, p := range f.Payloads {
		total += len(p.Data)
	}
	n := 0
	for n < len(buf) {
		if f.index == len(f.Payloads) {
			if n == 0 {
				return 0, io.EOF
			}
			return n, nil
		}
		p := f.Payloads[f.index]
		m := copy(buf[n:], p.Data[f.offset:])
		f.offset += m
		n += m
		if f.offset >= len(p.Data) {
			f.index++
			f.offset = 0
		}
	}
	return n, nil
}

// VROSTBridge change: frame and payload limits from the negotiated sizes, never below 1 MiB and
// 64 KiB (a device can report too little).
func frameLimits(vpcc *descriptors.VideoProbeCommitControl) (maxFrame, maxPayload int) {
	maxFrame = max(2*int(vpcc.MaxVideoFrameSize), 1<<20)
	maxPayload = max(int(vpcc.MaxPayloadTransferSize), 64<<10)
	return
}

func (si *StreamingInterface) NewFrameReader(endpointAddress uint8, vpcc *descriptors.VideoProbeCommitControl) (*FrameReader, error) {
	maxFrame, maxPayload := frameLimits(vpcc)
	useIsochronous := si.iface.num_altsetting > 1
	if useIsochronous {
		altsetting, packetSize, err := findIsochronousAltSetting(si.ctx, si.iface, C.uchar(endpointAddress), vpcc.MaxPayloadTransferSize)
		if err != nil {
			return nil, err
		}
		if ret := C.libusb_set_interface_alt_setting(si.handle, C.int(altsetting.bInterfaceNumber), C.int(altsetting.bAlternateSetting)); ret < 0 {
			return nil, fmt.Errorf("libusb_set_interface_alt_setting failed: %s", C.GoString(C.libusb_error_name(ret)))
		}
		packets := max(min((vpcc.MaxVideoFrameSize+packetSize-1)/packetSize, IsochronousMaxPackets), 1)
		ir, err := si.NewIsochronousReader(endpointAddress, packets, packetSize)
		if err != nil {
			return nil, err
		}
		reader := NewFrameReaderFrom(ir, maxFrame, max(maxPayload, int(packetSize)))
		reader.handle, reader.iface, reader.vpcc, reader.iso = si.handle, si.iface, vpcc, true
		return reader, nil
	} else {
		br, err := si.NewBulkReader(endpointAddress, vpcc.MaxPayloadTransferSize)
		if err != nil {
			return nil, err
		}
		reader := NewFrameReaderFrom(br, maxFrame, maxPayload)
		reader.ctx, reader.handle, reader.iface, reader.vpcc = si.ctx, si.handle, si.iface, vpcc
		return reader, nil
	}
}

// NewFrameReaderFrom puts frames together from payloads read from source, one UVC payload (with
// its header) per Read. VROSTBridge addition, used by tests and by NewFrameReader.
func NewFrameReaderFrom(source io.Reader, maxFrameSize, maxPayloadSize int) *FrameReader {
	return &FrameReader{pr: source, payload: make([]byte, maxPayloadSize), maxFrame: maxFrameSize}
}

// SetTimeout bounds how long a ReadFrame waits for USB data, and for a complete frame while data
// keeps coming (VROSTBridge addition; 0: no limit).
func (r *FrameReader) SetTimeout(timeout time.Duration) {
	r.wait = timeout
	switch reader := r.pr.(type) {
	case *IsochronousReader:
		reader.Timeout = timeout
	case *BulkReader:
		reader.Timeout = timeout
	}
}

// Negotiated returns the values the device agreed to (VROSTBridge addition).
func (r *FrameReader) Negotiated() *descriptors.VideoProbeCommitControl {
	return r.vpcc
}

func findAltEndpoint(endpoints []C.struct_libusb_endpoint_descriptor, endpointAddress C.uchar) (int, error) {
	for i, endpoint := range endpoints {
		if endpoint.bEndpointAddress == endpointAddress {
			return i, nil
		}
	}
	return 0, fmt.Errorf("endpoint not found")
}

func getEndpointMaxPacketSize(ctx *C.struct_libusb_context, endpoint C.struct_libusb_endpoint_descriptor) uint32 {
	var ssdesc *C.struct_libusb_ss_endpoint_companion_descriptor
	ret := C.libusb_get_ss_endpoint_companion_descriptor(ctx, &endpoint, &ssdesc)
	if ret == 0 {
		defer C.libusb_free_ss_endpoint_companion_descriptor(ssdesc)
		return uint32(ssdesc.wBytesPerInterval)
	}
	val := endpoint.wMaxPacketSize & 0x07ff
	endpointType := endpoint.bmAttributes & 0x03
	if endpointType == C.LIBUSB_TRANSFER_TYPE_ISOCHRONOUS || endpointType == C.LIBUSB_TRANSFER_TYPE_INTERRUPT {
		val *= 1 + ((val >> 1) & 3)
	}
	return uint32(val)

}

// findIsochronousAltSetting sets the isochronous alternate setting for the given interface and endpoint address to the
// first alternate setting that has a max packet size of at least mtu.
//
// UVC spec 1.5, section 2.4.3: A typical use of alternate settings is to provide a way to change the bandwidth requirements an active
// isochronous pipe imposes on the USB.
func findIsochronousAltSetting(ctx *C.struct_libusb_context, iface *C.struct_libusb_interface, endpointAddress C.uchar, payloadSize uint32) (*C.struct_libusb_interface_descriptor, uint32, error) {
	altsettings := unsafe.Slice(iface.altsetting, iface.num_altsetting)
	for i, altsetting := range altsettings {
		if altsetting.bNumEndpoints == 0 {
			// UVC spec 1.5, section 2.4.3: All devices that transfer isochronous video data must
			// incorporate a zero-bandwidth alternate setting for each VideoStreaming interface that has an
			// isochronous video endpoint, and it must be the default alternate setting (alternate setting zero).
			//
			// in other words, if there aren't any endpoints on this alternate setting it's reserved for a zero-bandwidth
			// alternate setting so we can't use it and should skip it.
			continue
		}
		endpoints := unsafe.Slice(altsetting.endpoint, altsetting.bNumEndpoints)

		j, err := findAltEndpoint(endpoints, endpointAddress)
		if err != nil {
			return nil, 0, err
		}

		packetSize := getEndpointMaxPacketSize(ctx, endpoints[j])
		if packetSize >= payloadSize || i == len(altsettings)-1 {
			return &altsetting, packetSize, nil
		}
	}
	panic("invalid state")
}

// ErrDamagedFrame is returned for a frame that lost data and was dropped (VROSTBridge addition);
// the next ReadFrame goes on with the next frame.
var ErrDamagedFrame = errors.New("a frame lost data on the USB link and was dropped")

// ReadFrame returns the next complete frame (VROSTBridge change: rewritten). A frame that lost
// data is dropped (ErrDamagedFrame; read on); ErrTimeout, ErrNoDevice and other read errors end
// the call (the frame being put together is abandoned). With a timeout set (SetTimeout), a call
// that has read USB data for that long without completing a frame returns ErrTimeout and keeps the
// frame being put together for the next call.
func (r *FrameReader) ReadFrame() (*Frame, error) {
	if pending := r.pending; pending != nil {
		r.pending = nil
		return pending, nil
	}
	var deadline time.Time
	if r.wait > 0 {
		deadline = time.Now().Add(r.wait)
	}
	for {
		if !deadline.IsZero() && !time.Now().Before(deadline) {
			return nil, ErrTimeout
		}
		n, err := r.pr.Read(r.payload)
		if err != nil {
			if errors.Is(err, ErrDamagedPacket) || errors.Is(err, io.ErrShortBuffer) {
				// A packet was lost: the frame being put together misses data (or, between two
				// frames, the next one may miss its start).
				r.DamagedPackets++
				r.damaged = true
				continue
			}
			r.inFrame, r.damaged = false, false
			return nil, err
		}
		if n < 2 || int(r.payload[0]) < headerSize(r.payload[1]) || int(r.payload[0]) > n {
			r.damaged = true // not a UVC payload: its frame is not complete
			continue
		}
		p := &Payload{}
		if err := p.UnmarshalBinary(r.payload[:n]); err != nil {
			r.damaged = true
			continue
		}
		p.Data = r.payload[int(r.payload[0]):n] // the data follows the header's stated length
		var done *Frame
		lost := false
		if r.inFrame && p.FrameID() != r.fid {
			// The next frame began without an end-of-frame bit on this one.
			done, lost = r.finish()
		}
		if !r.inFrame {
			if r.ended && r.haveFID && p.FrameID() == r.fid && len(p.Data) == 0 {
				// An empty payload after an end-of-frame bit, before the frame bit flips.
				if done != nil {
					return done, nil
				}
				if lost {
					return nil, ErrDamagedFrame
				}
				continue
			}
			r.start(p.FrameID())
		}
		if p.Error() {
			r.ErrorPayloads++
			r.damaged = true
		}
		frame := r.frames[r.current]
		if len(frame)+len(p.Data) > r.maxFrame {
			if !r.damaged {
				r.Overflows++
			}
			r.damaged = true
		} else {
			r.frames[r.current] = append(frame, p.Data...)
		}
		if p.EndOfFrame() {
			finished, lostNow := r.finish()
			lost = lost || lostNow
			if finished != nil {
				if done != nil { // this payload ended one frame and was all of the next
					r.pending = finished
					return done, nil
				}
				return finished, nil
			}
		}
		if done != nil {
			return done, nil
		}
		if lost {
			return nil, ErrDamagedFrame
		}
	}
}

// headerSize is the shortest payload header with the fields the header flags announce: the length and
// flag bytes, a presentation time stamp (4) and a source clock reference (6).
func headerSize(flags byte) int {
	size := 2
	if flags&0b00000100 != 0 {
		size += 4
	}
	if flags&0b00001000 != 0 {
		size += 6
	}
	return size
}

// start begins a frame in the buffer not holding the frame returned last.
func (r *FrameReader) start(fid bool) {
	r.current ^= 1
	r.frames[r.current] = r.frames[r.current][:0]
	r.inFrame, r.ended, r.haveFID, r.fid = true, false, true, fid
}

// finish ends the frame being put together: the frame, or nil and whether it lost data.
func (r *FrameReader) finish() (*Frame, bool) {
	damaged := r.damaged
	r.inFrame, r.ended, r.damaged = false, true, false
	data := r.frames[r.current]
	if damaged {
		r.DamagedFrames++
		return nil, true
	}
	if len(data) == 0 {
		return nil, false
	}
	r.Frames++
	return &Frame{data: data}, false
}

func (r *FrameReader) Close() error {
	// VROSTBridge change: stop the transfers first, then return the interface to its
	// zero-bandwidth setting and release it.
	var first error
	if closer, ok := r.pr.(io.Closer); ok {
		first = closer.Close()
	}
	if r.handle == nil || r.iface == nil {
		return first
	}
	ifnum := C.int(r.iface.altsetting.bInterfaceNumber)
	if r.iso {
		C.libusb_set_interface_alt_setting(r.handle, ifnum, 0)
	}
	if ret := C.libusb_release_interface(r.handle, ifnum); ret < 0 && first == nil {
		first = fmt.Errorf("libusb_release_interface failed: %s", C.GoString(C.libusb_error_name(ret)))
	}
	return first
}
