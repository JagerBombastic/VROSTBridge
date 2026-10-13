# stb

`stb_truetype.h` v1.26 and `stb_image.h` from https://github.com/nothings/stb at
commit 2c980bb59875b0d32144a71867fbdebb2f77cd20, copied unchanged. Sean Barrett and
contributors; public domain or MIT, at your choice (see `LICENSE`).

VROSTBridge uses stb_truetype to rasterise the Inter and Outfit fonts for the
in-headset status card (client/ui/raster.cpp) and the Game Mode screen
(libvrosttext.so), and stb_image (JPEG and PNG only) to load the Game Mode artwork.
