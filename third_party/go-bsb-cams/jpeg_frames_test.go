// Added by the VROSTBridge project in October 2026 (VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md.
// Tests for jpeg_frames.go, camera_stats.go and the frame reader in the vendored go-uvc
// (pkg/transfers/frame.go). Part of go-bsb-cams as modified by the VROSTBridge project; GPL-2.0.

package main

import (
	"bytes"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/jpeg"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/kevmo314/go-uvc/pkg/transfers"
)

// eyePicture is a Go-encoded JPEG of a two-eye camera picture (grey, with some structure).
func eyePicture(t *testing.T, width, height int) []byte {
	t.Helper()
	img := image.NewGray(image.Rect(0, 0, width, height))
	for y := 0; y < height; y++ {
		for x := 0; x < width; x++ {
			img.SetGray(x, y, color.Gray{Y: uint8((x*7 + y*3) % 256)})
		}
	}
	var out bytes.Buffer
	if err := jpeg.Encode(&out, img, &jpeg.Options{Quality: 80}); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

// segments splits a JPEG into its marker segments up to the scan (marker, whole segment bytes).
func segments(t *testing.T, data []byte) (out [][2][]byte, scan int) {
	t.Helper()
	i := 2
	for i+4 <= len(data) {
		marker := data[i+1]
		length := int(data[i+2])<<8 | int(data[i+3])
		out = append(out, [2][]byte{{marker}, data[i : i+2+length]})
		if marker == 0xDA {
			return out, i
		}
		i += 2 + length
	}
	t.Fatal("no scan")
	return nil, 0
}

// withoutTables removes every DHT segment, as a Motion-JPEG camera sends its frames.
func withoutTables(t *testing.T, data []byte) []byte {
	t.Helper()
	parts, scan := segments(t, data)
	out := []byte{0xFF, 0xD8}
	for _, part := range parts[:len(parts)-1] {
		if part[0][0] != 0xC4 {
			out = append(out, part[1]...)
		}
	}
	return append(out, data[scan:]...)
}

func TestStandardTablesAreTheOnesGoWrites(t *testing.T) {
	// Go's encoder writes the standard tables of the JPEG specification; ours must be the same bytes.
	img := image.NewYCbCr(image.Rect(0, 0, 16, 16), image.YCbCrSubsampleRatio420)
	var out bytes.Buffer
	if err := jpeg.Encode(&out, img, nil); err != nil {
		t.Fatal(err)
	}
	parts, _ := segments(t, out.Bytes())
	var written []byte
	for _, part := range parts {
		if part[0][0] == 0xC4 {
			written = append(written, part[1][4:]...) // the tables, without marker and length
		}
	}
	if !bytes.Equal(written, standardTables[4:]) {
		t.Fatalf("standard tables differ from Go's: %d bytes against %d", len(standardTables)-4, len(written))
	}
	if length := int(standardTables[2])<<8 | int(standardTables[3]); length != len(standardTables)-2 {
		t.Fatalf("segment length %d for %d bytes", length, len(standardTables)-2)
	}
}

func TestAFrameWithTablesIsPassedOnUnchanged(t *testing.T) {
	data := eyePicture(t, 800, 400)
	frame := append(append([]byte{}, data...), make([]byte, 300)...) // UVC frames can be padded
	picture, err := cleanJPEG(frame)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(picture.Data, data) || picture.AddedTables {
		t.Fatal("a complete picture must be passed on as it is, without its padding")
	}
	if picture.Width != 800 || picture.Height != 400 {
		t.Fatalf("size %dx%d", picture.Width, picture.Height)
	}
	if &picture.Data[0] != &frame[0] {
		t.Fatal("no copy is needed for a complete picture")
	}
}

func TestAFrameWithoutTablesGetsTheStandardOnes(t *testing.T) {
	data := eyePicture(t, 320, 240)
	stripped := withoutTables(t, data)
	if _, err := jpeg.Decode(bytes.NewReader(stripped)); err == nil {
		t.Log("Go's decoder reads a frame without tables (fine either way)")
	}
	picture, err := cleanJPEG(stripped)
	if err != nil {
		t.Fatal(err)
	}
	if !picture.AddedTables {
		t.Fatal("tables must be added")
	}
	want, err := jpeg.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	got, err := jpeg.Decode(bytes.NewReader(picture.Data))
	if err != nil {
		t.Fatalf("the picture with added tables does not decode: %v", err)
	}
	if !bytes.Equal(got.(*image.Gray).Pix, want.(*image.Gray).Pix) {
		t.Fatal("the picture with added tables decodes differently")
	}
}

func TestBrokenFramesAreRefused(t *testing.T) {
	data := eyePicture(t, 64, 32)
	cases := map[string][]byte{
		"empty":           nil,
		"no start":        data[2:],
		"cut short":       data[:len(data)/2],
		"no end":          data[:len(data)-2],
		"segment too big": append(append([]byte{0xFF, 0xD8, 0xFF, 0xE0, 0x7F, 0xFF}, data[2:40]...)),
		"header only":     {0xFF, 0xD8, 0xFF, 0xD9},
	}
	for name, frame := range cases {
		if _, err := cleanJPEG(frame); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	// A scan without a frame header.
	parts, scan := segments(t, data)
	noHeader := []byte{0xFF, 0xD8}
	for _, part := range parts[:len(parts)-1] {
		if part[0][0] != 0xC0 {
			noHeader = append(noHeader, part[1]...)
		}
	}
	noHeader = append(noHeader, data[scan:]...)
	if _, err := cleanJPEG(noHeader); !errors.Is(err, errNoHeader) {
		t.Errorf("no frame header: %v", err)
	}
}

// --- the frame reader (vendored go-uvc, pkg/transfers/frame.go) ---------------------------------

type payload struct {
	data []byte
	err  error
}

type payloads struct{ queue []payload }

func (p *payloads) Read(buf []byte) (int, error) {
	if len(p.queue) == 0 {
		return 0, transfers.ErrTimeout
	}
	next := p.queue[0]
	p.queue = p.queue[1:]
	if next.err != nil {
		return 0, next.err
	}
	return copy(buf, next.data), nil
}

// uvcPayload wraps data in a 2-byte UVC payload header.
func uvcPayload(fid, eof, errBit bool, data []byte) payload {
	bits := byte(0x80) // end of header
	if fid {
		bits |= 1
	}
	if eof {
		bits |= 2
	}
	if errBit {
		bits |= 0x40
	}
	return payload{data: append([]byte{2, bits}, data...)}
}

// frameOf splits a frame's bytes into payloads of at most size bytes, the last with end-of-frame.
func frameOf(fid bool, data []byte, size int, eof bool) []payload {
	var out []payload
	for len(data) > 0 {
		n := min(size, len(data))
		out = append(out, uvcPayload(fid, eof && n == len(data), false, data[:n]))
		data = data[n:]
	}
	return out
}

func TestFramesArePutTogetherAndDamagedOnesDropped(t *testing.T) {
	a, b, c, d := bytes.Repeat([]byte{0xA1}, 2500), bytes.Repeat([]byte{0xB2}, 2500), bytes.Repeat([]byte{0xC3}, 900), bytes.Repeat([]byte{0xD4}, 700)
	var queue []payload
	queue = append(queue, frameOf(false, a, 1000, true)...)
	// b loses its middle packet on the USB link: dropped.
	bp := frameOf(true, b, 1000, true)
	queue = append(queue, bp[0], payload{err: transfers.ErrDamagedPacket}, bp[2])
	// An empty payload after b's end, then c without an end-of-frame bit: c ends when d begins.
	queue = append(queue, uvcPayload(true, false, false, nil))
	queue = append(queue, frameOf(false, c, 1000, false)...)
	queue = append(queue, frameOf(true, d, 1000, true)...)
	// e carries the UVC error bit: dropped.
	queue = append(queue, uvcPayload(false, false, true, []byte{1, 2, 3}), uvcPayload(false, true, false, []byte{4}))
	reader := transfers.NewFrameReaderFrom(&payloads{queue: queue}, 1<<20, 64<<10)
	expect := func(want []byte) {
		t.Helper()
		frame, err := reader.ReadFrame()
		for errors.Is(err, transfers.ErrDamagedFrame) {
			frame, err = reader.ReadFrame()
		}
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(frame.Bytes(), want) {
			t.Fatalf("got %d bytes starting %x", len(frame.Bytes()), frame.Bytes()[:1])
		}
		// Frame still reads like upstream's (io.Reader over the frame's data).
		read, _ := io.ReadAll(frame)
		if !bytes.Equal(read, want) {
			t.Fatal("Read differs from Bytes")
		}
	}
	expect(a)
	expect(c)
	expect(d) // d's single payload also ended c: returned on the next call
	if reader.Frames != 3 {
		t.Fatalf("%d frames counted", reader.Frames)
	}
	if _, err := reader.ReadFrame(); !errors.Is(err, transfers.ErrDamagedFrame) {
		t.Fatalf("the frame with the error bit: %v", err)
	}
	if _, err := reader.ReadFrame(); !errors.Is(err, transfers.ErrTimeout) {
		t.Fatalf("timeouts are passed on: %v", err)
	}
	if reader.DamagedFrames != 2 || reader.DamagedPackets != 1 || reader.ErrorPayloads != 1 {
		t.Fatalf("counted %d damaged frames, %d lost packets, %d error payloads",
			reader.DamagedFrames, reader.DamagedPackets, reader.ErrorPayloads)
	}
}

func TestAFrameStaysValidUntilTheNextReadReturns(t *testing.T) {
	a, b := bytes.Repeat([]byte{1}, 3000), bytes.Repeat([]byte{2}, 3000)
	var queue []payload
	queue = append(queue, frameOf(false, a, 512, true)...)
	queue = append(queue, frameOf(true, b, 512, true)...)
	// A payload that ends one frame and holds all of the next: both are returned, in order.
	queue = append(queue, frameOf(false, []byte{7, 7, 7}, 512, false)...)
	queue = append(queue, uvcPayload(true, true, false, []byte{8, 8}))
	reader := transfers.NewFrameReaderFrom(&payloads{queue: queue}, 1<<20, 64<<10)
	first, err := reader.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	second, err := reader.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first.Bytes(), a) || !bytes.Equal(second.Bytes(), b) {
		t.Fatal("the first frame was overwritten while the second was put together")
	}
	third, err := reader.ReadFrame()
	if err != nil || !bytes.Equal(third.Bytes(), []byte{7, 7, 7}) {
		t.Fatalf("third frame: %v", err)
	}
	fourth, err := reader.ReadFrame()
	if err != nil || !bytes.Equal(fourth.Bytes(), []byte{8, 8}) {
		t.Fatalf("fourth frame: %v", err)
	}
}

func TestAFrameTooBigIsDropped(t *testing.T) {
	var queue []payload
	queue = append(queue, frameOf(false, bytes.Repeat([]byte{3}, 5000), 1000, true)...)
	queue = append(queue, frameOf(true, bytes.Repeat([]byte{4}, 1500), 1000, true)...)
	reader := transfers.NewFrameReaderFrom(&payloads{queue: queue}, 4000, 64<<10)
	if _, err := reader.ReadFrame(); !errors.Is(err, transfers.ErrDamagedFrame) {
		t.Fatalf("an oversized frame: %v", err)
	}
	frame, err := reader.ReadFrame()
	if err != nil || len(frame.Bytes()) != 1500 {
		t.Fatalf("the next frame: %v", err)
	}
	if reader.Overflows != 1 {
		t.Fatalf("overflows %d", reader.Overflows)
	}
}

// endless serves the same payload, or error, for ever (a camera that never completes a frame).
type endless struct {
	data  []byte
	err   error
	reads int
}

func (e *endless) Read(buf []byte) (int, error) {
	e.reads++
	if e.err != nil {
		return 0, e.err
	}
	return copy(buf, e.data), nil
}

func TestUSBDataThatNeverMakesAFrameStillReturns(t *testing.T) {
	// Header-only payloads with one frame bit and no end (a camera whose sensor stopped), damaged
	// packets only, and data that never ends a frame: each call returns within its timeout, so the
	// helper's checks run and it opens the camera again (up to 1.0.0.5's first build it waited for good).
	sources := map[string]*endless{
		"headers only": {data: []byte{2, 0x82}}, "damaged only": {err: transfers.ErrDamagedPacket},
		"never ends": {data: []byte{2, 0x80, 1, 2, 3}},
	}
	for name, source := range sources {
		reader := transfers.NewFrameReaderFrom(source, 1<<20, 64<<10)
		reader.SetTimeout(30 * time.Millisecond)
		started := time.Now()
		if _, err := reader.ReadFrame(); !errors.Is(err, transfers.ErrTimeout) {
			t.Fatalf("%s: %v", name, err)
		}
		if took := time.Since(started); took > time.Second {
			t.Fatalf("%s: returned after %v", name, took)
		}
		if source.reads < 2 {
			t.Fatalf("%s: %d reads", name, source.reads)
		}
		if lost := reader.DamagedPackets; (name == "damaged only") != (lost > 0) {
			t.Fatalf("%s: %d lost packets counted", name, lost)
		}
	}
}

// slow serves a frame's payloads one per wait, as a slow camera would.
type slow struct {
	queue []payload
	wait  time.Duration
}

func (s *slow) Read(buf []byte) (int, error) {
	time.Sleep(s.wait)
	if len(s.queue) == 0 {
		return 0, transfers.ErrTimeout
	}
	next := s.queue[0]
	s.queue = s.queue[1:]
	return copy(buf, next.data), nil
}

func TestASlowFrameIsKeptAcrossCalls(t *testing.T) {
	data := bytes.Repeat([]byte{0x5A}, 4000)
	reader := transfers.NewFrameReaderFrom(&slow{queue: frameOf(true, data, 500, true), wait: 10 * time.Millisecond}, 1<<20, 64<<10)
	reader.SetTimeout(25 * time.Millisecond) // a frame takes 80 ms: several calls
	timeouts := 0
	for {
		frame, err := reader.ReadFrame()
		if errors.Is(err, transfers.ErrTimeout) {
			timeouts++
			if timeouts > 50 {
				t.Fatal("the frame never completed")
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(frame.Bytes(), data) {
			t.Fatalf("got %d bytes", len(frame.Bytes()))
		}
		break
	}
	if timeouts == 0 || reader.DamagedFrames != 0 {
		t.Fatalf("%d timeouts, %d damaged frames", timeouts, reader.DamagedFrames)
	}
}

func TestAShortHeaderIsDamagedNotACrash(t *testing.T) {
	// The header announces a time stamp and a clock reference (12 bytes) but says it is 2 bytes long;
	// upstream's payload parser then sliced past the data and panicked.
	queue := []payload{{data: []byte{2, 0x8C, 0, 0}}, uvcPayload(false, true, false, []byte{9})}
	queue = append(queue, frameOf(true, []byte{1, 2, 3}, 100, true)...)
	reader := transfers.NewFrameReaderFrom(&payloads{queue: queue}, 1<<20, 64<<10)
	if _, err := reader.ReadFrame(); !errors.Is(err, transfers.ErrDamagedFrame) {
		t.Fatalf("the frame with the short header: %v", err)
	}
	frame, err := reader.ReadFrame()
	if err != nil || !bytes.Equal(frame.Bytes(), []byte{1, 2, 3}) {
		t.Fatalf("the next frame: %v", err)
	}
}

func TestTheDataStartsAfterTheStatedHeaderLength(t *testing.T) {
	// A 12-byte header with a time stamp and a clock reference, and one longer than its fields.
	full := append([]byte{12, 0x80 | 0x0C | 0x02, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, 0xAB, 0xCD)
	padded := append([]byte{4, 0x80 | 0x02 | 0x01, 0, 0}, 0xEF)
	reader := transfers.NewFrameReaderFrom(&payloads{queue: []payload{{data: full}, {data: padded}}}, 1<<20, 64<<10)
	for _, want := range [][]byte{{0xAB, 0xCD}, {0xEF}} {
		frame, err := reader.ReadFrame()
		if err != nil || !bytes.Equal(frame.Bytes(), want) {
			t.Fatalf("got %x (%v), want %x", frame.Bytes(), err, want)
		}
	}
}

func TestOnlyTransfersLibusbKeepsHoldTheContext(t *testing.T) {
	kept := fmt.Errorf("3 isochronous transfers: %w", transfers.ErrTransfersKept)
	gone := errors.New("libusb_release_interface failed: LIBUSB_ERROR_NO_DEVICE")
	if !errors.Is(kept, transfers.ErrTransfersKept) || errors.Is(gone, transfers.ErrTransfersKept) {
		t.Fatal("the closing errors are not told apart")
	}
	source, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(source, []byte("*inUse = *inUse || errors.Is(err, transfers.ErrTransfersKept)")) {
		t.Fatal("a camera that is gone would keep its libusb context")
	}
}

// --- statistics and the frame interval -------------------------------------------------------------

func TestTheFastestFrameIntervalIsChosen(t *testing.T) {
	ms := time.Millisecond
	if got := fastestInterval([]time.Duration{33 * ms, 8 * ms, 16 * ms}, 0, 33*ms); got != 8*ms {
		t.Fatalf("discrete: %v", got)
	}
	if got := fastestInterval(nil, 11*ms, 33*ms); got != 11*ms {
		t.Fatalf("continuous: %v", got)
	}
	if got := fastestInterval(nil, 0, 33*ms); got != 33*ms {
		t.Fatalf("default: %v", got)
	}
	if got := intervalsText([]time.Duration{8333 * time.Microsecond, 16666 * time.Microsecond}, 0, 0); got != "8333,16666" {
		t.Fatal(got)
	}
	if got := intervalsText(nil, 8*ms, 33*ms); got != "8000-33000" {
		t.Fatal(got)
	}
}

func TestTheStatsLine(t *testing.T) {
	start := time.Unix(1000, 0)
	stats := newCameraStats(start)
	stats.opened(800, 400, 8333*time.Microsecond)
	for i := 0; i < 50; i++ {
		stats.frame(start.Add(time.Duration(i)*10*time.Millisecond), i != 7)
	}
	stats.rejected(false)
	stats.usb(2, 9)
	stats.reopening(errors.New("no frame for 2s"))
	line := stats.line(start.Add(5 * time.Second))
	for _, want := range []string{"STATS fps=9.8 ", "sent=49 ", "received=50 ", "damaged_frames=2 ", "lost_packets=9 ",
		"invalid=1 ", "reopened=1 ", "max_gap_ms=20 ", "interval_us=8333 ", "size=800x400", "last_reopen=no-frame-for-2s"} {
		if !strings.Contains(line, want) {
			t.Fatalf("%q lacks %q", line, want)
		}
	}
	// The next window starts afresh; totals carry on.
	line = stats.line(start.Add(10 * time.Second))
	if !strings.Contains(line, "fps=0.0 ") || !strings.Contains(line, "sent=49 ") || !strings.Contains(line, "window_sent=0 ") {
		t.Fatalf("second window: %q", line)
	}
}

func TestAPictureThatWouldBeCutShortIsEncodedAgain(t *testing.T) {
	// A comment segment holding FF D9: Baballonia's reader would take it for the end of the picture.
	data := eyePicture(t, 64, 32)
	comment := []byte{0xFF, 0xFE, 0x00, 0x06, 'x', 0xFF, 0xD9, 'y'}
	frame := append(append(append([]byte{}, data[:2]...), comment...), data[2:]...)
	picture, err := cleanJPEG(frame)
	if err != nil {
		t.Fatal(err)
	}
	if !picture.EarlyEnd {
		t.Fatal("FF D9 inside a segment must be noticed")
	}
	again, err := reencode(picture.Data)
	if err != nil {
		t.Fatal(err)
	}
	if checked, err := cleanJPEG(again); err != nil || checked.EarlyEnd || bytes.Index(again[:len(again)-2], []byte{0xFF, 0xD9}) >= 0 {
		t.Fatalf("encoded again: %v", err)
	}
	if plain, _ := cleanJPEG(data); plain.EarlyEnd {
		t.Fatal("an ordinary picture has no early end")
	}
}
