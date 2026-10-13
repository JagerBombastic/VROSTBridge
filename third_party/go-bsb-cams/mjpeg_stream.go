// Added by the VROSTBridge project in October 2026; see VROSTBRIDGE_CHANGES.md.
//
// A small MJPEG HTTP server, written for VROSTBridge to replace the github.com/hybridgroup/mjpeg
// module (as LilliaElaine's fork), whose repositories state no licence. Clients receive the same
// stream as before: multipart/x-mixed-replace parts with the boundary MJPEGBOUNDARY, each holding
// one JPEG. Each part is exactly its header and its JPEG and is flushed to the client as soon as it
// is written, the response headers are sent when a client connects, and a client that disconnects
// is let go at once.
//
// VROSTBridge 1.0.0.3: a client gets the newest camera frame it does not have yet as soon as it
// arrives, at most one every 15 ms (about 66 a second). Until then a client got at most one frame
// per 50 ms (the old module's default), and only the first frame to arrive after that pause: about
// 17 a second. Eye tracking followed the eyes that much later, and the eye-tracked sharp region of
// the stream with it.
//
// VROSTBridge 1.0.0.5: at most one every 8 ms (125 a second), so a camera running faster than 66
// frames a second is no longer held back; the frames now cost little to pass on (they are no longer
// decoded and encoded again), and the limit still guards against a flood.
//
// Part of go-bsb-cams as modified by the VROSTBridge project; GPL-2.0, see LICENSE.

package main

import (
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"
)

const mjpegBoundary = "MJPEGBOUNDARY"

// frameInterval is the shortest time between two frames sent to one client (1.0.0.3: 15 ms, was 50 ms).
const frameInterval = 8 * time.Millisecond

// mjpegStream keeps the newest camera frame and hands it to every connected HTTP client.
type mjpegStream struct {
	lock     sync.Mutex
	part     []byte        // the newest frame as one multipart part; replaced, never changed in place
	sequence uint64        // counts the frames; a client is sent each one once
	arrived  chan struct{} // closed (and replaced) when the next frame arrives
	started  time.Time
	interval time.Duration // shortest time between two frames sent to one client (0: none)
}

func newMJPEGStream() *mjpegStream {
	return &mjpegStream{arrived: make(chan struct{}), started: time.Now(), interval: frameInterval}
}

// UpdateJPEG makes jpeg the newest frame and wakes the clients waiting for one.
func (s *mjpegStream) UpdateJPEG(jpeg []byte) {
	if len(jpeg) == 0 {
		return
	}
	elapsed := time.Since(s.started)
	header := fmt.Sprintf("\r\n--%s\r\nContent-Type: image/jpeg\r\nX-Timestamp: %d.%d\r\n\r\n",
		mjpegBoundary, int64(elapsed/time.Second), int64(elapsed%time.Second/time.Microsecond))
	part := make([]byte, 0, len(header)+len(jpeg))
	part = append(append(part, header...), jpeg...)
	s.lock.Lock()
	s.part = part
	s.sequence++
	close(s.arrived)
	s.arrived = make(chan struct{})
	s.lock.Unlock()
}

// ServeHTTP sends frames to one client until it disconnects.
func (s *mjpegStream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	log.Printf("Stream: %s connected", r.RemoteAddr)
	defer log.Printf("Stream: %s disconnected", r.RemoteAddr)
	w.Header().Set("Content-Type", "multipart/x-mixed-replace;boundary="+mjpegBoundary)
	flusher, _ := w.(http.Flusher)
	if flusher != nil {
		flusher.Flush() // the response headers go out now, before the first frame
	}
	var sent uint64 // the last frame this client was sent
	for {
		s.lock.Lock()
		sequence, part, arrived := s.sequence, s.part, s.arrived
		s.lock.Unlock()
		if sequence == sent || part == nil {
			// Nothing new yet: wait for the next frame (never one this client already has).
			select {
			case <-r.Context().Done():
				return
			case <-arrived:
			}
			continue
		}
		if _, err := w.Write(part); err != nil {
			return
		}
		if flusher != nil {
			flusher.Flush()
		}
		sent = sequence
		if s.interval > 0 {
			select {
			case <-r.Context().Done():
				return
			case <-time.After(s.interval):
			}
		}
	}
}
