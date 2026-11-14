/** Geometry shared by scene cameras. Style limits are inputs, not object-count rules. */
export type Rect={x:number;y:number;width:number;height:number};
export type Pose={zoom:number;x:number;y:number};
export function extent(rects:Rect[]):Rect {
  if(!rects.length)return {x:0,y:0,width:1,height:1};
  const x=Math.min(...rects.map(r=>r.x)),y=Math.min(...rects.map(r=>r.y));
  return {x,y,width:Math.max(...rects.map(r=>r.x+r.width))-x,height:Math.max(...rects.map(r=>r.y+r.height))-y};
}
export function fitFrame(bounds:Rect,view:Rect,margin={x:0,y:0},maxZoom=Infinity):Pose {
  const zoom=Math.min(maxZoom,view.width/Math.max(1,bounds.width+margin.x),view.height/Math.max(1,bounds.height+margin.y));
  return {zoom,x:view.x+view.width/2-(bounds.x+bounds.width/2)*zoom,
    y:view.y+view.height/2-(bounds.y+bounds.height/2)*zoom};
}
export function transformFor(pose:Pose):string {
  return `matrix(${pose.zoom},0,0,${pose.zoom},${pose.x},${pose.y})`;
}
