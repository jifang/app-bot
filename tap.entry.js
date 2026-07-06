// tap.entry.js — capture harness for the REAL user login + authenticated flow.
// Taps decrypted traffic both directions so the login token / uid / sid is caught
// in plaintext regardless of transport encryption.
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function line(dir, tag, s) { send({ t: 'cap', dir: dir, tag: tag, s: '' + s }); }
function hex(arr) {
  if (!arr) return '(null)';
  var b = Java.array('byte', arr), s = '';
  for (var i = 0; i < b.length; i++) s += ('0' + (b[i] & 0xff).toString(16)).slice(-2);
  return s;
}
// flag decoded blobs that likely carry identity so they're easy to grep
function interesting(s) {
  return /token|sid|uid|mobile|phone|passport|login|auth|tid|cookie|nickname|avatar|account|sessionid|refresh/i.test(s);
}

Java.perform(function () {
  var serverkey = Java.use('com.autonavi.server.aos.serverkey');

  // ---- outgoing params (plaintext BEFORE it becomes in=) ----
  ['amapEncode', 'amapEncodeV2'].forEach(function (name) {
    serverkey[name].overload('java.lang.String').implementation = function (s) {
      var out = this[name](s);
      line('REQ', name + (interesting(s) ? ' *AUTH*' : ''), s);
      return out;
    };
  });

  // ---- incoming responses (plaintext AFTER decrypt) — login token lands here ----
  ['amapDecode', 'amapDecodeV2'].forEach(function (name) {
    serverkey[name].overload('java.lang.String').implementation = function (s) {
      var out = this[name](s);
      if (interesting('' + out)) line('RESP', name + ' *AUTH*', out);
      else line('RESP', name, ('' + out).slice(0, 400));
      return out;
    };
  });

  // ---- the sign string (exact input the app MD5s) ----
  try {
    var Aos = Java.use('com.amap.bundle.network.context.AosEncryptor');
    Aos.sign.overload('[B').implementation = function (b) {
      var r = this.sign(b);
      try { line('SIGN', 'AosEncryptor.sign', Java.use('java.lang.String').$new(b)); } catch (e) {}
      return r;
    };
  } catch (e) { line('SIGN', 'hook-fail', e); }

  // ---- HTTP: every request, all hosts (login lives on passport/aps hosts) ----
  try {
    var Builder = Java.use('com.android.okhttp.Request$Builder');
    Builder.build.implementation = function () {
      var req = this.build();
      try {
        var u = '' + req.urlString();
        var hdr = '';
        var h = req.headers();
        for (var i = 0; i < h.size(); i++) hdr += '\n   ' + h.name(i) + ': ' + h.value(i);
        line('HTTP', req.method(), u + hdr);
      } catch (e) {}
      return req;
    };
  } catch (e) { line('HTTP', 'hook-fail', e); }

  send({ t: 'ready' });
});
