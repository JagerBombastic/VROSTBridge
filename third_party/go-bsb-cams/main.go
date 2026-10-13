package main

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"image/jpeg"
	"log"
	"net/http"
	"os"
	"os/user"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/google/gousb"
	// VROSTBridge change (October 2026): github.com/hybridgroup/mjpeg replaced by mjpeg_stream.go
	"github.com/kevmo314/go-uvc"
	"github.com/kevmo314/go-uvc/pkg/descriptors"
	"github.com/kevmo314/go-uvc/pkg/transfers" // VROSTBridge change (1.0.0.5)
)

const udevrule = `# Bigscreen Bigeye
KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35bd", ATTRS{idProduct}=="0202", MODE="0660", GROUP="users", TAG+="uaccess"
SUBSYSTEM=="usb", ATTRS{idVendor}=="35bd", ATTRS{idProduct}=="0202", MODE="0660", GROUP="users", TAG+="uaccess"
`
const udevfilename = "99-bsb-cams.rules"

// findDevice is the camera's device path, or an error (VROSTBridge change, 1.0.0.5: used when the
// camera is opened again, where getdevice's interactive and fatal paths do not belong).
func findDevice() (string, error) {
	ctx := gousb.NewContext()
	defer ctx.Close()
	dev, err := ctx.OpenDeviceWithVIDPID(0x35bd, 0x0202)
	if err != nil {
		return "", err
	}
	if dev == nil {
		return "", errors.New("could not find device")
	}
	defer dev.Close()
	return fmt.Sprintf("/dev/bus/usb/%03v/%03v", dev.Desc.Bus, dev.Desc.Address), nil
}

func getdevice() (device string) {
	ctx := gousb.NewContext()
	defer ctx.Close()
	dev, err := ctx.OpenDeviceWithVIDPID(0x35bd, 0x0202)
	user, _ := user.Current()
	if user.Username == "root" && !*sudo {
		log.Print("Running As Root Isnt Reccomended For Safety, Creating A UDEV Rule To Allow Rootless Access ")
		if _, err := os.Stat(udevfilename); err == nil {
			log.Print("File Already Exists")
		} else {
			err := os.WriteFile(udevfilename, []byte(udevrule), 0644)
			if err != nil {
				log.Fatalf("Could not Create File: %v", err)
			}
		}
		var response string
		log.Print("Would You Like The UDEV Rule Automatically Moved And Put In Place? (Y/N)")
		fmt.Scan(&response)
		switch strings.ToLower(response) {
		case "yes", "ye", "y":
			log.Printf("Would You Like The Rule Moved To The Userspace (/usr/lib/udev/rules.d/%v) Or The Linux OS Space (/etc/udev/rules.d/%v) (Good For Atomic Operating Systems) ", udevfilename)
			log.Print("0/Userspace    1/OS Space")
			fmt.Scan(&response)
			switch strings.ToLower(response) {
			case "0", "userspace":
				log.Printf("Putting File In /usr/lib/udev/rules.d/%v !!", udevfilename)
				err := os.Rename(udevfilename, "/usr/lib/udev/rules.d/"+udevfilename)
				if err != nil {
					log.Fatalf("Could Not Move File: %v", err)
				}
			case "1", "os space":
				log.Printf("Putting File In /etc/udev/rules.d/%v !!", udevfilename)
				err := os.Rename(udevfilename, "/etc/udev/rules.d/"+udevfilename)
				if err != nil {
					log.Fatalf("Could Not Move File: %v", err)
				}
			default:
				log.Fatal("Invalid Response")
			}
			log.Print("Please Reboot Your PC For Changes To Take Effect!!")
			os.Exit(0)
		case "n", "no":
			log.Printf("Please move the file (%v) into your udev rule directory and reboot for it to take effect, or if you REALLLLY want to run this program as sudo append --sudo to your run command", udevfilename)
			os.Exit(0)
		default:
			log.Fatal("Invalid Answer")
		}
	}
	if err != nil {
		if err == gousb.ErrorAccess {
			log.Print("It looks like the cameras cannot be accessed, udev file being created in this directory")
			log.Printf("Creating UDEV Rule At %v", udevfilename)
			err := os.WriteFile(udevfilename, []byte(udevrule), 0644)
			if err != nil {
				log.Fatalf("Could not Create File: %v", err)
			}
			log.Print("File Created ! Please copy to your udev directory, chown to root, and reboot for it to take effect")
			os.Exit(0)

		} else {
			log.Fatal(err)
		}
	}
	if dev == nil {
		log.Fatal("Could Not Find Device, Please Make Sure It Is On And Plugged In !")
	}
	defer dev.Close()
	return fmt.Sprintf("/dev/bus/usb/%03v/%03v", dev.Desc.Bus, dev.Desc.Address)
}

var gitVersion string
var verbosePtr = flag.Bool("verbose", false, "Whether or not to show libusb errors")
var port = flag.Int("port", 8080, "What Port To Output Frames To")
var version = flag.Bool("version", false, "Flag To Show Current Version")
var sudo = flag.Bool("sudo", false, "Force Program To Run As Sudo")

func main() {
	flag.Parse()
	if *version {
		log.Print("go-bsb-cams " + gitVersion)
		os.Exit(0)
	}
	stream := newMJPEGStream() // VROSTBridge change (October 2026): was mjpeg.NewLiveStream()
	device := getdevice()
	// Pass your jpegBuffer frames using stream.UpdateJPEG(<your-buffer>)
	stats := newCameraStats(time.Now()) // VROSTBridge change (1.0.0.5): counts, a STATS line every 5 s
	go func() {
		for now := range time.Tick(statsEvery) {
			log.Print(stats.line(now))
		}
	}()
	go imagestreamer(stream, device, stats)
	mux := http.NewServeMux()
	mux.Handle("/stream", stream)
	log.Print("Server Is Running And Can Be Accessed At: http://localhost:" + strconv.Itoa(*port) + "/stream")
	log.Print("Make Sure You Have No Ending / When Inputting The Url Into Baballonia !!!")
	log.Print("If You Are Here And Cannot See The Cams, Please Close This Program, Unplug And Replug Your BSB, And Try Again :)")
	log.Fatal(http.ListenAndServe("127.0.0.1:"+strconv.Itoa(*port), mux)) // VROSTBridge change (September 2026): listen on 127.0.0.1 only; upstream listened on all interfaces

}

// VROSTBridge change (1.0.0.5): imagestreamer was rewritten. The camera is opened at its fastest frame
// rate, its frames are passed on as it sent them (checked, not decoded and encoded again), a frame that
// lost data on the USB link is skipped instead of opening the camera again, and the camera is opened
// again (closing the earlier handle first) only when no frame has come for a while or the device is
// gone. The HTTP stream stays up meanwhile, so the eye-tracking program keeps its connection.
const (
	readTimeout  = 250 * time.Millisecond // a read waits at most this long for USB data
	firstFrame   = 5 * time.Second        // the first frame must come within this after opening
	stallLimit   = 2 * time.Second        // then frames must keep coming at least this often
	verifyEvery  = 120                    // one frame in this many is also decoded in full
	verifyFirst  = 5                      // ... and the first few after opening
	transfersISO = 100                    // isochronous transfers in flight (as upstream)
	packetsISO   = 32                     // packets per transfer (upstream: up to 128): 4 ms at high speed
)

func imagestreamer(stream *mjpegStream, device string, stats *cameraStats) { // VROSTBridge change (October 2026): was *mjpeg.Stream
	transfers.IsochronousTransfers = transfersISO
	transfers.IsochronousMaxPackets = packetsISO
	backoff := 250 * time.Millisecond
	for attempt := 0; ; attempt++ {
		if attempt > 0 {
			if path, err := findDevice(); err == nil {
				device = path // a camera plugged in again can have a new address
			}
		}
		opened := time.Now()
		err := streamFrames(stream, device, stats)
		stats.reopening(err)
		log.Printf("CAMERA opening the camera again (%v)", err)
		if time.Since(opened) > 30*time.Second {
			backoff = 250 * time.Millisecond
		}
		time.Sleep(backoff)
		backoff = min(backoff*2, 4*time.Second)
	}
}

// streamFrames opens the camera, sends its frames until it stalls or fails, and closes it again.
func streamFrames(stream *mjpegStream, device string, stats *cameraStats) error {
	deviceFd, err := syscall.Open(device, syscall.O_RDWR, 0)
	if err != nil {
		return err
	}
	defer syscall.Close(deviceFd) // closed last, after libusb let go of it
	ctx, err := uvc.NewUVCDevice(uintptr(deviceFd))
	if err != nil {
		return err
	}
	inUse := false // libusb did not give back every transfer: keep its context rather than free it
	defer func() {
		if !inUse {
			ctx.Close()
		}
	}()
	info, err := ctx.DeviceInfo()
	if err != nil {
		return err
	}
	defer info.Close()
	for _, iface := range info.StreamingInterfaces {
		for i, desc := range iface.Descriptors {
			fd, ok := desc.(*descriptors.MJPEGFormatDescriptor)
			if !ok || i+1 >= len(iface.Descriptors) {
				continue
			}
			frd, ok := iface.Descriptors[i+1].(*descriptors.MJPEGFrameDescriptor)
			if !ok {
				continue
			}
			return streamFrom(stream, stats, iface, fd, frd, &inUse)
		}
	}
	return errors.New("the camera offers no Motion-JPEG format")
}

func streamFrom(stream *mjpegStream, stats *cameraStats, iface *transfers.StreamingInterface,
	fd *descriptors.MJPEGFormatDescriptor, frd *descriptors.MJPEGFrameDescriptor, inUse *bool) error {
	ci := frd.ContinuousFrameInterval
	wanted := fastestInterval(frd.DiscreteFrameIntervals, ci.MinFrameInterval, frd.DefaultFrameInterval)
	resp, err := iface.ClaimFrameReaderAt(fd.Index(), frd.Index(), wanted)
	how := "fastest"
	if err != nil {
		log.Printf("CAMERA the camera did not take the %d us frame interval (%v); using its own", wanted.Microseconds(), err)
		how = "device"
		if resp, err = iface.ClaimFrameReader(fd.Index(), frd.Index()); err != nil {
			return err
		}
	}
	defer func() {
		if err := resp.Close(); err != nil {
			log.Printf("CAMERA closing: %v", err)
			// Only transfers libusb still owns keep its context alive; a camera that is gone fails
			// to release its interface, and its context is freed as usual.
			*inUse = *inUse || errors.Is(err, transfers.ErrTransfersKept)
		}
	}()
	resp.SetTimeout(readTimeout)
	negotiated := resp.Negotiated()
	log.Printf("CAMERA mjpeg %dx%d intervals_us=%s default_us=%d asked_us=%d negotiated_us=%d chosen=%s max_frame=%d max_payload=%d",
		frd.Width, frd.Height, intervalsText(frd.DiscreteFrameIntervals, ci.MinFrameInterval, ci.MaxFrameInterval),
		frd.DefaultFrameInterval.Microseconds(), wanted.Microseconds(), negotiated.FrameInterval.Microseconds(), how,
		negotiated.MaxVideoFrameSize, negotiated.MaxPayloadTransferSize)
	stats.opened(int(frd.Width), int(frd.Height), negotiated.FrameInterval)
	opened, last := time.Now(), time.Time{}
	checked := 0
	for {
		fr, err := resp.ReadFrame()
		stats.usb(resp.DamagedFrames, resp.DamagedPackets)
		now := time.Now()
		if err != nil {
			if !errors.Is(err, transfers.ErrTimeout) && !errors.Is(err, transfers.ErrDamagedFrame) {
				return err
			}
			if last.IsZero() && now.Sub(opened) > firstFrame {
				return fmt.Errorf("no frame within %v of opening", firstFrame)
			}
			if !last.IsZero() && now.Sub(last) > stallLimit {
				return fmt.Errorf("no frame for %v", stallLimit)
			}
			continue
		}
		picture, err := cleanJPEG(fr.Bytes())
		if err != nil {
			stats.frame(now, false)
			stats.rejected(false)
			if *verbosePtr {
				log.Print(err)
			}
			if !last.IsZero() && now.Sub(last) > stallLimit {
				return fmt.Errorf("no usable frame for %v", stallLimit)
			}
			continue
		}
		data := picture.Data
		if picture.EarlyEnd || len(data) > maxPassOn {
			// The rare picture that cannot be passed on as it is: decoded and encoded again.
			if data, err = reencode(data); err != nil {
				stats.frame(now, false)
				stats.rejected(true)
				continue
			}
			stats.reencoded()
		} else if checked < verifyFirst || resp.Frames%verifyEvery == 0 {
			// As the earlier helper did with every frame: a frame Go cannot decode is not sent.
			checked++
			if _, err := jpeg.Decode(bytes.NewReader(data)); err != nil {
				stats.frame(now, false)
				stats.rejected(true)
				if *verbosePtr {
					log.Print(err)
				}
				continue
			}
		}
		if picture.AddedTables {
			stats.tablesAdded()
		}
		stream.UpdateJPEG(data) // copies the picture
		stats.frame(now, true)
		last = now
	}
}

// Modified by the VROSTBridge project: in September 2026 the
// MJPEG HTTP server was made to listen on 127.0.0.1 only (upstream listened on all interfaces), in
// October 2026 the github.com/hybridgroup/mjpeg module was replaced by mjpeg_stream.go, and in October
// 2026 (VROSTBridge 1.0.0.5) imagestreamer was rewritten (frames passed on as the cameras sent them, the
// fastest frame rate, lost data skipped, the camera opened again cleanly) and findDevice added. The
// changed lines are marked "VROSTBridge change"; see VROSTBRIDGE_CHANGES.md. Upstream: https://github.com/LilliaElaine/go-bsb-cams
// at commit d0df45de2d9ce1a7f7febf6a2c1313812e5df287. GPL-2.0: see LICENSE.
