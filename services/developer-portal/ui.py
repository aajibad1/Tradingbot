"""Embedded single-page developer-portal UI (vanilla JS, no build step).

Served at GET / by main.py. The page only ever calls the portal's OWN /portal/*
endpoints (same origin), which proxy server-side to partner-auth and the gateway —
so there is no CORS surface and no Node/npm build. Open it locally in a browser.
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Developer Portal — Sandbox</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --fg:#e6edf3; --muted:#8b949e;
          --accent:#2f81f7; --ok:#3fb950; --warn:#d29922; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
  header { padding:18px 24px; border-bottom:1px solid var(--border); display:flex; align-items:center; gap:12px; }
  header h1 { font-size:16px; margin:0; font-weight:600; }
  .badge { background:var(--warn); color:#000; font-size:11px; padding:2px 8px; border-radius:10px; font-weight:700; }
  main { max-width:980px; margin:0 auto; padding:24px; display:grid; gap:20px; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:18px; }
  .panel h2 { font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 12px; }
  label { display:block; font-size:12px; color:var(--muted); margin:8px 0 3px; }
  input, select, textarea { width:100%; background:var(--bg); color:var(--fg); border:1px solid var(--border);
           border-radius:6px; padding:8px; font-family:inherit; font-size:13px; }
  button { background:var(--accent); color:#fff; border:0; border-radius:6px; padding:9px 14px; cursor:pointer;
           font-family:inherit; font-weight:600; font-size:13px; }
  button.ghost { background:transparent; border:1px solid var(--border); color:var(--fg); }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:end; }
  .row > * { flex:1; min-width:140px; }
  pre { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; overflow:auto;
        white-space:pre-wrap; word-break:break-all; max-height:320px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }
  .tok { color:var(--ok); }
  .muted { color:var(--muted); }
  code { color:var(--accent); }
</style>
</head>
<body>
<header><h1>Developer Portal</h1><span class="badge">SANDBOX</span>
  <span class="muted" style="margin-left:auto">live rails gated on licensing</span></header>
<main>
  <section class="panel">
    <h2>1 · Issue an API key</h2>
    <div class="row">
      <div><label>Tenant ID</label><input id="tenant" value="ten_demo"/></div>
      <div><label>Scopes (comma-separated)</label><input id="scopes" value="onramp,routing,wallets"/></div>
      <div style="flex:0"><label>&nbsp;</label><button onclick="issueKey()">Issue key</button></div>
    </div>
    <div id="issued"></div>
  </section>

  <section class="panel">
    <h2>2 · Your keys</h2>
    <div class="row"><div><label>Tenant filter</label><input id="listTenant" value="ten_demo"/></div>
      <div style="flex:0"><label>&nbsp;</label><button class="ghost" onclick="listKeys()">Refresh</button></div></div>
    <div id="keys"></div>
  </section>

  <section class="panel">
    <h2>3 · Try the API (via gateway)</h2>
    <div class="row">
      <div><label>Bearer token</label><input id="token" placeholder="pk_sandbox_....secret"/></div>
    </div>
    <div class="row">
      <div style="flex:0;min-width:110px"><label>Method</label>
        <select id="method"><option>POST</option><option>GET</option></select></div>
      <div><label>Service</label>
        <select id="service"><option>onramp</option><option>offramp</option><option>routes</option>
          <option>wallets</option><option>settlements</option></select></div>
      <div style="flex:2"><label>Path</label><input id="path" value="v1/onramp/quotes"/></div>
    </div>
    <label>Body (JSON)</label>
    <textarea id="body" rows="4">{"source_currency":"NGN","dest_asset":"USDC","amount":160000}</textarea>
    <div style="margin-top:10px"><button onclick="tryApi()">Send through gateway</button></div>
    <div id="resp"></div>
  </section>

  <section class="panel">
    <h2>API catalog</h2>
    <div id="catalog" class="muted">loading…</div>
  </section>

  <section class="panel">
    <h2>Agents (A2A mesh)</h2>
    <div id="agents" class="muted">loading…</div>
  </section>
</main>
<script>
async function jpost(url, body){ const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}); return {status:r.status, data:await r.json()}; }
async function jget(url){ const r=await fetch(url); return {status:r.status, data:await r.json()}; }
function show(id, obj){ document.getElementById(id).innerHTML = '<pre>'+JSON.stringify(obj,null,2)+'</pre>'; }

async function issueKey(){
  const scopes=document.getElementById('scopes').value.split(',').map(s=>s.trim()).filter(Boolean);
  const r=await jpost('/portal/keys',{tenant_id:document.getElementById('tenant').value, scopes});
  if(r.data.token){ document.getElementById('issued').innerHTML =
    '<p class="muted">Copy this token now — it is shown once:</p><pre class="tok">'+r.data.token+'</pre>';
    document.getElementById('token').value=r.data.token; }
  else show('issued', r.data);
  listKeys();
}
async function listKeys(){
  const r=await jget('/portal/keys?tenant='+encodeURIComponent(document.getElementById('listTenant').value));
  const keys=(r.data.keys)||[];
  let h='<table><tr><th>key_id</th><th>env</th><th>scopes</th><th>active</th><th></th></tr>';
  for(const k of keys){ h+='<tr><td><code>'+k.key_id+'</code></td><td>'+k.environment+'</td><td>'+(k.scopes||[]).join(', ')+
    '</td><td>'+(k.active?'✓':'revoked')+'</td><td><button class="ghost" onclick="rotate(\\''+k.key_id+'\\')">rotate</button> '+
    '<button class="ghost" onclick="revoke(\\''+k.key_id+'\\')">revoke</button></td></tr>'; }
  document.getElementById('keys').innerHTML = keys.length? h+'</table>' : '<p class="muted">no keys</p>';
}
async function rotate(id){ const r=await jpost('/portal/keys/'+id+'/rotate',{}); if(r.data.token){document.getElementById('token').value=r.data.token; document.getElementById('issued').innerHTML='<p class="muted">rotated — new token:</p><pre class="tok">'+r.data.token+'</pre>';} listKeys(); }
async function revoke(id){ await jpost('/portal/keys/'+id+'/revoke',{}); listKeys(); }
async function tryApi(){
  let body=null; try{ body=JSON.parse(document.getElementById('body').value||'{}'); }catch(e){ return show('resp',{error:'invalid JSON body'});}
  const r=await jpost('/portal/proxy',{token:document.getElementById('token').value, method:document.getElementById('method').value,
    service:document.getElementById('service').value, path:document.getElementById('path').value, body});
  show('resp', {status:r.data.status, response:r.data.response});
}
async function loadCatalog(){
  const r=await jget('/portal/services'); const c=document.getElementById('catalog');
  if(!r.data.services){ c.textContent='catalog unavailable'; return; }
  let h='';
  for(const [svc,routes] of Object.entries(r.data.services)){
    h+='<p style="margin:10px 0 4px"><b>'+svc+'</b></p>';
    for(const rt of routes){ h+='<div class="muted"><code>'+rt+'</code></div>'; }
  }
  c.innerHTML=h||'no services';
}
function esc(s){ return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function loadAgents(){
  const a=document.getElementById('agents');
  const r=await jget('/portal/agents'); const d=r.data||{};
  if(!Array.isArray(d.agents)){ a.textContent='agent mesh unavailable'; return; }
  let h='<p class="muted">status: <b>'+esc(d.status||'?')+'</b> · '+(d.count||0)+'/'+((d.roster||[]).length)+' answering</p>';
  for(const ag of d.agents){
    // card fields come from whatever answers on the mesh — escape EVERYTHING
    const skills=(ag.skills||[]).map(s=>'<code>'+esc(s.id)+'</code>').join(' ');
    h+='<p style="margin:10px 0 2px"><b>'+esc(ag.name)+'</b> <span class="muted">v'+esc(ag.version||'?')+'</span></p>';
    h+='<div class="muted">'+esc(ag.description||'')+'</div>';
    if(skills) h+='<div style="margin-top:3px">'+skills+'</div>';
  }
  for(const down of (d.unreachable||[])){ h+='<p style="margin:8px 0 0"><b>'+esc(down)+'</b> <span class="muted">— unreachable</span></p>'; }
  a.innerHTML=h;
}
listKeys(); loadCatalog(); loadAgents();
</script>
</body>
</html>"""
