// netscan.entry.js — find the SF page's network layer (class names only, no data).
import Java from 'frida-java-bridge';
globalThis.Java = Java;

Java.perform(function () {
  var hits = { okhttp: [], webview: [], http: [], bridge: [] };
  Java.enumerateLoadedClassesSync().forEach(function (n) {
    if (/OkHttpClient$/.test(n)) hits.okhttp.push(n);
    else if (/WebView$|WebViewClient$|WebResourceRequest$/.test(n)) hits.webview.push(n);
    else if (/HttpURLConnectionImpl$|HurlStack$|\.Retrofit$/.test(n)) hits.http.push(n);
    else if (/(JsBridge|JSBridge|JavascriptBridge|AjxHttp|H5.*Http|WebModule|AjxModuleNetwork)/.test(n)) hits.bridge.push(n);
  });
  function uniq(a) { return a.filter(function (v, i) { return a.indexOf(v) === i; }).slice(0, 25); }
  send({ okhttp: uniq(hits.okhttp), webview: uniq(hits.webview), http: uniq(hits.http), bridge: uniq(hits.bridge) });
});
