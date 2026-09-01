const $=id=>document.getElementById(id);
let snapshot=null,selected=null,screenedStocks=null,activeConditions=[],builderReady=false,mainControlsReady=false,conditionsHydrated=false;
let selectedIndustries=new Set(),availableIndustries=[],detailHistoryActive=false,touchStart=null,listScrollY=0;
const ACTIVE_CONDITIONS_KEY='tw-stock-mobile-conditions';
const ACTIVE_CONDITIONS_DB_KEY='active-conditions-v1';
const SAVED_STRATEGIES_KEY='tw-stock-mobile-saved-strategies-v1';
const SAVED_STRATEGIES_DB_KEY='saved-strategies-v1';
const INDUSTRIES_KEY='tw-stock-mobile-industries-v1';
const SORTS_KEY='tw-stock-mobile-sorts-v1';
const CLOUD_DATA=new URL('./data/',window.location.href).href;
const CLOUD_MANIFEST=CLOUD_DATA+'manifest.json';
const CLOUD_SNAPSHOT=CLOUD_DATA+'snapshot.json.gz';
const CLOUD_HASH_KEY='tw-stock-pwa-snapshot-sha256';
const SCREEN_STATE_KEY='tw-stock-pwa-screen-state-v1';

const SORT_SPECS=[
 ['change_desc','漲幅高 → 低'],['change_asc','跌幅大 → 小'],
 ['industry_asc','產業別 A → Z'],['industry_desc','產業別 Z → A'],
 ['capital_desc','股本大 → 小'],['capital_asc','股本小 → 大'],
 ['volume_desc','成交量大 → 小'],['volume_asc','成交量小 → 大'],
 ['price_desc','股價高 → 低'],['price_asc','股價低 → 高'],
 ['code_asc','代碼小 → 大'],['code_desc','代碼大 → 小']
];

const CONDITION_SPECS=[
 {id:'rev_consec_mom',category:'基本面',label:'最新N個月每期 MOM ≥ 門檻',params:[['n','最新月數 N','int',3],['min_pct','MOM至少(%)','float',0]]},
 {id:'rev_consec_yoy',category:'基本面',label:'最新N個月每期 YOY ≥ 門檻',params:[['n','最新月數 N','int',3],['min_pct','YOY至少(%)','float',0]]},
 {id:'rev_consec_mom_yoy',category:'基本面',label:'最新N個月 MOM與YOY 同時達標',params:[['n','最新月數 N','int',3],['min_mom','MOM至少(%)','float',0],['min_yoy','YOY至少(%)','float',0]]},
 {id:'rev_above_rolling_avg',category:'基本面',label:'近X月營收 > 前Y月平均',params:[['x','最近月數 X','int',3],['avg_window','平均月數 Y','int',12],['multiplier','門檻倍數','float',1]]},
 {id:'eps_all_positive_n',category:'基本面',label:'近N季 EPS 皆為正',params:[['n','季數 N','int',4]]},
 {id:'eps_sum_min_n',category:'基本面',label:'近N季 EPS 合計 ≥ 門檻',params:[['n','季數 N','int',4],['min_sum','EPS合計至少','float',0]]},
 {id:'eps_consec_qoq',category:'基本面',label:'連續N季 EPS 季增為正',params:[['n','連續季數 N','int',2]]},
 {id:'eps_consec_yoy',category:'基本面',label:'連續N季 EPS 年增為正',params:[['n','連續季數 N','int',2]]},
 {id:'ma_alignment',category:'技術面',label:'均線排列',params:[['direction','排列方向','choice','多頭排列',['多頭排列','空頭排列']],['line_count','均線條數','choice','3條',['3條','2條']],['ma_first','第一條天數','int',5],['ma_second','第二條天數','int',10],['ma_third','第三條天數','int',20]]},
 {id:'price_range',category:'技術面',label:'股價介於 X ~ Y 元',params:[['min_price','最低價 X','float',0],['max_price','最高價 Y','float',9999]]},
 {id:'single_day_change_pct',category:'技術面',label:'最新一日漲跌幅介於 X% ~ Y%',params:[['min_pct','最小漲跌幅','float',-3],['max_pct','最大漲跌幅','float',3]]},
 {id:'n_day_new_low',category:'技術面',label:'股價創N日新低（最低價）',params:[['n','天數 N','int',10]]},
 {id:'n_day_new_high',category:'技術面',label:'股價創N日新高（收盤價）',params:[['n','天數 N','int',10]]},
 {id:'price_vs_ma',category:'技術面',label:'股價站上／跌破 N日均線',params:[['ma_period','均線天數','int',20],['direction','方向','choice','站上',['站上','跌破']]]},
 {id:'volume_range_lots',category:'技術面',label:'今日成交量介於 X ~ Y 張',params:[['min_lots','最低張數 X','float',500],['max_lots','最高張數 Y','float',999999]]},
 {id:'avg_volume_min',category:'技術面',label:'近N日平均成交量 ≥ X 張',params:[['n','平均天數 N','int',20],['min_lots','平均量至少(張)','float',1000]]},
 {id:'volume_vs_avg',category:'技術面',label:'今日量 ≥ 前N日均量的 X 倍',params:[['n','比較天數 N','int',20],['multiplier','最少倍數 X','float',1.5]]},
 {id:'consec_volume_increase',category:'技術面',label:'連續N日成交量放大',params:[['n','連續天數 N','int',3]]},
 {id:'price_change_range',category:'技術面',label:'近N日漲跌幅介於 X% ~ Y%',params:[['n','天數 N','int',5],['min_pct','最小漲跌幅','float',-5],['max_pct','最大漲跌幅','float',5]]},
 {id:'morning_star',category:'技術面',label:'晨星反轉型態',params:[['pattern_type','晨星類型','choice','寬鬆晨星',['標準晨星','十字晨星','寬鬆晨星']],['downtrend_days','前置跌勢天數','int',5],['long_body_min_pct','長實體最小(%)','float',1]]},
 {id:'inst_buy_days_window',category:'籌碼面',label:'法人近N日買超天數 ≥ 門檻',params:[['investor','法人別','choice','投信',['外資','投信','自營商合計','自營商自行買賣','自營商避險']],['window','查詢區間(日)','int',10],['min_days','買超天數至少','int',3]]},
 {id:'inst_consec_buy',category:'籌碼面',label:'法人最近連續買超天數 ≥ 門檻',params:[['investor','法人別','choice','投信',['外資','投信','自營商合計','自營商自行買賣','自營商避險']],['min_days','連續天數至少','int',3]]},
 {id:'inst_net_shares_min',category:'籌碼面',label:'法人近N日累計買超張數 ≥ 門檻',params:[['investor','法人別','choice','投信',['外資','投信','自營商合計','自營商自行買賣','自營商避險']],['window','查詢區間(日)','int',10],['min_lots','累計買超張數至少','float',1000]]}
];

const dbOpen=()=>new Promise((ok,no)=>{const r=indexedDB.open('tw-stock-pwa',1);r.onupgradeneeded=()=>r.result.createObjectStore('data');r.onsuccess=()=>ok(r.result);r.onerror=()=>no(r.error)});
async function dbPut(key,value){const d=await dbOpen();return new Promise((ok,no)=>{const t=d.transaction('data','readwrite');t.objectStore('data').put(value,key);t.oncomplete=()=>{d.close();ok()};t.onerror=()=>no(t.error)})}
async function dbGet(key){const d=await dbOpen();return new Promise((ok,no)=>{const r=d.transaction('data').objectStore('data').get(key);r.onsuccess=()=>{d.close();ok(r.result||null)};r.onerror=()=>no(r.error)})}
async function dbSave(v){return dbPut('latest',v)}
async function dbLoad(){return dbGet('latest')}
const valid=v=>v!==null&&v!==''&&Number.isFinite(Number(v));
const num=(v,d=0)=>valid(v)?Number(v):d;
const fmt=(v,n=1)=>!valid(v)?'—':Number(v).toLocaleString('zh-TW',{minimumFractionDigits:n,maximumFractionDigits:n});
const pct=v=>`${num(v)>=0?'+':''}${num(v).toFixed(2)}%`;
const esc=x=>String(x==null?'':x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sorted=a=>(a||[]).slice().sort((x,y)=>String(x.date||'').localeCompare(String(y.date||'')));
const average=a=>a.length?a.reduce((s,v)=>s+v,0)/a.length:NaN;

function showMessage(text,good=false){$('message').textContent=text;$('message').style.background=good?'#123d34':'#4b1f2a';$('message').hidden=false;setTimeout(()=>$('message').hidden=true,7000)}
function normalise(x){
 if(!x||!Array.isArray(x.stocks))throw Error('找不到 stocks 股票清單');
 x.stocks=x.stocks.map(s=>Object.assign({},s,{stock_id:String(s.stock_id||''),name:String(s.name||''),industry:String(s.industry||'未分類'),close:num(s.close),change_pct:num(s.change_pct),volume_lots:num(s.volume_lots),capital_billion:s.capital_billion==null?null:num(s.capital_billion),price_history:sorted(s.price_history).map(b=>Object.assign({},b,{open:num(b.open),high:num(b.high),low:num(b.low),close:num(b.close),volume:num(b.volume)})),monthly_revenue:sorted(s.monthly_revenue),eps:sorted(s.eps),institutional:sorted(s.institutional)}));
 return x;
}

function readFileText(file){return new Promise((ok,no)=>{const reader=new FileReader();reader.onload=()=>ok(String(reader.result||''));reader.onerror=()=>no(reader.error||Error('無法讀取檔案'));reader.readAsText(file,'UTF-8')})}
async function importFile(file){if(!file)return;try{let text=await readFileText(file);if(text.charCodeAt(0)===0xFEFF)text=text.slice(1);snapshot=normalise(JSON.parse(text));await dbSave(snapshot);localStorage.removeItem(CLOUD_HASH_KEY);screenedStocks=null;setup();showMessage(`已匯入 ${snapshot.stocks.length} 檔股票`,true)}catch(e){showMessage(`匯入失敗：${e&&e.message?e.message:String(e)}`)}$('fileInput').value=''}

function setSyncBusy(busy){['syncTop','syncEmpty'].forEach(id=>{const b=$(id);if(!b)return;b.disabled=busy;b.textContent=busy?'正在下載…':(id==='syncTop'?'更新':'更新最新資料')})}
function setCloudStatus(text){const el=$('cloudStatus');if(el)el.textContent=text}
async function fetchNoCache(url,label){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),300000);
 try{const r=await fetch(url+(url.includes('?')?'&':'?')+'t='+Date.now(),{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error(`${label} HTTP ${r.status}`);return r}
 catch(e){if(e&&e.name==='AbortError')throw Error(`${label}逾時`);throw e}
 finally{clearTimeout(timer)}
}
async function sha256Hex(buffer){
 if(!crypto||!crypto.subtle)return '';
 const digest=await crypto.subtle.digest('SHA-256',buffer);
 return Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
}
async function decodeSnapshot(buffer){
 const bytes=new Uint8Array(buffer);
 if(bytes.length<2)throw Error('雲端快照內容不完整');
 let text;
 if(bytes[0]===0x1f&&bytes[1]===0x8b){
  if(typeof DecompressionStream!=='function')throw Error('這台 iPhone 系統版本太舊，無法解壓雲端資料');
  const stream=new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
  text=await new Response(stream).text();
 }else{text=new TextDecoder('utf-8').decode(buffer)}
 return normalise(JSON.parse(text));
}
async function checkCloudLatest(silent=false){
 if(!navigator.onLine){setCloudStatus('離線使用');if(!silent)showMessage('目前沒有網路，仍可使用上次下載的資料');return}
 setCloudStatus('檢查中…');
 try{
  const manifest=await (await fetchNoCache(CLOUD_MANIFEST,'版本檢查失敗：')).json();
  const remoteHash=String(manifest.snapshot_sha256||'');
  const localHash=localStorage.getItem(CLOUD_HASH_KEY)||'';
  if(snapshot&&remoteHash&&remoteHash===localHash){setCloudStatus(`已是最新 ${manifest.latest_trade_date||''}`.trim());if(!silent)showMessage('目前已經是最新盤後資料',true);return}
  setCloudStatus(`正在更新 ${manifest.latest_trade_date||''}`.trim());
  if(!silent)showMessage('正在下載並解壓縮最新盤後資料，第一次可能需要數分鐘…',true);
  const snapshotUrl=manifest.snapshot_file?new URL(manifest.snapshot_file,CLOUD_SNAPSHOT).href:CLOUD_SNAPSHOT;
  const buffer=await (await fetchNoCache(snapshotUrl,'快照下載失敗：')).arrayBuffer();
  if(remoteHash){const actualHash=await sha256Hex(buffer);if(actualHash&&actualHash!==remoteHash)throw Error('雲端資料校驗失敗，請稍後重試')}
  const nextSnapshot=await decodeSnapshot(buffer);
  await dbSave(nextSnapshot);
  snapshot=nextSnapshot;screenedStocks=null;
  if(remoteHash)localStorage.setItem(CLOUD_HASH_KEY,remoteHash);
  setup();if(activeConditions.length)runScreen();
  setCloudStatus(`最新 ${snapshot.latest_trade_date||''}`.trim());
  if(!silent)showMessage(`更新完成：${snapshot.latest_trade_date||'最新'}，共 ${snapshot.stocks.length} 檔`,true);
 }catch(e){setCloudStatus(snapshot?'使用上次資料':'更新失敗');if(!silent)showMessage('更新失敗：'+(e&&e.message?e.message:String(e)))}
}
async function startCloudSync(){setSyncBusy(true);try{await checkCloudLatest(false)}finally{setSyncBusy(false)}}

function setup(){
 $('empty').hidden=!!snapshot;$('dashboard').hidden=!snapshot;if(!snapshot)return;
 $('tradeDate').textContent=snapshot.latest_trade_date||latestDate(snapshot.stocks[0])||'—';$('stockCount').textContent=`${snapshot.stocks.length} 檔`;
 setCloudStatus(navigator.onLine?'本機資料':'離線使用');
 const inds=Array.from(new Set(snapshot.stocks.map(s=>s.industry))).sort((a,b)=>a.localeCompare(b,'zh-Hant'));setupMainControls(inds);
 const cond=snapshot.conditions||[];$('strategy').hidden=!cond.length;$('strategyTitle').textContent=`${snapshot.strategy_name||'本次篩選條件'} · ${cond.length} 項`;$('conditions').innerHTML=cond.map(x=>`<li>${esc(x)}</li>`).join('');
 setupConditionBuilder();render();
}
function latestDate(s){const p=s&&s.price_history||[];return p.length?p[p.length-1].date:''}

function setupMainControls(industries){
 availableIndustries=industries.slice();
 if(!mainControlsReady){
  try{const saved=JSON.parse(localStorage.getItem(INDUSTRIES_KEY)||'[]');if(Array.isArray(saved))selectedIndustries=new Set(saved.map(String))}catch(e){selectedIndustries=new Set()}
  const savedSorts=readSavedSorts();
  ['sort1','sort2','sort3'].forEach((id,i)=>{const none=i?'<option value="">不使用</option>':'',allowed=SORT_SPECS.map(x=>x[0]),wanted=savedSorts[i]||(!i?'change_desc':'');$(id).innerHTML=none+SORT_SPECS.map(x=>`<option value="${x[0]}">${x[1]}</option>`).join('');$(id).value=allowed.includes(wanted)?wanted:(!i?'change_desc':'');$(id).onchange=()=>{localStorage.setItem(SORTS_KEY,JSON.stringify(['sort1','sort2','sort3'].map(x=>$(x).value)));refreshSortAvailability();render()}});
  refreshSortAvailability();
  $('industryToggle').onclick=()=>{const panel=$('industryPanel'),open=panel.hidden;panel.hidden=!open;$('industryToggle').setAttribute('aria-expanded',String(open))};
  $('closeIndustries').onclick=()=>{$('industryPanel').hidden=true;$('industryToggle').setAttribute('aria-expanded','false')};
  $('clearIndustries').onclick=()=>{selectedIndustries.clear();saveIndustries();renderIndustryOptions(availableIndustries);render()};
  mainControlsReady=true;
 }
 selectedIndustries=new Set(Array.from(selectedIndustries).filter(x=>industries.includes(x)));
 renderIndustryOptions(industries);
}
function readSavedSorts(){try{const x=JSON.parse(localStorage.getItem(SORTS_KEY)||'[]');return Array.isArray(x)?x:[]}catch(e){return[]}}
function refreshSortAvailability(){const ids=['sort1','sort2','sort3'],values=ids.map(id=>$(id).value).filter(Boolean);ids.forEach(id=>Array.from($(id).options).forEach(o=>{o.disabled=!!o.value&&o.value!==$(id).value&&values.includes(o.value)}))}
function saveIndustries(){localStorage.setItem(INDUSTRIES_KEY,JSON.stringify(Array.from(selectedIndustries)))}
function renderIndustryOptions(industries){
 $('industryOptions').innerHTML=industries.map((name,i)=>`<label><input type="checkbox" data-industry-index="${i}"${selectedIndustries.has(name)?' checked':''}><span>${esc(name)}</span></label>`).join('');
 $('industryOptions').querySelectorAll('[data-industry-index]').forEach(box=>box.onchange=()=>{const name=industries[Number(box.dataset.industryIndex)];if(box.checked)selectedIndustries.add(name);else selectedIndustries.delete(name);saveIndustries();updateIndustrySummary();render()});
 updateIndustrySummary();
}
function updateIndustrySummary(){const n=selectedIndustries.size;$('industrySummary').textContent=n?`已選 ${n} 類`:'全部產業'}

function setupConditionBuilder(){
 if(!builderReady){
  if(!conditionsHydrated)activeConditions=loadConditions();
  const cats=Array.from(new Set(CONDITION_SPECS.map(s=>s.category)));$('conditionCategory').innerHTML=cats.map(x=>`<option>${x}</option>`).join('');
  $('conditionCategory').onchange=refreshConditionTypes;$('conditionType').onchange=refreshConditionParams;
  $('addCondition').onclick=addCondition;$('runScreen').onclick=()=>runScreen(false);$('clearConditions').onclick=()=>{activeConditions=[];screenedStocks=null;clearScreenState();saveConditions();renderActiveConditions();render()};
  $('saveStrategy').onclick=saveNamedStrategy;$('loadStrategy').onclick=loadNamedStrategy;$('deleteStrategy').onclick=deleteNamedStrategy;
  $('savedStrategy').onchange=()=>{if($('savedStrategy').value){$('strategyName').value=$('savedStrategy').value;loadNamedStrategy()}};
  builderReady=true;refreshConditionTypes();renderActiveConditions();
  refreshSavedStrategyOptions();
 }
}
function refreshConditionTypes(){const cat=$('conditionCategory').value;const specs=CONDITION_SPECS.filter(s=>s.category===cat);$('conditionType').innerHTML=specs.map(s=>`<option value="${s.id}">${esc(s.label)}</option>`).join('');refreshConditionParams()}
function selectedSpec(){return CONDITION_SPECS.find(s=>s.id===$('conditionType').value)}
function refreshConditionParams(){const spec=selectedSpec();if(!spec)return;$('conditionParams').innerHTML=spec.params.map(p=>{const [key,label,type,def,choices]=p;if(type==='choice')return `<label>${esc(label)}<select data-param="${key}">${choices.map(x=>`<option${x===def?' selected':''}>${esc(x)}</option>`).join('')}</select></label>`;return `<label>${esc(label)}<input data-param="${key}" type="number" step="${type==='int'?'1':'any'}" value="${def}"></label>`}).join('')}
function addCondition(){const spec=selectedSpec();if(!spec)return;const params={};let invalid=false;spec.params.forEach(p=>{const el=$('conditionParams').querySelector(`[data-param="${p[0]}"]`);if(p[2]==='choice')params[p[0]]=el.value;else{const v=Number(el.value);if(!Number.isFinite(v))invalid=true;params[p[0]]=p[2]==='int'?Math.trunc(v):v}});if(invalid){showMessage('條件參數必須是數字');return}activeConditions.push({id:spec.id,params});saveConditions();renderActiveConditions()}
function readLocalConditions(){try{const raw=localStorage.getItem(ACTIVE_CONDITIONS_KEY);if(raw===null)return null;const x=JSON.parse(raw);return Array.isArray(x)?x:null}catch(e){return null}}
function loadConditions(){return readLocalConditions()||[]}
async function saveConditions(){
 const copy=JSON.parse(JSON.stringify(activeConditions));
 try{localStorage.setItem(ACTIVE_CONDITIONS_KEY,JSON.stringify(copy))}catch(e){}
 await dbPut(ACTIVE_CONDITIONS_DB_KEY,copy).catch(()=>{});
}
async function hydrateActiveConditions(){
 const local=readLocalConditions(),stored=await dbGet(ACTIVE_CONDITIONS_DB_KEY).catch(()=>null),fromDb=Array.isArray(stored)?stored:[];
 activeConditions=JSON.parse(JSON.stringify(local!==null?local:fromDb));conditionsHydrated=true;
 try{localStorage.setItem(ACTIVE_CONDITIONS_KEY,JSON.stringify(activeConditions))}catch(e){}
 await dbPut(ACTIVE_CONDITIONS_DB_KEY,activeConditions).catch(()=>{});return activeConditions;
}
function readSavedStrategies(){try{const x=JSON.parse(localStorage.getItem(SAVED_STRATEGIES_KEY)||'[]');return Array.isArray(x)?x.filter(s=>s&&typeof s.name==='string'&&Array.isArray(s.conditions)):[]}catch(e){return []}}
async function writeSavedStrategies(items){
 let localError=null,dbError=null;
 try{localStorage.setItem(SAVED_STRATEGIES_KEY,JSON.stringify(items))}catch(e){localError=e}
 try{await dbPut(SAVED_STRATEGIES_DB_KEY,items)}catch(e){dbError=e}
 if(localError&&dbError)throw Error('瀏覽器拒絕儲存策略，請確認不是無痕模式且網站儲存空間未被停用');
 return{local:!localError,indexedDb:!dbError};
}
async function hydrateSavedStrategies(){
 const local=readSavedStrategies(),stored=await dbGet(SAVED_STRATEGIES_DB_KEY).catch(()=>null),fromDb=Array.isArray(stored)?stored.filter(s=>s&&typeof s.name==='string'&&Array.isArray(s.conditions)):[],byName=new Map();
 local.concat(fromDb).forEach(item=>{const old=byName.get(item.name);if(!old||String(item.updated_at||'')>=String(old.updated_at||''))byName.set(item.name,item)});
 const merged=Array.from(byName.values());try{localStorage.setItem(SAVED_STRATEGIES_KEY,JSON.stringify(merged))}catch(e){}await dbPut(SAVED_STRATEGIES_DB_KEY,merged).catch(()=>{});return merged;
}
function refreshSavedStrategyOptions(selectedName=''){
 const items=readSavedStrategies().sort((a,b)=>a.name.localeCompare(b.name,'zh-Hant'));
 $('savedStrategy').innerHTML='<option value="">選擇已儲存策略</option>'+items.map(s=>`<option value="${esc(s.name)}">${esc(s.name)}（${s.conditions.length}項）</option>`).join('');
 if(items.some(s=>s.name===selectedName))$('savedStrategy').value=selectedName;
}
async function saveNamedStrategy(){
 const name=$('strategyName').value.trim();if(!name){showMessage('請先輸入策略名稱');return}if(!activeConditions.length){showMessage('請先加入至少一個篩選條件');return}
 const items=await hydrateSavedStrategies(),copy=JSON.parse(JSON.stringify(activeConditions)),index=items.findIndex(s=>s.name===name);
 const item={name,conditions:copy,updated_at:new Date().toISOString()};if(index>=0)items[index]=item;else items.push(item);
 try{await writeSavedStrategies(items);await hydrateSavedStrategies();refreshSavedStrategyOptions(name);showMessage(`策略「${name}」已儲存並驗證，共 ${copy.length} 項條件`,true)}catch(e){showMessage('策略儲存失敗：'+(e&&e.message?e.message:String(e)))}
}
async function loadNamedStrategy(){
 const name=$('savedStrategy').value;if(!name){showMessage('請先選擇要載入的策略');return}
 await hydrateSavedStrategies();const item=readSavedStrategies().find(s=>s.name===name);if(!item){showMessage('找不到這個策略');refreshSavedStrategyOptions();return}
 const restored=JSON.parse(JSON.stringify(item.conditions)).filter(c=>c&&CONDITION_SPECS.some(s=>s.id===c.id));
 if(!restored.length){showMessage(`策略「${name}」沒有可辨識的條件，已保留目前設定`);return}
 activeConditions=restored;screenedStocks=null;await saveConditions();renderActiveConditions();$('strategyName').value=name;refreshSavedStrategyOptions(name);runScreen();showMessage(`已載入策略「${name}」，共 ${restored.length} 項條件`,true);
}
async function deleteNamedStrategy(){
 const name=$('savedStrategy').value;if(!name){showMessage('請先選擇要刪除的策略');return}if(!confirm(`確定刪除策略「${name}」？`))return;
 try{const items=await hydrateSavedStrategies();await writeSavedStrategies(items.filter(s=>s.name!==name));$('strategyName').value='';refreshSavedStrategyOptions();showMessage(`策略「${name}」已刪除`,true)}catch(e){showMessage('策略刪除失敗：'+(e&&e.message?e.message:String(e)))}
}
function conditionSummary(c){const s=CONDITION_SPECS.find(x=>x.id===c.id);return s?`[${s.category}] ${s.label}（${s.params.map(p=>`${p[1]}=${c.params[p[0]]}`).join('、')}）`:c.id}
function renderActiveConditions(){$('activeConditions').innerHTML=activeConditions.map((c,i)=>`<li><span>${esc(conditionSummary(c))}</span><button data-remove="${i}">×</button></li>`).join('')||'<li><span>尚未加入條件；不加條件會顯示全部股票</span></li>';$('activeConditions').querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>{activeConditions.splice(Number(b.dataset.remove),1);saveConditions();renderActiveConditions()})}

function runScreen(silent=false){
 if(!snapshot)return;const result=[];
 snapshot.stocks.forEach(s=>{const details=[];let ok=true;for(const c of activeConditions){const r=evaluateCondition(s,c);const spec=CONDITION_SPECS.find(x=>x.id===c.id);details.push(`${r.ok?'✓':'✗'} ${spec?spec.label:c.id}：${r.detail}`);if(!r.ok){ok=false;break}}if(ok){s._screenDetails=details;s.match=activeConditions.length?`${activeConditions.length}/${activeConditions.length}`:'—';result.push(s)}});
 screenedStocks=result;render();saveScreenState(window.scrollY||listScrollY);if(!silent)showMessage(`篩選完成：${result.length} / ${snapshot.stocks.length} 檔符合`,true);
}

function clearScreenState(){try{sessionStorage.removeItem(SCREEN_STATE_KEY)}catch(e){}}
function saveScreenState(scrollY=listScrollY){
 if(!snapshot||screenedStocks===null)return;
 try{sessionStorage.setItem(SCREEN_STATE_KEY,JSON.stringify({tradeDate:snapshot.latest_trade_date||'',ids:screenedStocks.map(s=>s.stock_id),scrollY:Math.max(0,Number(scrollY)||0)}))}catch(e){}
}
function restoreScreenState(){
 if(!snapshot)return false;let state=null;try{state=JSON.parse(sessionStorage.getItem(SCREEN_STATE_KEY)||'null')}catch(e){}
 if(!state||!Array.isArray(state.ids))return false;
 const byId=new Map(snapshot.stocks.map(s=>[s.stock_id,s]));screenedStocks=state.ids.map(id=>byId.get(String(id))).filter(Boolean);listScrollY=Math.max(0,Number(state.scrollY)||0);render();requestAnimationFrame(()=>window.scrollTo(0,listScrollY));return true;
}

function evaluateCondition(s,c){
 const p=c.params||{},prices=sorted(s.price_history),rev=sorted(s.monthly_revenue),eps=sorted(s.eps),chip=sorted(s.institutional);
 const fail=detail=>({ok:false,detail}),pass=(ok,detail)=>({ok:!!ok,detail});
 if(c.id.startsWith('rev_')){
  if(!rev.length)return fail('月營收資料不足');
  if(c.id==='rev_above_rolling_avg'){const x=Math.max(1,num(p.x)),y=Math.max(1,num(p.avg_window)),m=num(p.multiplier,1);if(rev.length<x+y)return fail(`至少需要${x+y}個月`);let ok=true,ds=[];for(let i=rev.length-x;i<rev.length;i++){const base=rev.slice(i-y,i).map(r=>num(r.revenue));const av=average(base),cur=num(rev[i].revenue);const hit=cur>av*m;ok=ok&&hit;ds.push(`${String(rev[i].date).slice(0,7)} ${hit?'✓':'✗'}`)}return pass(ok,ds.join('、'))}
  const n=Math.max(1,num(p.n));const tail=latestConsecutiveMonths(rev,n);if(!tail)return fail(`最新${n}期不足或月份不連續`);
  if(c.id==='rev_consec_mom'){const ok=tail.every(r=>valid(r.mom_pct)&&num(r.mom_pct)>=num(p.min_pct));return pass(ok,tail.map(r=>`${String(r.date).slice(0,7)}=${fmt(r.mom_pct,2)}%`).join('、'))}
  if(c.id==='rev_consec_yoy'){const ok=tail.every(r=>valid(r.yoy_pct)&&num(r.yoy_pct)>=num(p.min_pct));return pass(ok,tail.map(r=>`${String(r.date).slice(0,7)}=${fmt(r.yoy_pct,2)}%`).join('、'))}
  const ok=tail.every(r=>valid(r.mom_pct)&&valid(r.yoy_pct)&&num(r.mom_pct)>=num(p.min_mom)&&num(r.yoy_pct)>=num(p.min_yoy));return pass(ok,tail.map(r=>`${String(r.date).slice(0,7)} M${fmt(r.mom_pct,1)} / Y${fmt(r.yoy_pct,1)}`).join('、'));
 }
 if(c.id.startsWith('eps_')){
  const quarterly=eps.filter(x=>x.kind!=='cumulative'),values=quarterly.map(x=>num(x.value)),n=Math.max(1,num(p.n));if(!values.length&&eps.some(x=>x.kind==='cumulative'))return fail('目前只有官方累計EPS，單季歷史尚待補齊');if(c.id==='eps_consec_yoy'&&values.length<n+4)return fail('EPS資料不足');if(c.id!=='eps_consec_yoy'&&c.id==='eps_consec_qoq'&&values.length<n+1)return fail('EPS資料不足');if(values.length<n)return fail('EPS資料不足');
  if(c.id==='eps_all_positive_n'){const a=values.slice(-n);return pass(a.every(v=>v>0),a.map(v=>v.toFixed(2)).join('、'))}
  if(c.id==='eps_sum_min_n'){const total=values.slice(-n).reduce((a,b)=>a+b,0);return pass(total>=num(p.min_sum),`合計 ${total.toFixed(2)}`)}
  if(c.id==='eps_consec_qoq'){let ok=true;for(let i=values.length-n;i<values.length;i++)ok=ok&&values[i]>values[i-1];return pass(ok,`檢查最近 ${n} 季`)}
  let ok=true;for(let i=values.length-n;i<values.length;i++)ok=ok&&values[i]>values[i-4];return pass(ok,`同季比較最近 ${n} 季`);
 }
 if(c.id.startsWith('inst_')){
  const key={'外資':'foreign_net_lots','投信':'trust_net_lots','自營商合計':'dealer_net_lots','自營商自行買賣':'dealer_self_net_lots','自營商避險':'dealer_hedge_net_lots'}[p.investor]||'trust_net_lots';if(!chip.length)return fail('法人資料不足');
  if(c.id==='inst_buy_days_window'){const a=chip.slice(-Math.max(1,num(p.window))),days=a.filter(x=>num(x[key])>0).length;return pass(days>=num(p.min_days),`買超 ${days} 天`)}
  if(c.id==='inst_consec_buy'){let days=0;for(let i=chip.length-1;i>=0&&num(chip[i][key])>0;i--)days++;return pass(days>=num(p.min_days),`連續買超 ${days} 天`)}
  const a=chip.slice(-Math.max(1,num(p.window))),total=a.reduce((sum,x)=>sum+num(x[key]),0);return pass(total>=num(p.min_lots),`累計 ${total.toFixed(0)} 張`);
 }
 if(!prices.length)return fail('股價資料不足');
 const last=prices[prices.length-1];
 if(c.id==='price_range')return pass(last.close>=num(p.min_price)&&last.close<=num(p.max_price),`收盤 ${last.close.toFixed(2)}`);
 if(c.id==='single_day_change_pct'){if(prices.length<2)return fail('至少需要2日');const v=(last.close/prices[prices.length-2].close-1)*100;return pass(v>=num(p.min_pct)&&v<=num(p.max_pct),`${v.toFixed(2)}%`)}
 if(c.id==='volume_range_lots'){const lots=valid(s.volume_lots)?num(s.volume_lots):num(last.volume)/1000;return pass(lots>=num(p.min_lots)&&lots<=num(p.max_lots),`今日 ${lots.toFixed(0)} 張`)}
 if(c.id==='avg_volume_min'){const n=Math.max(1,num(p.n));if(prices.length<n)return fail(`至少需要${n}日`);const avg=average(prices.slice(-n).map(x=>num(x.volume)/1000));return pass(avg>=num(p.min_lots),`${n}日均量 ${avg.toFixed(0)} 張`)}
 if(c.id==='volume_vs_avg'){const n=Math.max(1,num(p.n));if(prices.length<n+1)return fail(`至少需要${n+1}日`);const avg=average(prices.slice(-n-1,-1).map(x=>num(x.volume)/1000)),today=num(last.volume)/1000;if(avg<=0)return fail('前期均量為0');const ratio=today/avg;return pass(ratio>=num(p.multiplier),`今日 ${today.toFixed(0)} 張 / 前${n}日均量 ${avg.toFixed(0)} 張 = ${ratio.toFixed(2)}倍`)}
 if(c.id==='n_day_new_low'){const n=Math.max(1,num(p.n));if(prices.length<n+1)return fail(`至少需要${n+1}日`);const prior=Math.min(...prices.slice(-n-1,-1).map(x=>x.low));return pass(last.low<prior,`前${n}日最低 ${prior.toFixed(2)}，今日 ${last.low.toFixed(2)}`)}
 if(c.id==='n_day_new_high'){const n=Math.max(1,num(p.n));if(prices.length<n)return fail('股價資料不足');const high=Math.max(...prices.slice(-n).map(x=>x.close));return pass(last.close>=high-1e-9,`近${n}日最高收盤 ${high.toFixed(2)}`)}
 if(c.id==='price_vs_ma'){const n=Math.max(1,num(p.ma_period)),ma=movingAverage(prices,n);if(!valid(ma))return fail('均線資料不足');const above=last.close>ma;return pass(p.direction==='站上'?above:!above,`收盤 ${last.close.toFixed(2)} / MA${n} ${ma.toFixed(2)}`)}
 if(c.id==='ma_alignment'){const periods=[num(p.ma_first),num(p.ma_second)];if(p.line_count==='3條')periods.push(num(p.ma_third));if(new Set(periods).size!==periods.length||periods.some(x=>x<=0))return fail('均線天數錯誤或重複');const mas=periods.map(n=>movingAverage(prices,n));if(mas.some(x=>!valid(x)))return fail('均線資料不足');const ok=mas.slice(1).every((v,i)=>p.direction==='多頭排列'?mas[i]>v:mas[i]<v);return pass(ok,periods.map((n,i)=>`MA${n}=${mas[i].toFixed(2)}`).join('、'))}
 if(c.id==='consec_volume_increase'){const n=Math.max(1,num(p.n));if(prices.length<n)return fail('成交量資料不足');const a=prices.slice(-n).map(x=>x.volume);return pass(a.slice(1).every((v,i)=>v>a[i]),a.map(v=>(v/1000).toFixed(1)+'張').join('、'))}
 if(c.id==='price_change_range'){const n=Math.max(1,num(p.n));if(prices.length<n+1)return fail('股價資料不足');const v=(last.close/prices[prices.length-n-1].close-1)*100;return pass(v>=num(p.min_pct)&&v<=num(p.max_pct),`${v.toFixed(2)}%`)}
 if(c.id==='morning_star')return evaluateMorningStar(prices,p);
 return fail('尚未支援此條件');
}

function latestConsecutiveMonths(rev,n){if(rev.length<n)return null;const a=rev.slice(-n);for(let i=1;i<a.length;i++){const prev=monthIndex(a[i-1]),cur=monthIndex(a[i]);if(cur-prev!==1)return null}return a}
function monthIndex(r){if(valid(r.revenue_year)&&valid(r.revenue_month))return num(r.revenue_year)*12+num(r.revenue_month);const m=String(r.date||'').match(/(\d{4})-(\d{1,2})/);return m?Number(m[1])*12+Number(m[2]):NaN}
function movingAverage(prices,n){n=Math.trunc(n);if(prices.length<n||n<=0)return NaN;return average(prices.slice(-n).map(x=>x.close))}
function evaluateMorningStar(d,p){const days=Math.max(1,num(p.downtrend_days)),need=days+3;if(d.length<need)return{ok:false,detail:`至少需要${need}日`};const a=d[d.length-3],star=d[d.length-2],c=d[d.length-1],prior=d[d.length-(days+3)],ab=Math.abs(a.open-a.close),sb=Math.abs(star.open-star.close),cb=Math.abs(c.open-c.close),ar=Math.max(star.high-star.low,1e-9),ratio=ab?sb/ab:Infinity;const common=a.close<prior.close&&a.close<a.open&&ab/a.close*100>=num(p.long_body_min_pct)&&c.close>c.open&&cb/c.open*100>=num(p.long_body_min_pct)&&c.close>=(a.open+a.close)/2,gap=Math.max(star.open,star.close)<=a.close*1.005,doji=sb/ar<=.1;let ok;if(p.pattern_type==='十字晨星')ok=common&&gap&&doji&&ratio<=.2;else if(p.pattern_type==='寬鬆晨星')ok=common&&ratio<=.65;else ok=common&&gap&&ratio<=.5;return{ok,detail:`星體/首根=${ratio.toFixed(2)}，第三根收 ${c.close.toFixed(2)}`}}

function compareStocks(key,a,b){
 const text=(x,y)=>String(x).localeCompare(String(y),'zh-Hant',{numeric:true});
 const number=(x,y)=>num(x,-Infinity)-num(y,-Infinity);
 const cmp={change_desc:()=>number(b.change_pct,a.change_pct),change_asc:()=>number(a.change_pct,b.change_pct),industry_asc:()=>text(a.industry,b.industry),industry_desc:()=>text(b.industry,a.industry),capital_desc:()=>number(b.capital_billion,a.capital_billion),capital_asc:()=>number(a.capital_billion,b.capital_billion),volume_desc:()=>number(b.volume_lots,a.volume_lots),volume_asc:()=>number(a.volume_lots,b.volume_lots),price_desc:()=>number(b.close,a.close),price_asc:()=>number(a.close,b.close),code_asc:()=>text(a.stock_id,b.stock_id),code_desc:()=>text(b.stock_id,a.stock_id)};
 return cmp[key]?cmp[key]():0;
}
function quarterLabel(date){
 const text=String(date||''),m=text.match(/^(\d{4})-(\d{2})/);if(!m)return text||'—';
 const q=Math.max(1,Math.min(4,Math.ceil(Number(m[2])/3)));return `${m[1]} Q${q}`;
}
function moneyInBillion(value){return valid(value)?fmt(num(value)/100000000,2):'—'}
function renderFinancialHealth(health){
 health=health&&typeof health==='object'?health:{};
 const coverage=health.coverage||{},highlights=Array.isArray(health.highlights)?health.highlights:[],risks=Array.isArray(health.risks)?health.risks:[],metrics=Array.isArray(health.metrics)?health.metrics:[],quarters=Array.isArray(health.quarters)?health.quarters:[];
 $('financialLatest').textContent=health.latest_date?`最新季報 ${quarterLabel(health.latest_date)}`:'尚無完整季報';
 const coverageItems=[['income','損益表','income_quarters'],['balance','資產負債表','balance_quarters'],['cashflow','現金流量表','cashflow_quarters']];
 $('financialCoverage').innerHTML=coverageItems.map(([key,label,count])=>`<span class="${coverage[key]?'ok':'missing'}">${coverage[key]?'✓':'—'} ${label}${coverage[key]&&valid(coverage[count])?` ${num(coverage[count])}季`:''}</span>`).join('');
 const signalHtml=(items,empty)=>items.length?items.map(item=>`<li><b>${esc(item.title||'')}</b><span>${esc(item.detail||'')}</span></li>`).join(''):`<li class="empty-signal">${esc(empty)}</li>`;
 $('highlightCount').textContent=String(highlights.length);$('riskCount').textContent=String(risks.length);
 $('financialHighlights').innerHTML=signalHtml(highlights,'目前沒有符合通用亮點門檻，或資料仍不足。');
 $('financialRisks').innerHTML=signalHtml(risks,'目前沒有觸發通用風險門檻；不代表完全沒有風險。');
 $('financialMetrics').innerHTML=metrics.length?metrics.map(item=>`<div><span>${esc(item.label||'')}</span><b>${fmt(item.value,item.unit==='元'?2:1)}${item.unit?` <small>${esc(item.unit)}</small>`:''}</b></div>`).join(''):'<div class="financial-empty">更新新版雲端資料後，這裡會顯示財務指標。</div>';
 $('financialRows').innerHTML=quarters.slice().reverse().map(row=>`<tr><td>${esc(quarterLabel(row.date))}</td><td>${fmt(row.eps,2)}</td><td>${moneyInBillion(row.revenue)}</td><td>${valid(row.gross_margin_pct)?fmt(row.gross_margin_pct,1)+'%':'—'}</td><td>${valid(row.operating_margin_pct)?fmt(row.operating_margin_pct,1)+'%':'—'}</td><td>${valid(row.debt_ratio_pct)?fmt(row.debt_ratio_pct,1)+'%':'—'}</td><td>${moneyInBillion(row.operating_cash_flow)}</td></tr>`).join('')||'<tr><td colspan="7">尚無季度財務明細，請等待下一次雲端更新。</td></tr>';
 $('financialNote').textContent=health.calculation_note||'亮點與風險採通用規則計算，資料不足時不下判斷；提示僅供研究，不是投資建議。';
}
function renderEps(items){
 const ordered=sorted(items),quarterly=ordered.filter(x=>x.kind!=='cumulative');
 const values=(quarterly.length?quarterly.slice(-4):ordered.filter(x=>x.kind==='cumulative').slice(-1)).reverse();
 $('epsTitle').textContent=quarterly.length>=4?'近四季單季 EPS':quarterly.length?`單季 EPS（目前 ${quarterly.length} 季）`:'EPS';
 $('eps').innerHTML=values.map(x=>`<div><span>${esc(x.date)} · ${esc(x.label||(x.kind==='cumulative'?'本年度累計':'單季'))}</span><b>${num(x.value).toFixed(2)}</b></div>`).join('')||(ordered.some(x=>x.kind==='cumulative')?'<div>歷史單季 EPS 補抓中，目前只有本年度累計</div>':'<div>沒有 EPS 資料</div>');
}
function render(){
 if(!snapshot)return;
 const q=$('search').value.trim().toLowerCase(),sorts=['sort1','sort2','sort3'].map(id=>$(id).value).filter((x,i,a)=>x&&a.indexOf(x)===i);
 let rows=(screenedStocks===null?snapshot.stocks:screenedStocks).filter(s=>(!q||`${s.stock_id} ${s.name}`.toLowerCase().includes(q))&&(!selectedIndustries.size||selectedIndustries.has(s.industry)));
 rows.sort((a,b)=>{for(const key of sorts){const result=compareStocks(key,a,b);if(result)return result}return compareStocks('code_asc',a,b)});
 $('visibleCount').textContent=`符合 ${rows.length} 檔`;
 $('stockList').innerHTML=rows.map(s=>`<button class="stock-card" data-id="${esc(s.stock_id)}"><div class="identity"><span class="code">${esc(s.stock_id)}</span><b>${esc(s.name)}</b><small>${esc(s.industry)}</small></div><div class="quote"><b>${s.close.toFixed(2)}</b><span class="change ${s.change_pct>=0?'up':'down'}">${pct(s.change_pct)}</span></div><div class="card-metrics"><span>股本<b>${fmt(s.capital_billion,2)} 億</b></span><span>成交量<b>${fmt(s.volume_lots,1)} 張</b></span><span>符合<b>${esc(s.match||'—')}</b></span></div></button>`).join('')||'<div class="empty-state">目前沒有符合股票</div>';
 $('stockList').querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>openDetail(rows.find(s=>s.stock_id===b.dataset.id)));
}
function openDetail(s){
 if(!s)return;listScrollY=window.scrollY||0;saveScreenState(listScrollY);selected=s;$('detail').hidden=false;document.body.style.top=`-${listScrollY}px`;document.body.style.position='fixed';document.body.style.width='100%';document.body.classList.add('detail-open');$('detailPanel').scrollTop=0;
 if(!detailHistoryActive){history.pushState({stockDetail:s.stock_id},'',`#stock-${encodeURIComponent(s.stock_id)}`);detailHistoryActive=true}
 $('detailCode').textContent=s.stock_id;$('detailName').textContent=s.name;$('detailIndustry').textContent=s.industry;$('detailClose').textContent=s.close.toFixed(2);$('detailChange').textContent=pct(s.change_pct);$('detailChange').className=s.change_pct>=0?'up':'down';$('detailCapital').textContent=`${fmt(s.capital_billion,2)} 億`;$('detailVolume').textContent=`${fmt(s.volume_lots,1)} 張`;$('detailMatch').textContent=s.match||'—';renderFinancialHealth(s.financial_health);renderEps(s.eps||[]);$('detailConditions').innerHTML=((s._screenDetails&&s._screenDetails.length?s._screenDetails:s.details)||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>沒有條件明細</li>';renderChip(s.institutional||[]);requestAnimationFrame(()=>{drawCandle();drawRevenue()});
}
function hideDetail(){if($('detail').hidden)return;const restoreY=listScrollY;$('detail').hidden=true;document.body.classList.remove('detail-open');document.body.style.position='';document.body.style.top='';document.body.style.width='';selected=null;detailHistoryActive=false;touchStart=null;requestAnimationFrame(()=>window.scrollTo(0,restoreY))}
function requestCloseDetail(){if(detailHistoryActive&&history.state&&history.state.stockDetail)history.back();else hideDetail()}
function tab(name){document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.id===`tab-${name}`));if(name==='price')setTimeout(drawCandle,20);if(name==='fund')setTimeout(drawRevenue,20)}
function canvas(id,h=340){const c=$(id),d=devicePixelRatio||1,w=c.clientWidth||700;c.width=w*d;c.height=h*d;const x=c.getContext('2d');x.scale(d,d);return{x,w,h}}
function drawCandle(){if(!selected)return;const d=(selected.price_history||[]).slice(-num($('days').value,180));if(d.length<2)return;const {x,w,h}=canvas('candle'),p={l:48,r:12,t:18,b:28},lo=Math.min(...d.map(b=>b.low)),hi=Math.max(...d.map(b=>b.high)),span=Math.max(hi-lo,.01),iw=w-p.l-p.r,ih=h-p.t-p.b,step=iw/d.length,Y=v=>p.t+(hi-v)/span*ih;x.clearRect(0,0,w,h);x.font='10px sans-serif';for(let i=0;i<5;i++){const v=hi-span*i/4;x.strokeStyle='#31505b';x.setLineDash([4,5]);x.beginPath();x.moveTo(p.l,Y(v));x.lineTo(w-p.r,Y(v));x.stroke();x.fillStyle='#829aa7';x.fillText(v.toFixed(1),5,Y(v)+3)}x.setLineDash([]);d.forEach((b,i)=>{const X=p.l+step*i+step/2,up=b.close>=b.open;x.strokeStyle=x.fillStyle=up?'#ff6374':'#55b6ff';x.beginPath();x.moveTo(X,Y(b.high));x.lineTo(X,Y(b.low));x.stroke();const top=Y(Math.max(b.open,b.close)),bottom=Y(Math.min(b.open,b.close)),bw=Math.max(1,Math.min(6,step*.65));x.fillRect(X-bw/2,top,bw,Math.max(bottom-top,1))})}
function drawRevenue(){
 if(!selected)return;
 const d=(selected.monthly_revenue||[]).slice(-12),{x,w,h}=canvas('revenue'),pad={l:52,r:12,t:18,b:34};x.clearRect(0,0,w,h);
 $('revenueRows').innerHTML=d.slice().reverse().map(p=>`<tr><td>${esc(String(p.date||'').slice(0,7))}</td><td class="${num(p.mom_pct)>=0?'up':'down'}">${pct(p.mom_pct)}</td><td class="${num(p.yoy_pct)>=0?'up':'down'}">${pct(p.yoy_pct)}</td></tr>`).join('')||'<tr><td colspan="3">沒有月營收資料</td></tr>';
 if(!d.length){x.fillStyle='#91a7b5';x.font='13px sans-serif';x.fillText('沒有月營收資料',20,30);return}
 const rawMax=Math.max(10,...d.flatMap(p=>[Math.abs(num(p.mom_pct)),Math.abs(num(p.yoy_pct))])),max=Math.ceil(rawMax/10)*10,innerH=h-pad.t-pad.b,mid=pad.t+innerH/2,scale=innerH/(max*2),group=(w-pad.l-pad.r)/d.length;
 x.font='10px sans-serif';x.textAlign='right';x.textBaseline='middle';
 for(let i=0;i<=4;i++){const value=max-i*(max/2),Y=pad.t+i*(innerH/4);x.strokeStyle=value===0?'#78909c':'#31505b';x.setLineDash(value===0?[]:[4,5]);x.beginPath();x.moveTo(pad.l,Y);x.lineTo(w-pad.r,Y);x.stroke();x.fillStyle='#9ab0bb';x.fillText(`${value.toFixed(0)}%`,pad.l-6,Y)}
 x.setLineDash([]);x.textAlign='center';x.textBaseline='alphabetic';
 d.forEach((p,i)=>{const center=pad.l+group*(i+.5),bar=Math.max(2,Math.min(12,group*.28)),m=Math.max(-max,Math.min(max,num(p.mom_pct))),y=Math.max(-max,Math.min(max,num(p.yoy_pct)));x.fillStyle='#26c5aa';x.fillRect(center-bar-1,m>=0?mid-m*scale:mid,bar,Math.max(Math.abs(m*scale),1));x.fillStyle='#efb65a';x.fillRect(center+1,y>=0?mid-y*scale:mid,bar,Math.max(Math.abs(y*scale),1));x.fillStyle='#9ab0bb';x.font='9px sans-serif';x.fillText(String(p.date||'').slice(5,7),center,h-12)});
}
function renderChip(points){const d=points.slice(-20).reverse();$('chip').innerHTML=d.length?`<table class="chip-table"><thead><tr><th>日期</th><th>外資</th><th>投信</th><th>自營商</th></tr></thead><tbody>${d.map(p=>`<tr><td>${esc(p.date).slice(5)}</td><td>${fmt(p.foreign_net_lots,0)}</td><td>${fmt(p.trust_net_lots,0)}</td><td>${fmt(p.dealer_net_lots,0)}</td></tr>`).join('')}</tbody></table>`:'沒有法人資料'}

['importTop','importEmpty'].forEach(id=>$(id).onclick=()=>$('fileInput').click());
['syncTop','syncEmpty'].forEach(id=>$(id).onclick=startCloudSync);
$('fileInput').onchange=e=>importFile(e.target.files[0]);
$('search').addEventListener('input',render);
$('backDetail').onclick=requestCloseDetail;$('closeDetail').onclick=requestCloseDetail;$('days').onchange=drawCandle;
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.tab));$('detail').onclick=e=>{if(e.target===$('detail'))$('closeDetail').click()};
$('detailPanel').addEventListener('touchstart',e=>{const t=e.touches[0];touchStart=t&&t.clientX<=70?{x:t.clientX,y:t.clientY}:null},{passive:true});
$('detailPanel').addEventListener('touchend',e=>{if(!touchStart)return;const t=e.changedTouches[0],dx=t.clientX-touchStart.x,dy=Math.abs(t.clientY-touchStart.y);touchStart=null;if(dx>=85&&dy<=70&&dx>dy*1.4)requestCloseDetail()},{passive:true});
if('scrollRestoration'in history)history.scrollRestoration='manual';
window.addEventListener('popstate',e=>{if(!$('detail').hidden){hideDetail();return}if(e.state&&e.state.stockDetail&&snapshot){const s=snapshot.stocks.find(x=>x.stock_id===String(e.state.stockDetail));if(s){detailHistoryActive=true;openDetail(s)}}else restoreScreenState()});
function setupPwa(){
 if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});
 const standalone=window.matchMedia('(display-mode: standalone)').matches||window.navigator.standalone===true;
 const isiOS=/iphone|ipad|ipod/i.test(navigator.userAgent);
 const dismissed=localStorage.getItem('tw-stock-pwa-install-dismissed')==='1';
 if(isiOS&&!standalone&&!dismissed)$('installHint').hidden=false;
 $('dismissInstall').onclick=()=>{$('installHint').hidden=true;localStorage.setItem('tw-stock-pwa-install-dismissed','1')};
 const updateOnlineState=()=>{$('offlineHint').hidden=navigator.onLine;if(!navigator.onLine)setCloudStatus('離線使用')};
 window.addEventListener('online',updateOnlineState);window.addEventListener('offline',updateOnlineState);updateOnlineState();
}
setupPwa();
dbLoad().then(async v=>{await Promise.all([hydrateSavedStrategies().catch(()=>{}),hydrateActiveConditions().catch(()=>{})]);snapshot=v;setup();restoreScreenState();if(navigator.onLine)setTimeout(()=>checkCloudLatest(true),800)}).catch(async()=>{await Promise.all([hydrateSavedStrategies().catch(()=>{}),hydrateActiveConditions().catch(()=>{})]);setup();restoreScreenState();if(navigator.onLine)setTimeout(()=>checkCloudLatest(true),800)});
