// jtap.entry.js — generic TLS-plaintext tap for com.hzpd.jwztc (警察叔叔 / 违章举报).
// Hooks native BoringSSL/OpenSSL SSL_write (plaintext out) + SSL_read (plaintext in)
// so we catch every HTTP request/response body regardless of OkHttp vs mPaaS RPC,
// cert pinning, or the alijtca hardening shell hiding Java classes.
// Full bytes -> python side (disk). Java OkHttp hook is best-effort on top for clean URLs.

function u8(ptr, len) {
  try { return new Uint8Array(ptr.readByteArray(len)); } catch (e) { return null; }
}
function toStr(bytes) {
  if (!bytes) return '';
  var s = '';
  for (var i = 0; i < bytes.length; i++) {
    var c = bytes[i];
    s += (c >= 0x09 && c < 0x7f) ? String.fromCharCode(c) : '.';
  }
  return s;
}
function looksHttp(s) {
  return /^(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH) |HTTP\/1\.|^\{|^\[/.test(s);
}

function hookSSL() {
  var w = Module.findExportByName(null, 'SSL_write');
  var r = Module.findExportByName(null, 'SSL_read');
  if (!w || !r) {
    // hardened build may statically link boringssl — scan all modules for the export
    Process.enumerateModules().forEach(function (m) {
      if (w && r) return;
      try {
        m.enumerateExports().forEach(function (e) {
          if (e.name === 'SSL_write') w = e.address;
          if (e.name === 'SSL_read') r = e.address;
        });
      } catch (x) {}
    });
  }
  if (!w || !r) { send({ t: 'log', s: 'SSL exports not found' }); return false; }

  Interceptor.attach(w, {
    onEnter: function (a) {
      var len = a[2].toInt32();
      if (len <= 0) return;
      var bytes = u8(a[1], len);
      var s = toStr(bytes);
      if (s.length) send({ t: 'OUT', s: s });
    }
  });
  Interceptor.attach(r, {
    onEnter: function (a) { this.buf = a[1]; },
    onLeave: function (ret) {
      var len = ret.toInt32();
      if (len <= 0) return;
      var s = toStr(u8(this.buf, len));
      if (s.length) send({ t: 'IN', s: s });
    }
  });
  send({ t: 'log', s: 'SSL hooked w=' + w + ' r=' + r });
  return true;
}

hookSSL();
send({ t: 'ready' });
