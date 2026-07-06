// antidetect.entry.js — neuter libmsaoaidsec / ali SecurityGuard frida-scanner.
// Loaded at SPAWN before the app's sec libs dlopen, so our pthread_create hook is
// live when they try to start the "ali_security" watchdog thread. We let the thread
// be created (so pthread_join etc. still work) but swap its start_routine for a no-op,
// so it never scans /proc/self/task or /proc/self/maps for frida artifacts.
// Layered defense: also rename our own gum threads + blind strstr to frida/gum needles.

var SEC = /msaoaidsec|alisecuritysdk|libsecApi|zxprotect|entryexpro|_alijtca_|jaffer|zwmonitor/i;

function log(s) { send({ t: 'log', s: s }); }

// frida 17 removed the static Module.findExportByName(mod, name). Use instance API.
var LIBC = Process.getModuleByName('libc.so');
function libcExport(name) { return LIBC.findExportByName(name); }

// no-op thread body: return immediately
var NOOP = new NativeCallback(function (arg) { return ptr(0); }, 'pointer', ['pointer']);

// ---- 1) redirect sec watchdog threads to the no-op body ----
var pc = libcExport('pthread_create');
if (pc) {
  var orig = new NativeFunction(pc, 'int', ['pointer', 'pointer', 'pointer', 'pointer']);
  Interceptor.replace(pc, new NativeCallback(function (thr, attr, start, arg) {
    try {
      var m = Process.findModuleByAddress(start);
      if (m && SEC.test(m.name)) {
        log('neutralized watchdog thread start_routine in ' + m.name);
        return orig(thr, attr, NOOP, arg);          // real thread, but does nothing
      }
    } catch (e) {}
    return orig(thr, attr, start, arg);
  }, 'int', ['pointer', 'pointer', 'pointer', 'pointer']));
  log('pthread_create hooked');
} else log('pthread_create not found');

// ---- 2) rename our own gum/frida threads so any comm scan sees nothing ----
var setname = libcExport('pthread_setname_np');
if (setname) {
  Interceptor.attach(setname, {
    onEnter: function (a) {
      try {
        var n = a[1].readCString();
        if (n && /gum|frida|gmain|gdbus|pool-frida/i.test(n)) {
          a[1].writeUtf8String('Thread-' + (n.length));
        }
      } catch (e) {}
    }
  });
}

// ---- 3) blind libc strstr/strcmp against frida/gum needles (defense in depth) ----
['strstr', 'strcasestr'].forEach(function (fn) {
  var p = libcExport(fn);
  if (!p) return;
  var o = new NativeFunction(p, 'pointer', ['pointer', 'pointer']);
  Interceptor.replace(p, new NativeCallback(function (hay, need) {
    try {
      var ns = need.readCString();
      if (ns && /frida|gum-js|gmain|linjector|pool-frida|gadget/i.test(ns)) return ptr(0);
    } catch (e) {}
    return o(hay, need);
  }, 'pointer', ['pointer', 'pointer']));
});

send({ t: 'ready' });
