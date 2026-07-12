// driver_fetch2.entry.js — Hijack MR.fetch to inject custom URL+body for AOS requests.
// Uses Ajx3 ModuleRequest.fetch to dispatch, with the response captured via notifyJs.
//
// rpc.exports:
//   call({ path, body, method?, ... }) -> { status, body, requestId }

import Java from 'frida-java-bridge';
globalThis.Java = Java;

Java.perform(function () {
  var MR = Java.use('com.autonavi.minimap.ajx3.modules.net.ModuleRequest');
  var lastResult = null;
  var pending = {};

  // Hook notifyJs to capture responses
  var notifyJs = MR.notifyJs;
  notifyJs.overload('com.autonavi.minimap.ajx3.core.JsFunctionCallback', 'int', 'int', 'java.lang.String', 'java.lang.String', 'int', 'java.lang.String', 'java.lang.String')
    .implementation = function (cb, a, b, s1, s2, c, s3, s4) {
      // Find which pending request this response is for
      // s4 is the response body (the largest of the strings)
      // The callback id is in s4 normally — but s4 is also body
      // We use the last-completed mapping
      if (lastResult && !lastResult.done) {
        // Find the response body (largest of s1, s2, s3, s4)
        var strs = ['' + s1, '' + s2, '' + s3, '' + s4].sort(function (x, y) { return y.length - x.length; });
        var body = strs[0];
        // headers is the second largest
        var headers = strs[1];
        lastResult.status = a;
        lastResult.body = body;
        lastResult.headers = headers;
        lastResult.done = true;
      }
      return notifyJs.call(this, cb, a, b, s1, s2, c, s3, s4);
    };

  // We need an instance of ModuleRequest. Find it via the ajx framework.
  // The Ajx3 framework instantiates ModuleRequest and registers it as a JS module.
  // We hook MR.fetch.implementation to inject our custom URL.
  MR.fetch.implementation = function (key, options, cb) {
    var opts = {};
    try { opts = JSON.parse(options); } catch (e) {}

    // If this is OUR driver call (key starts with 'appbot-')
    if (key.indexOf('appbot-') === 0) {
      // Reset lastResult
      lastResult = {done: false, requestId: key};

      // Strategy: build a fully-formed options JSON. We keep the body the user
      // passed, but the native AOS layer will compute sign/encrypt from the
      // standard params.
      var newOpts = {
        url: '$aos.m5zb$' + opts.path,
        method: opts.method || 'post',
        headers: opts.headers || {
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded'
        },
        timeout: opts.timeout || 8000,
        async: true,
        csid: true,
        bodytransfer: true,
        aosSign: opts.aosSign || { aos_params: true, ent: true, aos_params_inbody: false, aosmd5: false, sign: ['channel'] },
        wua: opts.wua !== false,
        body: opts.body || ''
      };
      console.log('[driver] INJECT url=' + newOpts.url + ' body-len=' + newOpts.body.length);
      return MR.fetch.call(this, key, JSON.stringify(newOpts), cb);
    }
    return MR.fetch.call(this, key, options, cb);
  };

  // Build a stub JsFunctionCallback
  var JsFunctionCallback = Java.use('com.autonavi.minimap.ajx3.core.JsFunctionCallback');
  var ProxyCls;
  try {
    ProxyCls = Java.registerClass({
      name: 'com.appbot.DriverCb',
      implements: [Java.use('com.autonavi.minimap.ajx3.core.JsFunctionCallback')],
      methods: {
        callback: function (args) {},
        isForMock: function () { return true; }
      }
    });
  } catch (e) {
    console.log('[reg err] ' + e);
  }

  console.log('[*] driver_fetch2 installed');

  // Stash a ModuleRequest instance
  var moduleInstance = null;
  Java.choose('com.autonavi.minimap.ajx3.modules.net.ModuleRequest', {
    onMatch: function (inst) {
      if (moduleInstance === null) moduleInstance = inst;
    },
    onComplete: function () { console.log('[*] module instances scanned'); }
  });

  rpc.exports = {
    call: function (params) {
      lastResult = null;
      if (!moduleInstance) {
        // Try again
        Java.choose('com.autonavi.minimap.ajx3.modules.net.ModuleRequest', {
          onMatch: function (inst) { if (moduleInstance === null) moduleInstance = inst; },
          onComplete: function () {}
        });
        if (!moduleInstance) return {error: 'no ModuleRequest instance found'};
      }
      // Build a unique key
      var key = 'appbot-' + Date.now() + '-' + Math.random().toString(36).substring(7);
      var opts = {
        path: params.path,
        method: params.method || 'post',
        body: params.body || '',
        timeout: params.timeout || 8000
      };
      if (params.headers) opts.headers = params.headers;
      if (params.aosSign) opts.aosSign = params.aosSign;
      if (params.wua !== undefined) opts.wua = params.wua;
      var optsStr = JSON.stringify(opts);
      try {
        moduleInstance.fetch(Java.use('java.lang.String').$new(key), Java.use('java.lang.String').$new(optsStr), ProxyCls.$new());
      } catch (e) {
        return {error: 'fetch call failed: ' + e};
      }
      // Wait for response
      var start = Date.now();
      var timeout = params.timeout || 8000;
      while ((!lastResult || !lastResult.done) && (Date.now() - start) < timeout) {
        Thread.sleep(0.05);
      }
      if (!lastResult || !lastResult.done) {
        return {error: 'timeout', requestId: key};
      }
      return lastResult;
    }
  };
});