// cap.entry.js — persistent capture for com.hzpd.jwztc (违章举报 automation RE).
// Part A: neuter ali SecurityGuard frida-scanner (pthread_create watchdog) at spawn.
// Part B: once TLS libs are loaded, tap SSL_write/SSL_read for plaintext HTTP.
// Must be loaded PRE-RESUME so Part A is live before the sec libs dlopen.

// ONLY the confirmed frida-scanner watchdog libs. Do NOT include the alijtca shell
// or other sec libs — their threads are load-bearing for app init; neutering them
// leaves Application half-built (NPE in getSharedPreferences on launch).
var SEC = /msaoaidsec|alisecuritysdk/i;
function log(s) { send({ t: 'log', s: s }); }

var LIBC = Process.getModuleByName('libc.so');
function libcExport(n) { return LIBC.findExportByName(n); }

/* ---------------- Part A: anti-detect ---------------- */
var NOOP = new NativeCallback(function (a) { return ptr(0); }, 'pointer', ['pointer']);
var pc = libcExport('pthread_create');
var orig_pc = new NativeFunction(pc, 'int', ['pointer', 'pointer', 'pointer', 'pointer']);
Interceptor.replace(pc, new NativeCallback(function (thr, attr, start, arg) {
  try {
    var m = Process.findModuleByAddress(start);
    if (m && SEC.test(m.name)) { log('neutered watchdog in ' + m.name); return orig_pc(thr, attr, NOOP, arg); }
  } catch (e) {}
  return orig_pc(thr, attr, start, arg);
}, 'int', ['pointer', 'pointer', 'pointer', 'pointer']));

var setname = libcExport('pthread_setname_np');
if (setname) Interceptor.attach(setname, { onEnter: function (a) {
  try { var n = a[1].readCString(); if (n && /gum|frida|gmain|gdbus|pool-frida/i.test(n)) a[1].writeUtf8String('Thread-' + n.length); } catch (e) {}
}});

// NOTE: deliberately NOT hooking strstr/strcasestr. Replacing a hot libc function
// process-wide with a JS callback slows every call enough to trip the app's 3s
// watchdog (crashsdk "timeout or died in 3000ms") and destabilize MainTabActivity.
// The pthread-watchdog neuter above is sufficient to defeat the frida scanner.
log('anti-detect armed');

/* ---------------- Part B: TLS tap ---------------- */
function u8(p, n) { try { return new Uint8Array(p.readByteArray(n)); } catch (e) { return null; } }
function toStr(b) { if (!b) return ''; var s = ''; for (var i = 0; i < b.length; i++) { var c = b[i]; s += (c >= 9 && c < 0x7f) ? String.fromCharCode(c) : '.'; } return s; }

// hook EVERY SSL_write/SSL_read provider (app bundles several BoringSSL copies:
// system libssl, cronet, libtnet/mtop, APSE, etc). Track by address so we don't dup.
var hookedAddrs = {};
function hookOne(name, w, r) {
  var kw = '' + w, kr = '' + r;
  if (hookedAddrs[kw]) return;
  hookedAddrs[kw] = 1;
  try {
    Interceptor.attach(w, { onEnter: function (a) {
      var n = a[2].toInt32(); if (n <= 0) return;
      var s = toStr(u8(a[1], n)); if (s.length) send({ t: 'OUT', s: s, lib: name });
    }});
    Interceptor.attach(r, { onEnter: function (a) { this.b = a[1]; }, onLeave: function (ret) {
      var n = ret.toInt32(); if (n <= 0) return;
      var s = toStr(u8(this.b, n)); if (s.length) send({ t: 'IN', s: s, lib: name });
    }});
    log('SSL tapped in ' + name + ' (w=' + w + ')');
  } catch (e) { log('SSL hook fail ' + name + ': ' + e); }
}
// only REAL TLS libraries — avoid random libs that happen to export an SSL_write
// symbol with a different ABI (those SEGV the agent when we read arg2 as a length).
var TLS_LIB = /libssl\.so|cronet|libtnet|boringssl|conscrypt|libmtopsdk|libgodfather/i;
function scanHookSSL() {
  Process.enumerateModules().forEach(function (m) {
    if (!TLS_LIB.test(m.name)) return;
    var w = null, r = null, hasNew = false;
    try { m.enumerateExports().forEach(function (e) {
      if (e.name === 'SSL_write') w = e.address;
      if (e.name === 'SSL_read')  r = e.address;
      if (e.name === 'SSL_new')   hasNew = true;   // real BoringSSL sanity check
    }); } catch (x) {}
    if (w && r && hasNew) hookOne(m.name, w, r);
  });
}
// re-scan on every dlopen (net libs load lazily / per feature) + periodic backstop
var dlopen = libcExport('android_dlopen_ext') || libcExport('dlopen');
if (dlopen) Interceptor.attach(dlopen, { onLeave: function () { scanHookSSL(); } });
scanHookSSL();
var tries = 0;
var iv = setInterval(function () { scanHookSSL(); if (++tries > 120) clearInterval(iv); }, 1000);

send({ t: 'ready' });
