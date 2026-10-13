// Added by the VROSTBridge project in October 2026: tests for mjpeg_stream.go
// (see VROSTBRIDGE_CHANGES.md).
// Run with: CGO_ENABLED=1 go test -mod=vendor -vet=off -race .
// Part of go-bsb-cams as modified by the VROSTBridge project; GPL-2.0, see LICENSE.

package main

import (
	"bufio"
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

func fakeJPEG(n int, fill byte) []byte {
	b := bytes.Repeat([]byte{fill}, n)
	b[0], b[1] = 0xFF, 0xD8
	b[n-2], b[n-1] = 0xFF, 0xD9
	return b
}

// readParts reads multipart parts the way Baballonia does: scan for FFD8 ... FFD9.
func readParts(t *testing.T, r io.Reader, want int, deadline time.Duration) ([][]byte, []string) {
	t.Helper()
	br := bufio.NewReader(r)
	var frames [][]byte
	var headers []string
	done := make(chan struct{})
	go func() {
		defer close(done)
		var cur []byte
		var head []byte
		in := false
		var prev byte
		for len(frames) < want {
			c, err := br.ReadByte()
			if err != nil {
				return
			}
			if !in {
				head = append(head, c)
				if prev == 0xFF && c == 0xD8 {
					in = true
					cur = []byte{0xFF, 0xD8}
					headers = append(headers, string(head[:len(head)-2]))
					head = nil
				}
			} else {
				cur = append(cur, c)
				if prev == 0xFF && c == 0xD9 {
					frames = append(frames, cur)
					in = false
				}
			}
			prev = c
		}
	}()
	select {
	case <-done:
	case <-time.After(deadline):
		t.Fatalf("timed out waiting for %d frames", want)
	}
	return frames, headers
}

func TestMJPEGWireFormatAndFrames(t *testing.T) {
	s := newMJPEGStream()
	srv := httptest.NewServer(s)
	defer srv.Close()
	stop := make(chan struct{})
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { // 100 frames per second, sizes vary so a shorter frame follows a longer one
		defer wg.Done()
		i := 0
		for {
			select {
			case <-stop:
				return
			case <-time.After(10 * time.Millisecond):
			}
			size := 3000 + (i%3)*20000
			s.UpdateJPEG(fakeJPEG(size, byte(0x10+i%7)))
			i++
		}
	}()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if ct := resp.Header.Get("Content-Type"); ct != "multipart/x-mixed-replace;boundary=MJPEGBOUNDARY" {
		t.Fatalf("content type %q", ct)
	}
	start := time.Now()
	frames, headers := readParts(t, resp.Body, 20, 5*time.Second)
	elapsed := time.Since(start)
	resp.Body.Close()
	close(stop)
	wg.Wait()
	header := regexp.MustCompile(`^\r\n--MJPEGBOUNDARY\r\nContent-Type: image/jpeg\r\nX-Timestamp: \d+\.\d+\r\n\r\n$`)
	for i, h := range headers {
		if !header.MatchString(h) {
			t.Fatalf("part %d header %q", i, h)
		}
	}
	for i, f := range frames {
		size := len(f)
		if size != 3000 && size != 23000 && size != 43000 {
			t.Fatalf("frame %d has %d bytes", i, size)
		}
		for _, b := range f[2 : size-2] {
			if b != f[2] {
				t.Fatalf("frame %d is mixed from two frames", i)
			}
		}
	}
	// 1.0.0.5: every frame of a 100 Hz camera (1.0.0.3 sent at most one per 15 ms, before that one per 50 ms).
	if elapsed < 20*8*time.Millisecond*9/10 {
		t.Fatalf("20 frames in %v: more than one per 8 ms", elapsed)
	}
	if elapsed > 20*14*time.Millisecond {
		t.Fatalf("20 frames took %v: a 100 Hz camera was held back", elapsed)
	}
	if elapsed > 700*time.Millisecond {
		t.Fatalf("20 frames took %v: one per 50 ms as before 1.0.0.3", elapsed)
	}
}

func TestMJPEGEveryFrameOnceInOrder(t *testing.T) {
	// 1.0.0.3: a camera up to about 66 frames a second: a client is sent each frame once, in order.
	s := newMJPEGStream()
	srv := httptest.NewServer(s)
	defer srv.Close()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	const count = 40
	go func() {
		time.Sleep(100 * time.Millisecond)
		for i := 0; i < count; i++ {
			s.UpdateJPEG(fakeJPEG(2000, byte(0x20+i)))
			time.Sleep(20 * time.Millisecond) // a 50 Hz camera
		}
	}()
	frames, _ := readParts(t, resp.Body, count-2, 5*time.Second)
	previous := -1
	for i, f := range frames {
		index := int(f[2]) - 0x20
		if index != previous+1 && !(i == 0 && index <= 1) {
			t.Fatalf("frame %d is camera frame %d after %d: skipped, repeated or out of order", i, index, previous)
		}
		previous = index
	}
}

func TestMJPEGFastCameraNewestFrameAtMost125PerSecond(t *testing.T) {
	// 1.0.0.5: a camera faster than the limit (250 Hz): the newest frame not yet sent, never more
	// than one per 8 ms (1.0.0.3: 15 ms), never one twice, never an older one after a newer one.
	s := newMJPEGStream()
	srv := httptest.NewServer(s)
	defer srv.Close()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; ; i++ {
			select {
			case <-stop:
				return
			case <-time.After(4 * time.Millisecond):
			}
			s.UpdateJPEG(fakeJPEG(2000, byte(i%250)))
		}
	}()
	start := time.Now()
	frames, _ := readParts(t, resp.Body, 40, 5*time.Second)
	elapsed := time.Since(start)
	close(stop)
	<-done
	if elapsed < 40*8*time.Millisecond*9/10 {
		t.Fatalf("40 frames in %v: more than one per 8 ms", elapsed)
	}
	if elapsed > 40*14*time.Millisecond {
		t.Fatalf("40 frames took %v: slower than about 70 a second", elapsed)
	}
	for i := 1; i < len(frames); i++ {
		if frames[i][2] == frames[i-1][2] {
			t.Fatalf("frame %d sent twice", i)
		}
	}
}

func TestMJPEGTwoClientsAndDisconnect(t *testing.T) {
	s := newMJPEGStream()
	srv := httptest.NewServer(s)
	defer srv.Close()
	stop := make(chan struct{})
	producer := make(chan struct{})
	go func() {
		defer close(producer)
		for {
			select {
			case <-stop:
				return
			case <-time.After(5 * time.Millisecond):
			}
			s.UpdateJPEG(fakeJPEG(5000, 0x33))
		}
	}()
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "GET", srv.URL, nil)
	a, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	b, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	readParts(t, a.Body, 3, 3*time.Second)
	cancel() // client a goes away; client b keeps receiving
	a.Body.Close()
	frames, _ := readParts(t, b.Body, 5, 3*time.Second)
	if len(frames) != 5 {
		t.Fatal("client b stopped")
	}
	b.Body.Close()
	close(stop)
	<-producer
	// The producer never blocks, even with nobody connected.
	done := make(chan struct{})
	go func() {
		for i := 0; i < 1000; i++ {
			s.UpdateJPEG(fakeJPEG(100, 0x44))
		}
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("UpdateJPEG blocked")
	}
	s.lock.Lock()
	part := s.part
	s.lock.Unlock()
	if !strings.HasPrefix(string(part), "\r\n--MJPEGBOUNDARY\r\n") {
		t.Fatal("part header")
	}
	s.UpdateJPEG(nil) // ignored
}

func TestMJPEGFrameReachesClientWithoutNextFrame(t *testing.T) {
	// A frame is flushed at once: the client gets it although no later frame follows.
	s := newMJPEGStream()
	srv := httptest.NewServer(s)
	defer srv.Close()
	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	go func() {
		time.Sleep(200 * time.Millisecond)
		s.UpdateJPEG(fakeJPEG(1000, 0x55)) // smaller than any write buffer
	}()
	frames, _ := readParts(t, resp.Body, 1, 2*time.Second)
	if len(frames[0]) != 1000 {
		t.Fatalf("got %d bytes", len(frames[0]))
	}
}
