"""Embedded single-page admin/ops console UI (vanilla JS, no build step).

Served at GET / by main.py; calls only the console's own /admin/* endpoints
(same origin), which proxy server-side to status-service, partner-auth,
api-metering, webhook-service and tenant-billing. No CORS, no npm.
"""

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin Console — Ops</title>
<style>
  :root { --bg:#0a0e14; --panel:#12181f; --border:#222c38; --fg:#e6edf3; --muted:#7d8896;
          --ok:#3fb950; --degraded:#d29922; --down:#f85149; --accent:#2f81f7; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:13px/1.5 ui-monospace,Menlo,monospace; }
  header { padding:16px 24px; border-bottom:1px solid var(--border); display:flex; gap:12px; align-items:center; }
  header h1 { font-size:15px; margin:0; } .sp { margin-left:auto; }
  main { max-width:1100px; margin:0 auto; padding:22px; display:grid; gap:18px; grid-template-columns:1fr 1fr; }
  .panel { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px; }
  .panel.wide { grid-column:1 / -1; }
  .panel h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 12px; }
  table { width:100%; border-collapse:collapse; } th,td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--border); }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  .ok{background:var(--ok)} .degraded{background:var(--degraded)} .down{background:var(--down)}
  input { background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:6px; padding:6px 8px; font-family:inherit; }
  button { background:transparent; border:1px solid var(--border); color:var(--fg); border-radius:6px; padding:6px 10px; cursor:pointer; font-family:inherit; }
  .muted{color:var(--muted)} code{color:var(--accent)} .big{font-size:22px;font-weight:700}
</style></head>
<body>
<header><h1>Admin Console</h1><span class="muted">control / ops / compliance</span>
  <span class="sp"></span><span id="overall" class="muted">checking…</span>
  <button onclick="refreshAll()">Refresh</button></header>
<main>
  <section class="panel wide"><h2>Platform health (status-service)</h2><div id="health" class="muted">loading…</div></section>

  <section class="panel wide"><h2>Agents (A2A mesh)</h2><div id="agents" class="muted">loading…</div></section>

  <section class="panel"><h2>Tenant lookup</h2>
    <div><input id="tenant" value="ten_demo"/> <button onclick="loadTenant()">Load</button></div>
    <div id="keys" style="margin-top:10px"></div>
    <div id="usage" style="margin-top:10px"></div>
    <div id="invoices" style="margin-top:10px"></div>
  </section>

  <section class="panel"><h2>Webhook deliveries</h2><div id="deliveries" class="muted">—</div>
    <button onclick="loadDeliveries()" style="margin-top:8px">Load deliveries</button></section>
</main>
<script>
async function jget(u){ const r=await fetch(u); return {status:r.status, data:await r.json().catch(()=>({}))}; }
function badge(s){ const c=s==='ok'?'ok':(s==='degraded'?'degraded':'down'); return '<span class="dot '+c+'"></span>'+s; }

async function loadHealth(){
  const r=await jget('/admin/health'); const h=document.getElementById('health');
  if(!r.data || r.data.reachable===false){ h.innerHTML='<span class="down">status-service unreachable</span>'; document.getElementById('overall').innerHTML=badge('down'); return; }
  const rep=r.data.report||{};
  document.getElementById('overall').innerHTML=badge(rep.status||'?');
  let t='<table><tr><th>component</th><th>status</th><th>latency</th><th>critical</th></tr>';
  for(const c of (rep.components||[])){ t+='<tr><td><code>'+c.name+'</code></td><td>'+badge(c.status)+'</td><td>'+(c.latency_ms??'')+'</td><td>'+(c.critical?'yes':'no')+'</td></tr>'; }
  h.innerHTML=t+'</table>';
}
async function loadTenant(){
  const t=document.getElementById('tenant').value;
  const k=await jget('/admin/keys?tenant='+encodeURIComponent(t));
  const keys=(k.data.keys)||[];
  document.getElementById('keys').innerHTML='<b>Keys ('+keys.length+')</b>'+(keys.map(x=>'<div class="muted"><code>'+x.key_id+'</code> '+(x.active?'':'(revoked) ')+(x.scopes||[]).join(',')+'</div>').join('')||' <span class="muted">none</span>');
  const u=await jget('/admin/usage?tenant='+encodeURIComponent(t));
  const us=u.data||{};
  document.getElementById('usage').innerHTML='<b>Usage</b> <span class="muted">'+(us.period||'')+'</span>: <span class="big">'+(us.total??0)+'</span> calls'+(us.over_quota?' <span class="down">OVER QUOTA</span>':'');
  const iv=await jget('/admin/invoices?tenant='+encodeURIComponent(t));
  const inv=(iv.data.invoices)||[];
  document.getElementById('invoices').innerHTML='<b>Invoices ('+inv.length+')</b>'+(inv.map(x=>'<div class="muted">'+x.period+' — $'+x.total+' ['+x.status+']</div>').join('')||' <span class="muted">none</span>');
}
async function loadDeliveries(){
  const r=await jget('/admin/deliveries'); const d=(r.data.deliveries)||[];
  let t='<table><tr><th>event</th><th>endpoint</th><th>status</th><th>code</th></tr>';
  for(const x of d){ t+='<tr><td><code>'+x.event_type+'</code></td><td>'+x.endpoint_id+'</td><td>'+x.status+'</td><td>'+(x.response_code??'')+'</td></tr>'; }
  document.getElementById('deliveries').innerHTML = d.length? t+'</table>' : '<span class="muted">no deliveries</span>';
}
function esc(s){ return String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
async function loadAgents(){
  const r=await jget('/admin/agents'); const a=document.getElementById('agents');
  if(!r.data || r.data.reachable===false){ a.innerHTML='<span class="down">status-service unreachable</span>'; return; }
  const m=r.data.roster||{}; const agents=m.agents||[];
  let h='<p class="muted">'+badge(m.status||'?')+' · '+(m.count||0)+'/'+((m.roster||[]).length)+' answering</p>';
  h+='<table><tr><th>agent</th><th>version</th><th>skills</th></tr>';
  // card fields come from whatever answers on the mesh — escape EVERYTHING
  for(const ag of agents){ h+='<tr><td><code>'+esc(ag.name)+'</code></td><td>'+esc(ag.version||'?')+'</td><td>'+
    (ag.skills||[]).map(s=>'<code>'+esc(s.id)+'</code>').join(' ')+'</td></tr>'; }
  for(const down of (m.unreachable||[])){ h+='<tr><td><code>'+esc(down)+'</code></td><td colspan="2"><span class="down">unreachable</span></td></tr>'; }
  a.innerHTML=h+'</table>';
}
function refreshAll(){ loadHealth(); loadAgents(); loadTenant(); loadDeliveries(); }
refreshAll();
</script>
</body></html>"""
