// entry.js (recon v2) — frida 17: Java bridge required explicitly.
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function hex(arr) {
  if (!arr) return '(null)';
  var b = Java.array('byte', arr), s = '';
  for (var i = 0; i < b.length; i++) s += ('0' + (b[i] & 0xff).toString(16)).slice(-2);
  return s;
}
function str(arr) {
  try { return Java.use('java.lang.String').$new(arr); } catch (e) { return hex(arr); }
}
// printable-ASCII heuristic: keep sign strings, drop DER certs / binary
function isText(arr) {
  if (!arr) return false;
  var b = Java.array('byte', arr);
  if (b.length === 0 || b.length > 4096) return false;
  if ((b[0] & 0xff) === 0x30) return false; // DER SEQUENCE => cert/key, skip
  var bad = 0;
  for (var i = 0; i < b.length; i++) {
    var c = b[i] & 0xff;
    if (c === 9 || c === 10 || c === 13) continue;
    if (c < 32 || c > 126) { if (++bad > 2) return false; }
  }
  return true;
}
function stack() {
  return Java.use('android.util.Log').getStackTraceString(
    Java.use('java.lang.Exception').$new());
}

Java.perform(function () {
  // ---- MessageDigest: the sign primitive (MD5/SHA1) ----
  var MD = Java.use('java.security.MessageDigest');
  var marked = {};      // instance hashCode -> true when a sign-marker string was fed
  var stacksLeft = 6;   // stack-trace budget for genuine sign calls only
  function isSignInput(s) {
    return s.indexOf('@xnaEwInMxa') >= 0 || s.indexOf('amap7a') >= 0 || s.indexOf('ANDH161900') >= 0;
  }
  MD.update.overload('[B').implementation = function (input) {
    if (isText(input)) {
      var s = '' + str(input);
      if (isSignInput(s)) { marked[this.hashCode()] = true; console.log('[SIGN.in ' + this.getAlgorithm() + '] ' + s); }
    }
    return this.update(input);
  };
  MD.digest.overload().implementation = function () {
    var out = this.digest();
    var hc = this.hashCode();
    if (marked[hc]) {                        // this digest consumed sign material
      delete marked[hc];
      var extra = stacksLeft > 0 ? (stacksLeft--, '\n--- signer ---\n' + stack() + '\n--------------') : '';
      console.log('[SIGN.out ' + this.getAlgorithm() + '] => ' + hex(out) + extra);
    }
    return out;
  };

  // ---- Cipher.init: grab DES/RSA key + IV ----
  var Cipher = Java.use('javax.crypto.Cipher');
  var SecretKeySpec = Java.use('javax.crypto.spec.SecretKeySpec');
  var IvParameterSpec = Java.use('javax.crypto.spec.IvParameterSpec');
  Cipher.init.overload('int', 'java.security.Key').implementation = function (m, k) {
    dumpKey(this, m, k, null); return this.init(m, k);
  };
  Cipher.init.overload('int', 'java.security.Key', 'java.security.spec.AlgorithmParameterSpec')
    .implementation = function (m, k, p) { dumpKey(this, m, k, p); return this.init(m, k, p); };
  function dumpKey(cipher, mode, key, spec) {
    try {
      var line = '[Cipher.init ' + cipher.getAlgorithm() + '] mode=' + mode;
      if (Java.cast) {
        try { var sk = Java.cast(key, SecretKeySpec);
          line += ' KEY.hex=' + hex(sk.getEncoded()) + ' KEY.str=' + str(sk.getEncoded()); } catch (e) {}
      }
      if (spec !== null) {
        try { var iv = Java.cast(spec, IvParameterSpec);
          line += ' IV.hex=' + hex(iv.getIV()); } catch (e) {}
      }
      console.log(line);
    } catch (e) { console.log('[Cipher.init] err ' + e); }
  }

  // ---- Cipher.doFinal: plaintext<->ciphertext ----
  Cipher.doFinal.overload('[B').implementation = function (input) {
    var out = this.doFinal(input);
    var alg = this.getAlgorithm();
    if (alg.indexOf('RSA') < 0 && isText(input))          // skip RSA binary; show DES/AES plaintext
      console.log('[Cipher:' + alg + '] in=' + str(input) + '\n   out.hex=' + hex(out));
    else
      console.log('[Cipher:' + alg + '] (binary) out.hex=' + hex(out).slice(0, 64) + '...');
    return out;
  };

  // ---- Mac / HMAC ----
  var Mac = Java.use('javax.crypto.Mac');
  Mac.doFinal.overload('[B').implementation = function (input) {
    var out = this.doFinal(input);
    console.log('[Mac:' + this.getAlgorithm() + '] in=' + str(input) + ' => ' + hex(out));
    return out;
  };

  // ---- okhttp is com.android.okhttp (AOSP-bundled), not okhttp3 ----
  try {
    var Builder = Java.use('com.android.okhttp.Request$Builder');
    Builder.build.implementation = function () {
      var req = this.build();
      try {
        var u = '' + req.urlString();
        if (u.indexOf('amap.com') >= 0 || u.indexOf('autonavi') >= 0) {
          console.log('\n[HTTP] ' + req.method() + ' ' + u);
          var h = req.headers();
          for (var i = 0; i < h.size(); i++) console.log('   ' + h.name(i) + ': ' + h.value(i));
        }
      } catch (e) {}
      return req;
    };
    console.log('[*] hooked com.android.okhttp.Request$Builder');
  } catch (e) { console.log('[okhttp] hook failed: ' + e); }

  console.log('[*] recon v2 hooks installed');
});
