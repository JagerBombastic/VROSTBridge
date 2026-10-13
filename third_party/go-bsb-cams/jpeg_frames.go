// Added by the VROSTBridge project in October 2026 (VROSTBridge 1.0.0.5); see VROSTBRIDGE_CHANGES.md.
//
// The cameras' Motion-JPEG frames are passed on as the cameras sent them. Until 1.0.0.5 every frame
// was decoded and encoded again (image/jpeg), which took 10 to 16 ms of processor time per frame on a
// handheld and delayed each frame by as much; when that was slower than the cameras, frames queued up
// behind each other. Now a frame is only checked: it must hold one JPEG picture from its
// start-of-image marker to its end-of-image marker, with a frame header and a scan. Motion-JPEG frames
// may leave out the Huffman tables (the standard ones are implied); the standard tables are then
// added, so any JPEG decoder reads the frame. Every so often a frame is also decoded in full
// (verifyEvery) as the earlier helper did with each one, and a frame Go cannot decode is not sent.
//
// Part of go-bsb-cams as modified by the VROSTBridge project; GPL-2.0, see LICENSE.

package main

import (
	"bytes"
	"errors"
	"image/jpeg"
)

var (
	errNoPicture   = errors.New("no JPEG start-of-image marker")
	errUnfinished  = errors.New("the JPEG picture has no end-of-image marker")
	errNoHeader    = errors.New("the JPEG picture has no frame header")
	errNoScan      = errors.New("the JPEG picture has no scan")
	errBadSegment  = errors.New("a JPEG segment runs past the frame")
	errBadGeometry = errors.New("the JPEG frame header has no size")
)

// jpegPicture describes a checked picture.
type jpegPicture struct {
	Data          []byte // the picture, from SOI to EOI, with Huffman tables
	Width, Height int
	AddedTables   bool // the standard Huffman tables were added
	// EarlyEnd: the bytes FF D9 occur inside a header segment. A reader that takes the first FF D9
	// as the end of the picture (Baballonia's MJPEG reader does) would cut it short there.
	EarlyEnd bool
}

// maxPassOn is the largest picture passed on as it is: Baballonia's MJPEG reader keeps a picture in
// a 1 MiB buffer and stops reading the stream for good if one is larger.
const maxPassOn = 900 << 10

// cleanJPEG checks a camera frame and returns its JPEG picture. data may be returned as a sub-slice of
// frame (tables present) or a new slice (tables added); it never decodes the picture.
func cleanJPEG(frame []byte) (jpegPicture, error) {
	var picture jpegPicture
	start := -1
	for i := 0; i+1 < len(frame) && i < 64; i++ { // the picture starts at once; allow a little padding
		if frame[i] == 0xFF && frame[i+1] == 0xD8 {
			start = i
			break
		}
	}
	if start < 0 {
		return picture, errNoPicture
	}
	i := start + 2
	hasTables, sos := false, -1
	for {
		// Markers may be preceded by any number of 0xFF fill bytes.
		for i < len(frame) && frame[i] == 0xFF && i+1 < len(frame) && frame[i+1] == 0xFF {
			i++
		}
		if i+1 >= len(frame) {
			return picture, errUnfinished
		}
		if frame[i] != 0xFF {
			return picture, errBadSegment
		}
		marker := frame[i+1]
		switch {
		case marker == 0xD9: // EOI
			if picture.Width == 0 {
				return picture, errNoHeader
			}
			if sos < 0 {
				return picture, errNoScan
			}
			end := i + 2
			picture.EarlyEnd = bytes.Index(frame[start:i], []byte{0xFF, 0xD9}) >= 0
			if hasTables {
				picture.Data = frame[start:end]
				return picture, nil
			}
			data := make([]byte, 0, end-start+len(standardTables))
			data = append(data, frame[start:sos]...)
			data = append(data, standardTables...)
			data = append(data, frame[sos:end]...)
			picture.Data, picture.AddedTables = data, true
			return picture, nil
		case marker >= 0xD0 && marker <= 0xD7, marker == 0x01: // RSTn, TEM: no length
			i += 2
			continue
		case marker == 0xD8:
			return picture, errBadSegment // a second start-of-image before this picture ended
		}
		if i+3 >= len(frame) {
			return picture, errUnfinished
		}
		length := int(frame[i+2])<<8 | int(frame[i+3])
		if length < 2 || i+2+length > len(frame) {
			return picture, errBadSegment
		}
		segment := frame[i+4 : i+2+length]
		switch marker {
		case 0xC4: // DHT
			hasTables = true
		case 0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF: // SOFn
			if len(segment) < 5 {
				return picture, errBadGeometry
			}
			picture.Height = int(segment[1])<<8 | int(segment[2])
			picture.Width = int(segment[3])<<8 | int(segment[4])
			if picture.Width == 0 || picture.Height == 0 {
				return picture, errBadGeometry
			}
		}
		i += 2 + length
		if marker != 0xDA { // not SOS
			continue
		}
		if picture.Width == 0 {
			return picture, errNoHeader
		}
		if sos < 0 {
			sos = i - 2 - length
		}
		// Entropy-coded data up to the next marker that is not a stuffed 0xFF00 or a restart.
		for i+1 < len(frame) {
			if frame[i] == 0xFF {
				next := frame[i+1]
				if next != 0x00 && !(next >= 0xD0 && next <= 0xD7) && next != 0xFF {
					break
				}
			}
			i++
		}
	}
}

// The standard Huffman tables of the JPEG specification (ITU-T T.81 Annex K.3), the ones a
// Motion-JPEG frame without tables implies: DC and AC, luminance and chrominance, in one DHT segment.
var standardTables = func() []byte {
	type table struct {
		class, id byte
		counts    [16]byte
		values    []byte
	}
	dcValues := []byte{0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}
	tables := []table{
		{0, 0, [16]byte{0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0}, dcValues},
		{1, 0, [16]byte{0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7d}, []byte{
			0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
			0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08, 0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
			0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
			0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
			0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
			0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
			0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
			0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
			0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
			0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
			0xf9, 0xfa}},
		{0, 1, [16]byte{0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0}, dcValues},
		{1, 1, [16]byte{0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77}, []byte{
			0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21, 0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
			0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91, 0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
			0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34, 0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
			0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38, 0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
			0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
			0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
			0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
			0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
			0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
			0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
			0xf9, 0xfa}},
	}
	body := []byte{}
	for _, t := range tables {
		body = append(body, t.class<<4|t.id)
		body = append(body, t.counts[:]...)
		body = append(body, t.values...)
	}
	length := len(body) + 2
	return append([]byte{0xFF, 0xC4, byte(length >> 8), byte(length)}, body...)
}()

// reencode decodes a picture and encodes it again, as the helper did with every frame before
// 1.0.0.5: for the rare picture that cannot be passed on as it is (EarlyEnd, or larger than
// maxPassOn).
func reencode(data []byte) ([]byte, error) {
	img, err := jpeg.Decode(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	if err := jpeg.Encode(&out, img, &jpeg.Options{Quality: 85}); err != nil {
		return nil, err
	}
	if out.Len() > maxPassOn {
		return nil, errors.New("the picture is too large even when encoded again")
	}
	return out.Bytes(), nil
}
