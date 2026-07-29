// mgop_oracle.entry.js — spawn-time antidetect + MGOP sign recon/oracle for jwztc.
// Build: cd compile && npx frida-compile ../mgop_oracle.entry.js -o ../mgop_oracle.bundle.js
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function log(s) { send({ t: 'log', s: '' + s }); }

/* ---------------- anti-detect (cap.entry.js style; no strstr) ---------------- */
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
      if (m && SEC.test(m.name)) {
        log('neutered watchdog in ' + m.name);
        return orig_pc(thr, attr, NOOP, arg);
      }
    } catch (e) {}
    return orig_pc(thr, attr, start, arg);
  }, 'int', ['pointer', 'pointer', 'pointer', 'pointer']));
}

var setname = libcExport('pthread_setname_np');
if (setname) {
  Interceptor.attach(setname, {
    onEnter: function (a) {
      try {
        var n = a[1].readCString();
        if (n && /gum|frida|gmain|gdbus|pool-frida/i.test(n))
          a[1].writeUtf8String('Thread-' + n.length);
      } catch (e) {}
    }
  });
}

// Soft-block abort/exit from sec libs (second-layer kill)
['abort', 'exit', '_exit'].forEach(function (fn) {
  var p = libcExport(fn);
  if (!p) return;
  Interceptor.attach(p, {
    onEnter: function () {
      try {
        var m = Process.findModuleByAddress(this.returnAddress);
        if (m && SEC.test(m.name)) {
          log('blocked ' + fn + ' from ' + m.name);
          // skip by patching LR to a benign ret — best-effort: replace with return
        }
      } catch (e) {}
    }
  });
});
// Stronger: replace abort when called from SEC module
(function () {
  var ab = libcExport('abort');
  if (!ab) return;
  var o = new NativeFunction(ab, 'void', []);
  Interceptor.replace(ab, new NativeCallback(function () {
    try {
      var m = Process.findModuleByAddress(this.returnAddress);
      // this.returnAddress unreliable inside replace; use backtrace
      var bt = Thread.backtrace(this.context, Backtracer.ACCURATE);
      for (var i = 0; i < bt.length; i++) {
        var mm = Process.findModuleByAddress(bt[i]);
        if (mm && SEC.test(mm.name)) {
          log('swallowed abort via ' + mm.name);
          return;
        }
      }
    } catch (e) {}
    o();
  }, 'void', []));
})();

log('anti-detect armed');

/* ---------------- helpers ---------------- */
function hexBytes(arr) {
  if (!arr) return '';
  var b = Java.array('byte', arr), s = '';
  for (var i = 0; i < b.length; i++) s += ('0' + (b[i] & 0xff).toString(16)).slice(-2);
  return s;
}
function jstr(s) { return Java.use('java.lang.String').$new('' + s); }

var lastDigests = [];
var signHits = [];
var API = { ready: false, classes: [] };

/* ---------------- Java recon + hooks ---------------- */
Java.perform(function () {
  log('Java.perform');

  // Enumerate interesting class names
  var interesting = [];
  Java.enumerateLoadedClasses({
    onMatch: function (name) {
      if (/sign|securityguard|mpaas|mgop|rpcinvoke|middlewaresecure|opensdk|sgmain|sgmiddle/i.test(name)
          && !/androidx|kotlin|google|bumptech|facebook|alibaba\.fastjson/i.test(name)) {
        interesting.push(name);
      }
    },
    onComplete: function () {
      interesting.sort();
      API.classes = interesting.slice(0, 200);
      log('interesting classes: ' + interesting.length);
      send({ t: 'classes', data: interesting.slice(0, 200) });
    }
  });

  // MessageDigest — catch MD5 inputs near sign time
  try {
    var MD = Java.use('java.security.MessageDigest');
    MD.digest.overload().implementation = function () {
      var algo = '' + this.getAlgorithm();
      var out = this.digest();
      var hex = hexBytes(out);
      if (algo.toUpperCase().indexOf('MD5') >= 0 && hex.length === 32) {
        lastDigests.push({ algo: algo, out: hex, ts: Date.now() });
        if (lastDigests.length > 30) lastDigests.shift();
        send({ t: 'md5', out: hex });
      }
      return out;
    };
    MD.digest.overload('[B').implementation = function (input) {
      var algo = '' + this.getAlgorithm();
      var out = this.digest(input);
      var hex = hexBytes(out);
      if (algo.toUpperCase().indexOf('MD5') >= 0) {
        var inn = '';
        try { inn = Java.use('java.lang.String').$new(input, 'UTF-8') + ''; } catch (e) { inn = hexBytes(input).slice(0, 200); }
        send({ t: 'md5_in', out: hex, input: inn.slice(0, 500) });
        lastDigests.push({ algo: algo, out: hex, input: inn.slice(0, 500), ts: Date.now() });
      }
      return out;
    };
    log('MessageDigest hooked');
  } catch (e) { log('MD hook fail ' + e); }

  // Hook header put for "sign"
  function hookMapPut(cn) {
    try {
      var C = Java.use(cn);
      var overloads = C.put.overloads;
      overloads.forEach(function (ov) {
        ov.implementation = function (k, v) {
          var ks = '' + k, vs = '' + v;
          if (ks.toLowerCase() === 'sign' || (vs.length === 32 && /^[0-9a-fA-F]+$/.test(vs) && ks.toLowerCase().indexOf('sign') >= 0)) {
            var st = Java.use('android.util.Log').getStackTraceString(Java.use('java.lang.Exception').$new());
            send({ t: 'sign_put', key: ks, val: vs, stack: st.slice(0, 2000) });
            signHits.push({ key: ks, val: vs, stack: st.slice(0, 1500) });
          }
          return ov.call(this, k, v);
        };
      });
      log('hooked put on ' + cn);
    } catch (e) { log('no put hook ' + cn + ': ' + e); }
  }
  ['java.util.HashMap', 'java.util.concurrent.ConcurrentHashMap'].forEach(hookMapPut);

  // Try known Alibaba SecurityGuard Java APIs
  var candidates = [
    'com.alibaba.wireless.security.open.SecurityGuardManager',
    'com.alibaba.wireless.security.open.securesignature.ISecureSignatureComponent',
    'com.alibaba.wireless.security.open.SecurityGuardParamContext',
    'com.alipay.mobile.common.transport.utils.RpcSignUtil',
    'com.alipay.mobile.common.rpc.RpcInvoker',
    'com.mpaas.mas.adapter.api.MPSign',
  ];
  candidates.forEach(function (cn) {
    try {
      var C = Java.use(cn);
      var ms = C.class.getDeclaredMethods();
      var names = [];
      for (var i = 0; i < ms.length; i++) names.push('' + ms[i].getName());
      send({ t: 'class_ok', cn: cn, methods: names.slice(0, 40) });
      log('found ' + cn);
    } catch (e) {
      send({ t: 'class_miss', cn: cn });
    }
  });

  API.ready = true;
  send({ t: 'ready' });
});

rpc.exports = {
  ping: function () { return API.ready ? 'ok' : 'not-ready'; },
  classes: function () { return API.classes; },
  lastdigests: function () { return lastDigests.slice(-20); },
  signhits: function () { return signHits.slice(-10); },
  // Placeholder: filled once we know the real API
  sign: function (api, appid, gsid, ts, bodyHex) {
    var result = { error: 'not-wired', api: api, appid: appid, gsid: gsid, ts: ts };
    Java.perform(function () {
      try {
        // Attempt SecurityGuard secure signature if present
        var SGM = Java.use('com.alibaba.wireless.security.open.SecurityGuardManager');
        var ctx = Java.use('android.app.ActivityThread').currentApplication().getApplicationContext();
        var sg = SGM.getInstance(ctx);
        var comp = sg.getSecureSignatureComp();
        var PC = Java.use('com.alibaba.wireless.security.open.SecurityGuardParamContext');
        var pcx = PC.$new();
        var HashMap = Java.use('java.util.HashMap');
        var map = HashMap.$new();
        // Common param keys used by Ali SG for request sign
        map.put('INPUT', jstr(api + '&' + appid + '&' + gsid + '&' + ts + '&' + bodyHex));
        pcx.appKey.value = appid;
        pcx.paramMap.value = map;
        pcx.requestType.value = 1;
        var sig = '' + comp.signRequest(pcx, '');
        result = { sign: sig };
      } catch (e) {
        result = { error: '' + e };
      }
    });
    return result;
  }
};
