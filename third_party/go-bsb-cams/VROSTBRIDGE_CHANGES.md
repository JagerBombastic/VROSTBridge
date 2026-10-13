Upstream: https://github.com/LilliaElaine/go-bsb-cams
Commit: d0df45de2d9ce1a7f7febf6a2c1313812e5df287
License: GPL-2.0, see LICENSE.

## Changes by the VROSTBridge project

- September 2026, main.go (line marked "VROSTBridge change"): the MJPEG HTTP server listens on
  127.0.0.1 only, never on all interfaces.
- October 2026: the github.com/hybridgroup/mjpeg module (used as LilliaElaine's fork), whose
  repositories state no licence, is replaced by mjpeg_stream.go, a small MJPEG server written for
  VROSTBridge. main.go's three lines that used the module are marked "VROSTBridge change"; go.mod,
  go.sum and vendor/modules.txt no longer list the module, and its vendored copy is removed.
  Clients get the same stream (multipart/x-mixed-replace, boundary MJPEGBOUNDARY, at most one
  frame per 50 ms each); each part is now exactly its header and its JPEG and is sent at once.
  mjpeg_stream_test.go tests it (`CGO_ENABLED=1 go test -mod=vendor -vet=off -race .`).
- October 2026 (1.0.0): the change notices name the project VROSTBridge (this file was
  VROSTBRIDGE_CHANGES.md); comments only, the program is unchanged.
- October 2026 (1.0.0.3), mjpeg_stream.go: each client is sent the newest camera frame it does
  not have yet as soon as it arrives, at most one every 15 ms, instead of at most one per 50 ms
  (about 17 a second in practice). Eye tracking then follows the eyes sooner.
  mjpeg_stream_test.go tests it.

- October 2026 (1.0.0.5), main.go (marked "VROSTBridge change"), with jpeg_frames.go and
  camera_stats.go added:
  - The cameras' Motion-JPEG frames are passed on as the cameras sent them instead of being decoded
    and encoded again, which took 10 to 16 ms of processor time per frame on a handheld (and frames
    queued up whenever that was slower than the cameras). Each frame is checked (one picture from its
    start-of-image to its end-of-image marker, with a frame header and a scan); the standard Huffman
    tables are added to a frame that has none; the first frames after opening and one in 120 later are
    also decoded in full, and a frame Go cannot decode is not sent, as before. A picture holding the
    bytes FF D9 inside a header segment, or larger than 900 KiB, is decoded and encoded again
    (Baballonia's MJPEG reader would cut it short, or stop reading).
  - The camera is asked for its fastest frame interval. Upstream committed the values of a GET_MAX
    request, and the maximum frame interval is the lowest frame rate. If the camera refuses, its own
    choice is used as before.
  - A frame that lost data on the USB link is skipped. Upstream opened the camera again after any
    damaged packet, without closing what it had opened; eye tracking paused each time.
  - The camera is opened again only when no frame has come for 2 s (5 s after opening) or the device
    is gone, after closing everything opened before (with increasing waits, up to 4 s). The HTTP
    stream stays up meanwhile, so the eye-tracking program keeps its connection.
  - A line starting with "STATS " every 5 s (frames sent and received, frames and packets lost,
    rejected frames, re-openings, the longest gap, the frame interval and size) and a line starting
    with "CAMERA " whenever the camera is opened (its frame intervals, the one asked for and the one
    agreed). The VROSTBridge companion reads them into its reports.
- October 2026 (1.0.0.5), mjpeg_stream.go: a client is sent at most one frame per 8 ms (was 15 ms),
  so a camera faster than 66 frames a second is not held back.
- October 2026 (1.0.0.5), the vendored github.com/kevmo314/go-uvc (Apache-2.0), files marked at
  their top:
  - pkg/transfers/isochronous_reader.go: a damaged packet is skipped and reported (ErrDamagedPacket)
    instead of failing every later read; a failed transfer is resubmitted; a read can be bounded in
    time (ErrTimeout); a gone device is reported (ErrNoDevice); the number of transfers and the
    packets per transfer can be set (go-bsb-cams uses 100 transfers of at most 32 packets, 4 ms each
    at high speed, so the end of a frame arrives sooner than with transfers of up to 128 packets);
    Close cancels the transfers and frees them only once libusb has given them back; transfers
    libusb keeps are never freed, and Close says so (ErrTransfersKept), so their libusb context is
    kept as well (and only then: a camera that is gone fails to release its interface, and its
    context is freed as usual).
  - pkg/transfers/bulk_reader.go: the same timeout, error and Close handling.
  - pkg/transfers/frame.go: ReadFrame puts each frame together in a buffer of its own and drops a
    frame that lost data, carries the UVC error bit or does not fit (ErrDamagedFrame; reading goes
    on), counting them; with a timeout set it also returns ErrTimeout after that long without a
    complete frame while USB data keeps coming (a camera sending only headers, or only damaged
    packets), keeping the frame being put together for the next call, so the helper's checks run
    and it can open the camera again; a payload whose header length is shorter than the fields its
    flags announce is skipped as damaged (upstream's parser sliced past it and panicked), and a
    payload's data starts after its stated header length; Close also stops the transfers and
    returns the interface to its zero-bandwidth setting; NewFrameReaderFrom puts frames together
    from any payload source (tests).
  - pkg/transfers/claim_interval.go (new): ClaimFrameReaderAt, ClaimFrameReader's requests in the
    same order with a given frame interval and a time limit on each request.
  - uvc.go: UVCDevice.Close releases the device handle and the libusb context (upstream only set a
    flag).
  - jpeg_frames_test.go tests the frame checks, the standard tables (byte for byte the ones Go's
    encoder writes), the frame reader with lost packets, error bits, oversized frames, endless
    headers or damaged packets, slow frames, short headers and longer headers, the closing errors,
    and the STATS line (`CGO_ENABLED=1 go test -mod=vendor -vet=off -race .`).

Nothing else is changed; the other vendored modules are the upstream module set as `go mod vendor`
produced it.

Built as a separate executable (tools/go-bsb-cams in the client package); no code of it is linked
into VROSTBridge. The package ships this complete source with it.

## Building
From this folder, with Go 1.24.5 or later (the shipped build used Go 1.24.7) and the system's
libusb-1.0 development files (cgo):

    CGO_ENABLED=1 go build -mod=vendor -buildvcs=false -trimpath -o go-bsb-cams .

`scripts/Build-Eye-Camera-Linux.sh` runs the same command.

## Modules compiled into the program
- github.com/google/gousb v1.1.3: Apache-2.0
- github.com/kevmo314/go-uvc (0eb292711e9f): Apache-2.0
- github.com/google/uuid v1.6.0: BSD-3-Clause
- The Go runtime and standard library: BSD-3-Clause (licenses/Go-BSD.txt).

echo, gommon, go-colorable, go-isatty, bytebufferpool, fasttemplate and golang.org/x/* are listed in
go.mod and vendored, but not compiled into the program.
