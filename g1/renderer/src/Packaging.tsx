import React from 'react';

export type Badge = {text?: string; color: string};

export function CornerBadge({badge, dark = false}: {badge?: Badge; dark?: boolean}) {
  if (badge && !badge.text) return null;
  const color = badge?.color || (dark ? '#4ED5E6' : '#17784B');
  return <div style={{position:'absolute',left:dark ? 80 : 140,top:dark ? 54 : 70,display:'flex',alignItems:'center',gap:14,fontSize:22,letterSpacing:2,color:dark ? '#F3F6FC' : color}}>
    <span style={{width:9,height:9,borderRadius:3,background:color,outline:dark ? '1px solid #FFFFFF88' : undefined}}/>
    {badge ? badge.text : <>AUTOVIDEO <span style={{color:'#69756E',letterSpacing:0,marginLeft:12}}>知识讲解</span></>}
  </div>;
}
