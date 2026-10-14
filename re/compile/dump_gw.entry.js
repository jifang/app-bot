
import Java from 'frida-java-bridge';
globalThis.Java = Java;
rpc.exports.dump = function() {
  var result = {ok:false};
  Java.perform(function() {
    try {
      var ActivityThread = Java.use('android.app.ActivityThread');
      var app = ActivityThread.currentApplication();
      var ctx = app.getApplicationContext();
      var G = Java.use('com.alibaba.gov.rpc.util.GatewayUtil');
      // reflect to get ProtectionDomain / CodeSource - often null on Android
      // Instead search loaded dex for class descriptor
      var BaseDex = Java.use('dalvik.system.BaseDexClassLoader');
      var loader = Java.cast(G.class.getClassLoader(), BaseDex);
      var pathList = loader.pathList.value;
      var dexElements = pathList.dexElements.value;
      var paths = [];
      for (var i = 0; i < dexElements.length; i++) {
        var de = dexElements[i];
        var dexFile = de.dexFile.value;
        if (dexFile) {
          try { paths.push(''+dexFile.getName()); } catch(e) { paths.push('cookie'); }
        }
        try {
          var file = de.path.value; // may be File
          if (file) paths.push('path:'+file);
        } catch(e) {}
      }
      result = {ok:true, paths:paths, loader:''+loader};
    } catch (e) { result = {ok:false, err:''+e}; }
  });
  return result;
};
