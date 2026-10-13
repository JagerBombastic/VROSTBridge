package transfers

// Modified by the VROSTBridge project (October 2026, VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md
// in the go-bsb-cams folder. Upstream: github.com/kevmo314/go-uvc at 0eb292711e9f, Apache-2.0.
// - A damaged packet (any status other than LIBUSB_TRANSFER_COMPLETED) is skipped and reported as
//   ErrDamagedPacket; reading continues with the next packet. Upstream returned an error for it on
//   every later call too, so the only way on was to open the camera again.
// - A transfer that failed as a whole is resubmitted and reported as ErrDamagedPacket; a device
//   that is gone is reported as ErrNoDevice.
// - Timeout bounds a Read with no USB data at all (ErrTimeout); 0 waits as upstream did.
// - The number of transfers and the packets per transfer can be set (IsochronousTransfers,
//   IsochronousMaxPackets). A transfer is handed over only once all its packets have passed, so
//   fewer packets per transfer deliver the end of a frame sooner.
// - Close cancels the transfers and waits for libusb to give them back before freeing them.
//   Upstream freed transfers libusb could still own. Transfers libusb does not give back are kept,
//   and Close says so with ErrTransfersKept (their libusb context must then stay too).

/*
#cgo LDFLAGS: -lusb-1.0
#include <libusb-1.0/libusb.h>
#include <stdlib.h>
#include <stddef.h>
#include <sys/time.h>

void isochronousReaderTransferCallback(struct libusb_transfer *transfer);

// Helper to get iso_packet_desc pointer, works around flexible array member issues
static inline struct libusb_iso_packet_descriptor* get_iso_packet_desc(struct libusb_transfer *transfer) {
    // The iso_packet_desc array starts immediately after the fixed part of the struct
    return (struct libusb_iso_packet_descriptor*)((char*)transfer + offsetof(struct libusb_transfer, iso_packet_desc));
}

// VROSTBridge change: pump libusb events for at most the given time.
static inline int handle_events_for(libusb_context *ctx, long microseconds) {
    struct timeval tv;
    tv.tv_sec = microseconds / 1000000;
    tv.tv_usec = microseconds % 1000000;
    return libusb_handle_events_timeout_completed(ctx, &tv, NULL);
}
*/
import "C"
import (
	"errors"
	"fmt"
	"io"
	"runtime/cgo"
	"time"
	"unsafe"
)

// VROSTBridge change: sizes that can be set before a reader is created.
var (
	// IsochronousTransfers is how many transfers are kept in flight (upstream: 100).
	IsochronousTransfers = 100
	// IsochronousMaxPackets is the most packets per transfer (upstream: 128).
	IsochronousMaxPackets uint32 = 128
)

// VROSTBridge change: errors a caller can continue after.
var (
	// ErrDamagedPacket means one packet (or one whole transfer) of data was lost or damaged. The
	// frame it belonged to is incomplete; reading can go on.
	ErrDamagedPacket = errors.New("damaged isochronous packet")
	// ErrTimeout means no USB data arrived within the reader's Timeout.
	ErrTimeout = errors.New("no USB data within the timeout")
	// ErrNoDevice means the device is gone.
	ErrNoDevice = errors.New("the USB device is gone")
	// ErrTransfersKept means Close could not get every transfer back from libusb in time. They are
	// kept, never freed, so the libusb context they belong to must not be freed either.
	ErrTransfersKept = errors.New("USB transfers were not given back by libusb and are kept")
)

//export isochronousReaderTransferCallback
func isochronousReaderTransferCallback(transfer *C.struct_libusb_transfer) {
	r := cgo.Handle(transfer.user_data).Value().(*IsochronousReader)

	r.inflight-- // VROSTBridge change: counted for Close
	r.completedTxReqs[r.head] = transfer
	r.head = (r.head + 1) % len(r.completedTxReqs)
	if r.size < len(r.completedTxReqs) {
		r.size++
	} else {
		panic("illegal state")
	}
}

type IsochronousReader struct {
	ctx *C.libusb_context
	// reference to all the transfers
	txReqs []*C.struct_libusb_transfer

	// circular buffer of completed transfers
	completedTxReqs []*C.struct_libusb_transfer
	head, size      int
	index           int

	// store handles for cleanup
	handles []cgo.Handle

	// VROSTBridge change: transfers submitted and not called back yet, and the longest a Read
	// waits for USB data (0: no limit).
	inflight int
	Timeout  time.Duration
}

func (si *StreamingInterface) NewIsochronousReader(endpointAddress uint8, packets, packetSize uint32) (*IsochronousReader, error) {
	r := &IsochronousReader{
		ctx: si.ctx,
	}
	handle := cgo.NewHandle(r)
	r.handles = []cgo.Handle{handle}

	for i := 0; i < IsochronousTransfers; i++ {
		tx := C.libusb_alloc_transfer(C.int(packets))
		if tx == nil {
			return nil, fmt.Errorf("libusb_alloc_transfer failed")
		}
		buf := C.malloc(C.size_t(packets * packetSize))
		if buf == nil {
			return nil, fmt.Errorf("malloc failed")
		}
		C.libusb_fill_iso_transfer(
			tx,
			(*C.struct_libusb_device_handle)(si.handle),
			C.uchar(endpointAddress),
			(*C.uchar)(buf),
			C.int(packets*packetSize),
			C.int(packets),
			(*[0]byte)(C.libusb_transfer_cb_fn(C.isochronousReaderTransferCallback)),
			unsafe.Pointer(handle),
			0)
		C.libusb_set_iso_packet_lengths(tx, C.uint(packetSize))
		// VROSTBridge change: a transfer that could not be submitted is freed, not leaked; a
		// submitted one is libusb's until it is called back (counted in inflight).
		if ret := C.libusb_submit_transfer(tx); ret < 0 {
			C.free(buf)
			C.libusb_free_transfer(tx)
			if i == 0 {
				return nil, fmt.Errorf("libusb_submit_transfer failed: %s", C.GoString(C.libusb_error_name(ret)))
			}
			break
		}
		r.inflight++
		r.txReqs = append(r.txReqs, tx)
	}
	r.completedTxReqs = make([]*C.struct_libusb_transfer, len(r.txReqs))
	return r, nil
}

// pump waits for libusb to call back a transfer, until deadline when one is set.
func (r *IsochronousReader) pump(deadline time.Time) error {
	if deadline.IsZero() {
		if ret := C.libusb_handle_events(r.ctx); ret < 0 && ret != C.LIBUSB_ERROR_INTERRUPTED {
			return fmt.Errorf("libusb_handle_events failed: %d", ret)
		}
		return nil
	}
	remaining := time.Until(deadline)
	if remaining <= 0 {
		return ErrTimeout
	}
	if ret := C.handle_events_for(r.ctx, C.long(remaining/time.Microsecond)+1); ret < 0 && ret != C.LIBUSB_ERROR_INTERRUPTED {
		return fmt.Errorf("libusb_handle_events failed: %d", ret)
	}
	return nil
}

func (r *IsochronousReader) resubmit(tx *C.struct_libusb_transfer) error {
	if ret := C.libusb_submit_transfer(tx); ret < 0 {
		if ret == C.LIBUSB_ERROR_NO_DEVICE {
			return ErrNoDevice
		}
		return fmt.Errorf("libusb_submit_transfer failed: %s", C.GoString(C.libusb_error_name(ret)))
	}
	r.inflight++
	return nil
}

func (r *IsochronousReader) Read(buf []byte) (int, error) {
	var deadline time.Time
	if r.Timeout > 0 {
		deadline = time.Now().Add(r.Timeout)
	}
	for {
		for r.size == 0 {
			// pump events.
			if err := r.pump(deadline); err != nil {
				return 0, err
			}
		}

		activeTx := r.completedTxReqs[(r.head-r.size+len(r.completedTxReqs))%len(r.completedTxReqs)]
		if activeTx.status != C.LIBUSB_TRANSFER_COMPLETED {
			// VROSTBridge change: the whole transfer failed. Its data is lost; submit it again.
			status := activeTx.status
			r.size--
			r.index = 0
			if status == C.LIBUSB_TRANSFER_NO_DEVICE {
				return 0, ErrNoDevice
			}
			if err := r.resubmit(activeTx); err != nil {
				return 0, err
			}
			return 0, ErrDamagedPacket
		}
		// Access iso_packet_desc array using C helper function
		descsPtr := C.get_iso_packet_desc(activeTx)
		descs := (*[1 << 16]C.struct_libusb_iso_packet_descriptor)(unsafe.Pointer(descsPtr))[:activeTx.num_iso_packets:activeTx.num_iso_packets]
		if r.index == len(descs) {
			// this tx is done, get the next one.
			r.size--
			r.index = 0
			if err := r.resubmit(activeTx); err != nil {
				return 0, err
			}
			continue
		}
		pkt := descs[r.index]
		if pkt.status != C.LIBUSB_TRANSFER_COMPLETED {
			// VROSTBridge change: skip the damaged packet and say so; the next Read goes on.
			r.index++
			return 0, ErrDamagedPacket
		}
		if pkt.actual_length == 0 {
			r.index++
			continue
		}
		if len(buf) < int(pkt.actual_length) {
			r.index++ // VROSTBridge change: skipped, so the next Read goes on
			return 0, io.ErrShortBuffer
		}
		pktbuf := C.libusb_get_iso_packet_buffer_simple(activeTx, C.uint(r.index))
		r.index++
		return copy(buf, unsafe.Slice((*byte)(pktbuf), int(pkt.actual_length))), nil
	}
}

func (r *IsochronousReader) Close() error {
	// VROSTBridge change: cancel what is in flight and wait (at most a second) until libusb has
	// called every transfer back; a transfer libusb may still own is never freed.
	for _, t := range r.txReqs {
		C.libusb_cancel_transfer(t)
	}
	deadline := time.Now().Add(time.Second)
	for r.inflight > 0 && time.Now().Before(deadline) {
		C.handle_events_for(r.ctx, 50000)
	}
	if r.inflight > 0 {
		return fmt.Errorf("%d isochronous transfers: %w", r.inflight, ErrTransfersKept)
	}
	for _, t := range r.txReqs {
		C.free(unsafe.Pointer(t.buffer))
		C.libusb_free_transfer(t)
	}
	r.txReqs = nil
	for _, h := range r.handles {
		h.Delete()
	}
	r.handles = nil
	return nil
}
