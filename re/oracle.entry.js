// oracle.entry.js — enumerate AOS crypto surface + expose a signing/enc oracle via RPC.
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function methodsOf(cn) {
  try {
    var C = Java.use(cn);
    var ms = C.class.getDeclaredMethods();
    var out = [];
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      var ps = m.getParameterTypes();
      var sig = [];
      for (var j = 0; j < ps.length; j++) sig.push(ps[j].getName());
      out.push(m.getReturnType().getName() + ' ' + m.getName() + '(' + sig.join(',') + ')');
    }
    return out;
  } catch (e) { return ['<err ' + e + '>']; }
}

var API = null;

Java.perform(function () {
  var report = {};
  ['com.autonavi.server.aos.serverkey',
   'com.amap.bundle.network.context.AosEncryptor'].forEach(function (cn) {
    report[cn] = methodsOf(cn);
  });
  send({ type: 'methods', data: report });

  var serverkey = Java.use('com.autonavi.server.aos.serverkey');
  var JString = Java.use('java.lang.String');
  function bytes(s) { return JString.$new(s).getBytes('UTF-8'); }
  API = {
    // native signer: sign(byte[]) -> String
    sign: function (s) { return '' + serverkey.sign(bytes(s)); },
    // AOS param codec (the `in=` transform)
    amapEncode:   function (s) { return '' + serverkey.amapEncode(s); },
    amapDecode:   function (s) { return '' + serverkey.amapDecode(s); },
    amapEncodeV2: function (s) { return '' + serverkey.amapEncodeV2(s); },
    amapDecodeV2: function (s) { return '' + serverkey.amapDecodeV2(s); },
    aosKey:  function () { return '' + serverkey.getAosKey(); },
    version: function () { try { return '' + serverkey.getVersion(); } catch (e) { return '<err ' + e + '>'; } }
  };
  send({ type: 'ready' });
});

function wrap(fn) { return function (a) { var r = null; Java.perform(function () { try { r = fn(a); } catch (e) { r = '<err ' + e + '>'; } }); return r; }; }
rpc.exports = {
  sign:         wrap(function (s) { return API.sign(s); }),
  amapencode:   wrap(function (s) { return API.amapEncode(s); }),
  amapdecode:   wrap(function (s) { return API.amapDecode(s); }),
  amapencodev2: wrap(function (s) { return API.amapEncodeV2(s); }),
  amapdecodev2: wrap(function (s) { return API.amapDecodeV2(s); }),
  aoskey:       wrap(function () { return API.aosKey(); }),
  version:      wrap(function () { return API.version(); })
};
