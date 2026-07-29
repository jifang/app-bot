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

var ready=false;
Java.perform(function(){
  // wait for GatewayUtil via poll
  var tries=0;
  var timer=setInterval(function(){
    tries++;
    Java.perform(function(){
      try{
        Java.use('com.alibaba.gov.rpc.util.GatewayUtil');
        ready=true; clearInterval(timer); log('GatewayUtil ready @'+tries);
        send({t:'gw_ready'});
      }catch(e){}
    });
    if(tries>40){ clearInterval(timer); log('GatewayUtil timeout'); }
  }, 500);
});

function jcall(fn){
  var out=null, err=null;
  Java.perform(function(){
    try{ out=fn(); }catch(e){ err=''+e; }
  });
  if(err) return {error:err};
  return out;
}

rpc.exports = {
  ping: function(){ return ready ? 'ready' : 'wait'; },
  secret: function(){
    return jcall(function(){
      var G=Java.use('com.alibaba.gov.rpc.util.GatewayUtil');
      return {
        secret: ''+G.getSignSecret(),
        appId: ''+G.getAppId(),
        ttid: ''+G.getTTID(),
        url: ''+G.getGatewayUrl(),
        cfg: ''+G.getGatewayConfig()
      };
    });
  },
  md5sign: function(a,b,c,d){
    return jcall(function(){
      var G=Java.use('com.alibaba.gov.rpc.util.GatewayUtil');
      return ''+G.md5Sign(a,b,c,d);
    });
  },
  // hook once to observe live args
  watch: function(){
    return jcall(function(){
      var G=Java.use('com.alibaba.gov.rpc.util.GatewayUtil');
      G.md5Sign.implementation=function(a,b,c,d){
        var ret=this.md5Sign(a,b,c,d);
        send({t:'md5Sign',a:''+a,b:''+b,c:''+c,d:''+d,ret:''+ret});
        return ret;
      };
      return 'watching';
    });
  }
};
