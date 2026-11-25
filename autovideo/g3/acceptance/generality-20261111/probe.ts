import fs from 'node:fs';
import {flowCameraAt,cameraAt,shotsFor,materialAt} from '../../../g1/renderer/src/ShotDirector';
import {imageRects} from '../../../g1/renderer/src/DirectedMaterials';
import {fitComparison} from '../../../g1/renderer/src/TextFit';
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const results=input.map((entry:any)=>{
  const {scene,frames,family}=entry;
  if(family==='comparison')return {id:entry.id,fit:fitComparison(entry.props,(text,size)=>Array.from(text).reduce((sum:number,c:string)=>sum+(entry.widths[c]??1)*size,0))};
  if(family==='flow')return {id:entry.id,shots:shotsFor(scene),cameras:frames.map((frame:number)=>flowCameraAt(scene,frame))};
  if(family==='motion')return {id:entry.id,shots:shotsFor(scene)};
  if(family==='material'){
    const view={x:0,y:0,width:entry.view.width,height:entry.view.height};
    const layout=imageRects(entry.image,0,view.width,view.height);
    return {id:entry.id,rects:layout.rects,cameras:frames.map((frame:number)=>cameraAt(scene,frame,layout.rects,view,{x:view.width*.07,y:view.height*.07}))};
  }
});
process.stdout.write(JSON.stringify(results));
