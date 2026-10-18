// Focused DOM smoke checks for the page-only host/client connection workflow.
// Run with: node tests/test_desktop_ui.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../launcher/desktop.html'), 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace('update();setInterval(update,1000);', '');

class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toUpperCase();
    this.children = []; this.style = {}; this.dataset = {};
    this.disabled = false; this.checked = false; this.value = '';
    this.textContent = ''; this.events = {};
    const classes=new Set();
    this.classList={toggle(name,force){const value=force===undefined?!classes.has(name):force;if(value)classes.add(name);else classes.delete(name);return value;},remove(name){classes.delete(name);},contains(name){return classes.has(name);}};
  }
  set innerHTML(_) { throw new Error('Unsafe HTML insertion'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; }
  addEventListener(name, action) { this.events[name] = action; }
  click() { return this.events.click?.(); }
}

function setup(initial, extra = {}) {
  const elements = new Map(), all = [];
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    assert(!elements.has(match[2]), 'Duplicate HTML ID: ' + match[2]);
    const element = new Element(match[1]);
    element.id = match[2]; element.value = match[0].match(/\bvalue="([^"]*)"/)?.[1] || '';
    elements.set(element.id, element); all.push(element);
  }
  const document = {
    body: new Element('body'),
    getElementById: id => { assert(elements.has(id), 'Missing element ' + id); return elements.get(id); },
    createElement: tag => { const element = new Element(tag); all.push(element); return element; },
    querySelectorAll: selector => selector === 'button' ? all.filter(el => el.tagName === 'BUTTON') :
      ['microphone', 'gaze', 'software', 'codec', 'bitrate', 'resolution', 'configure', 'auto_connect', 'host_ip','network-interface'].map(id => elements.get(id)),
  };
  const calls = [], state = {value: initial, fail: false, copied: ''};
  const context = vm.createContext({document,
    location: {hash: '#test-token', pathname: '/'}, history: {replaceState() {}},
    sessionStorage: {getItem() {}, setItem() {}},
    navigator: {clipboard: {writeText: async text => {state.copied = text;}}},
    fetch: async (url, options) => {
      if (url === '/status') {
        if (state.fail) throw new Error('offline');
        return {ok: true, json: async () => structuredClone(state.value)};
      }
      calls.push({url, ...options, body: JSON.parse(options.body)});
      if(JSON.parse(options.body).action==='start'&&state.holdStart)await state.holdStart;
      return {ok: true, json: async () => ({})};
    },
    setInterval() {}, setTimeout() {}, requestAnimationFrame() { return 1; }, cancelAnimationFrame() {},
    performance, URL, Blob, ...extra,
  });
  vm.runInContext(source, context);
  return {elements, state, calls, document, run: code => vm.runInContext(code, context)};
}

const base = {
  role: 'host', packaged: true, available: true, running: true, busy: false,
  state: 'host-pairing', settings: {codec: 'H264', bitrate: 40, auto_connect: false}, metrics: {}, logs: [],
  gaze_status: {state: 'off'}, host_control: {available: true, runtime_state: 'ready',
    clients: [{hostname: '1234.client.local.', display_name: '<img src=x onerror=alert(1)>',
      current_ip: '192.168.1.20', trusted: false, connection_state: 'Disconnected'}]},
};

(async () => {
  const host = setup(structuredClone(base));
  await host.run('update()');
  // The line under the name says what VROSTBridge stands for, the same on both PCs; the badge names the role.
  const tagline = html.match(/<small class="tagline" id="tagline">([\s\S]*?)<\/small>/)[1].replace(/<[^>]+>/g, '');
  assert.equal(tagline, 'VR Rendering OutSide The Box \u2013 Bridge');
  assert.equal(host.elements.get('tagline').textContent, '', 'The script leaves the tagline as written');
  assert.equal(host.elements.get('role').textContent, 'Rendering host');
  assert.equal(host.elements.get('status').textContent, 'Client approval needed');
  assert.equal(host.elements.get('codec').disabled, true, 'Stream settings lock while running');
  assert.equal(host.elements.get('connect-manual').disabled, false, 'Live manual pairing remains usable');
  const row = host.elements.get('host-clients').children[0];
  assert.equal(row.children[0].children[0].textContent, '<img src=x onerror=alert(1)>');
  const allow = row.children.at(-1).children[0];
  assert.equal(allow.disabled, false, 'Approval remains usable while host runs');
  assert.equal(allow.textContent, 'Allow & connect');
  await allow.click();
  assert.deepEqual(host.calls.at(-1).body, {action: 'trust-client', hostname: '1234.client.local.'});
  assert.equal(host.calls.at(-1).headers['X-VROSTBridge-Token'], 'test-token');
  host.elements.get('client_hostname').value = ' 1234.client.local. ';
  host.elements.get('client_ip').value = ' 192.168.1.20 ';
  host.run('manualConnect()');
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(host.calls.at(-1).body,
    {action: 'add-client', client_hostname: '1234.client.local.', client_ip: '192.168.1.20'});

  host.state.value.state = 'host-ready';
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent, 'Waiting for headset', 'A running process is not Streaming');
  host.state.value.host_control.clients[0].trusted = true;
  host.state.value.host_control.clients[0].connection_state = 'Streaming';
  host.state.value.state = 'host-connected';
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent, 'Headset connected');
  host.state.value.remote_diagnostics={summary:{metrics:{video_phase:'waiting_config',video_silence_ms:12000,decoded:0,frame_age_ms:0}}};
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent,'Video not arriving');
  assert.equal(host.elements.get('age').textContent,'—','No frames is not zero latency');
  host.state.value.remote_diagnostics=undefined;
  const revoke = host.elements.get('host-clients').children[0].children.at(-1).children[0];
  assert.equal(revoke.textContent, 'Remove access');
  await revoke.click();
  assert.deepEqual(host.calls.at(-1).body, {action: 'forget-client', hostname: '1234.client.local.'});

  host.state.value.host_control.available = false;
  host.state.value.host_control.runtime_state = 'starting';
  host.state.value.state = 'host-starting';
  await host.run('update()');
  assert.equal(host.elements.get('connect-manual').disabled, true);
  assert.equal(host.elements.get('status').textContent, 'Starting your host');
  host.state.value.host_control.available = true;
  host.state.value.host_control.runtime_state = 'ready';
  await host.run('update()');
  host.state.fail = true;
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent, 'Companion unavailable');
  assert.equal(host.elements.get('report').disabled, true);
  host.state.fail = false;
  await host.run('update()');
  assert.equal(host.elements.get('report').disabled, false, 'Report action recovers after connection outage');
  assert.equal(host.elements.get('host-clients').children[0].children.at(-1).children[0].disabled, false,
    'Client actions recover even when the device list is unchanged');

  host.state.value.settings.auto_connect = true;
  host.state.value.host_control.auto_connect = true;
  host.state.value.state = 'host-pairing';
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent, 'Connecting automatically');
  assert.equal(host.elements.get('auto_connect').checked, true);
  host.state.value.settings.auto_connect = false;
  host.state.value.host_control.auto_connect = false;
  await host.run('update()');
  assert.equal(host.elements.get('auto_connect').checked, false, 'Server-side removal updates automatic connection setting');
  host.state.value.running = false;
  host.state.value.state = 'stopped';
  await host.run('update()');
  host.elements.get('auto_connect').checked = true;
  host.elements.get('auto_connect').events.change();
  await host.run('update()');
  assert.equal(host.elements.get('auto_connect').checked, true, 'Polling preserves an unsaved preference edit');
  await host.run("act('configure')");
  assert.equal(host.calls.at(-1).body.auto_connect, true);

  const client = setup({...structuredClone(base), role: 'client', state: 'connecting', host_control: undefined,
    client_identity: {hostname: '', addresses: [], message: 'Starting the headset client.'}});
  await client.run('update()');
  assert.equal(client.elements.get('client-identity').textContent, 'Available after the client starts');
  assert.equal(client.elements.get('copy-identity').disabled, true, 'No invented hostname before first native launch');
  assert.equal(client.elements.get('tagline').textContent, '', 'The script leaves the tagline as written');
  assert.equal(client.elements.get('role').textContent, 'Headset client');
  client.state.value.client_identity = {hostname: '5678.client.local.',
    addresses: [{address: '192.168.1.21', interface: 'wlan0'}], message: 'Waiting for host approval.'};
  await client.run('update()');
  assert.equal(client.elements.get('hint').textContent, 'Waiting for host approval.');
  client.state.value.metrics={video_phase:'waiting_video',video_silence_ms:1000,decoded:0,frame_age_ms:0};
  await client.run('update()');
  assert.equal(client.elements.get('status').textContent,'Starting video');
  assert.equal(client.elements.get('age').textContent,'—');
  client.state.value.metrics={};
  assert.equal(client.elements.get('client-identity').textContent, '5678.client.local.');
  await client.run('copyIdentity()');
  assert.equal(client.state.copied, 'Client ID: 5678.client.local.\nLAN addresses: 192.168.1.21');
  client.state.value.client_network = {state: 'needs-permission', can_allow: true, message: 'Home-network access needs permission.',
    targets: [{interface: 'wlan0', subnet: '192.168.1.0/24', zone: 'public'}]};
  await client.run('update()');
  assert.equal(client.elements.get('allow-client-network').disabled, false, 'Firewall permission is available while client runs');
  assert.equal(client.elements.get('client-network-state').textContent, 'Home-network access needs permission.');
  assert.equal(client.elements.get('client-network-access').classList.contains('hidden'),false,'Required permission is visible in session card');
  assert(html.indexOf('id="client-network-access"')<html.indexOf('id="advanced"'),'Network permission is outside collapsed Advanced');
  client.state.value.busy=true;
  await client.run('update()');
  assert.equal(client.elements.get('allow-client-network').disabled,true,'Permission button disables during in-flight work');
  client.state.value.busy=false;
  await client.run('update()');
  await client.run("act('allow-client-network')");
  assert.deepEqual(client.calls.at(-1).body, {action: 'allow-client-network'});
  client.state.value.client_network.state = 'ready';
  client.state.value.client_network.can_allow = false;
  await client.run('update()');
  assert.equal(client.elements.get('allow-client-network').disabled, true);
  assert.equal(client.elements.get('client-network-access').classList.contains('hidden'),true,'Ready network does not clutter session card');
  client.state.value.client_network={state:'error',can_allow:false,message:'Network check failed.'};
  await client.run('update()');
  assert.equal(client.elements.get('client-network-access').classList.contains('hidden'),false,'Network error remains visible without an available action');
  assert.equal(client.elements.get('allow-client-network').classList.contains('hidden'),true);
  client.state.value.client_network={state:'ready',can_allow:false};
  client.state.value.state = 'streaming'; client.state.value.metrics = {decoded: 5, frame_age_ms: 10};
  await client.run('update()');
  assert.equal(client.elements.get('status').textContent, 'Streaming');
  assert.equal(client.elements.get('decoded').textContent, 5);
  await client.run("act('host-menu')");
  assert.deepEqual(client.calls.at(-1).body, {action:'host-menu'});
  client.state.value.running=false;
  client.state.value.client_runtime={settings_pending_restore:true};
  await client.run('update()');
  assert.equal(client.elements.get('stop').disabled,false,'Pending preference restoration remains actionable');
  assert.equal(client.elements.get('restore-runtime').disabled,false);
  await client.run("act('restore-client-runtime')");
  assert.deepEqual(client.calls.at(-1).body, {action:'restore-client-runtime'});
  // Standby host offers no primary Start control; the client owns the session.
  host.state.value.running=false;
  host.state.value.state='stopped';
  host.state.value.session_link={state:'waiting',message:'Waiting for a session request.'};
  await host.run('update()');
  assert.equal(host.elements.get('status').textContent,'Waiting for headset');
  assert.equal(host.elements.get('hint').textContent,'Waiting for a session request.');
  assert.equal(host.elements.get('emergency-stop').disabled,true);
  assert(html.includes('<div class="client session-actions"><button class="primary" id="start"'));
  assert(!html.includes('Local by design'));
  assert(!html.includes('hero-first'));
  assert(html.includes('href="/brand/favicon.ico"') && html.includes('href="/brand/favicon-32.png"'));
  assert(html.includes('src="/brand/vrostbridge-mark.svg"') && html.includes('/brand/vrostbridge-logo.svg'));
  assert(!/brand\/(icon|logo)\.(svg|png)/.test(html), 'only the VROSTBridge artwork');
  // Every name ending in "Bridge" on the page is VROSTBridge.
  assert(!/\b(?!vrostbridge\b)[a-z]+bridge\b/i.test(html) && !/\b[A-Z][a-z]+[ _-]Bridge\b/.test(html),
    'the page names VROSTBridge only');

  // Client sends quality and optional manual host to the orchestration layer.
  client.state.value.client_runtime={};
  client.state.value.running=false;
  client.state.value.state='stopped';
  client.state.value.session_link={state:'waiting'};
  await client.run('update()');
  client.elements.get('codec').value='Hevc';
  client.elements.get('bitrate').value='100';
  client.elements.get('resolution').value='sharp';
  client.elements.get('host_ip').value=' 192.168.1.8 ';
  await client.run("act('start')");
  assert.equal(client.calls.at(-1).body.codec,'Hevc');
  assert.equal(client.calls.at(-1).body.bitrate,100);
  assert.equal(client.calls.at(-1).body.resolution,'sharp');
  assert.equal(client.calls.at(-1).body.host_ip,'192.168.1.8');
  assert.equal(client.calls.at(-1).body.late_frame_catchup,true);
  client.elements.get('late_frame_catchup').checked=false;
  await client.run("act('start')");
  assert.equal(client.calls.at(-1).body.late_frame_catchup,false);
  assert.equal(client.calls.at(-1).body.motion_prediction,true,'Motion prediction defaults on');
  client.elements.get('motion_prediction').checked=false;
  await client.run("act('start')");
  assert.equal(client.calls.at(-1).body.motion_prediction,false);
  assert.equal(client.calls.at(-1).body.encoder,'speed','Encoder priority defaults to lowest latency');
  assert.equal(client.calls.at(-1).body.timing_lock,true,'Frame timing lock defaults on');
  client.elements.get('encoder').value='balanced';client.elements.get('timing_lock').checked=false;
  await client.run("act('start')");
  assert.equal(client.calls.at(-1).body.encoder,'balanced');
  assert.equal(client.calls.at(-1).body.timing_lock,false);
  assert(!('auto_connect' in client.calls.at(-1).body));
  client.state.value.session_link={state:'starting',lease:{active:true}};
  await client.run('update()');
  assert.equal(client.elements.get('stop').disabled,false,'A host reservation can be stopped before local video starts');
  assert.equal(client.elements.get('codec').disabled,true,'Quality locks during session startup');
  client.state.value.running=true;
  client.state.value.video={width:2016,height:2208,codec:'H264'};
  client.state.value.client_runtime={headset:{model:'Index',refresh_hz:90}};
  await client.run('update()');
  assert(client.elements.get('video-details').textContent.includes('2016 × 2208 per eye'));
  assert(client.elements.get('video-details').textContent.includes('Index · 90 Hz'));
  host.state.value.remote_diagnostics={summary:{video:{width:2016,height:2208,codec:'H264'},client_runtime:{headset:{model:'Index',refresh_hz:90}}}};
  await host.run('update()');
  assert(host.elements.get('video-details').textContent.includes('2016 × 2208 per eye'));
  client.state.value.running=false;


  // A long startup request must not trap the user: Stop is sent concurrently.
  client.state.value.session_link={state:'waiting',active:false};
  await client.run('update()');
  let finishStart;
  client.state.holdStart=new Promise(resolve=>{finishStart=resolve;});
  const inFlightStart=client.run("act('start')");
  assert.equal(client.elements.get('stop').disabled,false,'Stop is available during a pending startup');
  assert.equal(client.elements.get('start').disabled,true);
  await client.run("act('stop')");
  assert.equal(client.calls.at(-1).body.action,'stop','Stop reaches the backend before startup completes');
  assert.equal(client.run('pending'),true,'Start still pending after Stop response');
  finishStart(); await inFlightStart; client.state.holdStart=null;
  assert.equal(client.run('pending'),false);
  client.state.value.session_link={state:'discovering',active:true};
  await client.run('update()');
  assert.equal(client.elements.get('stop').disabled,false,'Discovery can be cancelled');
  assert.equal(client.elements.get('status').textContent,'Finding your host');

  host.state.value.running=true;
  host.state.value.remote_diagnostics={summary:{metrics:{frame_age_ms:19.3,decoded:72,dropped:2}}};
  await host.run('update()');
  assert.equal(host.elements.get('age').textContent,19.3,'Host shows real remote metrics');
  assert.equal(host.elements.get('decoded').textContent,72);
  assert.equal(host.elements.get('emergency-stop').disabled,false);
  assert(!html.includes('trust your client in ALVR'));
  assert(!html.includes('ALVR device list'));

  host.state.value.audio={microphone_requested:true,microphone_enabled:false,message:'Microphone unavailable; stream continues.'};
  await host.run('update()');
  assert.equal(host.elements.get('audio-status').textContent,'Microphone unavailable; stream continues.');
  client.state.value.session_link={host:{runtime:{audio:{message:'Headset microphone is off.'}}}};
  await client.run('update()');
  assert.equal(client.elements.get('audio-status').textContent,'Headset microphone is off.');
  // 1.0.0.13: a Beyond offers no choice: its one entry is the mode it is in, and the note says how that is switched.
  const beyondOnly=[{id:'automatic',label:'Set by the headset · 75 Hz - 2560 x 2560 per eye',available:true,width:2560,height:2560,encoded_width:5120,encoded_height:2560,codec:'Hevc'}];
  client.state.value.display={detected:true,model:'Bigscreen Beyond 2e',family:'beyond',refresh_hz:75,recommended_width:3560,recommended_height:3560,
    selected:'automatic',resolution_fixed:true,resolution_options:{Auto:beyondOnly,H264:beyondOnly,Hevc:beyondOnly,AV1:beyondOnly},
    refresh_text:"The stream follows the Beyond's display mode: 2560 x 2560 per eye at 75 Hz, or 1920 x 1920 at 90 Hz, which the headset upscales. VROSTBridge does not switch the mode: set it with Bigscreen's Beyond Utility on a Windows PC."};
  client.state.value.running=false;
  await client.run('update()');
  assert(client.elements.get('display-identity').textContent.includes('75 Hz'));
  assert(client.elements.get('display-identity').textContent.includes('checked again on Start'));
  assert(client.elements.get('display-render').textContent.includes('3560 × 3560'));
  assert(client.elements.get('display-mode-note').textContent.includes('2560 × 2560 per eye at 75 Hz'));
  assert(client.elements.get('display-mode-note').textContent.includes("set it with Bigscreen's Beyond Utility on a Windows PC"));
  assert.equal(client.elements.get('resolution').disabled,true,'Nothing to choose on a Beyond');
  assert.equal(client.elements.get('resolution').children.length,1);
  assert.equal(client.elements.get('resolution').children[0].textContent,'Set by the headset · 75 Hz · 2560 × 2560 per eye');
  // An Index rate the headset did not take is said under it (refresh_note).
  client.state.value.display={detected:true,model:'Index',family:'index',refresh_hz:120,recommended_width:2016,recommended_height:2240,resolution_fixed:false,
    refresh_text:'A rate chosen here is set in SteamVR on this PC for the session.',refresh_note:'SteamVR runs the Index at 120 Hz, not the 144 Hz chosen.'};
  await client.run('update()');
  assert(client.elements.get('display-mode-note').textContent.includes('SteamVR runs the Index at 120 Hz, not the 144 Hz chosen.'));
  assert.equal(client.elements.get('resolution').disabled,false);
  assert(html.includes('resolution_options'),'Headset-aware options come from the shared backend catalog');
  client.state.value.session_link={active:false,network:{interface:'wlan-usb',source_ip:'192.168.77.8',host_ip:'192.168.77.10',adapters:[{interface:'wlan-usb',address:'192.168.77.8'},{interface:'wlan-internal',address:'192.168.77.233'}]}};
  await client.run('update()');
  assert.equal(client.elements.get('network-interface').value,'wlan-usb');
  assert.equal(client.elements.get('network-interface').children.length,3);
  assert(client.elements.get('network-path').textContent.includes('192.168.77.8 → 192.168.77.10'));
  await client.run("act('select-client-network',{interface:'wlan-internal'})");
  assert.deepEqual(client.calls.at(-1).body,{action:'select-client-network',interface:'wlan-internal'});
  client.state.value.session_link.active=true;
  await client.run('update()');
  assert.equal(client.elements.get('network-interface').disabled,true,'Network selection is locked for an active session');
  client.state.value.session_link.active=false;
  client.state.value.session_link.network.adapters=[];
  await client.run('update()');
  assert.equal(client.elements.get('network-interface').children.at(-1).textContent,'wlan-usb · unavailable');
  assert.equal(client.elements.get('network-interface').value,'wlan-usb','Do not silently replace an unavailable chosen adapter');
  host.state.value.running=true;
  host.state.value.host_events={native_hands:{left:{source_curls_valid:true,source_curls:[0,.1,.2,.3,.4],applied_curls:[.1,.2,.3,.4],applied:true,report_age_s:.2}}};
  await host.run('update()');
  assert.equal(host.elements.get('finger-details').children[0].children[1].textContent,'0.10 / 0.20 / 0.30 / 0.40');
  assert.equal(host.elements.get('finger-details').children[0].children[2].textContent,'0.10 / 0.20 / 0.30 / 0.40');
  host.state.value.host_events.native_hands.left.report_age_s=3;
  await host.run('update()');
  assert.equal(host.elements.get('finger-details').children[0].children[2].textContent,'Waiting for current host evidence','Old successful data is not current acceptance');
  // Resolution and codec changes update display labels, keeping the user's exact ID.
  const quality=setup({...structuredClone(base),role:'client',running:false,state:'stopped',settings:{codec:'H264',resolution:'sharp',bitrate:80},display:{detected:true,model:'Index',family:'index',refresh_hz:120,recommended_width:2016,recommended_height:2240,resolution_options:{
    H264:[{id:'automatic',label:'Auto - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'H264'},{id:'sharp',label:'High - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'H264'}],
    Hevc:[{id:'automatic',label:'Auto - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'Hevc'},{id:'sharp',label:'High - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'Hevc'}]
  }}});
  await quality.run('update()');
  assert.equal(quality.elements.get('resolution').value,'sharp');
  assert.equal(quality.elements.get('resolution').children.at(-1).textContent,'High · 2016 × 2240 per eye');
  assert(quality.elements.get('display-target').textContent.includes('4032 × 2240'));
  quality.elements.get('codec').value='Hevc';quality.elements.get('codec').events.change();
  assert.equal(quality.elements.get('resolution').value,'sharp','Codec change must not silently replace saved resolution');
  assert(quality.elements.get('display-target').textContent.includes('HEVC'));
  // 1.0.0.13: no choice needs HEVC any more, so H.264 stays selectable whatever is chosen.
  quality.elements.get('codec').value='H264';quality.elements.get('codec').events.change();
  quality.elements.get('resolution').value='sharp';quality.elements.get('resolution').events.change();
  assert.equal(quality.elements.get('codec').value,'H264');
  assert.notEqual(quality.elements.get('codec-h264').disabled,true);
  assert(quality.elements.get('display-target').textContent.includes('4032 × 2240'));
  // 1.0.0.13: a saved size this headset no longer offers shows as the choice it follows (display.selected), not as a stray entry.
  const modes=setup({...structuredClone(base),role:'client',running:false,state:'stopped',settings:{codec:'Auto',resolution:'sharp',bitrate:80},display:{detected:true,model:'Index',family:'index',refresh_hz:120,recommended_width:2016,recommended_height:2240,selected:'automatic',resolution_options:{
    Auto:[{id:'automatic',label:'Auto · as SteamVR is set - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'H264'},{id:'index-144',label:'Native · 144 Hz (experimental) - 2016 x 2240 per eye',available:true,width:2016,height:2240,encoded_width:4032,encoded_height:2240,codec:'H264',refresh_hz:144,experimental:true}]}}});
  await modes.run('update()');
  assert.equal(modes.elements.get('resolution').value,'automatic');
  assert.equal(modes.elements.get('resolution').children.length,2,'No saved-choice entry for a size the headset no longer offers');
  assert.equal(modes.elements.get('resolution').children.at(-1).textContent,'Native · 144 Hz (experimental) · 2016 × 2240 per eye');
  quality.state.value.running=true;quality.state.value.inputs={left:{connected:true,tracked:false},right:{connected:false,tracked:false}};
  await quality.run('update()');
  assert.equal(quality.elements.get('finger-details').children[0].children[0].textContent,'Left · Tracking lost');
  assert.equal(quality.elements.get('finger-details').children[1].children[0].textContent,'Right · Disconnected');
  delete quality.state.value.inputs.left.tracked;
  await quality.run('update()');
  assert.equal(quality.elements.get('finger-details').children[0].children[0].textContent,'Left · Tracking','Older diagnostic reports fall back to connected');
  // RC4: start sends the new options; foveation, network test and system notes render.
  const rc4=setup({...structuredClone(base),role:'client',running:false,state:'stopped',host_control:undefined,
    settings:{codec:'Hevc',resolution:'beyond-balanced',bitrate:80,foveation:'eye-tracked',auto_bitrate:true,ten_bit:true,pacing:true,
      intra_refresh:true,headset_hud:false,keep_awake:true,headset_audio:true,wake_host:true,keep_warm_minutes:15,gaze:true},
    system:{foveation:{choice:'eye-tracked',preset:'eye-tracked',gaze_follow:true,encoded_eye:[1408,1408]},
      path_test:{message:'Network test: clean up to 600 Mbit/s on Ethernet; using 300 Mbit/s.',recommended_mbps:300},
      network_test:{state:'done'},power:{message:'This PC will stay awake during the session.'},headset_audio:{message:'Sound uses the headset.'},
      wake:{mac_known:true,message:''},async_reprojection:{state:'missing',message:'SteamVR cannot use asynchronous reprojection.'}},
    wifi:{state:'on',permanent_fix:true,message:'Wi-Fi power saving is on.'}});
  await rc4.run('update()');
  assert(rc4.elements.get('foveation-note').textContent.includes('follows your eyes'));
  assert(rc4.elements.get('foveation-note').textContent.includes('1408 × 1408'));
  assert(rc4.elements.get('network-test-result').textContent.includes('Automatic bitrate uses 300'));
  assert.equal(rc4.elements.get('bitrate').disabled,true,'Automatic bitrate replaces the manual value');
  assert.equal(rc4.elements.get('repair-async').classList.contains('hidden'),false);
  assert.equal(rc4.elements.get('wifi-fix').classList.contains('hidden'),false);
  assert.equal(rc4.elements.get('wake-now').disabled,false);
  assert.equal(rc4.elements.get('keep_warm_minutes').value,'15');
  await rc4.run("act('start')");
  const started=rc4.calls.at(-1).body;
  assert.equal(started.foveation,'eye-tracked');assert.equal(started.keep_warm_minutes,15);
  assert.equal(started.intra_refresh,true);assert.equal(started.auto_bitrate,true);assert.equal(started.headset_hud,false);
  assert.equal(started.pause_other_wifi,true,'RC4.1 option defaults on and is sent with Start');
  // RC4.1: live foveation status replaces the plan; frames whose centre moved are not called eye-following.
  const rc41=setup({...structuredClone(base),role:'client',running:true,state:'streaming',host_control:undefined,
    settings:{codec:'Hevc',bitrate:80,foveation:'balanced',pause_other_wifi:false},
    timings:{unique_fps:'74.9',reused_percent:'0.5',new_frame_interval_p95_ms:'14.2',foveated_frames:'75',gaze_centred_frames:'75'},
    system:{foveation:{choice:'balanced',preset:'balanced',gaze_follow:false,encoded_eye:[1408,1408],
      live:{active:true,mode:'centred',text:'Foveated encoding active: Balanced · the sharp region is centred on each eye\'s straight-ahead view; about 51% fewer pixels to encode and send.',short:'fov balanced centred -51% px'}},
      async_reprojection:{state:'disabled',message:'Background reprojection is turned off in SteamVR settings.'},
      other_wifi:{state:'paused',message:'Paused the other Wi-Fi adapter wlan1 on this network for the session.'}}});
  await rc41.run('update()');
  assert(rc41.elements.get('foveation-note').textContent.startsWith('Foveated encoding active: Balanced'));
  assert(!rc41.elements.get('video-pacing').textContent.includes('following your eyes'),'no gaze claim from client counters');
  assert(rc41.elements.get('video-pacing').textContent.includes('Foveated'));
  assert(!rc41.elements.get('video-pacing').textContent.includes('GPU'),'no GPU readout without a sample');
  // RC4.4: the live GPU clock, to watch a TDP change take effect.
  rc41.state.value.graphics={state:'limited',message:'',actions:[],gpu:{busy_percent:58,clock_mhz:800,max_mhz:2700,power_w:15}};
  await rc41.run('update()');
  assert(rc41.elements.get('video-pacing').textContent.includes(' · GPU 800 of 2700 MHz, 58% busy, 15 W'),rc41.elements.get('video-pacing').textContent);
  delete rc41.state.value.graphics;
  assert.equal(rc41.elements.get('repair-async').classList.contains('hidden'),false,'repair offered when the setting is off');
  assert(rc41.elements.get('other-wifi-status').textContent.includes('wlan1'));
  assert.equal(rc41.elements.get('pause_other_wifi').checked,false,'saved choice is shown');
  const hostPower=setup({...structuredClone(base),settings:{codec:'H264',bitrate:40,auto_connect:false,sleep_after_minutes:30},
    host_power:{sleep_after:{state:'armed',minutes:30,message:'The host PC will go to sleep in 30 minutes.'},wake:{message:'Wake from sleep is ready.'},
      resume:{parked:true,remaining_seconds:600},qos:{message:'Wi-Fi priority is off.'},features:['pacing','gaze-foveation']}});
  await hostPower.run('update()');
  assert.equal(hostPower.elements.get('sleep_after_minutes').value,'30');
  assert(hostPower.elements.get('resume-status').textContent.includes('10 more minutes'));
  assert(hostPower.elements.get('host-features').textContent.includes('gaze-foveation'));
  assert.equal(hostPower.elements.get('wake-readiness').textContent,'Wake from sleep is ready.');
  await hostPower.run("act('host-power',{sleep_after_minutes:60})");
  assert.deepEqual(hostPower.calls.at(-1).body,{action:'host-power',sleep_after_minutes:60});
  // RC4.3: one-step headset microphone on the host.
  hostPower.state.value.host_microphone={state:'missing',message:'Not set up: Windows has no virtual microphone for the headset yet.',busy:false};
  await hostPower.run('update()');
  assert(hostPower.elements.get('mic-status').textContent.includes('Not set up'));
  assert.equal(hostPower.elements.get('mic-setup').classList.contains('hidden'),false);
  assert.equal(hostPower.elements.get('mic-setup').disabled,false);
  await hostPower.run("act('microphone-setup')");
  assert.deepEqual(hostPower.calls.at(-1).body,{action:'microphone-setup'});
  hostPower.state.value.host_microphone={state:'installing',message:"VB-CABLE's setup is open.",busy:true};
  await hostPower.run('update()');
  assert.equal(hostPower.elements.get('mic-setup').disabled,true,'one setup at a time');
  assert(hostPower.elements.get('mic-setup').textContent.includes('Waiting'));
  hostPower.state.value.host_microphone={state:'ready',message:'Ready: apps on this PC can use “CABLE Output (VB-Audio Virtual Cable)” as the microphone.',busy:false};
  await hostPower.run('update()');
  assert.equal(hostPower.elements.get('mic-setup').classList.contains('hidden'),true,'nothing to set up');
  assert(html.includes('href="https://vb-cable.com"'),'VB-CABLE is credited with its home page');
  // RC4.2: Wi-Fi/Ethernet options, Steam, frame re-aim and its check, reports.
  const rc42=setup({...structuredClone(base),role:'client',running:false,state:'stopped',host_control:undefined,
    settings:{codec:'Hevc',bitrate:80,stay_on_access_point:true,reaim:true,pause_other_wifi:true},
    connection:{kind:'wifi',summary:'Wi-Fi · 6 GHz · -41 dBm · 2.9 Gbit/s link (wlp1s0)',warnings:[],notes:[]},
    wifi_roaming:{state:'locked',message:'Wi-Fi stays on access point 3c:84:6a:12:34:56 for this session.'},
    steam_shortcut:{available:true,added:false,message:'Add VROSTBridge to Steam to start sessions from Game Mode.'},
    alignment_check:{state:'idle'},report_delivery:{}});
  await rc42.run('update()');
  assert.equal(rc42.elements.get('connection-kind').textContent,'Wi-Fi');
  assert.equal(rc42.elements.get('wifi-options').classList.contains('hidden'),false,'Wi-Fi options on Wi-Fi');
  assert(rc42.elements.get('roaming-status').textContent.includes('3c:84:6a'));
  assert.equal(rc42.elements.get('steam-add').classList.contains('hidden'),false);
  assert.equal(rc42.elements.get('steam-remove').classList.contains('hidden'),true);
  assert.equal(rc42.elements.get('align-check').disabled,true,'The check needs a streaming session');
  await rc42.run("act('start')");
  assert.equal(rc42.calls.at(-1).body.stay_on_access_point,true);
  assert.equal(rc42.calls.at(-1).body.reaim,true);
  await rc42.run("act('steam-add')");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'steam-add'});
  rc42.state.value.steam_shortcut={available:true,added:false,message:'Add VROSTBridge to Steam to start sessions from Game Mode.'};
  rc42.state.value.connection={kind:'ethernet',summary:'Ethernet · 100 Mbit/s (enp1s0)',warnings:['Ethernet is connected at only 100 Mbit/s.'],notes:[]};
  rc42.state.value.steam_shortcut.added=true;
  await rc42.run('update()');
  assert.equal(rc42.elements.get('connection-kind').textContent,'Ethernet');
  assert.equal(rc42.elements.get('wifi-options').classList.contains('hidden'),true,'No Wi-Fi options on Ethernet');
  assert.equal(rc42.elements.get('pause-other-title').textContent,'Pause Wi-Fi on this network during sessions');
  assert.equal(rc42.elements.get('connection-warnings').children[0].textContent,'Ethernet is connected at only 100 Mbit/s.');
  assert.equal(rc42.elements.get('steam-remove').classList.contains('hidden'),false);
  rc42.state.value.running=true;rc42.state.value.state='streaming';
  await rc42.run('update()');
  assert.equal(rc42.elements.get('align-check').disabled,false);
  assert.equal(rc42.elements.get('reaim').disabled,false,'Re-aim stays switchable during a session');
  rc42.elements.get('reaim').checked=false;
  await rc42.run("act('set-reaim',{enabled:false})");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'set-reaim',enabled:false});
  rc42.state.value.alignment_check={state:'running',remaining_s:7};
  await rc42.run('update()');
  assert(rc42.elements.get('align-status').textContent.includes('7 s'));
  assert.equal(rc42.elements.get('align-question').classList.contains('hidden'),true);
  rc42.state.value.alignment_check={state:'ask'};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('align-question').classList.contains('hidden'),false,'The question appears after the check');
  await rc42.run("act('align-answer',{jumped:false})");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'align-answer',jumped:false});
  await rc42.run("act('send-report')");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'send-report'});
  rc42.state.value.report_delivery={state:'sending',message:'Sending the report to the host…'};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('send-report').disabled,true,'One upload at a time; it runs in the background');
  assert.equal(rc42.elements.get('stop').disabled,false,'Stop stays available while a report is sent');
  rc42.state.value.report_delivery={state:'sent',message:'Sent to the host as VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json.'};
  await rc42.run('update()');
  assert(rc42.elements.get('report-delivery').textContent.includes('VROSTBridge-Client-report_'));
  assert.equal(rc42.elements.get('send-report').disabled,false);
  // RC4.3: this PC's graphics at their limit: message, one-tap Standard, and the form follows it.
  assert.equal(rc42.elements.get('graphics-limit').classList.contains('hidden'),true,'No notice without a limit');
  rc42.state.value.settings.resolution='beyond-balanced';
  rc42.state.value.graphics={state:'limited',message:"This PC's graphics can't keep up with the stream.",actions:['use-standard','repair-reprojection'],short:'PC graphics at their limit',smaller:{label:'Standard',width:2016,height:2016}};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('graphics-limit').classList.contains('hidden'),false);
  assert(rc42.elements.get('graphics-message').textContent.includes("can't keep up"));
  assert.equal(rc42.elements.get('use-standard').classList.contains('hidden'),false);
  assert.equal(rc42.elements.get('use-standard').textContent,'Use Standard · 2016 per eye','The headset\'s own smaller size');
  assert.equal(rc42.elements.get('use-standard').disabled,false,'Allowed during a session; it applies at the next Start');
  await rc42.run("act('use-standard')");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'use-standard'});
  assert.equal(rc42.elements.get('resolution').value,'sharp','Start sends the new size, not the old form value');
  assert.equal(rc42.elements.get('notice').textContent,'Stream size set to Standard (2016 per eye). It applies at your next Start.');
  rc42.state.value.settings.resolution='sharp';
  await rc42.run('update()');
  assert.equal(rc42.elements.get('use-standard').classList.contains('hidden'),true,'Already on Standard');
  // 1.0.0.13: an Index's lighter choice is its next lower rate, named by it.
  rc42.state.value.settings.resolution='index-120';
  rc42.state.value.graphics={...rc42.state.value.graphics,smaller:{id:'index-90',label:'90 Hz',width:2016,height:2240,refresh_hz:90}};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('use-standard').textContent,'Use 90 Hz');
  await rc42.run("act('use-standard')");
  assert.equal(rc42.elements.get('resolution').value,'index-90');
  assert.equal(rc42.elements.get('notice').textContent,'Refresh rate set to 90 Hz (2016 per eye). It applies at your next Start.');
  // A Beyond has none: the advice says how its mode is switched, without a button.
  rc42.state.value.graphics={state:'limited',message:"This PC's graphics can't keep up with the stream. The Beyond's 90 Hz mode is lighter for this PC.",actions:['repair-reprojection'],short:'PC graphics at their limit',smaller:null};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('use-standard').classList.contains('hidden'),true,'Nothing to choose on a Beyond');
  assert(rc42.elements.get('graphics-message').textContent.includes("Beyond's 90 Hz mode"));
  rc42.state.value.settings.resolution='sharp';
  // RC4.3: sharpening applies at once; the eye-tracked tuning shows only with eye tracking.
  rc42.elements.get('sharpening').value='high';
  await rc42.run("act('set-sharpening',{level:'high'})");
  assert.deepEqual(rc42.calls.at(-1).body,{action:'set-sharpening',level:'high'});
  rc42.state.value.system={...(rc42.state.value.system||{}),foveation:{choice:'auto',preset:'subtle',panel:{show:false}}};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('fov-region-field').classList.contains('hidden'),true,'No tuning without eye tracking');
  assert.equal(rc42.elements.get('fovea-panel').classList.contains('hidden'),true,'No panel without eye tracking');
  // RC4.4: Eye-tracked chosen with eye tracking off shows its settings and says what is missing.
  rc42.state.value.settings={...rc42.state.value.settings,foveation:'eye-tracked',gaze:false};
  rc42.state.value.system.foveation={choice:'eye-tracked',preset:'balanced',gaze_follow:false,panel:{show:false},eye_state:'off-here'};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('fov-region-field').classList.contains('hidden'),false,'Eye-tracked chosen: its settings show');
  assert(rc42.elements.get('eye-tuning-note').textContent.startsWith('Eye tracking is off, so the fixed Balanced region is used'),rc42.elements.get('eye-tuning-note').textContent);
  rc42.state.value.settings={...rc42.state.value.settings,gaze:true};
  // RC4.5: set up and calibrate first; then the host's own part.
  rc42.state.value.system.foveation={...rc42.state.value.system.foveation,eye_state:'setup'};
  await rc42.run('update()');
  assert(rc42.elements.get('eye-tuning-note').textContent.includes('Baballonia is not set up yet'));
  assert(rc42.elements.get('foveation-note').textContent.includes('set up and calibrated'),rc42.elements.get('foveation-note').textContent);
  rc42.state.value.system.foveation={...rc42.state.value.system.foveation,eye_state:'calibrate'};
  await rc42.run('update()');
  assert(rc42.elements.get('eye-tuning-note').textContent.includes('Calibrate it once in Desktop Mode'));
  rc42.state.value.system.foveation={...rc42.state.value.system.foveation,preset:'eye-tracked',eye_state:'host-off'};
  await rc42.run('update()');
  assert(rc42.elements.get('eye-tuning-note').textContent.includes('update VROSTBridge on the host PC'),'the host keeps it centred');
  rc42.state.value.system.foveation={...rc42.state.value.system.foveation,eye_state:'following'};
  await rc42.run('update()');
  assert(rc42.elements.get('eye-tuning-note').textContent.startsWith('With eye tracking the sharp region follows your gaze'));
  rc42.state.value.runtime_restart={state:'restarting',message:"SteamVR's compositor on this PC closed unexpectedly at 17:35:12."};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('hint').textContent,"SteamVR's compositor on this PC closed unexpectedly at 17:35:12.");
  delete rc42.state.value.runtime_restart;
  rc42.state.value.settings={...rc42.state.value.settings,foveation:'auto',gaze:false};
  rc42.state.value.settings={...rc42.state.value.settings,fov_region:'small',fov_edge:'light',sharpening:'low'};
  rc42.state.value.system.foveation={choice:'eye-tracked',preset:'eye-tracked',gaze_follow:true,
    panel:{show:true,region_percent:30,region_label:'Small',edge_ratio:5,encoded_eye:[1152,1152],stream_eye:[2560,2560],
           saving_percent:80,gaze_share:.97,gaze_rate:120,frame_pass:'fused',sharpening:'low'}};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('fov-region-field').classList.contains('hidden'),false,'Tuning with eye tracking');
  assert.equal(rc42.elements.get('fovea-panel').classList.contains('hidden'),false,'The live panel while eye tracking runs');
  const tiles=rc42.elements.get('fovea-stats').children.map(tile=>tile.children[0].textContent+' | '+tile.children[1].textContent);
  assert(tiles.includes('30% · 768 px | Sharp region · Small'),tiles.join('\n'));
  assert(tiles.includes('⅕ resolution | Outside the sharp region'),tiles.join('\n'));
  assert(tiles.includes('1152 × 1152 | Sent per eye, of 2560 × 2560'),tiles.join('\n'));
  assert(tiles.includes('80% fewer | Pixels to encode, send and decode'),tiles.join('\n'));
  assert(tiles.includes('97% of frames | Centred on your gaze'),tiles.join('\n'));
  assert(tiles.includes('One pass | Frame pass on the headset PC'),tiles.join('\n'));
  await rc42.run("foveaUpdate({live:true,region:[.3,.3],edge:[5,5],encoded:[1152,1152],eye:[2560,2560],left:[.6,.45],right:[.4,.45],gaze_left:[.61,.44],gaze_right:[.41,.44],gaze_age_ms:6})");
  assert.equal(rc42.elements.get('fovea-live-note').textContent,'Following your eyes · sample 6 ms old');
  await rc42.run("foveaUpdate({live:false})");
  assert.equal(rc42.elements.get('fovea-live-note').textContent,'Waiting for the stream…');
  rc42.elements.get('fov_region').value='small';rc42.elements.get('fov_edge').value='light';
  await rc42.run("act('start')");
  const tunedStart=rc42.calls.at(-1).body;
  assert.equal(tunedStart.fov_region,'small');assert.equal(tunedStart.fov_edge,'light');
  assert.equal(tunedStart.one_pass,true,'One-pass frame path is on by default');
  rc42.elements.get('one_pass').checked=false;
  await rc42.run("act('start')");
  assert.equal(rc42.calls.at(-1).body.one_pass,false,'Start sends the troubleshooting switch');
  rc42.state.value.settings={...rc42.state.value.settings,one_pass:false};
  await rc42.run('update()');
  assert.equal(rc42.elements.get('sharpening-note').textContent,'Sharpening needs the one-pass frame path, which is off in Preferences.');
  assert(html.includes('<input id="one_pass" type="checkbox" checked>'),'One-pass frame path checkbox in Preferences');
  assert(!html.includes('localStorage'),'No browser storage for the live view');
  const reports=setup({...structuredClone(base),received_reports:[{name:'VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json',bytes:140000,received:1790000000}]});
  await reports.run('update()');
  const reportRow=reports.elements.get('received-reports').children[0];
  assert.equal(reportRow.children[0].textContent,'VROSTBridge-Client-report_1.0.0-rc4.2_2026-10-03_19-45-12.json');
  assert(html.indexOf('id="diagnostics"')>0&&html.includes('<details class="card" id="diagnostics">'),'Diagnostics is a collapsible section');
  assert(html.includes("@font-face{font-family:Inter"),'The page uses the bundled fonts');
  assert(!html.includes('DejaVu'),'DejaVu is no longer used');
  // RC4.5: eye tracking set up and run by VROSTBridge (the Eye tracking card).
  const babOff={state:'missing',progress:null,version:'1.1.1.0rc6',size_mb:517,disk_mb:810,licence_url:'https://github.com/Project-Babble/Baballonia/blob/main/LICENSE',running:false,calibrated:false,date:null,
    message:'Not set up. Set up eye tracking downloads Baballonia 1.1.1.0rc6 (about 517 MB) from Project Babble.'};
  const eye=setup({...structuredClone(base),role:'client',running:false,state:'stopped',host_control:undefined,
    settings:{codec:'Auto',bitrate:80,gaze:false,foveation:'auto'},
    eye_tracking:{phase:'off',message:'',owners:[],camera_access:false,relay:{state:'off',rate:null},cameras:{state:'off',message:''},baballonia:babOff,enabled:false}});
  await eye.run('update()');
  const hidden=id=>eye.elements.get(id).classList.contains('hidden');
  assert.equal(eye.elements.get('eye_tracking').checked,false);
  assert.equal(hidden('eye-licence'),false,'The licence shows before the download');
  assert.equal(hidden('eye-setup'),false);assert.equal(hidden('eye-open'),true);assert.equal(hidden('eye-cancel'),true);
  assert.equal(eye.elements.get('eye-size').textContent,517);
  assert.equal(eye.elements.get('eye-status').textContent,'Baballonia is not set up yet.');
  await eye.run("act('eye-setup',{accept_licence:true})");
  assert.deepEqual(eye.calls.at(-1).body,{action:'eye-setup',accept_licence:true});
  eye.elements.get('eye_tracking').checked=true;
  await eye.run("act('eye-tracking',{enabled:true})");
  assert.deepEqual(eye.calls.at(-1).body,{action:'eye-tracking',enabled:true});
  eye.state.value.eye_tracking.enabled=true;eye.state.value.settings.gaze=true;  // what the companion saved
  await eye.run('update()');
  assert.equal(eye.elements.get('eye_tracking').checked,true);
  await eye.run("act('start')");
  assert.equal(eye.calls.at(-1).body.gaze,true,'Start sends the Eye tracking switch');
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,enabled:true,baballonia:{...babOff,state:'downloading',progress:.42,message:'Downloading Baballonia 1.1.1.0rc6…'}};
  await eye.run('update()');
  assert.equal(eye.elements.get('eye-status').textContent,'Downloading Baballonia 1.1.1.0rc6… 42%');
  assert.equal(hidden('eye-progress'),false);assert.equal(eye.elements.get('eye-progress-bar').style.width,'42%');
  assert.equal(hidden('eye-cancel'),false);assert.equal(hidden('eye-setup'),true);assert.equal(hidden('eye-licence'),true);
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,baballonia:{...babOff,state:'installed',message:'Baballonia 1.1.1.0rc6 is set up. Calibrate it once in the headset.'}};
  await eye.run('update()');
  assert.equal(eye.elements.get('eye-status').textContent,'Baballonia 1.1.1.0rc6 is set up · not calibrated yet. It starts with each session.');
  assert.equal(hidden('eye-open'),false);assert.equal(hidden('eye-close'),true);assert.equal(hidden('eye-remove'),false);
  eye.state.value.running=true;eye.state.value.state='streaming';
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,phase:'running',owners:['session'],relay:{state:'receiving',rate:29.8,received:900,forwarded:880},
    message:'',baballonia:{...eye.state.value.eye_tracking.baballonia,running:true,calibrated:true,date:'2026-10-05'}};
  await eye.run('update()');
  assert.equal(eye.elements.get('eye-status').textContent,'Eye tracking is running: 30 samples a second · calibrated 2026-10-05.');
  assert.equal(hidden('eye-open'),true);assert.equal(hidden('eye-close'),false);
  assert.equal(hidden('eye-remove'),true,'Not while Baballonia runs');
  assert(eye.elements.get('eye-relay').textContent.includes('900 samples received'));
  // 1.0.0.6: the gaze reach, with a hint when a way stops short, and Measure again.
  assert.equal(hidden('eye-reach-row'),true,'No reach before enough samples');
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,relay:{...eye.state.value.eye_tracking.relay,reach:{samples:900,left:41,right:43,up:22,down:35}}};
  await eye.run('update()');
  assert.equal(hidden('eye-reach-row'),false);
  const reachText=eye.elements.get('eye-reach').textContent;
  assert(reachText.startsWith('Gaze reach this session: 41° left · 43° right · 22° up · 35° down'),reachText);
  assert(reachText.includes('calibrate again and turn your head further'),'22° up is short of the 30° hint');
  eye.state.value.eye_tracking.relay.reach={samples:900,left:41,right:43,up:38,down:35};
  await eye.run('update()');
  assert(!eye.elements.get('eye-reach').textContent.includes('calibrate again'),'no hint when every way reaches 30°');
  await eye.run("act('eye-reach-reset')");
  assert.deepEqual(eye.calls.at(-1).body,{action:'eye-reach-reset'});
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,phase:'cameras',camera_access:true,cameras:{state:'access'},
    message:'This PC does not let VROSTBridge open the eye cameras yet: choose Allow eye camera access (it asks for your password once).'};
  await eye.run('update()');
  assert.equal(hidden('eye-access'),false);
  assert.equal(hidden('eye-open'),true,'Open and Close are never offered together');assert.equal(hidden('eye-close'),false);
  assert(eye.elements.get('eye-status').textContent.startsWith('This PC does not let VROSTBridge open the eye cameras'));
  await eye.run("act('eye-camera-access')");
  assert.deepEqual(eye.calls.at(-1).body,{action:'eye-camera-access'});
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,access:{state:'asking',message:'Waiting for your password in the system prompt…'}};
  await eye.run('update()');
  assert.equal(eye.elements.get('eye-status').textContent,'Waiting for your password in the system prompt…');
  assert.equal(eye.elements.get('eye-access').disabled,true,'one prompt at a time');
  // Opened by hand but not started yet (port taken, cameras failing): Close is offered.
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,phase:'error',owners:['user'],access:{state:'idle'},camera_access:false,
    message:'Port 9005 on this PC is in use by another program.',baballonia:{...eye.state.value.eye_tracking.baballonia,running:false}};
  await eye.run('update()');
  assert.equal(hidden('eye-close'),false);assert.equal(hidden('eye-open'),true);
  assert(html.includes('Babble Software Distribution License 1.0'),'The licence is named on the page');
  assert(!html.includes("act('camera-start')"),'RC4.4 camera buttons are gone');
  // RC4.6: the calibration needs SteamVR on this PC; a missing SteamVR OpenXR runtime is named.
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,phase:'running',owners:['session'],message:'',access:{state:'idle'},camera_access:false,
    relay:{state:'receiving',rate:30},baballonia:{...eye.state.value.eye_tracking.baballonia,state:'installed',running:true,calibrated:true,date:'2026-10-05',steamvr_openxr:false}};
  await eye.run('update()');
  assert(eye.elements.get('eye-status').textContent.startsWith('Eye tracking is running: 30 samples a second'));
  assert(eye.elements.get('eye-status').textContent.endsWith("so Baballonia's calibration may not show in the headset."));
  eye.state.value.eye_tracking.baballonia.steamvr_openxr=true;
  await eye.run('update()');
  assert(!eye.elements.get('eye-status').textContent.includes('OpenXR'),'nothing to say when it is there');
  assert(html.includes('choose the <b>Eye</b> calibration (the first one)'),'The card names the calibration to use');
  assert(html.includes('only while SteamVR runs on this PC, so start the session first'),'and that it needs a running session');
  // RC4.6: Show Baballonia while it runs (it starts minimised for a session), not in Game Mode.
  assert.equal(hidden('eye-show'),false);
  await eye.run("act('eye-show')");
  assert.deepEqual(eye.calls.at(-1).body,{action:'eye-show'});
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,game_mode:true};
  await eye.run('update()');
  assert.equal(hidden('eye-show'),true);
  eye.state.value.eye_tracking={...eye.state.value.eye_tracking,game_mode:false,baballonia:{...eye.state.value.eye_tracking.baballonia,running:false}};
  await eye.run('update()');
  assert.equal(hidden('eye-show'),true,'nothing to show');
  // RC4.6: opening VROSTBridge again keeps the newest tab; earlier ones close themselves.
  const hub=[],closedPages=[];
  class FakeChannel{constructor(name){this.name=name;hub.push(this);}postMessage(message){for(const other of hub)if(other!==this&&other.name===this.name&&other.onmessage)other.onmessage({data:structuredClone(message)});}}
  const page=(name,now)=>setup(structuredClone(base),{BroadcastChannel:FakeChannel,window:{close(){closedPages.push(name);}},Date:{now:()=>now}});
  const older=page('older',1000),newer=page('newer',2000);
  assert.deepEqual(closedPages,['older'],'the earlier tab closes itself');
  assert.equal(older.document.body.children[0].children[0].textContent,'VROSTBridge is open in a newer tab.');
  assert.equal(newer.document.body.children.length,0,'the newest tab is untouched');
  await older.run('update()');assert.equal(older.calls.length,0,'a replaced tab stops asking the companion');
  page('third',3000);
  assert.deepEqual(closedPages,['older','newer'],'each newer tab replaces the one before; a replaced tab stays replaced');

  // 1.0.0.7: the Windows client (status.platform 'windows'). The same status without platform is the Bazzite
  // client, which keeps every Linux control and its wording.
  const PLATFORM_TEXTS=['network-note','eye-note','eye-calibrate','eye-restart','eye-folder','stay-ap','keep-awake','headset-audio','fonts','credit-ffmpeg','credit-python','credit-deps'];
  const LINUX_ONLY=['game-mode','credit-cams','credit-sdl'];
  const spanText=id=>html.match(new RegExp('id="'+id+'"[^>]*>([^<]*)<'))[1];
  const windowsStatus={...structuredClone(base),role:'client',platform:'windows',running:false,state:'stopped',host_control:undefined,
    settings:{codec:'Hevc',bitrate:80,foveation:'eye-tracked',gaze:true,pause_other_wifi:true,stay_on_access_point:true},
    connection:{kind:'wifi',summary:'Wi-Fi · 6 GHz · -45 dBm · 2.4 Gbit/s link (Wi-Fi)',warnings:[],notes:[],details:{connected:true}},
    client_network:{state:'needs-permission',can_allow:true,message:'Allow your home network to reach this PC\'s VROSTBridge client. Windows asks for administrator permission once.',
      targets:[{interface:'Wi-Fi',subnet:'192.168.1.0/24',category:'Private'}],rules:{'VROSTBridge.Client.VR.TCP':'missing'},blocking:[]},
    windows_startup:{available:true,enabled:false,message:'Start quietly in the system tray. No browser or SteamVR opens.'},
    wifi:{state:'on',interface:'Wi-Fi',streaming_mode:true,permanent_fix:true,power_saving_ac:'Maximum Performance',power_saving_dc:'Medium Power Saving',on_battery:true,
      message:'Wi-Fi power saving is set to Medium Power Saving on battery in the Windows power plan, which lets the radio doze and can make the stream stutter.'},
    power_balance:{state:'starved',message:'The processor is held back by the shared power limit.',actions:['gpu-auto']},
    graphics:{state:'limited',message:"This PC's graphics can't keep up with the stream.",actions:['use-standard','gpu-auto'],smaller:{label:'Standard',width:2016,height:2016}},
    system:{async_reprojection:{state:'missing',message:'SteamVR cannot use asynchronous reprojection.'},power:{},headset_audio:{},wake:{mac_known:false},
      foveation:{choice:'eye-tracked',preset:'balanced',gaze_follow:false,eye_state:'calibrate',panel:{show:false}}},
    steam_shortcut:{available:true,added:false,message:'Add VROSTBridge to Steam to start sessions from Game Mode.'},
    eye_tracking:{phase:'cameras',message:'The eye cameras cannot be opened yet.',owners:['session'],camera_access:true,relay:{state:'off',rate:null},cameras:{state:'access'},access:{state:'idle'},
      baballonia:{state:'installed',version:'1.1.1.0rc6',running:true,calibrated:false,steamvr_openxr:false},enabled:true,game_mode:false},
    video:{width:2016,height:2208,codec:'Hevc',decoder:'d3d11va'}};
  const linuxStatus=structuredClone(windowsStatus);delete linuxStatus.platform;
  const win=setup(structuredClone(windowsStatus)),bazzite=setup(linuxStatus);
  await win.run('update()');await bazzite.run('update()');
  const winHidden=id=>win.elements.get(id).classList.contains('hidden'),linuxHidden=id=>bazzite.elements.get(id).classList.contains('hidden');
  // Linux-only controls hide on Windows; the Bazzite client keeps them for the same status.
  for(const id of ['game-mode','repair-async','gpu-auto','eye-access',...LINUX_ONLY]){
    assert.equal(winHidden(id),true,'Windows hides '+id);assert.equal(linuxHidden(id),false,'Linux keeps '+id);
  }
  assert.equal(winHidden('graphics-gpu-auto'),true);assert.equal(linuxHidden('graphics-gpu-auto'),true,'the power card offers it already');
  win.state.value.power_balance={};bazzite.state.value.power_balance={};
  await win.run('update()');await bazzite.run('update()');
  assert.equal(winHidden('graphics-gpu-auto'),true,'steamos-manager: Linux only');assert.equal(linuxHidden('graphics-gpu-auto'),false);
  assert.equal(win.elements.get('async-status').textContent,'','no Linux reprojection note on Windows');
  assert.equal(bazzite.elements.get('async-status').textContent,'SteamVR cannot use asynchronous reprojection.');
  assert.equal(winHidden('use-standard'),false,'the smaller stream size is offered on both');
  assert.equal(winHidden('eye-show'),false,'Show Baballonia works on Windows');
  // Wording: each part is written for Linux and for Windows; only one shows.
  for(const name of PLATFORM_TEXTS){
    assert.equal(winHidden(name+'-linux'),true,name+'-linux on Windows');assert.equal(winHidden(name+'-windows'),false,name+'-windows on Windows');
    assert.equal(linuxHidden(name+'-linux'),false,name+'-linux on Linux');assert.equal(linuxHidden(name+'-windows'),true,name+'-windows on Linux');
  }
  for(const [id,words] of [['network-note-windows','administrator (UAC) prompt'],['eye-note-windows','“BigEye”'],['eye-note-windows',"close Bigscreen's own eye-tracking app"],
    ['eye-note-windows',"in Baballonia's window during a session"],['eye-folder-windows','%APPDATA%\\ProjectBabble'],['stay-ap-windows',"background Wi-Fi scans during sessions"],
    ['keep-awake-windows','Best performance power mode'],['headset-audio-windows','SteamVR switches the sound to the headset itself']]){
    assert(spanText(id).includes(words),id+': '+spanText(id));
  }
  for(const id of PLATFORM_TEXTS.filter(name=>!name.startsWith('credit-')).map(name=>name+'-windows'))
    assert(!/Bazzite|Desktop Mode|Game Mode|~\/\.config|password|lid switch/.test(spanText(id)),'no Linux wording in '+id);
  assert(html.includes('<div id="credit-ffmpeg-windows" class="hidden"><dt><a href="https://ffmpeg.org/" target="_blank" rel="noreferrer">FFmpeg · Windows client</a>'));
  assert(/id="credit-ffmpeg-windows"[^]*?mingw-w64[^]*?LGPL 2\.1 or later/.test(html),'the Windows FFmpeg build is credited');
  assert(/id="credit-python-windows"[^]*?The embedded Python runtime runs the desktop companion/.test(html));
  // The firewall: Windows' administrator prompt, not Bazzite's password.
  assert.equal(winHidden('client-network-access'),false);assert.equal(winHidden('allow-client-network'),false);assert.equal(winHidden('client-network-note'),false);
  assert.equal(win.elements.get('allow-client-network').disabled,false);
  await win.run("act('allow-client-network')");
  assert.deepEqual(win.calls.at(-1).body,{action:'allow-client-network'});
  win.state.value.client_network={state:'unavailable',can_allow:false,message:'The firewall check is missing from this installation. Repair or reinstall VROSTBridge.'};
  bazzite.state.value.client_network={state:'unavailable',can_allow:false,message:'firewalld is not installed. Other firewall software has not been checked.'};
  await win.run('update()');await bazzite.run('update()');
  assert.equal(winHidden('client-network-access'),false,'a broken Windows installation is shown');
  assert.equal(linuxHidden('client-network-access'),true,'Linux without firewalld stays quiet, as before');
  assert.equal(win.elements.get('client-network-state').textContent,'The firewall check is missing from this installation. Repair or reinstall VROSTBridge.');
  // Sign-in startup: the Windows client's own switch, action windows-startup.
  assert(html.includes('<input id="client-startup" type="checkbox" onchange="act(\'windows-startup\',{enabled:this.checked})"><span>Start VROSTBridge Client when I sign in'));
  assert.equal(winHidden('client-startup-row'),false);assert.equal(linuxHidden('client-startup-row'),true,'no startup switch on Linux');
  assert.equal(win.elements.get('client-startup').checked,false);assert.equal(win.elements.get('client-startup').disabled,false);
  assert.equal(win.elements.get('client-startup-hint').textContent,'Start quietly in the system tray. No browser or SteamVR opens.');
  win.elements.get('client-startup').checked=true;
  await win.run("act('windows-startup',{enabled:true})");
  assert.deepEqual(win.calls.at(-1).body,{action:'windows-startup',enabled:true});
  win.state.value.windows_startup={supported:true,enabled:true,message:''};  // the host's field name works too
  await win.run('update()');
  assert.equal(win.elements.get('client-startup').checked,true);assert.equal(win.elements.get('client-startup').disabled,false);
  assert.equal(win.elements.get('client-startup-hint').textContent,'Starts quietly in the system tray. No browser or SteamVR opens.');
  win.state.value.windows_startup={available:true,enabled:false,conflict:true,message:"Another program owns VROSTBridge's sign-in startup entry (VROSTBridgeClient); it was left unchanged."};
  await win.run('update()');
  assert.equal(win.elements.get('client-startup').disabled,true,'never over another program\'s entry');
  assert(win.elements.get('client-startup-hint').textContent.includes('VROSTBridgeClient'));
  win.state.value.windows_startup={available:false,enabled:false,message:'Available in the installed Windows client.'};
  await win.run('update()');
  assert.equal(win.elements.get('client-startup').disabled,true);
  win.state.value.windows_startup=null;
  await win.run('update()');
  assert.equal(winHidden('client-startup-row'),true,'no switch without its status');
  // Wi-Fi power saving: the Windows power plan's setting, for all networks.
  assert.equal(winHidden('wifi-fix'),false);
  assert.equal(win.elements.get('wifi-fix-note').textContent,'Sets Wi-Fi power saving in the Windows power plan (Wireless Adapter Settings) to Maximum Performance, plugged in and on battery. '+
    'It applies to all networks. Now: Maximum Performance plugged in · Medium Power Saving on battery.');
  assert.equal(bazzite.elements.get('wifi-fix-note').textContent,'','Linux: as before');
  assert.equal(win.elements.get('wifi-status').textContent,windowsStatus.wifi.message,'power saving on is a session-card warning on both');
  await win.run("act('wifi-powersave-off')");
  assert.deepEqual(win.calls.at(-1).body,{action:'wifi-powersave-off'});
  assert.equal(win.elements.get('notice').textContent,'Wi-Fi power saving is off in the Windows power plan, for all networks.');
  await bazzite.run("act('wifi-powersave-off')");
  assert.equal(bazzite.elements.get('notice').textContent,'Wi-Fi power saving is off for your home network.');
  // The other Wi-Fi adapters, the session's Wi-Fi mode and Windows' location consent.
  assert.equal(win.elements.get('pause-other-note').textContent,'A second Wi-Fi adapter on your home network shares the radio channel and can stop the stream from reconnecting. VROSTBridge disconnects it during the session and reconnects it after.');
  assert.equal(bazzite.elements.get('pause-other-note').textContent,'A second Wi-Fi adapter on your home network shares the radio channel and can stop the stream from reconnecting. It is reconnected when the session stops.');
  assert.equal(win.elements.get('wifi-location').textContent,'','nothing to say while Windows gives the network name');
  assert.equal(win.elements.get('wifi-mode').textContent,'','no session yet');
  win.state.value={...win.state.value,running:true,state:'streaming',metrics:{decoded:5,frame_age_ms:9},
    wifi:{state:'off',interface:'Wi-Fi',streaming_mode:true,permanent_fix:false,power_saving_ac:'Maximum Performance',power_saving_dc:'Maximum Performance',
      message:'Wi-Fi power saving is off in the Windows power plan on Wi-Fi. Media streaming mode is on for this session, so the adapter avoids scans that interrupt the stream; it ends when the session stops.'},
    wifi_roaming:{state:'locked',interface:'Wi-Fi',locked:true,background_scan:false,message:'Wi-Fi background scans are off for this session, so Windows keeps Wi-Fi on its current access point; scanning returns when the session stops.'},
    connection:{...win.state.value.connection,details:{connected:true,location_blocked:true}}};
  await win.run('update()');
  assert(win.elements.get('wifi-mode').textContent.includes('Media streaming mode is on for this session'));
  assert(win.elements.get('roaming-status').textContent.startsWith('Wi-Fi background scans are off for this session'));
  assert.equal(win.elements.get('wifi-status').textContent,'','power saving off: no warning');
  assert.equal(winHidden('wifi-fix'),true);assert.equal(win.elements.get('wifi-fix-note').textContent,'');
  assert.equal(win.elements.get('wifi-location').textContent,'Windows didn’t allow reading the Wi-Fi network name; roaming detection is off. To allow it: Settings → Privacy & security → Location → Let desktop apps access your location.');
  win.state.value.connection={...win.state.value.connection,details:{connected:true}};win.state.value.wifi={...win.state.value.wifi,location_blocked:true};
  await win.run('update()');
  assert(win.elements.get('wifi-location').textContent.startsWith('Windows didn’t allow reading the Wi-Fi network name'),'read from the Wi-Fi status too');
  bazzite.state.value={...bazzite.state.value,running:true,state:'streaming',wifi:{...win.state.value.wifi}};
  await bazzite.run('update()');
  assert.equal(bazzite.elements.get('wifi-mode').textContent,'','Linux: as before');assert.equal(bazzite.elements.get('wifi-location').textContent,'');
  win.state.value.connection={kind:'ethernet',summary:'Ethernet · 2.5 Gbit/s (Ethernet)',warnings:[],notes:[]};
  await win.run('update()');
  assert.equal(win.elements.get('wifi-mode').textContent,'');assert.equal(win.elements.get('wifi-location').textContent,'','no Wi-Fi notes on Ethernet');
  assert.equal(winHidden('wifi-options'),true);
  // Eye tracking: Windows wording, no udev camera access, no Linux OpenXR file.
  assert.equal(win.elements.get('eye-tuning-note').textContent,'Baballonia is not calibrated yet, so the fixed Balanced region is used and these two settings wait. Calibrate it once during a session (Eye tracking card → Calibrate once).');
  assert(bazzite.elements.get('eye-tuning-note').textContent.includes('Calibrate it once in Desktop Mode during a session'),'Linux: as before');
  win.state.value.eye_tracking={...win.state.value.eye_tracking,phase:'running',message:'',camera_access:false,relay:{state:'receiving',rate:60}};
  bazzite.state.value.eye_tracking={...win.state.value.eye_tracking};
  await win.run('update()');await bazzite.run('update()');
  assert.equal(win.elements.get('eye-status').textContent,'Eye tracking is running: 60 samples a second · not calibrated yet.');
  assert(bazzite.elements.get('eye-status').textContent.includes('steamxr_linux64.json'),'Linux names its OpenXR runtime');
  // The decoder in the video details; unknown values are left out.
  assert(win.elements.get('video-details').textContent.includes('2016 × 2208 per eye · HEVC · decoded with D3D11VA'),win.elements.get('video-details').textContent);
  win.state.value.video={width:2016,height:2208,codec:'Hevc',decoder:'cpu'};
  await win.run('update()');
  assert(win.elements.get('video-details').textContent.includes('HEVC · decoded on the CPU'));
  bazzite.state.value.video={width:2016,height:2208,codec:'Hevc',decoder:'vaapi'};
  await bazzite.run('update()');
  assert(bazzite.elements.get('video-details').textContent.includes('HEVC · decoded with VA-API'));
  for(const decoder of [undefined,'constructor','toString',42]){
    bazzite.state.value.video={width:2016,height:2208,codec:'Hevc',decoder};
    await bazzite.run('update()');
    assert(!bazzite.elements.get('video-details').textContent.includes('decoded'),String(decoder));
  }
  // Baballonia already installed on the Windows PC: the companion's own words, and whose copy is set up.
  const usable='Not set up. Baballonia v1.1.1.0rc6 is installed on this PC: Set up eye tracking uses it, with nothing to download.';
  for(const app of [win,bazzite])app.state.value.eye_tracking={...app.state.value.eye_tracking,phase:'off',relay:{state:'off',rate:null},
    baballonia:{state:'missing',message:usable,installed_here:{version:'v1.1.1.0rc6',usable:true}}};
  await win.run('update()');await bazzite.run('update()');
  assert.equal(win.elements.get('eye-status').textContent,usable);
  assert.equal(bazzite.elements.get('eye-status').textContent,'Baballonia is not set up yet.','Linux: as before');
  win.state.value.eye_tracking={...win.state.value.eye_tracking,baballonia:{state:'missing',message:'Not set up. Set up eye tracking downloads Baballonia v1.1.1.0rc6 (about 423 MB) from Project Babble.',installed_here:null}};
  await win.run('update()');
  assert.equal(win.elements.get('eye-status').textContent,'Baballonia is not set up yet.','nothing installed here: the card\'s own words (the download note follows)');
  win.state.value.eye_tracking={...win.state.value.eye_tracking,enabled:true,
    baballonia:{state:'installed',version:'v1.1.1.0rc6',installation:'yours',calibrated:true,date:'2026-10-08'}};
  await win.run('update()');
  assert.equal(win.elements.get('eye-status').textContent,'Baballonia v1.1.1.0rc6 is set up (the one installed on this PC) · calibrated 2026-10-08. It starts with each session.');
  win.state.value.eye_tracking.baballonia.installation='vrostbridge';
  await win.run('update()');
  assert(!win.elements.get('eye-status').textContent.includes('installed on this PC'),'VROSTBridge\'s own copy');
  // The host page is unchanged whatever its platform says.
  const windowsHost=setup({...structuredClone(base),platform:'windows'});
  await windowsHost.run('update()');
  for(const name of PLATFORM_TEXTS)assert.equal(windowsHost.elements.get(name+'-windows').classList.contains('hidden'),true,'host: '+name);
  for(const id of LINUX_ONLY)assert.equal(windowsHost.elements.get(id).classList.contains('hidden'),false,'host: '+id);
  assert.equal(windowsHost.elements.get('client-startup-row').classList.contains('hidden'),true);
  // 1.0.0.8: the bitrate as the stream runs, the performance balance, the base stations and gaze centring.
  const babReady={state:'installed',version:'1.1.1.0rc6',running:true,calibrated:true,date:'2026-10-07'};
  const v8=setup({...structuredClone(base),role:'client',running:true,state:'streaming',host_control:undefined,
    settings:{codec:'Auto',bitrate:80,auto_bitrate:true,balance_performance:true,base_stations_off:false,gaze_centre:true,gaze:true},
    system:{power:{},headset_audio:{},wake:{mac_known:true},foveation:{},host_features:['foveation','bitrate-control'],
      bitrate:{active:true,mbps:40,ceiling:49,floor:20,steps_down:1,steps_up:0,reason:'decoding behind',host_applies:true},
      tuning:{state:'active',message:'Balancing this PC for the session: power limit 20 -> 25 W while on the charger.'},
      base_stations:{state:'idle',message:'',stations:['LHB-1A2B3C4D','LHB-5E6F7A8B'],
        last:{action:'off',time:1790000000,message:'2 of 2 base stations turned off (LHB-1A2B3C4D, LHB-5E6F7A8B).'}}},
    eye_tracking:{phase:'running',message:'',owners:['session'],camera_access:false,enabled:true,cameras:{state:'running'},
      relay:{state:'receiving',rate:55,centre:{yaw:12.4,pitch:-1.0,samples:900,middle:[16,-2],off_centre:true,enabled:true}},
      baballonia:babReady,previous_calibration:{name:'tuned_temporal_eye_tracking_20261001_201500.onnx',date:'2026-10-01 20:15'}}});
  await v8.run('update()');
  const v8text=id=>v8.elements.get(id).textContent,v8hidden=id=>v8.elements.get(id).classList.contains('hidden');
  assert.equal(v8text('bitrate-status'),'Bitrate now 40 Mbit/s (most for this picture: 49 Mbit/s; 1 down, 0 up so far). Last change: decoding behind.');
  assert.equal(v8text('tuning-status'),'Balancing this PC for the session: power limit 20 -> 25 W while on the charger.');
  assert(v8text('base-station-status').startsWith('Last command ('),v8text('base-station-status'));
  assert(v8text('base-station-status').endsWith('): 2 of 2 base stations turned off (LHB-1A2B3C4D, LHB-5E6F7A8B).'));
  assert.equal(v8.elements.get('balance_performance').checked,true);
  assert.equal(v8.elements.get('base_stations_off').checked,false,'the saved switch, not the page default');
  assert.equal(v8.elements.get('base-stations-on').disabled,false);
  assert.equal(v8text('gaze-centre-status'),'Your gaze rests 16° right and 2° down of straight ahead this session. Centring takes 12° right and 1° down off this calibration. This calibration looks off-centre: calibrate again, or go back to your previous calibration, for the best result.');
  assert.equal(v8hidden('gaze-centre-reset'),false);
  assert.equal(v8hidden('eye-previous'),true,'not while Baballonia runs');
  v8.state.value.system.base_stations={state:'working',message:'Turning the base stations on…',stations:[],last:null};
  await v8.run('update()');
  assert.equal(v8text('base-station-status'),'Turning the base stations on…');
  assert.equal(v8.elements.get('base-stations-on').disabled,true);assert.equal(v8.elements.get('base-stations-off').disabled,true);
  await v8.run("act('client-option',{name:'base_stations_off',enabled:true})");
  assert.deepEqual(v8.calls.at(-1).body,{action:'client-option',name:'base_stations_off',enabled:true});
  await v8.run("act('base-stations',{power:'off'})");
  assert.deepEqual(v8.calls.at(-1).body,{action:'base-stations',power:'off'});
  // A host without 'bitrate-control' keeps its starting bitrate; a fixed bitrate says so; stopped: nothing.
  v8.state.value.system.bitrate={active:false,mbps:null,ceiling:null,host_applies:false};v8.state.value.system.host_features=['foveation'];
  await v8.run('update()');
  assert.equal(v8text('bitrate-status'),'This host keeps the bitrate the session started with. With VROSTBridge Host 1.0.0.8 it follows how the stream runs.');
  v8.state.value.settings={...v8.state.value.settings,auto_bitrate:false,bitrate:60};
  await v8.run('update()');
  assert.equal(v8text('bitrate-status'),'Bitrate fixed at 60 Mbit/s (Automatic bitrate is off).');
  v8.state.value={...v8.state.value,running:false,state:'stopped',eye_tracking:{...v8.state.value.eye_tracking,phase:'off',owners:[],
    relay:{state:'off',rate:null,centre:{yaw:12.4,pitch:-1.0,samples:0,middle:null,off_centre:false,enabled:true}},baballonia:{...babReady,running:false}}};
  await v8.run('update()');
  assert.equal(v8text('bitrate-status'),'');
  assert.equal(v8text('gaze-centre-status'),'Centring takes 12° right and 1° down off this calibration.');
  assert.equal(v8hidden('eye-previous'),false);
  assert.equal(v8text('eye-previous'),'Use previous calibration (2026-10-01 20:15)');
  await v8.run("act('eye-calibration',{name:'previous'})");
  assert.deepEqual(v8.calls.at(-1).body,{action:'eye-calibration',name:'previous'});
  assert.equal(v8text('notice'),'The previous calibration is chosen. It applies when eye tracking next starts.');
  // Use recommended settings: the page then shows the settings the companion chose.
  v8.state.value.settings={...v8.state.value.settings,codec:'Auto',resolution:'automatic',auto_bitrate:true,pacing:true};
  await v8.run("act('recommended-settings')");
  assert.deepEqual(v8.calls.at(-1).body,{action:'recommended-settings'});
  assert.equal(v8.elements.get('auto_bitrate').checked,true,'read again from the saved settings');
  assert.equal(v8text('notice'),'Recommended settings chosen. They apply at your next Start.');
  // The Windows client keeps the base stations and centring, without the steamos-manager balance.
  const v8win=setup({...structuredClone(v8.state.value),platform:'windows',settings:{...v8.state.value.settings,base_stations_off:true},
    system:{...v8.state.value.system,tuning:{}}});
  await v8win.run('update()');
  assert.equal(v8win.elements.get('balance-row').classList.contains('hidden'),true);
  assert.equal(v8win.elements.get('base_stations_off').checked,true);
  assert.equal(v8win.elements.get('tuning-status').textContent,'');
  // 1.0.0.9: frames rebuilt from the host's parity, and the game audio playing on this PC.
  const v9=setup({...structuredClone(base),role:'client',running:true,state:'streaming',host_control:undefined,
    settings:{codec:'Auto',bitrate:80,auto_bitrate:true},
    system:{power:{},headset_audio:{},wake:{mac_known:true},foveation:{},host_features:['foveation','bitrate-control','parity'],
      bitrate:{active:true,mbps:55,ceiling:65,floor:23,steps_down:0,steps_up:0,reason:'',host_applies:true,repaired_frames:12},
      game_audio:{active:true,stream_hz:48000,device_hz:48000,channels:2,buffer_ms:52.4,target_ms:50,dropouts:0}}});
  await v9.run('update()');
  const v9text=id=>v9.elements.get(id).textContent;
  assert.equal(v9text('bitrate-status'),'Bitrate now 55 Mbit/s (most for this picture: 65 Mbit/s). Rebuilt on the way: 12 frames that would have been lost.');
  assert.equal(v9text('game-audio-status'),'Game audio: 48 kHz, 52 ms buffered (aim 50 ms); no dropouts.');
  v9.state.value.system.game_audio={active:true,stream_hz:48000,device_hz:44100,channels:2,buffer_ms:61,target_ms:60,dropouts:1};
  v9.state.value.system.bitrate={...v9.state.value.system.bitrate,repaired_frames:1};
  await v9.run('update()');
  assert.equal(v9text('game-audio-status'),'Game audio: 48 kHz, played at 44.1 kHz, 61 ms buffered (aim 60 ms); ran dry 1 time this session (late packets).');
  assert(v9text('bitrate-status').endsWith('Rebuilt on the way: 1 frame that would have been lost.'),v9text('bitrate-status'));
  v9.state.value={...v9.state.value,running:false,state:'stopped'};
  await v9.run('update()');
  assert.equal(v9text('game-audio-status'),'','only while a session runs');
  console.log('Desktop connection workflow smoke checks passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
