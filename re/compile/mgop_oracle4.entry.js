
import Java from 'frida-java-bridge';
globalThis.Java = Java;
function log(s){ send({t:'log',s:''+s}); }
var SEC=/msaoaidsec|alisecuritysdk/i;
var LIBC=Process.getModuleByName('libc.so');
function le(n){return LIBC.findExportByName(n);}
var NOOP=new NativeCallback(function(a){return ptr(0);},'pointer',['pointer']);
var pc=le('pthread_create');
if(pc){var o=new NativeFunction(pc,'int',['pointer','pointer','pointer','pointer']);
Interceptor.replace(pc,new NativeCallback(function(thr,attr,start,arg){
 try{var m=Process.findModuleByAddress(start); if(m&&SEC.test(m.name)) return o(thr,attr,NOOP,arg);}catch(e){}
 return o(thr,attr,start,arg);
},'int',['pointer','pointer','pointer','pointer']));}
var ab=le('abort'); if(ab) Interceptor.replace(ab,new NativeCallback(function(){log('abort');},'void',[]));
log('armed');

function methodsOf(cn){
  var out=[];
  try{
    var C=Java.use(cn);
    var ms=C.class.getDeclaredMethods();
    for(var i=0;i<ms.length;i++){
      var m=ms[i]; var ps=m.getParameterTypes(); var sig=[];
      for(var j=0;j<ps.length;j++) sig.push(ps[j].getName());
      out.push(m.getReturnType().getName()+' '+m.getName()+'('+sig.join(',')+')');
    }
  }catch(e){ out=['ERR '+e]; }
  return out;
}

function hookAll(cn){
  try{
    var C=Java.use(cn);
    var ms=C.class.getDeclaredMethods();
    for(var i=0;i<ms.length;i++){
      (function(method){
        var name=method.getName();
        try{
          var overs=C[name].overloads;
          overs.forEach(function(ov){
            ov.implementation=function(){
              var args=[];
              for(var k=0;k<arguments.length;k++){
                try{ args.push((''+arguments[k]).slice(0,200)); }catch(e){ args.push('?'); }
              }
              var ret=ov.apply(this, arguments);
              var rs='';
              try{ rs=(''+ret).slice(0,200); }catch(e){}
              if(/sign/i.test(name) || /sign/i.test(args.join(' ')) || /^[0-9a-fA-F]{32}$/.test(rs)){
                send({t:'call', cn:cn, name:name, args:args, ret:rs});
              }
              return ret;
            };
          });
        }catch(e){}
      })(ms[i]);
    }
    log('hooked '+cn);
  }catch(e){ log('hook fail '+cn+' '+e); }
}

Java.perform(function(){
  log('java');
  function stage(tag){
    var targets=[
      'com.alibaba.gov.rpc.util.GatewayUtil',
      'com.alipay.alipaysecuritysdk.common.crypto.bridge.JNIBridge',
      'com.hzpd.jwztc.network.core.HttpClientManager',
      'com.alibaba.gov.android.api.rpc.annotation.RpcRequest',
    ];
    // also search class names containing Sign
    var found=[];
    Java.enumerateLoadedClasses({
      onMatch:function(n){
        if(/GatewayUtil|JNIBridge|SecureSign|signRequest|Mgop|RpcSign|SecurityGuard|HttpClientManager/i.test(n) && n.indexOf('com.')===0)
          found.push(n);
      },
      onComplete:function(){}
    });
    send({t:'found', tag:tag, found:found.slice(0,80)});
    targets.concat(found).forEach(function(cn){
      send({t:'methods', cn:cn, m:methodsOf(cn).slice(0,60)});
      hookAll(cn);
    });
  }
  setTimeout(function(){ Java.perform(function(){ stage('t8'); }); }, 8000);
  setTimeout(function(){ Java.perform(function(){ stage('t12'); }); }, 12000);
});
rpc.exports={ping:function(){return 'ok';}};
