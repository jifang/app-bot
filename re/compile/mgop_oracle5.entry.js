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
var ab=le('abort'); if(ab) Interceptor.replace(ab,new NativeCallback(function(){},'void',[]));
log('armed');

function listMethods(cn){
  var out=[];
  try{
    var C=Java.use(cn);
    var ms=C.class.getDeclaredMethods();
    for(var i=0;i<ms.length;i++){
      var m=ms[i]; var ps=m.getParameterTypes(); var sig=[];
      for(var j=0;j<ps.length;j++) sig.push(''+ps[j].getName());
      out.push((''+m.getReturnType().getName())+' '+m.getName()+'('+sig.join(',')+')');
    }
  }catch(e){ out=['ERR '+e]; }
  return out;
}

function hookSignish(cn){
  try{
    var C=Java.use(cn);
    var ms=C.class.getDeclaredMethods();
    var hooked=0;
    for(var i=0;i<ms.length;i++){
      var name=''+ms[i].getName();
      if(!/sign|Sign|sg_|SG_|md5|Mac|hmac|secure/i.test(name)) continue;
      try{
        C[name].overloads.forEach(function(ov){
          ov.implementation=function(){
            var args=[]; for(var k=0;k<arguments.length;k++){ try{args.push((''+arguments[k]).slice(0,300));}catch(e){args.push('?');} }
            var ret=ov.apply(this,arguments);
            var rs=''; try{rs=(''+ret).slice(0,300);}catch(e){}
            send({t:'call',cn:cn,name:name,args:args,ret:rs});
            return ret;
          };
        });
        hooked++;
      }catch(e){ log('hook '+cn+'.'+name+' '+e); }
    }
    log('hooked '+hooked+' on '+cn);
  }catch(e){ log('miss '+cn+' '+e); }
}

Java.perform(function(){
  function go(tag){
    var want=[];
    Java.enumerateLoadedClasses({
      onMatch:function(n){
        if(/GatewayUtil$|JNIBridge$|SecureSignature|signRequest|SecurityGuardManager|RpcSign|MgopSign|ISecureSignature/i.test(n))
          want.push(n);
      }, onComplete:function(){}
    });
    // always try known
    ['com.alibaba.gov.rpc.util.GatewayUtil',
     'com.alipay.alipaysecuritysdk.common.crypto.bridge.JNIBridge'].forEach(function(n){ if(want.indexOf(n)<0) want.push(n); });
    send({t:'want',tag:tag,want:want});
    want.forEach(function(cn){
      send({t:'methods',cn:cn,m:listMethods(cn)});
      hookSignish(cn);
    });
    // also dump GatewayUtil ALL methods once
    send({t:'gw_all', m:listMethods('com.alibaba.gov.rpc.util.GatewayUtil')});
  }
  setTimeout(function(){ Java.perform(function(){ go('t6'); }); }, 6000);
});
rpc.exports={ping:function(){return 'ok';}};
