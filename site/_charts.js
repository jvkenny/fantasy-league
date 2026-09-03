/* Shared SVG chart primitives. No dependencies: the CSP on the artifact host
   blocks external scripts, and these are small enough not to want a library.
   Every chart takes a host element and returns nothing; all colour comes from
   CSS custom properties so both themes work without redraw. */
const NS_='http://www.w3.org/2000/svg';
function mkS(n,a){const e=document.createElementNS(NS_,n);for(const k in a)e.setAttribute(k,a[k]);return e}
function svgBox(host,w,h){const s=mkS('svg',{viewBox:`0 0 ${w} ${h}`});host.append(s);return s}
function axisLabel(svg,x,y,txt,anchor,rot){
  const t=mkS('text',{x,y,class:'axl','text-anchor':anchor||'middle'});
  if(rot)t.setAttribute('transform',`rotate(${rot} ${x} ${y})`);
  t.textContent=txt; svg.append(t); return t;
}
function quantile(sorted,q){
  if(!sorted.length)return null;
  const i=(sorted.length-1)*q, lo=Math.floor(i), hi=Math.ceil(i);
  return lo===hi?sorted[lo]:sorted[lo]+(sorted[hi]-sorted[lo])*(i-lo);
}

/* Histogram with optional vertical reference marks. */
function histogram(host,values,o){
  o=o||{}; if(!values.length){host.append(el('div','empty','No data yet.'));return}
  const W=o.w||560,H=o.h||280,M={t:12,r:14,b:38,l:44};
  const sorted=[...values].sort((a,b)=>a-b);
  const lo=o.min!==undefined?o.min:Math.floor(sorted[0]/10)*10;
  const hi=o.max!==undefined?o.max:Math.ceil(sorted[sorted.length-1]/10)*10;
  const n=o.bins||24, bw=(hi-lo)/n;
  const counts=new Array(n).fill(0);
  values.forEach(v=>{const i=Math.min(n-1,Math.max(0,Math.floor((v-lo)/bw)));counts[i]++});
  const peak=Math.max(...counts)||1;
  const sx=v=>M.l+((v-lo)/(hi-lo))*(W-M.l-M.r);
  const sy=c=>H-M.b-(c/peak)*(H-M.t-M.b);
  const svg=svgBox(host,W,H);
  for(let i=0;i<=4;i++){const y=M.t+(H-M.t-M.b)*i/4;
    svg.append(mkS('line',{x1:M.l,x2:W-M.r,y1:y,y2:y,class:'ax'}));
    axisLabel(svg,M.l-6,y+3,Math.round(peak*(1-i/4)),'end');}
  counts.forEach((c,i)=>{
    if(!c)return;
    const x=sx(lo+i*bw), w=Math.max(1,sx(lo+(i+1)*bw)-x-1);
    const r=mkS('rect',{x,y:sy(c),width:w,height:H-M.b-sy(c),
      fill:'var(--accent)','fill-opacity':.5});
    const ttl=mkS('title'); ttl.textContent=`${Math.round(lo+i*bw)}-${Math.round(lo+(i+1)*bw)}: ${c}`;
    r.append(ttl); svg.append(r);
  });
  for(let i=0;i<=5;i++){const v=lo+(hi-lo)*i/5; axisLabel(svg,sx(v),H-M.b+14,Math.round(v));}
  (o.marks||[]).forEach(m=>{
    const x=sx(m.v);
    svg.append(mkS('line',{x1:x,x2:x,y1:M.t,y2:H-M.b,
      stroke:m.color||'var(--text)','stroke-width':m.w||1.5,
      'stroke-dasharray':m.dash||'4 3','stroke-opacity':.85}));
    const t=axisLabel(svg,x,M.t-1,m.label,'middle');
    t.setAttribute('fill',m.color||'var(--text)');
  });
  if(o.xl)axisLabel(svg,W/2,H-4,o.xl);
  if(o.yl)axisLabel(svg,13,H/2,o.yl,'middle',-90);
}

/* A continuous function plotted over a domain, with optional guide points. */
function curve(host,fn,o){
  o=o||{};
  const W=o.w||560,H=o.h||280,M={t:14,r:16,b:38,l:46};
  const [x0,x1]=o.domain,[y0,y1]=o.range||[0,1];
  const sx=v=>M.l+((v-x0)/(x1-x0))*(W-M.l-M.r);
  const sy=v=>H-M.b-((v-y0)/(y1-y0))*(H-M.t-M.b);
  const svg=svgBox(host,W,H);
  for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4;
    svg.append(mkS('line',{x1:M.l,x2:W-M.r,y1:sy(v),y2:sy(v),class:'ax'}));
    axisLabel(svg,M.l-6,sy(v)+3,o.yfmt?o.yfmt(v):v.toFixed(2),'end');}
  for(let i=0;i<=6;i++){const v=x0+(x1-x0)*i/6; axisLabel(svg,sx(v),H-M.b+14,Math.round(v));}
  if(o.hline!==undefined)
    svg.append(mkS('line',{x1:M.l,x2:W-M.r,y1:sy(o.hline),y2:sy(o.hline),class:'ref'}));
  if(o.vline!==undefined)
    svg.append(mkS('line',{x1:sx(o.vline),x2:sx(o.vline),y1:M.t,y2:H-M.b,class:'ref'}));
  (o.bands||[]).forEach(b=>{
    svg.append(mkS('rect',{x:sx(b.from),y:M.t,width:sx(b.to)-sx(b.from),height:H-M.t-M.b,
      fill:b.color||'var(--accent)','fill-opacity':b.opacity||.08}));
  });
  const pts=[];
  for(let i=0;i<=160;i++){const x=x0+(x1-x0)*i/160; pts.push(`${sx(x)},${sy(fn(x))}`)}
  svg.append(mkS('polyline',{points:pts.join(' '),fill:'none',
    stroke:'var(--accent)','stroke-width':2.5}));
  (o.marks||[]).forEach(m=>{
    const cx=sx(m.x),cy=sy(m.y);
    svg.append(mkS('circle',{cx,cy,r:4,fill:'var(--accent)'}));
    const t=axisLabel(svg,cx+7,cy-6,m.label,'start'); t.setAttribute('fill','var(--text)');
  });
  if(o.xl)axisLabel(svg,W/2,H-4,o.xl);
  if(o.yl)axisLabel(svg,13,H/2,o.yl,'middle',-90);
}

/* Reliability diagram: predicted probability vs observed rate, dot area ~ n. */
function reliability(host,bins,o){
  o=o||{};
  const W=o.w||560,H=o.h||300,M={t:14,r:16,b:38,l:46};
  const sx=v=>M.l+v*(W-M.l-M.r), sy=v=>H-M.b-v*(H-M.t-M.b);
  const svg=svgBox(host,W,H);
  for(let i=0;i<=4;i++){const v=i/4;
    svg.append(mkS('line',{x1:M.l,x2:W-M.r,y1:sy(v),y2:sy(v),class:'ax'}));
    axisLabel(svg,M.l-6,sy(v)+3,v.toFixed(2),'end');
    axisLabel(svg,sx(v),H-M.b+14,v.toFixed(2));}
  svg.append(mkS('line',{x1:sx(0),y1:sy(0),x2:sx(1),y2:sy(1),class:'ref'}));
  const maxN=Math.max(...bins.map(b=>b.n))||1;
  bins.forEach(b=>{
    const r=4+10*Math.sqrt(b.n/maxN);
    const c=mkS('circle',{cx:sx(b.pred),cy:sy(b.act),r,
      fill:b.act>=b.pred?'var(--win)':'var(--loss)','fill-opacity':.75});
    const t=mkS('title');
    t.textContent=`predicted ${(b.pred*100).toFixed(0)}%, actual ${(b.act*100).toFixed(0)}% (n=${b.n})`;
    c.append(t); svg.append(c);
  });
  if(o.xl)axisLabel(svg,W/2,H-4,o.xl);
  if(o.yl)axisLabel(svg,13,H/2,o.yl,'middle',-90);
}

/* Scatter with an optional binned trend line through it. */
function scatterTrend(host,pts,o){
  o=o||{}; if(!pts.length){host.append(el('div','empty','No data yet.'));return}
  const W=o.w||560,H=o.h||300,M={t:14,r:16,b:38,l:48};
  const xs=pts.map(p=>p.x), ys=pts.map(p=>p.y);
  const x0=o.x0!==undefined?o.x0:Math.min(...xs), x1=o.x1!==undefined?o.x1:Math.max(...xs);
  const y0=o.y0!==undefined?o.y0:0, y1=o.y1!==undefined?o.y1:Math.max(...ys)*1.05;
  const sx=v=>M.l+((v-x0)/(x1-x0||1))*(W-M.l-M.r);
  const sy=v=>H-M.b-((v-y0)/(y1-y0||1))*(H-M.t-M.b);
  const svg=svgBox(host,W,H);
  for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4;
    svg.append(mkS('line',{x1:M.l,x2:W-M.r,y1:sy(v),y2:sy(v),class:'ax'}));
    axisLabel(svg,M.l-6,sy(v)+3,Math.round(v),'end');}
  for(let i=0;i<=6;i++){const v=x0+(x1-x0)*i/6; axisLabel(svg,sx(v),H-M.b+14,Math.round(v));}
  pts.forEach(p=>{
    const c=mkS('circle',{cx:sx(p.x),cy:sy(p.y),r:p.r||2.6,
      fill:p.hot?'var(--accent)':'var(--muted)','fill-opacity':p.hot?.95:.4});
    if(p.tip){const t=mkS('title');t.textContent=p.tip;c.append(t)}
    svg.append(c);
  });
  if(o.trendBins){
    const nb=o.trendBins, step=(x1-x0)/nb, acc=[];
    for(let i=0;i<nb;i++){
      const lo=x0+i*step, hi=lo+step;
      const inb=pts.filter(p=>p.x>=lo&&p.x<hi).map(p=>p.y);
      if(inb.length>=3) acc.push([lo+step/2, inb.reduce((a,b)=>a+b,0)/inb.length]);
    }
    if(acc.length>1)
      svg.append(mkS('polyline',{points:acc.map(a=>`${sx(a[0])},${sy(a[1])}`).join(' '),
        fill:'none',stroke:'var(--accent)','stroke-width':2.5}));
  }
  if(o.xl)axisLabel(svg,W/2,H-4,o.xl);
  if(o.yl)axisLabel(svg,13,H/2,o.yl,'middle',-90);
}
