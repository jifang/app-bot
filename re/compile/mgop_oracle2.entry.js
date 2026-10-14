// mgop_oracle2.entry.js — catch mgop sign header before anti-frida kill.
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
  var o = new NativeFunction(ab, 'void', []);
  Interceptor.replace(ab, new NativeCallback(function () {
    try {
      var bt = Thread.backtrace(this.context, Backtracer.FUZZY);
      for (var i = 0; i < bt.length; i++) {
        var mm = Process.findModuleByAddress(bt[i]);
        if (mm && SEC.test(mm.name)) { log('swallow abort'); return; }
      }
    } catch (e) {}
    // also swallow unknown aborts during oracle window
    log('swallow abort (blanket)');
  }, 'void', []));
})();
// swallow tgkill/kill for self from sec
['kill', 'tgkill'].forEach(function (fn) {
  var p = libcExport(fn);
  if (!p) return;
  Interceptor.attach(p, {
    onEnter: function (a) {
      try {
        var bt = Thread.backtrace(this.context, Backtracer.FUZZY);
        for (var i = 0; i < bt.length; i++) {
          var mm = Process.findModuleByAddress(bt[i]);
          if (mm && SEC.test(mm.name)) {
            log('block ' + fn);
            a[0] = ptr(0); // best-effort noop target
          }
        }
      } catch (e) {}
    }
  });
});
log('anti-detect armed');

function stack() {
  return Java.use('android.util.Log').getStackTraceString(Java.use('java.lang.Exception').$new());
}

Java.perform(function () {
  log('Java.perform');

  // Ali / app class names only
  var found = [];
  Java.enumerateLoadedClasses({
    onMatch: function (name) {
      if (/^com\.(alibaba|alipay|taobao|mpaas|ali\.|hzpd|uc\.|antgroup)/.test(name))
        found.push(name);
    },
    onComplete: function () {
      found.sort();
      send({ t: 'ali_classes', n: found.length, data: found.filter(function (n) {
        return /sign|security|guard|mgop|rpc|mtop|http|gateway|mpaas|wireless/i.test(n);
      }).slice(0, 150) });
      log('ali classes ' + found.length);
    }
  });

  function reportSign(src, key, val) {
    var v = '' + val;
    if (('' + key).toLowerCase() !== 'sign') return;
    send({
      t: 'sign_hdr',
      src: src,
      val: v,
      len: v.length,
      stack: stack().slice(0, 2500)
    });
  }

  // HttpURLConnection
  try {
    var HU = Java.use('java.net.HttpURLConnection');
    HU.setRequestProperty.implementation = function (k, v) {
      reportSign('HttpURLConnection', k, v);
      return this.setRequestProperty(k, v);
    };
    log('HttpURLConnection hooked');
  } catch (e) { log('HU fail ' + e); }

  // okhttp2/3 Builder addHeader / header
  ['okhttp3.Request$Builder', 'com.android.okhttp.Request$Builder',
   'okhttp3.Headers$Builder', 'com.squareup.okhttp.Request$Builder'].forEach(function (cn) {
    try {
      var B = Java.use(cn);
      if (B.addHeader) {
        B.addHeader.overloads.forEach(function (ov) {
          ov.implementation = function () {
            reportSign(cn + '.addHeader', arguments[0], arguments[1]);
            return ov.apply(this, arguments);
          };
        });
      }
      if (B.header) {
        B.header.overloads.forEach(function (ov) {
          ov.implementation = function () {
            reportSign(cn + '.header', arguments[0], arguments[1]);
            return ov.apply(this, arguments);
          };
        });
      }
      log('okhttp hooked ' + cn);
    } catch (e) {}
  });

  // HashMap put — only 32-hex sign (mgop style)
  try {
    var HM = Java.use('java.util.HashMap');
    HM.put.implementation = function (k, v) {
      var ks = '' + k, vs = '' + v;
      if (ks.toLowerCase() === 'sign' && /^[0-9a-fA-F]{32}$/.test(vs)) {
        send({ t: 'sign_hdr', src: 'HashMap32', val: vs, stack: stack().slice(0, 2500) });
      }
      return this.put(k, v);
    };
    log('HashMap32 hooked');
  } catch (e) {}

  send({ t: 'ready' });
});

rpc.exports = {
  ping: function () { return 'ok'; },
  enumnow: function () {
    var out = [];
    Java.perform(function () {
      Java.enumerateLoadedClasses({
        onMatch: function (name) {
          if (/sign|SecurityGuard|Mgop|mpaas|SecureSignature|sgmiddle/i.test(name)
              && /^com\./.test(name)) out.push(name);
        },
        onComplete: function () {}
      });
    });
    return out.slice(0, 100);
  }
};
