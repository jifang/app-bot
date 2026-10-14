// sftap.entry.js — capture the okhttp3 / AjxHttp request that loads the 顺风车
// order list, so it can be replayed. Request line + headers + body only, to disk.
import Java from 'frida-java-bridge';
globalThis.Java = Java;

function send_req(via, method, url, headers, body) {
  send({ t: 'req', via: via, method: method, url: url, headers: headers, body: body });
}

Java.perform(function () {
  // ---- okhttp3 (square) ----
  try {
    var OkReq = Java.use('okhttp3.Request');
    var Buffer = Java.use('okio.Buffer');
    var Interceptor = Java.use('okhttp3.Interceptor');
    var OkClient = Java.use('okhttp3.OkHttpClient');
    // read a request's body to string (safe: writes to a throwaway Buffer)
    function bodyOf(req) {
      try {
        var b = req.body();
        if (b === null) return null;
        var buf = Buffer.$new();
        b.writeTo(buf);
        return '' + buf.readUtf8();
      } catch (e) { return '<body ' + e + '>'; }
    }
    function headersOf(req) {
      var h = req.headers(), o = {};
      for (var i = 0; i < h.size(); i++) o[h.name(i)] = h.value(i);
      return o;
    }
    // hook newCall — sees every outbound okhttp3 request
    OkClient.newCall.overload('okhttp3.Request').implementation = function (req) {
      try {
        var url = '' + req.url().toString();
        send_req('okhttp3', '' + req.method(), url, headersOf(req), bodyOf(req));
      } catch (e) {}
      return this.newCall(req);
    };
    send({ t: 'log', s: '[*] hooked okhttp3.OkHttpClient.newCall' });
  } catch (e) { send({ t: 'log', s: '[okhttp3] ' + e }); }

  // ---- AjxHttpLoader (Ajx3 framework network) — enumerate its methods to target ----
  try {
    var Ajx = Java.use('com.autonavi.minimap.ajx3.loader.AjxHttpLoader');
    var ms = Ajx.class.getDeclaredMethods();
    var list = [];
    for (var i = 0; i < ms.length; i++) {
      var p = ms[i].getParameterTypes(), sig = [];
      for (var j = 0; j < p.length; j++) sig.push(p[j].getName());
      list.push(ms[i].getName() + '(' + sig.join(',') + ')');
    }
    send({ t: 'ajx', methods: list });
  } catch (e) { send({ t: 'log', s: '[ajx] ' + e }); }

  send({ t: 'ready' });
});
