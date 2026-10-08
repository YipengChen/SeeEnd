import {api, $, tell, el} from '/shared.js';
let fields=[], params={}, session=null, active=false, busy=false, dirty=false, key=null, devices=[], revision=0, activeDevice=null, stopping=false;
const storageKey='aiflame-tuning-v1';
// Keep the complete schema for existing presets; expose only the main controls.
const visibleKeys=new Set([
  'height','width','tip',
  'peak','brightness','edge','flicker',
  'speed','motion','sway','sway_speed','flutter','height_noise',
  'wind','scatter',
]);
const visibleFields=()=>fields.filter(f=>visibleKeys.has(f[0]));
const defaults=()=>Object.fromEntries(fields.map(f=>[f[0],f[5]]));
function validate(values){
  if(!values || typeof values!=='object' || Array.isArray(values)) throw Error('方案格式错误');
  for(const [k,v] of Object.entries(values)){
    const f=fields.find(f=>f[0]===k);
    if(!f || typeof v!=='number' || !Number.isFinite(v) || v<f[2] || v>f[3]) throw Error(`参数超出范围：${k}`);
  }
  return {...defaults(),...values};
}
function render(){
  $('controls').replaceChildren();
  for(const group of [...new Set(visibleFields().map(f=>f[6]))]){
    const section=el('section','','group');section.append(el('h3',group));const grid=el('div','','fields');section.append(grid);
    for(const [name,label,min,max,step] of visibleFields().filter(f=>f[6]===group)){
      const field=el('div','','field'), top=el('div','','field-top');const title=el('label',label);title.htmlFor=`range-${name}`;
      const number=document.createElement('input'),range=document.createElement('input');number.type='number';range.type='range';range.id=`range-${name}`;number.setAttribute('aria-label',label+'数值');
      for(const node of [number,range]){Object.assign(node,{min,max,step,value:params[name]});}
      const change=(source,target)=>{const value=Number(source.value);if(source.value==='' || !Number.isFinite(value) || value<min || value>max){source.setCustomValidity('请输入范围内的数值');source.reportValidity();return;}source.setCustomValidity('');target.value=value;params[name]=value;dirty=true;revision++;if(active)tell('参数已修改，等待硬件确认…');};
      range.oninput=()=>change(range,number);number.onchange=()=>change(number,range);
      top.append(title,number);const limits=el('div','','limits');limits.append(el('span',String(min)),el('span',String(max)));field.append(top,range,limits);grid.append(field);
    }
    $('controls').append(section);
  }
}
function buttons(){const d=devices.find(d=>d.id===$('device').value);$('start').disabled=active||busy||stopping||!d?.online||!d?.supported;$('stop').disabled=!active;$('device').disabled=active||busy||stopping;$('connection').textContent=!d?'暂无配对设备，请到「我的设备」连接。':!d.online?(d.error||'设备离线'):!d.supported?'需要通过 USB 更新灯光程序至 0.4.8。':'设备在线 · 支持实时调参';}
async function send(){
  if(!active||busy)return;busy=true;dirty=false;const sentRevision=revision;
  try{const result=await api('/api/tuning',{action:'apply',device_id:activeDevice,session,params:{...params}});session=result.session;key=result.key;if(revision===sentRevision)tell('参数已下发，等待设备确认…');}
  catch(e){active=false;tell(e.message,true);}finally{busy=false;buttons();}
}
async function stop(){const target=activeDevice;stopping=true;active=false;buttons();while(busy)await new Promise(r=>setTimeout(r,50));if(session){try{await api('/api/tuning',{action:'stop',device_id:target,session});session=null;key=null;tell('已停止调参，恢复任务灯效。');}catch(e){tell(e.message+'；会话将在失联后自动结束。',true);}}stopping=false;buttons();}
$('start').onclick=()=>{activeDevice=$('device').value;active=true;buttons();send();};$('stop').onclick=stop;$('device').onchange=()=>{session=null;key=null;buttons();};
function replace(values){params=validate(values);revision++;dirty=true;render();tell(active?'方案已载入，正在同步…':'方案已载入；点击开始实时调参。');}
$('preset').onchange=()=>{const presets={default:{},quiet:{height:10,width:.8,motion:.3,wind:0,peak:.75},lively:{motion:1.4,speed:1.3,wind:.7,flutter:1.4},working:{height:5.5,peak:.3,motion:.18,wind:0}};replace({...defaults(),...presets[$('preset').value]});};
$('reset').onclick=()=>{replace(defaults());$('preset').value='default';};
$('save').onclick=()=>{try{localStorage.setItem(storageKey,JSON.stringify(params));tell('已保存到此浏览器。');}catch(e){tell('浏览器无法保存，请导出 JSON。',true);}};
$('restore').onclick=()=>{try{const value=localStorage.getItem(storageKey);if(!value)throw Error('还没有保存的方案');replace(JSON.parse(value));}catch(e){tell(e.message,true);}};
$('export').onclick=()=>{const blob=new Blob([JSON.stringify({version:1,params},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=el('a','');link.href=url;link.download='ai-flame-parameters.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('import').onclick=()=>$('file').click();$('file').onchange=async()=>{try{const file=$('file').files[0];if(!file)return;if(file.size>16384)throw Error('文件太大，请选择导出的参数 JSON');const doc=JSON.parse(await file.text());if(doc.version!==1)throw Error('不支持的方案版本');replace(doc.params);}catch(e){tell(e.message,true);}finally{$('file').value='';}};
window.addEventListener('pagehide',()=>{active=false;if(session)fetch('/api/tuning',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'stop',device_id:activeDevice,session}),keepalive:true}).catch(()=>{});});
async function refresh(){
  const data=await api('/api/tuning');devices=data.devices;const selected=$('device').value;
  if(!fields.length){fields=data.fields;params=defaults();render();$('count').textContent=`${visibleFields().length} 项核心参数`;tell('连接硬件后，点击开始实时调参。');}
  $('device').replaceChildren(...devices.map(d=>{const option=el('option',`${d.name} · ${d.id}`);option.value=d.id;return option;}));if(devices.some(d=>d.id===selected))$('device').value=selected;
  if(active&&!devices.some(d=>d.id===activeDevice)){active=false;session=null;key=null;tell('调试设备已移除，请重新选择设备。',true);}
  const d=devices.find(d=>d.id===$('device').value);
  if(active&&!busy&&!dirty&&d?.key===key&&d.confirmed)tell('● 硬件已确认 · 正在实时呈现');
  if(active&&(!d?.online||!d?.supported)){active=false;tell(d?.error||'设备已离线，请重新连接后开始。',true);}buttons();
}
try{await refresh();setInterval(()=>{if(active&&dirty)send();},180);setInterval(()=>{if(active)send();},2000);let polling=false;setInterval(async()=>{if(polling)return;polling=true;try{await refresh();}catch(e){tell(e.message,true);}finally{polling=false;}},700);}catch(e){tell(e.message,true);}
