import fs from 'node:fs';
import {flowCameraAt} from '../../data/validation/relation-layout-20261111/baseline/renderer-source/ShotDirector';
const inputs=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
process.stdout.write(JSON.stringify(inputs.map((c:any)=>({id:c.id,scales:[1,.9].map(scale=>({scale,mode:'graph',frames:c.frames.map((frame:number)=>({frame,camera:flowCameraAt(c.scene,frame)}))}))}))));
