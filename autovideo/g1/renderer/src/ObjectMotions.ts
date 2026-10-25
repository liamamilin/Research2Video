import type {LottieAnimationData} from '@remotion/lottie';

// Authored vector clips: no external files, expressions, fonts, images or loops.
// Object identity remains in Lucide; these clips only communicate an action.
const fixed = (k: unknown) => ({a:0,k});
const tween = (a: number, b: number, end = 42) => ({a:1,k:[
  {t:0,s:[a],e:[b],o:{x:.25,y:0},i:{x:.65,y:1}}, {t:end,s:[b]},
]});
const rgba = (hex: string) => [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)/255).concat(1);
const path = (points: number[][]) => ({ty:'sh',ks:fixed({i:points.map(()=>[0,0]),o:points.map(()=>[0,0]),v:points,c:false})});
const stroke = (color: string, width = 4) => ({ty:'st',c:fixed(rgba(color)),o:fixed(100),w:fixed(width),lc:2,lj:2});
const trim = () => ({ty:'tm',s:fixed(0),e:tween(0,100,36),o:fixed(0),m:1});
const layer = (name: string, shapes: unknown[], rotation: unknown = fixed(0)) => ({
  ddd:0,ind:1,ty:4,nm:name,sr:1,ao:0,ip:0,op:61,st:0,bm:0,
  ks:{o:fixed(100),r:rotation,p:fixed([50,50,0]),a:fixed([50,50,0]),s:fixed([100,100,100])}, shapes,
});
const cache = new Map<string,LottieAnimationData>();
export function objectMotion(action: string, color: string): LottieAnimationData {
  const key = `${action}:${color}`;
  const hit = cache.get(key);
  if (hit) return hit;
  let shapes: unknown[], rotation: unknown = fixed(0), layers: unknown[] | undefined;
  if (action === 'process') {
    shapes = [{ty:'el',p:fixed([50,50]),s:fixed([86,86])},stroke(color,3),
      {ty:'tm',s:fixed(0),e:fixed(70),o:fixed(0),m:1}];
    rotation = tween(-90,270,54);
  } else if (action === 'complete') {
    shapes = [];
    layers = [layer('completion-check',[path([[70,78],[76,84],[86,72]]),stroke('#FFFFFF',3),trim()]),
      {...layer('completion-badge',[{ty:'el',p:fixed([78,78]),s:fixed([29,29])},
        {ty:'fl',c:fixed(rgba(color)),o:fixed(100)}]),ind:2}];
  } else {
    shapes = [path([[18,82],[82,18]]),stroke('#B84A42',5),trim()];
  }
  const data = {v:'5.12.2',fr:30,ip:0,op:61,w:100,h:100,nm:`研映/${action}`,ddd:0,assets:[],
    layers:layers ?? [layer(action,shapes,rotation)]} as LottieAnimationData;
  cache.set(key,data);
  return data;
}
