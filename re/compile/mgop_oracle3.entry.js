// mgop_oracle3 — antidetect + SSL tap for mgop sign header + delayed class enum
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function log(s) { send({ t: 'log', s: '' + s }); }

var SEC = /msaoaidsec|alisecuritysdk/i;
var LIBC = Process.getModuleByName('libc.so');
function libcExport(n) { return LIBC.findExportByName(n); }
var NOOP = new NativeCallback(function (a) { return ptr(0); }, 'pointer', ['pointer']);
var pc = libcExport('pthread_create');
if (pc) {
  var orig_pc = new NativeFunction(pc, 'int', ['pointer', 'pointer', 'pointer', 'pointer']);
  Interceptor.replace(pc, new NativeCallback(function (thr, attr, start, arg) {
    try {
      var m = Process.findModuleByAddress(start);
      if (m && SEC.test(m.name)) return orig_pc(thr, attr, NOOP, arg);
    } catch (e) {}
    return orig_pc(thr, attr, start, arg);
  }, 'int', ['pointer', 'pointer', 'pointer', 'pointer']));
}
(function () {
  var ab = libcExport('abort');
  if (!ab) return;
  Interceptor.replace(ab, new NativeCallback(function () {
    log('swallow abort');
  }, 'void', []));
})();
log('anti-detect armed');

function u8(p, n) {
  try { return new Uint8Array(p.readByteArray(n)); } catch (e) { return null; }
}
function toStr(b) {
  if (!b) return '';
  var s = '';
  for (var i = 0; i < b.length; i++) {
    var c = b[i];
    s += (c >= 9 && c < 0x7f) ? String.fromCharCode(c) : '.';
  }
  return s;
}

var hooked = {};
function hookSSL(name, w, r) {
  var k = '' + w;
  if (hooked[k]) return;
  hooked[k] = 1;
  Interceptor.attach(w, {
    onEnter: function (a) {
      var n = a[2].toInt32();
      if (n <= 0 || n > 65536) return;
      var s = toStr(u8(a[1], n));
      if (!s) return;
      if (/sign:|mgop\.trustway|\/app\/mgop|wfjb\.auth/i.test(s)) {
        send({ t: 'tls_out', lib: name, s: s.slice(0, 4000) });
      }
    }
  });
  log('SSL_write hooked ' + name);
}

var TLS_LIB = /libssl\.so|cronet|libtnet|boringssl|conscrypt|libmtopsdk|libgodfather|libAPSE/i;
function scan() {
  Process.enumerateModules().forEach(function (m) {
    if (!TLS_LIB.test(m.name)) return;
    var w = null, r = null, hasNew = false;
    try {
      m.enumerateExports().forEach(function (e) {
        if (e.name === 'SSL_write') w = e.address;
        if (e.name === 'SSL_read') r = e.address;
        if (e.name === 'SSL_new') hasNew = true;
      });
    } catch (e) {}
    if (w && hasNew) hookSSL(m.name, w, r);
  });
}
var dl = libcExport('android_dlopen_ext') || libcExport('dlopen');
if (dl) Interceptor.attach(dl, { onLeave: function () { scan(); } });
scan();
setInterval(scan, 2000);

Java.perform(function () {
  log('Java.perform');
  function dumpAli() {
    var all = [], hit = [];
    Java.enumerateLoadedClasses({
      onMatch: function (name) {
        if (/^com\.(alibaba|alipay|taobao|mpaas|ali\.|hzpd)/.test(name)) {
          all.push(name);
          if (/sign|security|guard|mgop|rpc|mtop|http|gateway|wireless|sg/i.test(name))
            hit.push(name);
        }
      },
      onComplete: function () {
        send({ t: 'ali', n: all.length, hit: hit.slice(0, 200), sample: all.slice(0, 40) });
      }
    });
  }
  dumpAli();
  setTimeout(function () { Java.perform(dumpAli); }, 8000);
  setTimeout(function () { Java.perform(dumpAli); }, 15000);

  try {
    var HM = Java.use('java.util.HashMap');
    HM.put.implementation = function (k, v) {
      var ks = '' + k, vs = '' + v;
      if (ks.toLowerCase() === 'sign' && /^[0-9a-fA-F]{32}$/.test(vs)) {
        var st = Java.use('android.util.Log').getStackTraceString(Java.use('java.lang.Exception').$new());
        send({ t: 'sign32', val: vs, stack: st.slice(0, 3000) });
      }
      return this.put(k, v);
    };
  } catch (e) {}

  try {
    var B = Java.use('okhttp3.Request$Builder');
    B.header.overloads.forEach(function (ov) {
      ov.implementation = function () {
        if (('' + arguments[0]).toLowerCase() === 'sign')
          send({ t: 'sign32', val: '' + arguments[1], src: 'okhttp3', stack: 'okhttp' });
        return ov.apply(this, arguments);
      };
    });
  } catch (e) {}

  send({ t: 'ready' });
});

rpc.exports = { ping: function () { return 'ok'; } };
