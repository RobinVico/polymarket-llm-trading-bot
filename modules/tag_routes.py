"""
/tags 标签 + 市场扫描中心 (注册在 register_tag_routes(app), 不污染 dashboard.py).

把首页那套市场扫描器整套搬过来 (用户 2026-06-22): 🔍市场扫描器(tab/kw扫/tag扫/一键全扫/复制/报告) → 🧲动态采纳表 → 🔥今日热门 → 🚫黑名单 → 📅规则.
tier 重排: T1/T2 不变, **T3=热门(动态采纳的, chip)**, T4=原T3过度自信, T5=原T4长尾; T4/5 折叠且一键全扫不含.
后端对得上: doScanAll 传 tiers:[1,2], scan_all_tags 合并动态 → 扫 T1+T2+热门; 原T3/4(tags.py tier3/4) 不在 filter 不扫.
复用路由: /api/scan_all(+status) · /api/control(scan/scan_tag) · /api/scan_report · /api/full_prompt · /api/tags/*.
"""
from flask import jsonify, request as flask_request

TAGS_HTML = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>标签 / 扫描</title><style>
:root{--bg:#0a0a14;--sf:#15151f;--sf2:#1d1d2c;--bd:#2a2a40;--bd2:#2a2a40;--tx:#e8e8f4;--tx2:#a8a8c8;--tx3:#6a6a98;
--g:#00e5a0;--r:#ff4070;--c:#00c8ff;--p:#8a6cff;--y:#ffc040;
--ac:#00e5a0;--ac2:#00c8ff;--vi:#8a6cff;--am:#ffc040;--rd:#ff4070;--acd:rgba(0,229,160,.12);--rdd:rgba(255,64,112,.12)}
*{box-sizing:border-box}body{background:var(--bg);color:var(--tx);font-family:-apple-system,'Segoe UI',sans-serif;font-size:13px;margin:0;padding:0 0 50px;-webkit-font-smoothing:antialiased}
.nav{display:flex;align-items:center;gap:6px;padding:12px 20px;border-bottom:1px solid var(--bd);background:var(--sf);position:sticky;top:0;z-index:10}
.nav .logo{font-weight:800;font-size:15px;margin-right:10px;background:linear-gradient(90deg,var(--g),var(--c));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.nav a{color:var(--tx2);text-decoration:none;font-size:12px;padding:4px 10px;border-radius:6px}.nav a.active{background:var(--sf2);color:var(--tx)}
.wrap{max-width:1000px;margin:0 auto;padding:18px 20px}
.sec{background:var(--sf);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin-bottom:18px}
.sec h2{margin:0 0 4px;font-size:15px}.sec .sub{color:var(--tx3);font-size:11px;margin-bottom:12px;line-height:1.5}
.sec.scan{border-color:rgba(0,229,160,.3);padding:0;overflow:hidden}.sec.hot{border-color:rgba(255,192,64,.35)}
.scanhd{padding:14px 18px 6px;font-size:15px;font-weight:700}
.tabs{padding:10px 18px 0;display:flex;gap:4px;border-bottom:1px solid var(--bd)}
.tab{background:none;border:none;color:var(--tx3);font-size:12px;padding:7px 14px;cursor:pointer;border-bottom:2px solid transparent;border-radius:6px 6px 0 0}
.tab.tab-active{color:var(--tx);border-bottom-color:var(--ac)}
.tab-panel{padding-bottom:4px}
.btn{background:var(--sf2);border:1px solid var(--bd);color:var(--tx2);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer}
.btn:hover{filter:brightness(1.3)}.btn:disabled{opacity:.5;cursor:default}
.btn-primary{background:linear-gradient(135deg,var(--g),var(--c));color:#04120c;border:none;font-weight:700}
.btn-g{border-color:var(--g);color:var(--g)}.btn-r{border-color:var(--r);color:var(--r)}
.chip{font-family:'JetBrains Mono',monospace;font-size:10px;padding:4px 9px;background:var(--sf);color:var(--tx2);border:1px solid var(--bd);border-radius:14px;cursor:pointer;transition:all .15s}
.chip:hover{background:var(--sf2);color:var(--tx);border-color:var(--ac)}.chip-flash{background:var(--acd);color:var(--ac);border-color:var(--ac)}
.tag-chip{font-family:'JetBrains Mono',monospace;font-size:10px;padding:5px 10px;background:var(--sf);color:var(--tx);border:1px solid var(--bd);border-radius:14px;cursor:pointer;transition:all .15s;letter-spacing:.3px}
.tag-chip.chip-pending{opacity:.5}.tag-chip.chip-running{background:rgba(255,192,64,.15);border-color:var(--am);color:var(--am);animation:chipPulse 1.2s ease-in-out infinite}
.tag-chip.chip-done{background:rgba(0,229,160,.12);border-color:rgba(0,229,160,.55);color:#00e5a0}.tag-chip.chip-error{background:rgba(255,64,112,.12);border-color:var(--rd);color:var(--rd)}
@keyframes chipPulse{0%,100%{opacity:1}50%{opacity:.55}}
.tag-chip:hover{transform:translateY(-1px)}.tag-chip:active{transform:scale(.94)}
.tag-chip.tier1{border-color:rgba(0,229,160,.3)}.tag-chip.tier1:hover{background:var(--acd);border-color:var(--ac);color:var(--ac)}
.tag-chip.tier2{border-color:rgba(0,200,255,.3)}.tag-chip.tier2:hover{background:rgba(0,200,255,.1);border-color:var(--ac2);color:var(--ac2)}
.tag-chip.tier3{border-color:rgba(255,192,64,.4)}.tag-chip.tier3:hover{background:rgba(255,192,64,.12);border-color:var(--am);color:var(--am)}
.tag-chip.tier4{border-color:rgba(128,96,255,.3)}.tag-chip.tier4:hover{background:rgba(128,96,255,.1);border-color:var(--vi);color:var(--vi)}
.tag-chip.tier5{border-color:rgba(106,106,152,.4)}.tag-chip.tier5:hover{background:rgba(106,106,152,.12);border-color:var(--tx3);color:var(--tx2)}
.pbox{margin:0 18px 16px;padding:12px 14px;background:var(--bg);border:1px solid var(--bd);border-radius:8px;font-family:'JetBrains Mono',monospace;font-size:11px;line-height:1.55;white-space:pre-wrap;word-break:break-word;overflow:auto;color:var(--tx2)}
.trow{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;background:var(--sf2);margin-bottom:6px}
.trow .nm{font-weight:600;flex-shrink:0;min-width:150px;max-width:250px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trow .vol{font-family:'JetBrains Mono',monospace;color:var(--g);font-size:12px;flex-shrink:0}
.trow .ev{color:var(--tx3);font-size:11px;flex-shrink:0}.trow .sm{color:var(--tx3);font-size:11px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trow .acts{margin-left:auto;display:flex;gap:6px;flex-shrink:0}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.bchip{font-size:11px;padding:3px 9px;border-radius:12px;background:var(--sf2);border:1px solid rgba(255,64,112,.5);color:#ff8fa8}
.ctl{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:4px;padding:10px 12px;background:var(--sf2);border-radius:8px}
.ctl input[type=number]{width:70px;background:var(--bg);border:1px solid var(--bd);color:var(--tx);border-radius:6px;padding:5px 8px;font-size:13px}
.tierlab{font-size:10px;font-weight:600;letter-spacing:.5px;margin:0 0 8px}
.rules{font-size:12.5px;line-height:1.8;color:var(--tx2)}.rules b{color:var(--tx)}.rules code{background:var(--sf2);padding:1px 6px;border-radius:4px;font-size:11px;color:var(--y)}
.empty{color:var(--tx3);font-size:12px;padding:8px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--sf2);border:1px solid var(--bd);border-radius:8px;padding:10px 18px;font-size:12px;opacity:0;transition:all .25s;z-index:99;max-width:80%}
.toast.show{transform:translateX(-50%) translateY(0);opacity:1}.toast.ok{border-color:var(--g);color:var(--g)}.toast.err{border-color:var(--r);color:var(--r)}
</style></head><body>
<div id="toast" class="toast"></div>
<div class="nav"><span class="logo">🏷️ 标签 / 扫描</span><a href="/">🏠 主页</a><a href="/panel">🖥️ 控制台</a><a href="/history">📊 往期</a><a href="/tags" class="active">🏷️ 标签</a><span style="margin-left:auto;color:var(--tx3);font-size:11px" id="upd">—</span></div>
<div class="wrap">

<div class="sec scan"><div class="scanhd">🔍 市场扫描器</div>
<div class="tabs"><button class="tab tab-active" id="tab-tag" onclick="switchTab('tag')">🏷️ Tag扫描</button><button class="tab" id="tab-kw" onclick="switchTab('kw')">🔍 关键词扫描</button></div>

<div id="panel-kw" class="tab-panel" style="display:none">
<div style="padding:14px 18px 6px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span style="font-size:10px;color:var(--tx3);margin-right:4px">快捷:</span>
<button class="chip" onclick="setKw('iran')">iran</button><button class="chip" onclick="setKw('israel')">israel</button><button class="chip" onclick="setKw('ukraine')">ukraine</button><button class="chip" onclick="setKw('russia')">russia</button><button class="chip" onclick="setKw('ceasefire')">ceasefire</button><button class="chip" onclick="setKw('taiwan')">taiwan</button><button class="chip" onclick="setKw('china')">china</button><button class="chip" onclick="setKw('venezuela')">venezuela</button><button class="chip" onclick="setKw('election')">election</button><button class="chip" onclick="setKw('spacex')">spacex</button><button class="chip" onclick="setKw('nobel')">nobel</button></div>
<div style="padding:0 18px 14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
<input id="scanKw" placeholder="关键词 (如 iran, trump)" style="padding:8px 14px;border-radius:8px;border:1px solid var(--bd);background:var(--bg);color:var(--tx);font-size:12px;width:240px">
<button class="btn" onclick="doScan('standard')">🚀 标准扫描</button><button class="btn" onclick="doScan('medium')">📊 中范围</button><button class="btn" onclick="doScan('wide')">🌐 大范围</button>
<button class="btn" onclick="copyScan()">📋 复制报告</button><button class="btn btn-primary" onclick="copyP()">🤖 复制给Claude</button><span id="scanStatus" style="font-size:11px;color:var(--tx3)"></span></div>
</div>

<div id="panel-tag" class="tab-panel"><div style="padding:14px 18px 8px">
<div style="display:flex;gap:10px;align-items:center;margin-bottom:10px;padding:8px 10px;background:rgba(0,200,255,0.06);border:1px solid rgba(0,200,255,0.2);border-radius:6px">
<button class="btn btn-primary" onclick="doScanAll()" id="scan-all-btn">🚀 一键全扫 (核心+热门)</button>
<button class="btn" onclick="doResetScanMarks()" title="清掉所有 chip 标记 (缓存仍在)">🔄 重置标记</button>
<span id="scan-all-progress" style="font-size:11px;color:var(--tx2);font-family:'JetBrains Mono'"></span>
<span style="font-size:10px;color:var(--tx3);margin-left:auto">点 🤖 复制后会自动清掉 ✓</span></div>
<div style="display:flex;gap:14px;align-items:center;margin-bottom:12px"><span style="font-size:11px;color:var(--tx3);font-weight:600">范围:</span>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="standard"> 标准</label>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="medium" checked> 中范围</label>
<label style="display:flex;align-items:center;gap:4px;font-size:11px;cursor:pointer"><input type="radio" name="tagMode" value="wide"> 大范围</label>
<span style="font-size:10px;color:var(--tx3);margin-left:auto">点 tag 单独扫</span></div>
<div class="tierlab" style="color:var(--ac)">TIER 1 重点 ⭐</div><div class="chips" id="tier1-chips" style="margin-bottom:14px"></div>
<div class="tierlab" style="color:var(--ac2)">TIER 2 中等</div><div class="chips" id="tier2-chips" style="margin-bottom:14px"></div>
<div class="tierlab" style="color:var(--am)">TIER 3 🔥 热门 (你纳入的动态标签, 跟着一键全扫)</div><div class="chips" id="tier3-chips" style="margin-bottom:14px"></div>
<details style="margin-bottom:12px"><summary style="cursor:pointer;font-size:10px;color:var(--tx3);font-weight:600;padding:4px 0;user-select:none">▸ 更多 (TIER 4 过度自信 + TIER 5 长尾 · 默认收起、一键全扫不含)</summary>
<div class="tierlab" style="color:var(--vi);margin-top:10px">TIER 4 反向操作 (优先卖 NO)</div><div class="chips" id="tier4-chips" style="margin-bottom:14px"></div>
<div class="tierlab" style="color:var(--tx3)">TIER 5 少量 (off-season / 长 fuse / 低 vol)</div><div class="chips" id="tier5-chips"></div></details>
<div style="display:flex;gap:8px;align-items:center;padding-top:8px;border-top:1px solid var(--bd)">
<button class="btn" onclick="copyScan()">📋 复制报告</button><button class="btn btn-primary" onclick="copyP()">🤖 复制给Claude</button><span id="tagScanStatus" style="font-size:11px;color:var(--tx3)"></span></div>
</div></div>
<div class="pbox" id="scanReport" style="max-height:400px">点击扫描按钮拉取市场数据... (或点上面「一键全扫」)</div>
</div>

<div class="sec"><h2>🧲 动态采纳 <span style="color:var(--tx3);font-size:12px">· = TIER 3 热门</span><button class="btn btn-r" style="margin-left:10px;font-size:11px;padding:3px 10px" onclick="retireAll()">🗑 一键清空</button></h2>
<div class="sub">你纳入的动态标签(上面扫描器 TIER 3 也是这些)。点「复制」拿提示词, 「删除」退场。</div><div id="dyn-list"></div></div>

<div class="sec hot"><h2>🔥 今日热门 · 当天建议</h2>
<div class="sub">按交易量汇总, 排除黑名单/体育/币价/结构性。纳入后进上面 TIER 3 热门、跟着被一键全扫。</div>
<div class="ctl"><label style="font-size:12px;color:var(--tx2)">门槛 7天成交 ≥ $<input type="number" id="thr" value="10" min="0" step="1">M</label>
<span id="cnt" style="color:var(--tx3);font-size:11px"></span><button class="btn btn-g" onclick="adoptAll()">✅ 一键纳入达标的</button><button class="btn" onclick="refresh()">🔄 重新拉取</button></div><div id="new-list"></div></div>

<div class="sec"><h2>🚫 黑名单</h2><div class="sub">永不纳入(AI 没 edge / 结构性噪音)。</div><div id="bl" class="chips"></div>
<div id="xbl-wrap" style="display:none;margin-top:12px"><div class="tierlab">你额外拉黑的:</div><div id="xbl" class="chips"></div></div></div>

<div class="sec"><h2>📅 每天怎么筛的(规则)</h2><div class="rules" id="rules"></div></div>
</div>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function q(s){return String(s==null?'':s).replace(/['"\\]/g,'')}
function m(n){n=+n||0;if(n>=1e6)return '$'+(n/1e6).toFixed(1)+'M';if(n>=1e3)return '$'+(n/1e3).toFixed(0)+'K';return '$'+n.toFixed(0)}
async function post(u,b){try{return await(await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})).json()}catch(e){return{ok:false}}}
function showT(t,msg){var e=document.getElementById('toast');e.className='toast '+t+' show';e.textContent=msg;setTimeout(function(){e.classList.remove('show')},3000)}
function switchTab(name){['kw','tag'].forEach(function(id){var tab=document.getElementById('tab-'+id),p=document.getElementById('panel-'+id);if(id===name){tab.classList.add('tab-active');p.style.display=''}else{tab.classList.remove('tab-active');p.style.display='none'}})}
function slugifyTag(label){return label.toLowerCase().replace(/ /g,'-').replace(/\//g,'-')}
function setChipStatus(slug,status){var chips=document.querySelectorAll('.tag-chip[data-tag]');for(var i=0;i<chips.length;i++){var chip=chips[i],label=chip.getAttribute('data-tag');if(slugifyTag(label)!==slug)continue;chip.classList.remove('chip-pending','chip-running','chip-done','chip-error');var em={pending:' ⏳',running:' 🔄',done:' ✓',error:' ❌'};chip.textContent=label+(em[status]||'');if(status)chip.classList.add('chip-'+status);return}}
function doResetScanMarks(){var chips=document.querySelectorAll('.tag-chip[data-tag]'),n=0;chips.forEach(function(chip){if(chip.classList.contains('chip-pending')||chip.classList.contains('chip-running')||chip.classList.contains('chip-done')||chip.classList.contains('chip-error')){chip.classList.remove('chip-pending','chip-running','chip-done','chip-error');chip.textContent=chip.getAttribute('data-tag');n++}});document.getElementById('scan-all-progress').textContent='';showT('ok','已重置 '+n+' 个标记 (缓存还在)')}
function doScanAll(){var radios=document.querySelectorAll('input[name="tagMode"]'),mode='medium';for(var i=0;i<radios.length;i++){if(radios[i].checked){mode=radios[i].value;break}}var btn=document.getElementById('scan-all-btn');btn.disabled=true;btn.textContent='扫描中...';
 fetch('/api/scan_all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tiers:[1,2],mode:mode})}).then(function(r){return r.json()}).then(function(d){if(!d.ok){showT('err',d.message||'启动失败');btn.disabled=false;btn.textContent='🚀 一键全扫 (核心+热门)';return}showT('ok',d.message);pollScanAll(0)}).catch(function(){showT('err','启动失败');btn.disabled=false;btn.textContent='🚀 一键全扫 (核心+热门)'})}
function pollScanAll(attempt){fetch('/api/scan_all_status').then(function(r){return r.json()}).then(function(d){if(!d.ok)return;var s=d.summary||{},total=s.total||0,done=s.done||0,running=s.running||0,pending=s.pending||0,err=s.error||0;
 document.getElementById('scan-all-progress').textContent='完成 '+done+'/'+total+' | 进行中 '+running+' | 待扫 '+pending+(err>0?(' | 失败 '+err):'');
 Object.keys(d.manifest||{}).forEach(function(slug){setChipStatus(slug,d.manifest[slug].status)});
 if(done+err>=total&&total>0){var btn=document.getElementById('scan-all-btn');btn.disabled=false;btn.textContent='🚀 重扫 (核心+热门)';showT('ok','全扫完成! 点 chip 看缓存');return}
 if(attempt>=300){showT('err','轮询超时');return}setTimeout(function(){pollScanAll(attempt+1)},2000)}).catch(function(){setTimeout(function(){pollScanAll(attempt+1)},2000)})}
var _scanPollTimer=null;
function doTagScan(tagLabel){var slug=slugifyTag(tagLabel),chip=document.querySelector('.tag-chip[data-tag="'+tagLabel+'"]'),isCached=chip&&chip.classList.contains('chip-done');
 if(isCached){fetch('/api/scan_report?tag='+encodeURIComponent(tagLabel)).then(function(r){return r.json()}).then(function(d){if(d.ok){document.getElementById('scanReport').textContent=d.report;var dt=d.mtime?new Date(d.mtime*1000):null;document.getElementById('tagScanStatus').textContent='📦 缓存 ['+tagLabel+']'+(dt?' '+dt.toLocaleTimeString():'');document.getElementById('scanReport').setAttribute('data-current-tag',tagLabel);showT('ok','切换到 '+tagLabel+' (缓存)')}else{showT('err',d.report||'缓存读取失败')}});return}
 var radios=document.querySelectorAll('input[name="tagMode"]'),mode='standard';for(var i=0;i<radios.length;i++){if(radios[i].checked){mode=radios[i].value;break}}
 var startTs=Date.now()/1000,modeLabel=mode==='wide'?'大范围':mode==='medium'?'中范围':'标准';
 document.getElementById('scanReport').textContent='🏷️ Tag扫描 ['+tagLabel+'] '+modeLabel+'模式 进行中...';document.getElementById('scanReport').setAttribute('data-current-tag',tagLabel);
 var status=document.getElementById('tagScanStatus');if(status)status.textContent='⏳ 扫描中...';
 if(_scanPollTimer){clearInterval(_scanPollTimer);_scanPollTimer=null}
 fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan_tag',tag:tagLabel,mode:mode})}).then(function(r){return r.json()}).then(function(d){if(!d.ok){showT('err',d.message);if(status)status.textContent='❌ '+d.message;return}showT('ok',d.message);pollScan(startTs,0)}).catch(function(){showT('err','网络错误');if(status)status.textContent='❌ 网络错误'})}
function setKw(kw){var el=document.getElementById('scanKw');el.value=kw;el.focus();document.querySelectorAll('.chip').forEach(function(b){if(b.textContent===kw){b.classList.add('chip-flash');setTimeout(function(){b.classList.remove('chip-flash')},400)}})}
function doScan(mode){mode=mode||'standard';var kw=document.getElementById('scanKw').value,startTs=Date.now()/1000;
 document.getElementById('scanReport').textContent=(mode==='wide'?'🌐 大范围扫描':mode==='medium'?'📊 中范围扫描':'🚀 标准扫描')+' 进行中,请等待...';document.getElementById('scanStatus').textContent='⏳ 扫描中...';
 if(_scanPollTimer){clearInterval(_scanPollTimer);_scanPollTimer=null}
 fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan',keyword:kw,mode:mode})}).then(function(r){return r.json()}).then(function(d){if(!d.ok){showT('err',d.message);document.getElementById('scanStatus').textContent='❌ '+d.message;return}showT('ok',d.message);pollScan(startTs,0)}).catch(function(){showT('err','请求错误');document.getElementById('scanStatus').textContent='❌ 网络错误'})}
function setScanStatus(text){['scanStatus','tagScanStatus'].forEach(function(id){var el=document.getElementById(id);if(el)el.textContent=text})}
function pollScan(startTs,attempt){fetch('/api/scan_report').then(function(r){return r.json()}).then(function(d){var elapsed=Math.floor(Date.now()/1000-startTs);if(d.ok&&d.mtime&&d.mtime>=startTs){document.getElementById('scanReport').textContent=d.report;setScanStatus('✅ 扫描完成 ('+elapsed+'秒) '+new Date().toLocaleTimeString());return}if(attempt>=30){setScanStatus('⚠️ 扫描超时, 请重试');return}setScanStatus('⏳ 扫描中... '+elapsed+'秒');setTimeout(function(){pollScan(startTs,attempt+1)},2000)}).catch(function(){setTimeout(function(){pollScan(startTs,attempt+1)},2000)})}
function loadScan(){fetch('/api/scan_report').then(function(r){return r.json()}).then(function(d){document.getElementById('scanReport').textContent=d.report;if(d.ok&&d.mtime){document.getElementById('scanStatus').textContent='上次扫描: '+new Date(d.mtime*1000).toLocaleString()}}).catch(function(){})}
function copyScan(){var t=document.getElementById('scanReport').textContent;if(navigator.clipboard){navigator.clipboard.writeText(t).then(function(){showT('ok','报告已复制! 粘到 Claude')})}else{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a);showT('ok','报告已复制!')}}
async function copyP(){var cur=document.getElementById('scanReport').getAttribute('data-current-tag');showT('ok','正在准备最新Prompt...');try{var url=cur?('/api/full_prompt?tag='+encodeURIComponent(cur)):'/api/full_prompt';var d=await(await fetch(url)).json();if(!d.ok){showT('err','获取Prompt失败: '+(d.message||''));return}if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(d.prompt)}else{var a=document.createElement('textarea');a.value=d.prompt;a.style.position='fixed';a.style.left='-9999px';document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a)}if(cur){setChipStatus(slugifyTag(cur),'')}showT('ok','✅ Prompt 已复制'+(cur?(' ['+cur+']'):'')+'! 去 Claude.ai 粘贴')}catch(e){showT('err','复制失败: '+e.message)}}
// ---- tag-discovery (今日热门 / 动态 / 黑名单 / tier chips) ----
var DATA=null,LISTS=null;
function thr(){return (+document.getElementById('thr').value||0)*1e6}
function volMap(){var mp={};if(DATA){(DATA.new||[]).concat(DATA.tracked||[]).forEach(function(r){mp[r.label]=r.vol7d})}return mp}
async function adopt(lab,slug){await post('/api/tags/adopt',{label:lab,slug:slug});await loadAll()}
async function adoptAll(){if(!DATA)return;var t=thr(),ds={};((LISTS&&LISTS.dynamic)||[]).forEach(function(d){ds[d.label]=1});var nw=(DATA.new||[]).filter(function(r){return r.vol7d>=t&&!ds[r.label]}).slice(0,40);if(!nw.length){showT('ok','当前门槛下没有可纳入的');return}if(!confirm('一键纳入当前门槛($'+(+document.getElementById('thr').value||0)+'M 以上)的 '+nw.length+' 个热门标签? 会进动态采纳、跟着一键全扫。'))return;var d=await post('/api/tags/adopt_many',{items:nw.map(function(r){return{label:r.label,slug:r.slug}})});await loadAll();showT('ok','已纳入 '+((d&&d.added)||nw.length)+' 个')}
async function retire(lab){if(!confirm('删除(退场) '+lab+'? 不影响固定核心'))return;await post('/api/tags/retire',{label:lab});await loadAll()}
async function retireAll(){var n=((LISTS&&LISTS.dynamic)||[]).length;if(!n){showT('ok','没有动态标签可清');return}if(!confirm('一键清空全部 '+n+' 个动态采纳标签? (不影响固定核心/黑名单)'))return;var d=await post('/api/tags/retire_all',{});await loadAll();showT('ok','已清空 '+((d&&d.removed)||n)+' 个动态标签')}
async function blk(lab){if(!confirm('拉黑 '+lab+'? 以后永不再建议'))return;await post('/api/tags/blacklist',{label:lab});await loadAll()}
function chipFor(tier,t){return '<button class="tag-chip tier'+tier+'" data-tag="'+esc(t)+'" onclick="doTagScan(\''+q(t)+'\')">'+esc(t)+'</button>'}
function renderTierChips(){if(!LISTS)return;var wl=LISTS.whitelist_by_tier||{},dyn=LISTS.dynamic||[];
 document.getElementById('tier1-chips').innerHTML=(wl['1']||[]).map(function(t){return chipFor(1,t)}).join('')||'<span class="empty">—</span>';
 document.getElementById('tier2-chips').innerHTML=(wl['2']||[]).map(function(t){return chipFor(2,t)}).join('')||'<span class="empty">—</span>';
 document.getElementById('tier3-chips').innerHTML=dyn.length?dyn.map(function(d){return chipFor(3,d.label)}).join(''):'<span class="empty">还没纳入热门标签。去下面「今日热门」纳入。</span>';
 document.getElementById('tier4-chips').innerHTML=(wl['3']||[]).map(function(t){return chipFor(4,t)}).join('')||'<span class="empty">—</span>';
 document.getElementById('tier5-chips').innerHTML=(wl['4']||[]).map(function(t){return chipFor(5,t)}).join('')||'<span class="empty">—</span>'}
function renderDynamic(){if(!LISTS)return;var mp=volMap(),dyn=LISTS.dynamic||[];
 if(!dyn.length){document.getElementById('dyn-list').innerHTML='<div class="empty">还没采纳任何动态标签。去下面「今日热门」点「纳入」。</div>';return}
 document.getElementById('dyn-list').innerHTML=dyn.map(function(d){var v=mp[d.label];var vs=v?'<span class="vol">'+m(v)+'/7d</span>':'<span class="vol" style="color:var(--r)">凉了, 建议删</span>';
   return '<div class="trow"><span class="nm" title="'+esc(d.label)+'">'+esc(d.label)+'</span>'+vs+'<span class="acts"><button class="btn btn-g" onclick="doTagScan(\''+q(d.label)+'\')">扫</button><button class="btn btn-g" onclick="copyPromptOf(\''+q(d.label)+'\')">📋复制</button><button class="btn btn-r" onclick="retire(\''+q(d.label)+'\')">删除</button></span></div>'}).join('')}
async function copyPromptOf(tag){try{var d=await(await fetch('/api/full_prompt?tag='+encodeURIComponent(tag))).json();if(!d.ok||!d.prompt){showT('err','还没扫到「'+tag+'」, 先点上面一键全扫');return}if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(d.prompt)}else{var a=document.createElement('textarea');a.value=d.prompt;a.style.position='fixed';a.style.left='-9999px';document.body.appendChild(a);a.select();document.execCommand('copy');document.body.removeChild(a)}showT('ok','✅ ['+tag+'] 提示词已复制')}catch(e){showT('err','复制失败')}}
function renderHot(){if(!DATA)return;var t=thr(),ds={};((LISTS&&LISTS.dynamic)||[]).forEach(function(d){ds[d.label]=1});var nw=(DATA.new||[]).filter(function(r){return r.vol7d>=t&&!ds[r.label]});
 document.getElementById('cnt').textContent='达标 '+nw.length+' 个 (扫了 '+(DATA.event_sample||0)+' 个高量 event)';
 document.getElementById('new-list').innerHTML=nw.slice(0,40).map(function(r){return '<div class="trow"><span class="nm" title="'+esc(r.label)+'">'+esc(r.label)+'</span><span class="vol">'+m(r.vol7d)+'/7d</span><span class="ev">'+r.events+'ev</span><span class="sm">'+esc((r.samples||[])[0]||'')+'</span><span class="acts"><button class="btn btn-g" onclick="adopt(\''+q(r.label)+'\',\''+q(r.slug)+'\')">纳入</button><button class="btn btn-r" onclick="blk(\''+q(r.label)+'\')">🚫</button></span></div>'}).join('')||'<div class="empty">这个门槛下没有新热门, 调低门槛试试。</div>';
 if(DATA.generated_at)document.getElementById('upd').textContent='更新于 '+String(DATA.generated_at).substring(5,16).replace('T',' ')}
function renderBlacklist(){if(!LISTS)return;document.getElementById('bl').innerHTML=(LISTS.blacklist||[]).concat(LISTS.meta||[]).map(function(t){return '<span class="bchip">'+esc(t)+'</span>'}).join('');
 var xbl=LISTS.extra_blacklist||[],xw=document.getElementById('xbl-wrap');if(xbl.length){xw.style.display='';document.getElementById('xbl').innerHTML=xbl.map(function(t){return '<span class="bchip">'+esc(t)+'</span>'}).join('')}else xw.style.display='none'}
function renderAll(){renderTierChips();renderDynamic();renderHot();renderBlacklist()}
async function loadAll(){try{var s=await(await fetch('/api/tags/suggestions')).json();if(s.ok)DATA=s}catch(e){}try{var l=await(await fetch('/api/tags/lists')).json();if(l.ok)LISTS=l}catch(e){}renderAll()}
async function refresh(){document.getElementById('cnt').textContent='重新拉取中…';try{var s=await(await fetch('/api/tags/suggestions?force=1')).json();if(s.ok)DATA=s}catch(e){}renderHot()}
document.getElementById('thr').addEventListener('input',renderHot);
document.getElementById('rules').innerHTML='<b>数据源</b>: 拉 Polymarket 交易量最高约 300 个活跃 event。<br><b>汇总</b>: 每个 event 的 7 天成交量累加到它的每个 tag。<br><b>排除</b>: 黑名单(tags.py 体育/币价/天气 + 结构 meta) + 体育/币价变体兜底 + 你额外拉黑的。<br><b>门槛</b>: 7天成交 ≥ 你设金额(默认 $10M) 且 ≥2 个 event。<br><b>扫描</b>: 🚀一键全扫 = TIER 1+2(核心) + TIER 3(热门动态), <b>不含 T4/T5</b>; 扫完点任意 chip 看报告、🤖 复制给 Claude。';
loadScan();loadAll();
</script></body></html>"""


def register_tag_routes(app):
    """在 create_app 里调一次: register_tag_routes(app). 走同一套 _require_auth 中间件."""

    @app.route("/tags")
    def tags_page():
        return TAGS_HTML

    @app.route("/api/tags/suggestions")
    def api_tags_suggestions():
        from modules.tag_discovery import get_suggestions
        force = flask_request.args.get("force") == "1"
        try:
            res = get_suggestions(force=force, min_vol_7d=200_000)
            return jsonify({"ok": True, **res})
        except Exception as e:
            return jsonify({"ok": False, "message": str(e)})

    @app.route("/api/tags/lists")
    def api_tags_lists():
        from modules.tags import BLACKLIST_TAGS, list_tags_by_tier, TAGS
        from modules.tag_discovery import META_TAGS, get_dynamic_tags, get_extra_blacklist
        return jsonify({
            "ok": True,
            "whitelist_by_tier": list_tags_by_tier(),
            "whitelist_count": len(TAGS),
            "dynamic": get_dynamic_tags(),
            "blacklist": sorted(BLACKLIST_TAGS),
            "meta": sorted(META_TAGS),
            "extra_blacklist": sorted(get_extra_blacklist()),
        })

    @app.route("/api/tags/adopt", methods=["POST"])
    def api_tags_adopt():
        from modules.tag_discovery import adopt_tag
        d = flask_request.get_json() or {}
        lab = (d.get("label") or "").strip()
        if not lab:
            return jsonify({"ok": False, "message": "missing label"})
        return jsonify({"ok": adopt_tag(lab, (d.get("slug") or "").strip() or None, d.get("tier", 2))})

    @app.route("/api/tags/adopt_many", methods=["POST"])
    def api_tags_adopt_many():
        from modules.tag_discovery import adopt_many
        d = flask_request.get_json() or {}
        return jsonify({"ok": True, "added": adopt_many(d.get("items") or [])})

    @app.route("/api/tags/retire", methods=["POST"])
    def api_tags_retire():
        from modules.tag_discovery import retire_tag
        d = flask_request.get_json() or {}
        return jsonify({"ok": retire_tag((d.get("label") or "").strip())})

    @app.route("/api/tags/retire_all", methods=["POST"])
    def api_tags_retire_all():
        from modules.tag_discovery import retire_all_tags
        return jsonify({"ok": True, "removed": retire_all_tags()})

    @app.route("/api/tags/blacklist", methods=["POST"])
    def api_tags_blacklist():
        from modules.tag_discovery import blacklist_tag
        d = flask_request.get_json() or {}
        lab = (d.get("label") or "").strip()
        if not lab:
            return jsonify({"ok": False, "message": "missing label"})
        return jsonify({"ok": blacklist_tag(lab)})
