/*
 * recon.js — app-agnostic capture hooks for Amap RE
 * Run:  frida -U -n gadget -l recon.js        (patched-apk gadget)
 *   or: frida -U -f <pkg> -l recon.js --no-pause   (rooted + frida-server)
 *
 * Dumps: sign/crypto inputs, OkHttp requests, param maps — BEFORE encryption.
 * This is where you catch the plaintext + how `sign` is computed.
 */
'use strict';

function bytes2hex(arr) {
  if (!arr) return '(null)';
  var b = Java.array('byte', arr), s = '';
  for (var i = 0; i < b.length; i++) s += ('0' + (b[i] & 0xff).toString(16)).slice(-2);
  return s;
}
function bytes2str(arr) {
  try { return Java.use('java.lang.String').$new(arr); } catch (e) { return bytes2hex(arr); }
}

Java.perform(function () {
  // ---- 1. Message digests (MD5/SHA — common sign primitive) ----
  var MD = Java.use('java.security.MessageDigest');
  MD.digest.overload().implementation = function () {
    var out = this.digest();
    console.log('[MD:' + this.getAlgorithm() + '] => ' + bytes2hex(out));
    return out;
  };
  MD.update.overload('[B').implementation = function (input) {
    console.log('[MD.update ' + this.getAlgorithm() + '] ' + bytes2str(input));
    return this.update(input);
  };

  // ---- 2. HMAC / Mac ----
  var Mac = Java.use('javax.crypto.Mac');
  Mac.doFinal.overload('[B').implementation = function (input) {
    var out = this.doFinal(input);
    console.log('[Mac:' + this.getAlgorithm() + '] in=' + bytes2str(input) + ' => ' + bytes2hex(out));
    return out;
  };

  // ---- 3. AES/DES Cipher (body encryption) ----
  var Cipher = Java.use('javax.crypto.Cipher');
  Cipher.doFinal.overload('[B').implementation = function (input) {
    var out = this.doFinal(input);
    // opmode 1=encrypt 2=decrypt
    console.log('[Cipher:' + this.getAlgorithm() + '] in=' + bytes2str(input));
    console.log('               out.hex=' + bytes2hex(out) + '  out.str=' + bytes2str(out));
    return out;
  };

  // ---- 4. OkHttp requests (endpoint + params) ----
  try {
    var Builder = Java.use('okhttp3.Request$Builder');
    Builder.build.implementation = function () {
      var req = this.build();
      console.log('\n[HTTP] ' + req.method() + ' ' + req.url().toString());
      var h = req.headers();
      for (var i = 0; i < h.size(); i++) console.log('   ' + h.name(i) + ': ' + h.value(i));
      return req;
    };
  } catch (e) { console.log('okhttp3 not found (may be shaded/renamed): ' + e); }

  console.log('[*] recon hooks installed');
});
