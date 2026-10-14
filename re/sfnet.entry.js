// sfnet.entry.js — tap Ajx3 ModuleRequest (the 顺风车 list network).
// Full request/response go to the python side (disk); here we just forward.
import Java from 'frida-java-bridge';
globalThis.Java = Java;

Java.perform(function () {
  var MR = Java.use('com.autonavi.minimap.ajx3.modules.net.ModuleRequest');

  // request: fetch(key, optionsJSON, callback) — optionsJSON has url + params
  MR.fetch.overload('java.lang.String', 'java.lang.String', 'com.autonavi.minimap.ajx3.core.JsFunctionCallback')
    .implementation = function (key, options, cb) {
      try { send({ t: 'REQ', key: '' + key, options: '' + options }); } catch (e) {}
      return this.fetch(key, options, cb);
    };
  try {
    MR.binaryFetch.overload('java.lang.String', 'java.lang.String', 'com.autonavi.minimap.ajx3.core.JsFunctionCallback')
      .implementation = function (key, options, cb) {
        try { send({ t: 'REQ', bin: true, key: '' + key, options: '' + options }); } catch (e) {}
        return this.binaryFetch(key, options, cb);
      };
  } catch (e) {}

  // response delivered to JS: notifyJs(cb, ..., String, String) — trailing strings = data
  MR.notifyJs.overload('com.autonavi.minimap.ajx3.core.JsFunctionCallback', 'int', 'int', 'java.lang.String', 'java.lang.String', 'int', 'java.lang.String', 'java.lang.String')
    .implementation = function (cb, a, b, s1, s2, c, s3, s4) {
      try { send({ t: 'RESP', a: a, b: b, c: c, s1: '' + s1, s2: '' + s2, s3: '' + s3, s4: '' + s4 }); } catch (e) {}
      return this.notifyJs(cb, a, b, s1, s2, c, s3, s4);
    };
  MR.notifyJs.overload('com.autonavi.minimap.ajx3.core.JsFunctionCallback', 'int', 'int', 'long', 'int', 'java.lang.String', 'java.lang.String')
    .implementation = function (cb, a, b, l, c, s3, s4) {
      try { send({ t: 'RESP', a: a, b: b, c: c, s3: '' + s3, s4: '' + s4 }); } catch (e) {}
      return this.notifyJs(cb, a, b, l, c, s3, s4);
    };

  send({ t: 'ready' });
});
