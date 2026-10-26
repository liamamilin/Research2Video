import fs from 'node:fs';
import {cameraAt,linkScenes,materialAt,shotsFor,transitionAt} from '../../../g1/renderer/src/ShotDirector';
import {imageRects} from '../../../g1/renderer/src/DirectedMaterials';

const ir=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const scenes=linkScenes(ir.scenes);
const pose=(scene:any,frame:number)=>{
  const state=materialAt(scene,frame);
  return state.images.map((index:number,slot:number)=>{
    const width=(state.images.length>1?1080*state.weights[slot]:1100)-22;
    const height=state.images.length>1?450:530;
    const {rects}=imageRects(scene.props.images[index],index,width,height);
    return {index,width,height,weight:state.weights[slot],
      pose:cameraAt(scene,frame,rects,{x:0,y:0,width,height},{x:width*.07,y:height*.07}).pose};
  });
};
const boundaries=scenes.slice(1).map((scene:any,i:number)=>{
  const transition=transitionAt(scene,scene.start_frame);
  const before=transition.entry==='carry'?pose(scenes[i],scenes[i].end_frame-1):null;
  const after=transition.entry==='carry'?pose(scene,scene.start_frame):null;
  return {scene:scene.id,entry:transition.entry,start:scene.start_frame,
    previousEnd:scenes[i].end_frame,contiguous:scenes[i].end_frame===scene.start_frame,
    samePicturePose:before?JSON.stringify(before)===JSON.stringify(after):null,before,after};
});
const result={boundaries,shots:scenes.map((scene:any)=>({id:scene.id,shots:shotsFor(scene)}))};
console.log(JSON.stringify(result,null,2));
if(boundaries.some(b=>!b.contiguous||b.samePicturePose===false))process.exitCode=1;
