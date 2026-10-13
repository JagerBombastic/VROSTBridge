"""Exercise the compiled C ABI without SteamVR or a rendering host."""
import ctypes as c
import sys,time
from pathlib import Path
lib=c.CDLL(str(Path(sys.argv[1]).resolve()))
lib.vrost_create.argtypes=[c.c_uint32,c.c_uint32,c.c_float];lib.vrost_create.restype=c.c_void_p
for name in ('vrost_destroy','vrost_resume','vrost_pause'):getattr(lib,name).argtypes=[c.c_void_p]
lib.vrost_message.argtypes=[c.c_void_p,c.c_char_p,c.c_uint64];lib.vrost_message.restype=c.c_uint64
class Event(c.Structure):_fields_=[('kind',c.c_uint32),('width',c.c_uint32),('height',c.c_uint32),('codec',c.c_uint32),('refresh',c.c_float),('device',c.c_uint64),('duration',c.c_float),('frequency',c.c_float),('amplitude',c.c_float)]
lib.vrost_poll.argtypes=[c.c_void_p,c.POINTER(Event)];lib.vrost_poll.restype=c.c_int
assert c.sizeof(Event)==48
assert not lib.vrost_create(0,1,90)
context=lib.vrost_create(1024,1024,90);assert context
try:
 lib.vrost_resume(context)
 deadline=time.monotonic()+3;seen=False
 while time.monotonic()<deadline:
  event=Event()
  if lib.vrost_poll(context,c.byref(event)):
   if event.kind==1:
    size=lib.vrost_message(context,None,0);assert 1<size<4096
    buffer=c.create_string_buffer(size);lib.vrost_message(context,buffer,size);assert buffer.value
    seen=True;break
  time.sleep(.01)
 assert seen,'No discovery status event'
 lib.vrost_pause(context)
finally:lib.vrost_destroy(context)
# RC4 ABI: capabilities, foveation state, counters and loss policy without a stream.
class Foveation(c.Structure):_fields_=[('encoded',c.c_uint32*2),('view_ratio',c.c_float*2),('center_size',c.c_float*2),('center_shifts',(c.c_float*2)*2),('edge_ratio',c.c_float*2)]
class Counters(c.Structure):_fields_=[('frames',c.c_uint64),('network_losses',c.c_uint64),('idr_requests',c.c_uint64),('decoder_saturation',c.c_uint64),('skipped_until_idr',c.c_uint64)]
assert c.sizeof(Foveation)==48 and c.sizeof(Counters)==40
lib.vrost_create2.argtypes=[c.c_uint32,c.c_uint32,c.c_float,c.c_uint32];lib.vrost_create2.restype=c.c_void_p
lib.vrost_foveation.argtypes=[c.c_void_p,c.POINTER(Foveation)];lib.vrost_foveation.restype=c.c_int
lib.vrost_stream_counters.argtypes=[c.c_void_p,c.POINTER(Counters)]
lib.vrost_set_gradual_recovery.argtypes=[c.c_void_p,c.c_int]
lib.vrost_frame_views2.argtypes=[c.c_void_p,c.c_uint64,c.c_void_p,c.POINTER(c.c_float)];lib.vrost_frame_views2.restype=c.c_int
assert not lib.vrost_create2(0,1,90,7)
context=lib.vrost_create2(1024,1024,90,7);assert context
try:
 fov=Foveation();assert lib.vrost_foveation(context,c.byref(fov))==0,'no foveated stream before negotiation'
 counters=Counters(1,1,1,1,1);lib.vrost_stream_counters(context,c.byref(counters))
 assert (counters.frames,counters.network_losses,counters.idr_requests)==(0,0,0)
 lib.vrost_set_gradual_recovery(context,1);lib.vrost_set_gradual_recovery(context,0)
 shifts=(c.c_float*4)();views=c.create_string_buffer(256)
 assert lib.vrost_frame_views2(context,12345,views,shifts)==0,'unknown frame has no views or centres'
finally:lib.vrost_destroy(context)
print('Compiled transport: capability validation, discovery status, bounded message copy, resume/pause/destroy passed')
print('Compiled transport RC4 ABI: create2 capabilities, foveation state, stream counters, loss policy, frame centres passed')
