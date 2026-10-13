"""Preflight optional microphone routing against the pinned ALVR device rules.

The device list comes from the small Core Audio helper (VROSTBridgeAudioProbe.exe).
Security software can stop it from starting ("Access is denied"); the same list is
then read from Windows' device manager through PowerShell (RC4.1), and the message
says which step failed instead of blaming a missing virtual cable.
"""
import json,os,subprocess
from pathlib import Path

PAIRS={'VAC':('Line 1','Line 1'),'VBCable':('CABLE Input','CABLE Output'),
       'VoiceMeeter':('VoiceMeeter Input','VoiceMeeter Output'),
       'VoiceMeeterAux':('VoiceMeeter Aux Input','VoiceMeeter Aux Output'),
       'VoiceMeeterVaio3':('VoiceMeeter VAIO3 Input','VoiceMeeter VAIO3 Output')}

def endpoints(helper):
    result=subprocess.run([str(helper)],capture_output=True,encoding='utf-8',timeout=8,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or len(result.stdout)>65536:raise ValueError('Windows audio device check failed.')
    value=json.loads(result.stdout)
    if not isinstance(value,dict):raise ValueError('Invalid Windows audio device list.')
    for key in ('render','capture'):
        rows=value.get(key)
        if not isinstance(rows,list) or len(rows)>256 or any(not isinstance(x,str) or len(x)>4096 for x in rows):
            raise ValueError('Invalid Windows audio device list.')
    return value

# Audio endpoint instance IDs: SWD\\MMDEVAPI\\{0.0.0.00000000}.{guid} (playback) and {0.0.1.00000000}.{guid} (recording).
RENDER_ID,CAPTURE_ID='{0.0.0.00000000}','{0.0.1.00000000}'
PNP_SCRIPT=("$ErrorActionPreference='Stop';"
            "@(Get-PnpDevice -Class AudioEndpoint -PresentOnly | Where-Object {$_.Status -eq 'OK'} | "
            "ForEach-Object {[pscustomobject]@{id=[string]$_.InstanceId;name=[string]$_.FriendlyName}}) | ConvertTo-Json -Compress")

def endpoints_from_pnp(rows):
    if isinstance(rows,dict):rows=[rows]
    if not isinstance(rows,list) or len(rows)>512:raise ValueError('Invalid Windows audio device list.')
    result={'render':[],'capture':[]}
    for row in rows:
        if not isinstance(row,dict):continue
        identity,name=str(row.get('id') or '').upper(),row.get('name')
        if not isinstance(name,str) or not name or len(name)>4096:continue
        if RENDER_ID.upper() in identity:result['render'].append(name)
        elif CAPTURE_ID.upper() in identity:result['capture'].append(name)
    return result

def pnp_endpoints(runner=subprocess.run):
    """Active playback and recording endpoints from Windows' device manager (no helper executable)."""
    if os.name!='nt':raise OSError('the PowerShell device list is only available on Windows')
    result=runner(['powershell','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',PNP_SCRIPT],
                  capture_output=True,encoding='utf-8',errors='replace',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:
        detail=((result.stderr or result.stdout or '').strip().splitlines() or ['exit code '+str(result.returncode)])[-1]
        raise ValueError('PowerShell could not list audio devices ('+detail[:160]+')')
    if len(result.stdout)>262144:raise ValueError('Windows audio device list is too large.')
    return endpoints_from_pnp(json.loads(result.stdout.strip() or '[]'))

def describe_failure(error):
    """A short, accurate reason the helper could not list devices."""
    if isinstance(error,PermissionError) or getattr(error,'winerror',None)==5:
        return 'Windows blocked the audio check helper (Access is denied); security software may be blocking VROSTBridgeAudioProbe.exe'
    if isinstance(error,FileNotFoundError):return 'the audio check helper is missing; repair the VROSTBridge Host installation'
    if isinstance(error,subprocess.TimeoutExpired):return 'the audio check helper did not answer within 8 seconds'
    return 'the audio check helper failed ('+str(error)[:160]+')'

def system_endpoints(helper,probe=endpoints,fallback=pnp_endpoints):
    """(device lists, note) from the helper, or from PowerShell when the helper cannot run."""
    try:return probe(helper),''
    except (OSError,ValueError,subprocess.SubprocessError) as error:first=error
    try:return fallback(),describe_failure(first)+'; the device list was read through PowerShell instead.'
    except (OSError,ValueError,subprocess.SubprocessError) as second:
        raise ValueError('The host could not list its audio devices: '+describe_failure(first)+', and '+str(second)[:200]+'.') from second

def select_pair(config,available):
    mode=config.get('variant')
    modes=PAIRS if mode=='Automatic' else [mode]
    if mode=='Custom':
        custom=config.get('Custom',{})
        # Index ordering is not guaranteed across Core Audio and CPAL. Do not
        # turn on an unverified route or substitute an unrelated physical mic.
        def resolve(name,rows):
            item=custom.get(name,{})
            term=item.get('NameSubstring')
            if item.get('variant')!='NameSubstring' or not isinstance(term,str) or not term:return None
            return next((x for x in rows if term.lower() in x.lower()),None)
        sink=resolve('sink',available['render']);source=resolve('source',available['capture'])
        return (sink,source) if sink and source else None
    for candidate in modes:
        if candidate not in PAIRS:continue
        a,b=PAIRS[candidate]
        sink=next((x for x in available['render'] if a in x),None)
        source=next((x for x in available['capture'] if b in x),None)
        if sink and source:return sink,source
    return None

def microphone_status(requested,document,helper,probe=endpoints,fallback=pnp_endpoints,setup_state=None):
    """Whether the headset microphone can be forwarded this session; setup_state is the host
    page's microphone setup state (host/microphone_setup.py), for a precise warning."""
    result={'microphone_requested':requested,'microphone_enabled':False,'warning':'','message':'Headset microphone is off; game audio stays enabled.'}
    if not requested:return result
    try:
        available,note=system_endpoints(helper,probe,fallback)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        result['warning']=('Headset microphone unavailable for this session: the host could not list its audio devices; '
                           'continuing with video, tracking and game audio. '+str(error)[:300])
        result['message']=result['warning'];result['detail']=str(error)[:400]
        return result
    if note:result['detail']=note[:400]
    try:
        pair=select_pair(document['session_settings']['audio']['microphone']['content']['devices'],available)
    except (KeyError,TypeError,AttributeError) as error:
        pair=None;result['detail']='Invalid microphone settings: '+str(error)[:200]
    if not pair:
        # RC4.3: the host page sets one up (host/microphone_setup.py); say which step is missing.
        half=([x for x in available['render'] if 'CABLE Input' in x][:1]+[x for x in available['capture'] if 'CABLE Output' in x][:1])
        if len(half)==1:
            result['warning']=('Headset microphone unavailable: Windows lists only “'+half[0][:80]+'”. Enable both CABLE Input '
                               '(Playback) and CABLE Output (Recording) in Windows Sound settings; continuing with video, '
                               'tracking and game audio.')
        elif select_pair({'variant':'Automatic'},available):
            result['warning']=('Headset microphone unavailable: the host\'s microphone setting selects a device that is not '
                               'connected, while VB-CABLE is ready. Set the host\'s headset microphone device to Automatic; '
                               'continuing with video, tracking and game audio.')
        elif setup_state=='restart':
            result['warning']=('Headset microphone unavailable: VB-CABLE is installed, but Windows needs a restart to finish '
                               'adding it. Restart the host PC; continuing with video, tracking and game audio.')
        else:
            result['warning']=('Headset microphone unavailable: the host PC has no virtual microphone yet. On the host PC\'s '
                               'VROSTBridge page, select Set up headset microphone (once); continuing with video, tracking and '
                               'game audio.')
        result['message']=result['warning']
        return result
    result.update(microphone_enabled=True,message='Headset microphone forwarding enabled.'+(' ('+note+')' if note else ''),sink=pair[0],source=pair[1])
    return result
