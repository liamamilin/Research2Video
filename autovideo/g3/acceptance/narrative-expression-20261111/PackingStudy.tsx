import React from 'react';

// Local, manually authored expression study. Not a registered production component.
// Equal blocks illustrate the narration's analogy; geometry is schematic, not a density measurement.
export function PackingStudy({scene,frame}:{scene:any;frame:number}) {
  const spaceAt=scene.beats.find((b:any)=>b.target_id==='node:volume')?.frame??scene.start_frame;
  const densityAt=scene.beats.find((b:any)=>b.target_id==='node:density')?.frame??scene.end_frame;
  const space=frame>=spaceAt, density=frame>=densityAt;
  return <svg viewBox="0 0 1640 560" style={{position:'absolute',left:140,top:300,width:1640,height:560}}>
    <rect x="0" y="0" width="790" height="550" rx="22" fill="#FFFFFF" stroke="#DDE5DB"/>
    <rect x="830" y="0" width="810" height="550" rx="22" fill="#FFFFFF" stroke="#DDE5DB"/>
    <text x="40" y="60" fill="#202D28" fontSize="42" fontWeight="700">水：压得更实</text>
    <text x="870" y="60" fill="#202D28" fontSize="42" fontWeight="700">冰：装得更松</text>
    <rect x="160" y="155" width="420" height="260" rx="12" fill="#F3F6F4" stroke="#879B91" strokeWidth="3"/>
    <rect x="935" y="105" width="600" height="370" rx="12" fill={space?'#E3F2EA':'#F3F6F4'} stroke={space?'#17784B':'#879B91'} strokeWidth={space?5:3}/>
    {Array.from({length:12},(_,i)=>{
      const col=i%4,row=Math.floor(i/4);
      return <React.Fragment key={i}>
        <rect x={205+85*col} y={177+80*row} width="55" height="55" rx="5" fill="#2A8C8C" stroke="#1C6262" strokeWidth="2"/>
        <rect x={970+150*col} y={135+125*row} width="55" height="55" rx="5" fill="#2A8C8C" stroke="#1C6262" strokeWidth="2"/>
      </React.Fragment>;
    })}
    <text x="395" y="505" textAnchor="middle" fill="#202D28" fontSize="32">同样数量的积木 · 重量相同</text>
    <text x="1235" y="520" textAnchor="middle" fill="#17784B" fontSize="32" fontWeight="700">
      {density?'占的地方更大 → 密度更小':space?'占的地方更大':'同样数量的积木 · 重量相同'}
    </text>
    <text x="1630" y="555" textAnchor="end" fill="#758178" fontSize="20">积木疏密示意，不按比例</text>
  </svg>;
}
