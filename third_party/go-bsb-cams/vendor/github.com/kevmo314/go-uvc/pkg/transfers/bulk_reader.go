package transfers

// Modified by the VROSTBridge project (October 2026, VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md
// in the go-bsb-cams folder. Upstream: github.com/kevmo314/go-uvc at 0eb292711e9f, Apache-2.0.
// As in isochronous_reader.go: Timeout bounds a Read with no USB data (ErrTimeout), a failed
// transfer is resubmitted and reported (ErrDamagedPacket, ErrNoDevice), and Close cancels the
// transfers and waits for libusb to give them back before freeing them (ErrTransfersKept if not).

/*
#cgo LDFLAGS: -lusb-1.0
#include <libusb-1.0/libusb.h>
#include <stdlib.h>
#include <sys/time.h>

void bulkReaderTransferCallback(struct libusb_transfer *transfer);

static inline int bulk_handle_events_for(libusb_context *ctx, long microseconds) {
    struct timeval tv;
    tv.tv_sec = microseconds / 1000000;
    tv.tv_usec = microseconds % 1000000;
    return libusb_handle_events_timeout_completed(ctx, &tv, NULL);
}
*/
import "C"
import (
	"fmt"
	"runtime/cgo"
	"time"
	"unsafe"
)

type BulkReader struct {
	ctx    *C.libusb_context
	txReqs []*C.struct_libusb_transfer

	// circular buffer of completed transfers
	completedTxReqs []*C.struct_libusb_transfer
	head, size      int

	// store handles for cleanup
	handles []cgo.Handle

	// VROSTBridge change: transfers submitted and not called back yet, and the longest a Read
	// waits for USB data (0: no limit).
	inflight int
	Timeout  time.Duration
}

//export bulkReaderTransferCallback
func bulkReaderTransferCallback(transfer *C.struct_libusb_transfer) {
	r := cgo.Handle(transfer.user_data).Value().(*BulkReader)

	r.inflight-- // VROSTBridge change: counted for Close
	r.completedTxReqs[r.head] = transfer
	r.head = (r.head + 1) % len(r.completedTxReqs)
	if r.size < len(r.completedTxReqs) {
		r.size++
	} else {
		panic("illegal state")
	}
}

func (si *StreamingInterface) NewBulkReader(endpointAddress uint8, mtu uint32) (*BulkReader, error) {
	// the libusb sync api seems to result in some partial reads on some devices so we use the async api
	r := &BulkReader{
		ctx:    si.ctx,
		txReqs: make([]*C.struct_libusb_transfer, 0, 100),
	}
	handle := cgo.NewHandle(r)
	r.handles = []cgo.Handle{handle}

	for i := 0; i < 100; i++ {
		tx := C.libusb_alloc_transfer(0)
		if tx == nil {
			return nil, fmt.Errorf("libusb_alloc_transfer failed")
		}
		buf := C.malloc(C.size_t(mtu))
		if buf == nil {
			return nil, fmt.Errorf("malloc failed")
		}
		C.libusb_fill_bulk_transfer(
			tx,
			(*C.struct_libusb_device_handle)(si.handle),
			C.uchar(endpointAddress),
			(*C.uchar)(buf),
			C.int(mtu),
			(*[0]byte)(C.libusb_transfer_cb_fn(C.bulkReaderTransferCallback)),
			unsafe.Pointer(handle),
			0)
		if ret := C.libusb_submit_transfer(tx); ret < 0 {
			C.free(buf) // VROSTBridge change: freed, not leaked
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

func (r *BulkReader) Read(buf []byte) (int, error) {
	var deadline time.Time
	if r.Timeout > 0 {
		deadline = time.Now().Add(r.Timeout)
	}
	for r.size == 0 {
		// pump events.
		if deadline.IsZero() {
			C.libusb_handle_events(r.ctx)
			continue
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return 0, ErrTimeout
		}
		C.bulk_handle_events_for(r.ctx, C.long(remaining/time.Microsecond)+1)
	}

	activeTx := r.completedTxReqs[(r.head-r.size+len(r.completedTxReqs))%len(r.completedTxReqs)]
	r.size--
	status := activeTx.status
	n := 0
	if status == C.LIBUSB_TRANSFER_COMPLETED {
		n = copy(buf, unsafe.Slice((*byte)(activeTx.buffer), activeTx.actual_length))
	} else if status == C.LIBUSB_TRANSFER_NO_DEVICE {
		return 0, ErrNoDevice
	}
	if ret := C.libusb_submit_transfer(activeTx); ret < 0 {
		if ret == C.LIBUSB_ERROR_NO_DEVICE {
			return 0, ErrNoDevice
		}
		return 0, fmt.Errorf("libusb_submit_transfer failed: %s", C.GoString(C.libusb_error_name(ret)))
	}
	r.inflight++
	if status != C.LIBUSB_TRANSFER_COMPLETED {
		return 0, ErrDamagedPacket // VROSTBridge change: reported, and reading can go on
	}
	return n, nil
}

func (r *BulkReader) Close() error {
	// VROSTBridge change: cancel, then free only what libusb has given back.
	for _, t := range r.txReqs {
		C.libusb_cancel_transfer(t)
	}
	deadline := time.Now().Add(time.Second)
	for r.inflight > 0 && time.Now().Before(deadline) {
		C.bulk_handle_events_for(r.ctx, 50000)
	}
	if r.inflight > 0 {
		return fmt.Errorf("%d bulk transfers: %w", r.inflight, ErrTransfersKept)
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
