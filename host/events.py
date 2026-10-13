"""Read the pinned ALVR event stream locally; never retain raw tracking/session data."""
import copy
from collections import OrderedDict
import json
import re
import threading
import time
from urllib.parse import urlsplit
from host.vendor.websockets.sync.client import connect
from launcher.diagnostics import clean
from host.finger_diagnostics import native_hand_sample
from host.pipeline_stats import PipelineStats
from host.stream_options import parse_ib_values

# Driver lines kept as the latest structured values (patches 0009-0012 and encoder choice;
# 1.0.0.5, patch 0018: the stream's colour as checked at encoder start, and frames dropped for
# lack of a head pose, which means the headset PC is not tracking the headset).
STREAM_LINES = {'VROST_STREAM_CONFIG': 'StreamConfig', 'VROST_GAZE_FOVEATION': 'GazeFoveation',
                'VROST_INTRA_REFRESH': 'IntraRefresh', 'VROST_COLOR': 'VideoColour',
                'VROST_NO_HEAD_POSE': 'NoHeadPose',
                # 1.0.0.6, patch 0020: the controllers' distance as received and as submitted while held still.
                'VROST_CONTROLLER_PAIR': 'ControllerPair'}
# Groups recorded in the journal every time (they are rare), not at most every 5 s.
EVERY_LINE = ('VideoColour', 'NoHeadPose')
_ENCODER = re.compile(r'VROST_HOST_ENCODER selected backend=([A-Za-z]{2,16})\b')

def parse_vsync_lock(content):
    """Structured frame-timing lock line from patch 0007 (decimal values)."""
    text=content.strip()
    if not text.startswith('VROST_VSYNC_LOCK '):return None
    values={}
    for key,value in re.findall(r'([a-z_]+)=(-?[0-9]+(?:\.[0-9]+)?)',text[len('VROST_VSYNC_LOCK '):])[:16]:
        number=float(value)
        values[key]=int(number) if key in ('enabled','steering','updates','interval_us') else number
    return values or None

class HostEvents:
    def __init__(self, api, journal):
        self.api=api;self.journal=journal;self.stop_event=threading.Event()
        self.lock=threading.Lock();self.connected=False;self.message='Waiting for the host runtime.'
        self.connection_error='';self.latest={};self.last_record={};self.log_window=0;self.log_count=0;self.skipped=0
        self.repeats=OrderedDict();self.coalesced=0;self.native_hands={};self.pipeline=PipelineStats()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def accept(self,raw):
        event=json.loads(raw);kind=event.get('event_type',{})
        if not isinstance(kind,dict):return
        name=kind.get('id');data=kind.get('data');now=time.monotonic()
        if name in ('StatisticsSummary','GraphStatistics') and isinstance(data,dict):
            if name=='GraphStatistics':self.pipeline.add(data)
            with self.lock:self.latest[name]={'received_utc':time.time(),'values':data}
            if now-self.last_record.get(name,0)>=1:
                self.journal.record('alvr-'+name,data);self.last_record[name]=now
        elif name=='Log' and isinstance(data,dict):
            content=str(data.get('content',''))
            # Native stage counters are structured telemetry, not unique log
            # messages. Keeping them out of the repetition table preserves its
            # capacity for actual warnings and the incident before/after trace.
            native=re.fullmatch(r'(VROST_HOST_AMF|VROST_HOST_CAPTURE|VROST_HOST_ENCODER) ((?:[a-z_]+=[0-9]+ ?)+)', content.strip())
            if native:
                group={'VROST_HOST_AMF':'HostAMF','VROST_HOST_CAPTURE':'HostCapture','VROST_HOST_ENCODER':'HostEncoder'}[native[1]]
                values={key:int(value) for key,value in re.findall(r'([a-z_]+)=([0-9]+)',native[2])}
                with self.lock:self.latest[group]={'received_utc':time.time(),'values':values}
                if now-self.last_record.get(group,0)>=1:
                    self.journal.record('native-'+group,values);self.last_record[group]=now
                return
            for prefix,group in STREAM_LINES.items():
                if content.startswith(prefix+' '):
                    values=parse_ib_values(prefix,content)
                    if values:
                        with self.lock:self.latest[group]={'received_utc':time.time(),'values':values}
                        if group in EVERY_LINE or now-self.last_record.get(group,0)>=5:
                            # RC4.3: the foveation centres follow where the user looks; reports keep the counts only.
                            recorded={key:value for key,value in values.items() if key not in ('left','right')} if group=='GazeFoveation' else values
                            self.journal.record('native-'+group,recorded);self.last_record[group]=now
                    return
            encoder=_ENCODER.match(content.strip())
            if encoder:
                with self.lock:self.latest['EncoderBackend']={'received_utc':time.time(),'values':{'backend':encoder[1]}}
            lock=parse_vsync_lock(content)
            if lock is not None:
                with self.lock:self.latest['VsyncLock']={'received_utc':time.time(),'values':lock}
                self.journal.record('native-VsyncLock',lock)
                return
            native=native_hand_sample(content)
            if native:
                hand,sample=native;sample['received_monotonic']=now
                with self.lock:self.native_hands[hand]=sample
                self.journal.record('native-hand',dict(sample))
                return
            if str(data.get('severity','')).lower()=='error' and content.startswith('Handshake error'):
                with self.lock:self.connection_error=clean(content)[:500]
            # A control failure must survive a burst of per-frame warnings.
            # Preserve its incident before the ordinary log rate limiter.
            if content.startswith('TRANSPORT_INCIDENT ') or 'client disconnected. timeout' in content.lower():
                self.journal.record('transport-incident',clean(content)[:4096])
                return
            message=clean(str(data.get('severity',''))+': '+content)[:4096]
            # Per-frame warnings used to consume the complete report ring.
            # Count identical messages, retaining the first and last occurrence,
            # and write at most one duplicate summary per 30 seconds.
            with self.lock:
                stamp=time.time();previous=self.repeats.get(message)
                if previous:
                    previous['count']+=1;previous['last_utc']=stamp;self.coalesced+=1
                    self.repeats.move_to_end(message)
                    if now-previous['reported_at']<30:return
                    recorded={'message':message,'count':previous['count'],
                              'first_utc':previous['first_utc'],'last_utc':stamp}
                    previous['reported_at']=now;event_name='alvr-log-repeated'
                else:
                    self.repeats[message]={'count':1,'first_utc':stamp,'last_utc':stamp,'reported_at':now}
                    if len(self.repeats)>128:self.repeats.popitem(last=False)
                    recorded=message;event_name='alvr-log'
                if now-self.log_window>=1:self.log_window=now;self.log_count=0
                if self.log_count>=20:self.skipped+=1;return
                self.log_count+=1
            self.journal.record(event_name,recorded)
        # Session, Tracking, Buttons, Haptics, and unknown event types are ignored.
    def reset(self):
        with self.lock:
            self.connection_error='';self.latest={};self.repeats.clear();self.coalesced=0;self.native_hands={}
            self.skipped=0;self.log_count=0;self.log_window=0
        self.pipeline.reset()
    def status(self):
        with self.lock:
            repeats=[{'message':message,'count':row['count'],'first_utc':row['first_utc'],'last_utc':row['last_utc']}
                     for message,row in self.repeats.items() if row['count']>1]
            return {'connected':self.connected,'message':self.message,'statistics':copy.deepcopy(self.latest),
                    'rate_limited_logs':self.skipped,'coalesced_logs':self.coalesced,
                    'repeated_logs':sorted(repeats,key=lambda row:row['count'],reverse=True)[:16],
                    'connection_error':self.connection_error,'pipeline':self.pipeline.summary(),'native_hands':{hand:dict(sample,report_age_s=max(0,time.monotonic()-sample['received_monotonic'])) for hand,sample in self.native_hands.items()}}
    def run(self):
        while not self.stop_event.is_set():
            api=self.api()
            if api:
                try:
                    parsed=urlsplit(api.base)
                    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port:raise ValueError('Host event API must be loopback')
                    with connect('ws://127.0.0.1:'+str(parsed.port)+'/api/events',
                                 additional_headers={'X-ALVR':'true'},compression=None,proxy=None,
                                 open_timeout=2,close_timeout=1,max_size=1024*1024,max_queue=16,ping_interval=None) as ws:
                        with self.lock:self.connected=True;self.message='Host encoder and network event diagnostics connected.'
                        while not self.stop_event.is_set() and self.api() is api:
                            try:self.accept(ws.recv(timeout=1))
                            except TimeoutError:continue
                except Exception as error:
                    with self.lock:self.message='Host event diagnostics reconnecting: '+str(error)[:300]
                finally:
                    with self.lock:self.connected=False
            self.stop_event.wait(1)
    def close(self):self.stop_event.set();self.thread.join(4)
