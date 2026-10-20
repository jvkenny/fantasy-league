// Weekly ESPN pull, run in John's logged-in Chrome on any lm-api-reads page
// (e.g. .../leagues/582222?view=mStatus) via the Chrome javascript tool.
// Set W to the completed week. Writes a compact JSON into the page body; read
// it with get_page_text and save as data/raw/<yr>/_pull_wkNN.json, then
//   python3 -m ffl.refresh --pull data/raw/<yr>/_pull_wkNN.json
const B='https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leagues/582222';
const W=7;
const g=u=>fetch(B+u,{credentials:'include'}).then(r=>r.json());
const a=await g('?view=mMatchupScore&view=mBoxscore&view=mRoster&view=mTeam&view=mStatus&scoringPeriodId='+W);
const st=(p,src)=>{const s=(p.stats||[]).find(s=>s.scoringPeriodId===W&&s.statSourceId===src&&s.statSplitTypeId===1);return s?+s.appliedTotal.toFixed(4):null};
const ms=a.schedule.filter(m=>m.matchupPeriodId===W).map(m=>({w:m.winner,s:['home','away'].map(k=>{const t=m[k];return [t.teamId,t.totalPoints,t.totalPointsLive??null,
  (t.rosterForCurrentScoringPeriod?.entries||[]).map(e=>{const p=e.playerPoolEntry.player;return [e.lineupSlotId,p.id,p.fullName,p.defaultPositionId,p.proTeamId,p.eligibleSlots.join('|'),st(p,0),st(p,1)]})]})}));
const rec=a.teams.map(t=>{const o=t.record.overall;return [t.id,o.wins,o.losses,o.ties,+o.pointsFor.toFixed(2),+o.pointsAgainst.toFixed(2)]});
const b=await g('?view=mRoster&scoringPeriodId='+(W+1));
const look=[];b.teams.forEach(t=>(t.roster?.entries||[]).forEach(e=>{const p=e.playerPoolEntry.player;
  const f=(src)=>(p.stats||[]).find(s=>s.seasonId===2026&&s.scoringPeriodId===0&&s.statSourceId===src&&s.statSplitTypeId===0);
  const pr=f(1),ac=f(0);if(!pr)return;look.push([t.id,p.id,+(pr.appliedTotal-(ac?ac.appliedTotal:0)).toFixed(1),e.lineupSlotId,p.eligibleSlots.join('|')])}));
const c=await g('?view=mTransactions2&scoringPeriodId='+W);
const tx=(c.transactions||[]).map(t=>[t.type,t.status,t.bidAmount||0,t.teamId,(t.items||[]).map(i=>[i.type,i.playerId,i.toTeamId??null,i.fromTeamId??null])]);
document.body.textContent=JSON.stringify({W,cur:a.status.currentMatchupPeriod,latest:a.status.latestScoringPeriod,ms,rec,look,tx});
[document.body.textContent.length,a.status.currentMatchupPeriod,ms.map(m=>m.w).join(','),look.length,tx.length]
