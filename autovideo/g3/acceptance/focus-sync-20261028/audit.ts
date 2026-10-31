import fs from 'node:fs';
import {shotsFor,linkScenes,comparisonAt} from '../../../g1/renderer/src/ShotDirector';
import {scatterState} from '../../../g1/renderer/src/ScatterChart';

const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const ir=input.ir??input;
const rows=linkScenes(ir.scenes).map((scene:any)=>{
  const frames=Array.from(new Set([scene.start_frame+16,...scene.beats.map((b:any)=>b.frame+1)]))
    .filter((frame:any)=>frame<scene.end_frame).sort((a:any,b:any)=>a-b) as number[];
  return {id:scene.id,component:scene.component,start_frame:scene.start_frame,end_frame:scene.end_frame,
    flow:scene.props.scene_flow,shots:shotsFor(scene),
    samples:frames.map(frame=>{
      const caption=ir.caption_cues?.find((c:any)=>c.start_ms<=frame*1000/30&&c.end_ms>frame*1000/30)?.text;
      if(scene.component==='scatter_chart'){
        const state=scatterState(scene,frame);
        return {frame,caption,selected:state.selected.map(i=>scene.props.points[i].name),
          visible:state.shown.map(i=>scene.props.points[i].name),annotation:state.caption};
      }
      return {frame,caption,...(scene.component==='comparison'?comparisonAt(scene,frame):{})};
    })};
});
process.stdout.write(JSON.stringify(rows,null,2)+'\n');
