/** Fit both comparison columns from measured glyph widths, keeping all text. */
type Side={label:string;items:string[]};
type Measure=(text:string,size:number,weight:number)=>number;
let context:CanvasRenderingContext2D|null=null;
export const measureText:Measure=(text,size,weight)=>{
  context??=document.createElement('canvas').getContext('2d');
  if(!context)throw new Error('Text measurement requires a canvas');
  context.font=`${weight} ${size}px "Hiragino Sans GB", "PingFang SC", sans-serif`;
  return context.measureText(text).width+(text.match(/-(?=-)/g)?.length??0)*size*.12;
};
export function wrapMeasured(text:string,width:number,size:number,weight:number,measure:Measure):string[] {
  const lines:string[]=[];let line='';
  // Keep technical words together when possible; split only a token which
  // cannot fit the available width by itself.
  for(const token of text.match(/[!-~]{2,40}|[^\n]|\n/g)??[]){
    if(token==='\n'){lines.push(line);line='';continue;}
    const pieces=measure(token,size,weight)>width?Array.from(token):[token];
    for(const piece of pieces){
      if(line&&measure(line+piece,size,weight)>width){lines.push(line);line=piece;}else line+=piece;
    }
  }
  if(line)lines.push(line);
  return lines.length?lines:[''];
}
export function fitComparison(props:{left:Side;right:Side},measure:Measure=measureText,scale=1){
  let result:any;
  for(let step=0;step<=12;step++){
    const t=step/12,body=34*scale*(1-t)+28*t,label=42*scale*(1-t)+30*t;
    const padding=34-18*t,gap=20-14*t,headerGap=30-18*t,lineHeight=1.5-.25*t,icon=64-16*t;
    const width=798-2*padding-2;
    const sides=Object.fromEntries(['left','right'].map(side=>{
      const data=props[side as 'left'|'right'];
      const labels=wrapMeasured(data.label,width-icon-20,label,650,measure);
      const items=data.items.map(item=>wrapMeasured(item,width-30,body,400,measure));
      const headerHeight=Math.max(icon,labels.length*label*lineHeight);
      const height=padding*2+3+headerHeight+headerGap+items.reduce((sum,lines)=>sum+lines.length*body*lineHeight,0)+gap*(items.length-1);
      return [side,{labels,items,height,headerHeight}];
    }));
    result={body,label,padding,gap,headerGap,lineHeight,icon,sides,height:560,fits:Math.max(sides.left.height,sides.right.height)<=560};
    if(result.fits)return result;
  }
  return result; // Capacity is explicit; never silently truncate a supplied item.
}
