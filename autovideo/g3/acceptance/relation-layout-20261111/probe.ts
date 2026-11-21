import fs from 'node:fs';
import {flowCameraAt} from '../../../g1/renderer/src/ShotDirector';
import {flowPresentation} from '../../../g1/renderer/src/RelationLayout';
const inputs=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
process.stdout.write(JSON.stringify(inputs.map((c:any)=>({id:c.id,scales:[1,.9].map(scale=>{
 const measure=(text:string,size:number)=>Array.from(text).reduce((sum,c2)=>sum+(c.widths[c2]??1)*size,0)+(text.match(/-(?=-)/g)?.length??0)*size*.12;
 const presentation=flowPresentation(c.scene,scale,measure);
 return {scale,...presentation,frames:c.frames.map((frame:number)=>({frame,camera:flowCameraAt(c.scene,frame,presentation.reason==='capacity-unmet')}))};
})}))));
