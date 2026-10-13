// Added by the VROSTBridge project in October 2026 (VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md.
//
// Counts of what the camera sent and what was passed on, written every five seconds as one line
// starting with "STATS " (the VROSTBridge companion reads it into its reports), and the camera's
// description as a line starting with "CAMERA " whenever it is opened.
//
// Part of go-bsb-cams as modified by the VROSTBridge project; GPL-2.0, see LICENSE.

package main

import (
	"fmt"
	"strings"
	"sync"
	"time"
)

const statsEvery = 5 * time.Second

type cameraStats struct {
	lock sync.Mutex
	// totals since the program started
	sent, received, damagedFrames, damagedPackets, invalid, undecodable, reopened, addedTables, reencodedCount uint64
	// this window
	windowStart                time.Time
	windowSent, windowReceived uint64
	lastSent                   time.Time
	maxGap                     time.Duration
	// the camera as last opened
	width, height int
	interval      time.Duration
	reason        string // why it was last opened again
}

func newCameraStats(now time.Time) *cameraStats {
	return &cameraStats{windowStart: now}
}

func (s *cameraStats) opened(width, height int, interval time.Duration) {
	s.lock.Lock()
	defer s.lock.Unlock()
	s.width, s.height, s.interval = width, height, interval
	s.lastSent = time.Time{} // the gap while it was closed is not a gap between frames
}

func (s *cameraStats) reopening(reason error) {
	s.lock.Lock()
	defer s.lock.Unlock()
	s.reopened++
	s.reason = oneWord(reason)
}

// frame records one frame put together from USB data, and whether it was sent.
func (s *cameraStats) frame(now time.Time, sent bool) {
	s.lock.Lock()
	defer s.lock.Unlock()
	s.received++
	s.windowReceived++
	if !sent {
		return
	}
	s.sent++
	s.windowSent++
	if !s.lastSent.IsZero() && now.Sub(s.lastSent) > s.maxGap {
		s.maxGap = now.Sub(s.lastSent)
	}
	s.lastSent = now
}

func (s *cameraStats) rejected(undecodable bool) {
	s.lock.Lock()
	defer s.lock.Unlock()
	if undecodable {
		s.undecodable++
	} else {
		s.invalid++
	}
}

func (s *cameraStats) tablesAdded() {
	s.lock.Lock()
	s.addedTables++
	s.lock.Unlock()
}

func (s *cameraStats) reencoded() {
	s.lock.Lock()
	s.reencodedCount++
	s.lock.Unlock()
}

// usb sets the reader's running totals of frames and packets lost on the USB link.
func (s *cameraStats) usb(damagedFrames, damagedPackets uint64) {
	s.lock.Lock()
	s.damagedFrames, s.damagedPackets = damagedFrames, damagedPackets
	s.lock.Unlock()
}

// line returns the STATS line for the window ending now and starts the next window.
func (s *cameraStats) line(now time.Time) string {
	s.lock.Lock()
	defer s.lock.Unlock()
	seconds := now.Sub(s.windowStart).Seconds()
	fps := 0.0
	if seconds > 0 {
		fps = float64(s.windowSent) / seconds
	}
	line := fmt.Sprintf("STATS fps=%.1f sent=%d received=%d window_sent=%d window_received=%d damaged_frames=%d "+
		"lost_packets=%d invalid=%d undecodable=%d tables_added=%d reencoded=%d reopened=%d max_gap_ms=%d interval_us=%d size=%dx%d",
		fps, s.sent, s.received, s.windowSent, s.windowReceived, s.damagedFrames, s.damagedPackets, s.invalid,
		s.undecodable, s.addedTables, s.reencodedCount, s.reopened, s.maxGap.Milliseconds(), s.interval.Microseconds(), s.width, s.height)
	if s.reason != "" {
		line += " last_reopen=" + s.reason
	}
	s.windowStart, s.windowSent, s.windowReceived, s.maxGap = now, 0, 0, 0
	return line
}

// oneWord turns an error into one short token for a key=value line.
func oneWord(err error) string {
	if err == nil {
		return "none"
	}
	text := strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '-', r == '_', r == '.':
			return r
		default:
			return '-'
		}
	}, err.Error())
	if len(text) > 40 {
		text = text[:40]
	}
	return text
}

// intervalsText lists a frame descriptor's frame intervals in microseconds ("a,b,c" or "min-max").
func intervalsText(discrete []time.Duration, minimum, maximum time.Duration) string {
	if len(discrete) > 0 {
		parts := make([]string, 0, len(discrete))
		for _, interval := range discrete {
			parts = append(parts, fmt.Sprint(interval.Microseconds()))
		}
		return strings.Join(parts, ",")
	}
	return fmt.Sprintf("%d-%d", minimum.Microseconds(), maximum.Microseconds())
}

// fastestInterval is the shortest frame interval a frame descriptor offers (0 when it offers none).
func fastestInterval(discrete []time.Duration, minimum, fallback time.Duration) time.Duration {
	best := time.Duration(0)
	for _, interval := range discrete {
		if interval > 0 && (best == 0 || interval < best) {
			best = interval
		}
	}
	if best == 0 && minimum > 0 {
		best = minimum
	}
	if best == 0 {
		best = fallback
	}
	return best
}
