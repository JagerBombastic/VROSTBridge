"""Reversible Bazzite gamescope setup and owned headset-lease supervision."""
import hashlib,json,os,re,shlex,stat,subprocess,sys,tempfile,threading,time,queue
from pathlib import Path
from launcher.client_runtime import steam_environment,process_identity
MARKER='# VROSTBridge Game Mode v1'
PATTERN=re.compile(r'^(card\d+)-(DP-\d+(?:-\d+)*)$')
def in_game_mode():
    return bool(os.environ.get('GAMESCOPE_WAYLAND_DISPLAY') or 'gamescope' in (os.environ.get('XDG_CURRENT_DESKTOP','')+' '+os.environ.get('XDG_SESSION_DESKTOP','')).lower())
def _extensions(edid):
    """EDID extension blocks present (the count the base block announces, bounded by the data)."""
    return [edid[128*i:128*(i+1)] for i in range(1,min(edid[126],len(edid)//128-1)+1)]
def microsoft_hmd(edid):
    """A Microsoft HMD / specialized display VSDB marking a head-mounted display (kernel drm_parse_microsoft_vsdb).

    As the kernel reads it: CTA-861 extensions of revision 3 or later (earlier ones have no data
    block collection), vendor block with OUI CA125C and a 21-byte payload, version 1 or 2, or 3
    without the desktop-usage bit.
    """
    for ext in _extensions(edid):
        if ext[0]!=0x02 or ext[1]<3:continue
        end=ext[2]
        if not 4<=end<=127:continue
        at=4
        while at<end:
            tag,length=ext[at]>>5,ext[at]&0x1f
            if at+1+length>end:break
            if tag==3 and length==21 and ext[at+1:at+4]==b'\x5c\x12\xca':
                version,desktop=ext[at+4],bool(ext[at+5]&0x40)
                if version in (1,2) or (version==3 and not desktop):return True
            at+=1+length
    return False
def displayid_hmd(edid):
    """A DisplayID 2.0 base section whose primary use is a head-mounted VR (7) or AR (8) display.

    As the kernel reads it (update_displayid_info): the first DisplayID extension whose section
    checksum is valid and that holds at least one complete data block decides.
    """
    for ext in _extensions(edid):
        if ext[0]!=0x70:continue
        size=ext[2]
        if size>121 or sum(ext[1:6+size])%256:continue  # header, payload and checksum fit and sum to zero
        if size<3 or 3+ext[7]>size:continue  # one complete data block (tag, revision, length, payload)
        return ext[1]==0x20 and ext[3] in (7,8)
    return False
def edid_product(edid):
    """(maker letters, product code) of a valid EDID base block, else None."""
    if len(edid)<128 or edid[:8]!=b'\x00\xff\xff\xff\xff\xff\xff\x00' or sum(edid[:128])%256:return None
    m=int.from_bytes(edid[8:10],'big');return ''.join(chr(64+((m>>i)&31)) for i in (10,5,0)),int.from_bytes(edid[10:12],'little')
MAKERS={'VLV':'Valve Index','BIG':'Bigscreen Beyond'}
def identity(edid,non_desktop=False):
    """The head-mounted display an EDID describes, mirroring core/headset_edid.hpp (RC4.3).

    RC4.2 knew only the Index's product codes and one Bigscreen code, so a Bigscreen Beyond 2e
    was not recognised, Game Mode never reserved it and Start stayed unavailable there. The
    kernel's own head-mounted markers in the EDID are used now, and non_desktop is the kernel's
    verdict for the connector (its quirk list included), read through the lease helper. No
    desktop display is matched.
    """
    product=edid_product(edid)
    if product is None:return None
    maker,code=product
    if maker=='VLV' and (code==0x91a8 or 0x91b0<=code<=0x91bf):return 'Valve Index'
    if maker=='BIG' and code==0x1234:return 'Bigscreen Beyond'
    if not non_desktop and not microsoft_hmd(edid) and not displayid_hmd(edid):return None
    return MAKERS.get(maker,'VR headset')
def describe_output(edid,non_desktop=False):
    """Plain description of a connected display for diagnostics: maker, product and head-mounted markers."""
    product=edid_product(edid)
    if product is None:return 'no readable EDID'
    marks=[name for name,found in (('Microsoft HMD block',microsoft_hmd(edid)),('DisplayID head-mounted',displayid_hmd(edid)),
                                   ('kernel non-desktop',non_desktop)) if found]
    return '%s %04X%s'%(product[0],product[1],' ('+', '.join(marks)+')' if marks else '')
def kernel_connectors(helper,runner=None,timeout=3):
    """{(card, connector name): row} for connected connectors as the kernel sees them (RC4.3).

    The lease helper (VROSTBridgeLease --list-connectors) reads each card's connectors without
    probing them: the kernel's "non-desktop" property is what compositors use to offer a display
    for leasing, and it also covers headsets only the kernel's quirk list knows. Empty when the
    helper is missing or fails; the EDID markers then decide alone.
    """
    try:
        result=(runner or subprocess.run)([str(helper),'--list-connectors'],capture_output=True,text=True,timeout=timeout)
    except (OSError,ValueError,subprocess.SubprocessError):return {}
    rows={}
    for line in (getattr(result,'stdout','') or '').splitlines()[:64]:
        try:row=json.loads(line)
        except ValueError:continue
        if (isinstance(row,dict) and isinstance(row.get('name'),str) and re.fullmatch(r'[A-Za-z]+(?:-[A-Za-z]+)?-\d+(?:-\d+)*',row['name'])
                and isinstance(row.get('card'),str) and re.fullmatch(r'/dev/dri/card\d+',row['card'])):
            rows[(row['card'],row['name'])]=row
    return rows
def _non_desktop(kernel,card,name):
    return bool((kernel or {}).get(('/dev/dri/'+card,name),{}).get('non_desktop'))
def connectors(root=Path('/sys/class/drm'),kernel=None):
    rows=[]
    for p in sorted(root.glob('card*-DP-*')):
        m=PATTERN.fullmatch(p.name)
        if not m:continue
        try:
            if (p/'status').read_text().strip()!='connected':continue
            edid=(p/'edid').read_bytes();model=identity(edid,_non_desktop(kernel,m[1],m[2]))
            if model:
                rows.append(dict(id=p.name,name=m[2],card='/dev/dri/'+m[1],connector_id=int((p/'connector_id').read_text()),edid_hex=edid.hex(),model=model))
        except (OSError,ValueError):pass
    return rows
def unrecognised_outputs(root=Path('/sys/class/drm')):
    """Whether a connected DisplayPort output is not recognised as a headset from its EDID alone."""
    for p in sorted(root.glob('card*-DP-*')):
        if not PATTERN.fullmatch(p.name):continue
        try:
            if (p/'status').read_text().strip()!='connected':continue
            if identity((p/'edid').read_bytes()) is None:return True
        except OSError:return True
    return False
def unique(row,root):
    for p in root.glob('card*-'+row['name']):
        try:
            if p.name!=row['id'] and (p/'status').read_text().strip()=='connected':return False
        except OSError:pass
    return True
HEADSETS='headsets.json'
def remember_headsets(directory,rows):
    """Remember connector names a headset used (not part of the setup receipt). Returns all known names."""
    path=Path(directory)/HEADSETS;known=known_headsets(directory)
    names=[row['name'] for row in rows if isinstance(row.get('name'),str) and re.fullmatch(r'DP-\d+(?:-\d+)*',row['name'])]
    merged=list(dict.fromkeys(names+known))[:8]
    if merged!=known:
        try:atomic(path,json.dumps(dict(version=1,connectors=merged))+'\n')
        except OSError:pass
    return merged
def known_headsets(directory):
    value=read_json(Path(directory)/HEADSETS).get('connectors')
    return [name for name in value if isinstance(name,str) and re.fullmatch(r'DP-\d+(?:-\d+)*',name)][:8] if isinstance(value,list) else []
def connected_outputs(root=Path('/sys/class/drm'),kernel=None):
    """{connector name: headset model or ''} for every connected display output."""
    result={}
    for p in sorted(root.glob('card*-*')):
        m=re.fullmatch(r'(card\d+)-(.+)',p.name)
        if not m:continue
        try:
            if (p/'status').read_text().strip()!='connected':continue
        except OSError:continue
        try:model=identity((p/'edid').read_bytes(),_non_desktop(kernel,m[1],m[2])) or ''
        except OSError:model=''
        # gamescope names outputs without the card: a name is a headset only if every card agrees.
        result[m[2]]=model if m[2] not in result else (result[m[2]] and model)
    return result
def output_descriptions(root=Path('/sys/class/drm'),kernel=None):
    """{connector name: maker, product and head-mounted markers} for every connected output (diagnostics)."""
    result={}
    for p in sorted(root.glob('card*-*')):
        m=re.fullmatch(r'(card\d+)-(.+)',p.name)
        if not m:continue
        try:
            if (p/'status').read_text().strip()!='connected':continue
            result[m[2]]=describe_output((p/'edid').read_bytes(),_non_desktop(kernel,m[1],m[2]))
        except OSError:continue
    return result
def headset_outputs(known,outputs):
    """Outputs Game Mode must rank last: connected headsets, and remembered headset ports
    that are empty now (a headset plugged in later must not become the display). A
    remembered port with another display connected now keeps its normal place."""
    connected=[name for name,model in outputs.items() if model]
    idle=[name for name in known if name not in outputs]
    return list(dict.fromkeys(connected+idle))
def rank_headsets_last(value,headsets):
    """gamescope --prefer-output list with the headset connectors after every other output.

    gamescope ranks a connector by its position in the list; names not listed take the
    position of "*". Bazzite's Game Mode passes "*,eDP-1" (any external output before the
    built-in screen), so a connected headset became the Game Mode display and the PC's
    own screen went dark. Moving headset connectors to the end keeps external monitors
    first and the built-in screen ahead of any headset.
    """
    names=[name for name in str(value).split(',') if name and name not in headsets]
    if '*' not in names:names.append('*')
    return ','.join(names+[name for name in headsets if name not in names])
def prefer_output(arguments,headsets):
    if not headsets:return list(arguments)
    args=list(arguments)
    for i,a in enumerate(args):
        if a in ('--prefer-output','-O') and i+1<len(args):
            args[i+1]=rank_headsets_last(args[i+1],headsets);return args
        if a.startswith('--prefer-output='):
            args[i]='--prefer-output='+rank_headsets_last(a.split('=',1)[1],headsets);return args
    return ['--prefer-output',rank_headsets_last('*',headsets)]+args
def runtime_dir():
    p=Path(os.environ.get('XDG_RUNTIME_DIR',''))
    if not p.is_absolute():raise ValueError('A graphical user session is required.')
    m=p.lstat()
    if not stat.S_ISDIR(m.st_mode) or m.st_uid!=os.getuid() or m.st_mode&0o077:raise ValueError('Runtime directory is not private to this user.')
    p=p/'vrostbridge'
    p.mkdir(mode=0o700,exist_ok=True);m=p.lstat()
    if not stat.S_ISDIR(m.st_mode) or m.st_uid!=os.getuid() or m.st_mode&0o077:raise ValueError('VROSTBridge runtime directory is not private.')
    return p
def read_json(p):
    try:
        x=json.loads(p.read_text());return x if isinstance(x,dict) else {}
    except (OSError,ValueError):return {}
def atomic(p,text,mode=0o600):
    p.parent.mkdir(parents=True,exist_ok=True);fd,name=tempfile.mkstemp(dir=p.parent,prefix='.vrostbridge-')
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(name,p)
    finally:Path(name).unlink(missing_ok=True)
def digest(data):return hashlib.sha256(data).hexdigest()
class GameMode:
    def __init__(self,root,log=lambda x:None,sysfs=Path('/sys/class/drm')):
        self.root=Path(root);self.log=log;self.sysfs=sysfs
        cfg=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))
        self.directory=cfg/'vrostbridge/game-mode';self.wrapper=self.directory/'gamescope';self.options=self.directory/'settings.json';self.receipt=self.directory/'receipt.json'
        # gamescope-session explicitly sources HOME/.config/environment.d.
        self.config=Path.home()/'.config/environment.d/99-vrostbridge-game-mode.conf'
        self.executable=(self.root.parent.parent/'current' if self.root.parent.name=='versions' else self.root)/'VROSTBridge-Client'
        self._capability=None;self._kernel=(0.0,{});self._kernel_refreshing=False
    def lease_helper(self):
        return next((p for p in (self.root/'VROSTBridgeLease',self.root/'dist/client/VROSTBridgeLease') if p.is_file()),self.root/'VROSTBridgeLease')
    def kernel(self,fresh=False):
        """The kernel's connector list (non-desktop flags) from the lease helper, cached for 10 s.

        Read only while a connected DisplayPort display is not recognised from its EDID alone
        (only then does the kernel's flag add anything), so the helper rarely opens a card.
        After the first read a stale list is refreshed in the background: status() runs under
        the companion's lock, which every native log line also takes (RC4.3 review).
        """
        when,value=self._kernel
        if fresh or not when:return self._probe_kernel()
        if time.monotonic()-when>10 and not self._kernel_refreshing:
            self._kernel_refreshing=True
            def refresh():
                try:self._probe_kernel()
                finally:self._kernel_refreshing=False
            threading.Thread(target=refresh,daemon=True,name='VROSTBridge-connectors').start()
        return value
    def _probe_kernel(self):
        try:needed=unrecognised_outputs(self.sysfs)
        except OSError:needed=True
        value=kernel_connectors(self.lease_helper()) if needed and self.lease_helper().is_file() else {}
        self._kernel=(time.monotonic(),value)
        return value
    def owned(self,p,receipt):
        try:return p.is_file() and not p.is_symlink() and p.stat().st_uid==os.getuid() and digest(p.read_bytes()) in (receipt.get('files',{}).get(str(p)),receipt.get('previous',{}).get(str(p)))
        except OSError:return False
    def capability(self):
        if self._capability is not None:return self._capability
        reason=''
        try:
            if not self.executable.is_file():raise ValueError('Install the Bazzite package before enabling Game Mode.')
            if any(c.isspace() for c in str(self.wrapper)):raise ValueError('This gamescope-session version requires a wrapper path without spaces.')
            try:session=Path('/usr/share/gamescope-session-plus/gamescope-session-plus').read_text()
            except FileNotFoundError:raise ValueError('This system has no Steam Game Mode session (gamescope-session-plus).') from None
            if 'GAMESCOPE_BIN' not in session:raise ValueError('The installed Game Mode session lacks compositor-wrapper support.')
            r=subprocess.run(['/usr/bin/gamescope','--help'],env=steam_environment(),capture_output=True,text=True,timeout=5)
            if '--lease-connector' not in r.stdout+r.stderr:raise ValueError('The installed gamescope lacks headset display leasing. Update Bazzite before enabling Game Mode.')
        except (OSError,ValueError,subprocess.SubprocessError) as e:reason=str(e)
        self._capability=(not reason,reason);return self._capability
    def conflict(self):
        for d in (Path('/etc/environment.d'),self.config.parent):
            for p in d.glob('*.conf'):
                if p==self.config:continue
                try:
                    if re.search(r'^\s*(?:export\s+)?GAMESCOPE_BIN\s*=',p.read_text(),re.M):return 'Another Game Mode compositor override exists: '+str(p)
                except OSError:continue
        env=os.environ.get('GAMESCOPE_BIN','')
        if env and env not in ('/usr/bin/gamescope',str(self.wrapper)):return 'The current session already overrides gamescope.'
        return ''
    IDENTITY=('id','name','card','connector_id','edid_hex')
    def details(self):
        d=runtime_dir();sock=d/'gamescope-lease.sock';active=read_json(d/'gamescope-lease.json');row=active.get('connector',{})
        try:m=sock.lstat()
        except FileNotFoundError:raise ValueError('Game Mode has not reserved the headset display.') from None
        if not stat.S_ISSOCK(m.st_mode) or m.st_uid!=os.getuid():raise ValueError('No valid Game Mode display socket.')
        # The same connector and display identity as at Game Mode start (the model name may be worded differently).
        current=next((r for r in connectors(self.sysfs,self.kernel()) if isinstance(row,dict) and all(r.get(k)==row.get(k) for k in self.IDENTITY)),None)
        if current is None:raise ValueError('The headset reserved at Game Mode start is no longer connected, or another one is.')
        if not gamescope_alive(active):raise ValueError('The gamescope that reserved the headset display is no longer running.')
        return dict(current,lease_socket=str(sock))
    def wrapper_record(self):
        """What the gamescope wrapper did when this Game Mode session started ({} if it did not run)."""
        try:value=read_json(runtime_dir()/WRAPPER_RECORD)
        except (OSError,ValueError):return {}
        return value if value.get('version')==1 else {}
    RESTART=' Switch to Desktop and back to Game Mode with the headset plugged in and powered.'
    ADVICE={'unrecognised':' Send a report from Reports so the headset can be supported.',
            'several':' Choose the headset on the VROSTBridge page in Desktop Mode, then switch back to Game Mode.',
            'chosen-missing':' Choose the connected headset on the VROSTBridge page in Desktop Mode, then switch back to Game Mode.'}
    def lease_problem(self,error,rows,game):
        """Why Game Mode has no headset display, as precisely as the evidence allows (RC4.3)."""
        if not game:
            if rows:return 'Enabled. Game Mode reserves the '+rows[0]['model']+' on '+rows[0]['name']+' when it starts with the headset connected.'
            return 'Enabled. Connect the headset before entering Game Mode.'
        record=self.wrapper_record()
        if not record:
            return "This Game Mode session started without VROSTBridge's display reservation."+self.RESTART
        if not record.get('leased'):
            advice=self.ADVICE.get(record.get('code'),self.RESTART)
            return 'Game Mode started without the headset ('+str(record.get('reason') or 'unknown reason')+').'+advice
        if isinstance(error,OSError):error='The headset display reservation could not be read ('+(error.strerror or type(error).__name__)+')'
        return str(error).rstrip('.')+'.'+self.RESTART
    def status(self):
        available,reason=self.capability();receipt=read_json(self.receipt)
        kernel=self.kernel()
        enabled=all(self.owned(p,receipt) for p in (self.config,self.wrapper,self.options));rows=connectors(self.sysfs,kernel);active=False
        conflict=self.conflict()
        if conflict:available=False;reason=conflict
        if enabled and not conflict:
            try:self.details();active=True;reason='Headset display reserved. Open VROSTBridge Game Mode from Steam.'
            except (OSError,ValueError) as error:reason=self.lease_problem(error,rows,in_game_mode())
        elif available:reason='Not set up yet: Add to Steam sets it up.'
        if rows:remember_headsets(self.directory,rows)
        try:outputs=output_descriptions(self.sysfs,kernel)
        except OSError:outputs={}
        record=self.wrapper_record() if in_game_mode() else {}
        return dict(available=available,enabled=enabled,lease_active=active,message=reason,connectors=[{k:v for k,v in r.items() if k!='edid_hex'} for r in rows],selected_connector=read_json(self.options).get('connector','auto'),
                    headset_connectors=known_headsets(self.directory),outputs=outputs,
                    wrapper={key:record.get(key) for key in ('time','leased','reason','code')} if record else None)
    def refresh(self):self._capability=None;return self.status()
    def wrapper_text(self):
        exe=shlex.quote(str(self.executable))
        # Plain gamescope when VROSTBridge was removed without undoing this setup.
        return '#!/bin/sh\n'+MARKER+'\n[ -x '+exe+' ] || exec /usr/bin/gamescope "$@"\nexec '+exe+' --gamescope-wrapper "$@"\n'
    def update_wrapper(self):
        """Bring an enabled setup's wrapper from an earlier release up to date. True if it changed."""
        receipt=read_json(self.receipt);text=self.wrapper_text()
        if not self.owned(self.wrapper,receipt):return False
        try:old=self.wrapper.read_bytes()
        except OSError:return False
        if old==text.encode():return False
        files=dict(receipt.get('files') or {});previous=dict(receipt.get('previous') or {})
        # The old wrapper stays recognised as ours if the write below does not happen.
        previous[str(self.wrapper)]=digest(old);files[str(self.wrapper)]=digest(text.encode())
        atomic(self.receipt,json.dumps(dict(receipt,files=files,previous=previous)))
        atomic(self.wrapper,text,0o700)
        return True
    def enable(self,connector='auto'):
        available,reason=self.capability()
        if not available:raise ValueError(reason)
        if self.conflict():raise ValueError(self.conflict())
        rows=connectors(self.sysfs,self.kernel(fresh=True))
        if connector!='auto' and not any(r['id']==connector for r in rows):raise ValueError('Choose a connected supported headset.')
        if connector=='auto' and len(rows)>1:raise ValueError('Choose which headset to reserve.')
        old=read_json(self.receipt)
        for p in (self.config,self.wrapper,self.options):
            if (p.exists() or p.is_symlink()) and not self.owned(p,old):raise ValueError('Modified setup file preserved: '+str(p))
        contents={self.wrapper:self.wrapper_text(),self.config:MARKER+'\nGAMESCOPE_BIN='+shlex.quote(str(self.wrapper))+'\n',self.options:json.dumps(dict(version=1,connector=connector))+'\n'}
        previous={p:p.read_text() if p.exists() else None for p in contents}
        receipt=dict(version=1,files={str(p):digest(x.encode()) for p,x in contents.items()},previous={str(p):digest(x.encode()) for p,x in previous.items() if x is not None})
        atomic(self.receipt,json.dumps(receipt))
        try:
            # Activate the environment override only after wrapper/options exist.
            for p in (self.wrapper,self.options,self.config):atomic(p,contents[p],0o700 if p==self.wrapper else 0o600)
        except BaseException:
            for p,x in previous.items():
                if x is None:p.unlink(missing_ok=True)
                else:atomic(p,x,0o700 if p==self.wrapper else 0o600)
            atomic(self.receipt,json.dumps(old));raise
        self.log('Game Mode integration enabled. Connect the headset before entering Game Mode.')
    def disable(self):
        receipt=read_json(self.receipt);conflicts=[]
        for p in (self.config,self.wrapper,self.options):
            if self.owned(p,receipt):p.unlink()
            elif p.exists() or p.is_symlink():conflicts.append(str(p))
        if conflicts:raise ValueError('Modified files preserved: '+', '.join(conflicts))
        self.receipt.unlink(missing_ok=True)

WRAPPER_RECORD='gamescope-wrapper.json'
GAMESCOPE='/usr/bin/gamescope'
def gamescope_alive(record,proc=Path('/proc'),readlink=os.readlink):
    """Whether the gamescope the wrapper started for this Game Mode session still runs (record: gamescope-lease.json).

    The wrapper records its own process and start time, then becomes gamescope (exec keeps both). Bazzite's gamescope
    has a file capability (cap_sys_nice, for real-time scheduling). Starting a program with one makes the process
    non-dumpable, and Linux then refuses even its own user the link to its program (/proc/PID/exe). Up to 1.0.0.2 this
    check needed that link, so every Game Mode session's display reservation looked stale (1.0.0.3). What any user
    can read identifies it instead: the same process and start time, this user's (real and effective), running
    gamescope with the headset's --lease-connector. Where the link can be read, it must lead to gamescope too."""
    try:
        pid=int(record.get('pid') or 0);connector=record.get('connector') or {}
        if pid<=0 or not isinstance(connector,dict) or not connector.get('name'):return False
        directory=proc/str(pid)
        fields=(directory/'stat').read_text().rsplit(')',1)[1].split()
        if fields[0] in ('Z','X','x') or int(fields[19])!=record.get('started'):return False
        uids=next(line.split()[1:3] for line in (directory/'status').read_text().splitlines() if line.startswith('Uid:'))
        if [int(u) for u in uids]!=[os.getuid()]*2:return False
        argv=(directory/'cmdline').read_bytes().split(b'\0')
        if os.path.basename(argv[0])!=b'gamescope':return False
        if not any(a==b'--lease-connector' and b==os.fsencode(connector['name']) for a,b in zip(argv,argv[1:])):return False
        try:program=readlink(directory/'exe')
        except PermissionError:return True  # non-dumpable (file capability): identified by everything above
        program=str(program).removesuffix(' (deleted)')
        return str(Path(program).resolve())==str(Path(GAMESCOPE).resolve()) or Path(program).name=='gamescope'
    except (OSError,ValueError,IndexError,StopIteration,TypeError,AttributeError):
        return False
def _say(text):
    """stderr notes from the wrapper; a closed or broken stderr must never stop Game Mode from starting."""
    try:print(text,file=sys.stderr)
    except Exception:pass
def wrapper_plan(arguments,sysfs=Path('/sys/class/drm'),kernel=None):
    """(command, environment, record) for this Game Mode start; any failure leaves plain gamescope."""
    env=steam_environment();cmd=['/usr/bin/gamescope',*arguments]
    rows=[];record=dict(version=1,time=time.time(),pid=os.getpid(),leased='',reason='',code='',outputs={})
    try:
        setup=GameMode(Path(sys.executable).parent,sysfs=sysfs)
        if kernel is None:kernel=kernel_connectors(setup.lease_helper()) if setup.lease_helper().is_file() else {}
    except Exception as e:
        setup=None;kernel={};_say('VROSTBridge could not read the connector list: '+str(e))
    try:record['outputs']=output_descriptions(sysfs,kernel)
    except Exception:pass
    try:
        # Never let Game Mode use a VR headset as its display (RC4.2).
        rows=connectors(sysfs,kernel)
        known=remember_headsets(setup.directory,rows) if setup else []
        ranked=prefer_output(arguments,headset_outputs(known,connected_outputs(sysfs,kernel)))
        arguments,cmd=ranked,['/usr/bin/gamescope',*ranked]
    except Exception as e:_say('VROSTBridge could not rank headset outputs last: '+str(e))
    try:
        opt=read_json(setup.options) if setup else {};selected=opt.get('connector','auto')
        row=rows[0] if selected=='auto' and len(rows)==1 else next((x for x in rows if x['id']==selected),None)
        others=sorted(name+': '+text for name,text in record['outputs'].items() if not name.startswith(('eDP','LVDS','DSI')))
        if not rows and others:
            record['code']='unrecognised'
            record['reason']='no connected display is marked as a headset ('+'; '.join(others)[:160]+')'
        elif not rows:record['code']='none';record['reason']='no supported headset was plugged in and powered'
        elif row is None and selected=='auto':record['code']='several';record['reason']='more than one headset was connected'
        elif row is None:record['code']='chosen-missing';record['reason']='the headset chosen on the VROSTBridge page ('+str(selected)+') was not connected'
        elif not unique(row,sysfs):record['code']='duplicate';record['reason']='two graphics cards report an output named '+row['name']
        elif any(x.startswith('--lease-connector') for x in arguments):record['code']='already';record['reason']='the Game Mode session already reserves a display'
        else:
            d=runtime_dir();sock=d/'gamescope-lease.sock'
            if len(os.fsencode(sock))>=104:raise ValueError('Display socket path is too long.')
            proc=process_identity(os.getpid());atomic(d/'gamescope-lease.json',json.dumps(dict(pid=os.getpid(),started=proc['started'],connector=row)))
            leased=['/usr/bin/gamescope','--lease-connector',row['name'],*arguments]
            env['GAMESCOPE_LEASE_SOCK']=str(sock);cmd=leased;record['leased']=row['name']
    except Exception as e:
        env.pop('GAMESCOPE_LEASE_SOCK',None);cmd=['/usr/bin/gamescope',*arguments];record['leased']=''
        record['code']='error';record['reason']=str(e)[:200] or type(e).__name__
        _say('VROSTBridge display reservation unavailable: '+str(e))
    return cmd,env,record
def wrapper(arguments,sysfs=Path('/sys/class/drm'),execve=None,kernel=None):
    """gamescope with VROSTBridge's additions; whatever happens here, gamescope is started.

    RC4.3: what it did is recorded in the runtime directory (gamescope-wrapper.json), so the
    pages can say why Game Mode has no headset display instead of only greying out Start.
    """
    execve=execve or os.execve
    plain=['/usr/bin/gamescope',*arguments]
    cmd,env=plain,None
    try:
        if any(a in ('--help','--version','-h') for a in arguments):env=steam_environment()
        else:
            cmd,env,record=wrapper_plan(arguments,sysfs,kernel)
            try:atomic(runtime_dir()/WRAPPER_RECORD,json.dumps(record))
            except Exception as e:_say('VROSTBridge could not record the Game Mode start: '+str(e))
    except BaseException as e:  # never keep Game Mode from starting
        cmd,env=plain,None
        _say('VROSTBridge Game Mode additions skipped: '+str(e))
    if env is None:
        env=plain_environment()
    execve(cmd[0],cmd,env)

def plain_environment():
    """gamescope's own environment: without the lease socket and without PyInstaller's library path."""
    env=dict(os.environ);env.pop('GAMESCOPE_LEASE_SOCK',None)
    if 'LD_LIBRARY_PATH_ORIG' in env:env['LD_LIBRARY_PATH']=env['LD_LIBRARY_PATH_ORIG']
    else:env.pop('LD_LIBRARY_PATH',None)
    return env

class ProcessStarter:
    """Starts processes from one thread that lasts as long as the companion (RC4.4).

    Linux delivers a child's PR_SET_PDEATHSIG when the *thread* that started it exits, not the
    process. VROSTBridgeLease sets it (so it never outlives the companion), and it used to be
    started from the Start request's own thread: when that request finished, the headset display
    adapter was told to terminate, ending the Game Mode session."""
    Thread=threading.Thread  # bound here: tests that patch threading.Thread must not replace this one
    REPLY_TIMEOUT_S=30
    def __init__(self):
        self.lock=threading.Lock();self.requests=queue.Queue();self.thread=None
    def popen(self,args,**options):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread=self.Thread(target=self.serve,daemon=True,name='VROSTBridge-process-starter');self.thread.start()
        reply=queue.Queue(maxsize=1);abandoned=threading.Event()
        self.requests.put((args,options,reply,abandoned))
        try:started,value=reply.get(timeout=self.REPLY_TIMEOUT_S)
        except queue.Empty:
            abandoned.set()  # nobody will own a process started for this request now: end it
            try:started,value=reply.get_nowait()
            except queue.Empty:started=False
            if started:self.end(value)
            raise OSError('The process starter did not respond.') from None
        if started:return value
        raise value
    @staticmethod
    def end(process):
        try:process.kill();process.wait(3)
        except Exception:pass
    def serve(self):
        while True:
            args,options,reply,abandoned=self.requests.get()
            if abandoned.is_set():continue
            try:process=subprocess.Popen(args,**options)
            except BaseException as error:reply.put((False,error));continue
            reply.put((True,process))
            if abandoned.is_set():self.end(process)  # its caller gave up while it started

PROCESS_STARTER=ProcessStarter()

def display_name(pid):
    """The Wayland display the headset display adapter serves (lease.cpp), in the runtime directory."""
    return 'vrostbridge-headset-'+str(int(pid))

class GameModeStartLog:
    """1.0.0.10: what the last Start in Game Mode logged, kept on disk for the reports.

    In the 1.0.0.9 test Game Mode did not start a session, and the report saved afterwards in
    Desktop Mode had lost those lines under the Desktop session's. The lines from a Start in Game
    Mode to its outcome, and the headset display adapter's later lines while that session runs,
    are kept in game-mode-last-start.json, replaced by the next Start in Game Mode; every client
    report carries them."""
    LIMIT=400
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.record=None;self.lock=threading.Lock()
    def begin(self,version):
        with self.lock:
            self.record={'version':1,'app_version':str(version),'started_utc':self.clock(),'ended_utc':None,
                         'outcome':'starting','lines':[]}
            self._write()
    def line(self,text):
        with self.lock:
            record=self.record
            if record is None or len(record['lines'])>=self.LIMIT:return
            starting=record['outcome']=='starting'
            if not starting and not text.startswith('Game Mode: '):return
            record['lines'].append([round(self.clock()-record['started_utc'],2),str(text)[:600]])
            if not starting or len(record['lines'])%20==0:self._write()
    def end(self,outcome):
        with self.lock:
            if self.record is None or self.record['outcome']!='starting':return
            self.record['outcome']=str(outcome)[:400];self.record['ended_utc']=self.clock();self._write()
    def close(self):
        """The session is over: later lines belong to no Game Mode start."""
        with self.lock:
            if self.record is not None and self.record['outcome']=='starting':
                self.record['outcome']='stopped while starting';self.record['ended_utc']=self.clock();self._write()
            self.record=None
    def load(self):
        try:value=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return None
        return value if isinstance(value,dict) and value.get('version')==1 else None
    def _write(self):
        try:atomic(self.path,json.dumps(self.record))
        except OSError:pass

class LeaseRuntime:
    def __init__(self,root,setup,log,on_failure):
        self.root=Path(root);self.setup=setup;self.log=log;self.on_failure=on_failure
        self.process=None;self.lock=threading.RLock();self.closed=threading.Event();self.display='';self.message='';self.reader=None
    def executable(self):
        return next((p for p in (self.root/'VROSTBridgeLease',self.root/'dist/client/VROSTBridgeLease') if p.is_file()),self.root/'VROSTBridgeLease')
    def status(self):
        game=in_game_mode();s=self.setup.status() if game else {}
        return dict(in_game_mode=game,supported=not game or bool(s.get('enabled') and s.get('lease_active') and self.executable().is_file()),backend='gamescope-lease' if game else 'desktop',reason=self.message or s.get('message','Desktop SteamVR runtime.'),physical_validation='pending',wayland_display=self.display)
    def prepare(self,cancel):
        if not in_game_mode():return {}
        if cancel.is_set():raise ValueError('Startup cancelled.')
        with self.lock:
            if self.process is not None:raise ValueError('Previous display adapter is still active.')
            try:details=self.setup.details()
            except (OSError,ValueError) as error:
                # Start explains why, in the same words as the session card (RC4.2 showed a raw OS error).
                raise ValueError(self.setup.lease_problem(error,connectors(self.setup.sysfs,self.setup.kernel()),True)) from error
            executable=self.executable()
            if not os.environ.get('DISPLAY'):raise ValueError('Game Mode Xwayland display is missing.')
            args=[str(executable),'--gamescope-socket',details['lease_socket'],'--connector',details['name'],'--card',details['card'],'--connector-id',str(details['connector_id']),'--edid-hex',details['edid_hex']]
            env=os.environ.copy();env['LD_LIBRARY_PATH']=str(executable.parent/'lib')
            # From the long-lived starter thread: the helper's parent-death signal follows that thread.
            self.closed.clear();p=PROCESS_STARTER.popen(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace');self.process=p
        events=queue.Queue();ready=threading.Event()
        def read():
            try:
                for line in p.stdout:
                    if len(line)>8192:raise ValueError('Oversized display response.')
                    if line.startswith('LEASE_READY '):
                        try:
                            # 1.0.0.10: a plain display name in the runtime directory (lease.cpp), the way Wayland
                            # displays are normally named, instead of a path to a socket in a folder of its own.
                            data=json.loads(line[12:]);name=data['wayland_display'];path=Path(data['path']);m=path.lstat()
                            expected=Path(os.environ['XDG_RUNTIME_DIR'])/display_name(p.pid)
                            if ready.is_set() or name!=display_name(p.pid) or path!=expected or not stat.S_ISSOCK(m.st_mode) or m.st_uid!=os.getuid() or m.st_mode&0o077 or data['connector_id']!=details['connector_id']:raise ValueError('Display adapter identity mismatch.')
                            ready.set();events.put(('ready',name))
                        except (OSError,ValueError,KeyError,TypeError) as e:raise ValueError(str(e)) from e
                    else:self.log('Game Mode: '+line.rstrip())
                code=p.wait();message='Headset display adapter exited ('+str(code)+').'
                events.put(('error',message))
                if ready.is_set() and not self.closed.is_set():self.on_failure(p,message)
            except (OSError,ValueError) as e:
                events.put(('error',str(e)))
                if ready.is_set() and not self.closed.is_set():self.on_failure(p,str(e))
            finally:p.stdout.close()
        self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
        try:
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                if cancel.is_set():raise ValueError('Startup cancelled.')
                try:kind,value=events.get(timeout=.1)
                except queue.Empty:continue
                if kind=='error' or p.poll() is not None:raise ValueError(value)
                self.display=value;self.message='Headset display reserved for SteamVR.'
                return dict(WAYLAND_DISPLAY=value,DISPLAY=os.environ['DISPLAY'],SDL_VIDEODRIVER='x11',QT_QPA_PLATFORM='xcb')
            raise ValueError('Timed out acquiring the Game Mode display.')
        except BaseException:self.close();raise
    def close(self):
        # SteamVR MUST be stopped first; closing a duplicate does not revoke its FD.
        with self.lock:
            self.closed.set();p=self.process
            if p is not None and p.poll() is None:
                p.terminate()
                try:p.wait(3)
                except subprocess.TimeoutExpired:p.kill();p.wait(3)
            self.process=None;self.display='';self.message=''
        if self.reader and self.reader is not threading.current_thread():self.reader.join(1)
