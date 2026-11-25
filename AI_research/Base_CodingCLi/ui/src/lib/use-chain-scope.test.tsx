import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor,fireEvent} from '@testing-library/react';
import {MemoryRouter,Link} from 'react-router-dom';
import {useChainScope} from './use-chain-scope';
import {api} from '@/api/client';
vi.mock('@/api/client',()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks()});
function List(){const scope=useChainScope();return <><Link to="/outputs">查看全部</Link>{['a','b'].filter(scope.script).map(id=><p key={id}>{'视频 '+id}</p>)}</>}
it('只显示本链路的视频，退出关联恢复全局列表',async()=>{
 vi.mocked(api).mockResolvedValue({key:'science',name:'科研',job:'jobs/science',outputs:['science_each'],script_ids:['a']});
 render(<MemoryRouter initialEntries={['/outputs?chain=science']}><List/></MemoryRouter>);
 await waitFor(()=>expect(screen.getByText('视频 a')).toBeTruthy());expect(screen.queryByText('视频 b')).toBeNull();
 fireEvent.click(screen.getByText('查看全部'));await waitFor(()=>expect(screen.getByText('视频 b')).toBeTruthy());
});
it('关联范围未返回时不展示其他链路内容',()=>{
 vi.mocked(api).mockReturnValue(new Promise(()=>{}));render(<MemoryRouter initialEntries={['/outputs?chain=science']}><List/></MemoryRouter>);
 expect(screen.queryByText('视频 a')).toBeNull();expect(screen.queryByText('视频 b')).toBeNull();
});
it('shares one scope across the header and page without blanking it on navigation',async()=>{
 const {ChainScopeProvider}=await import('./use-chain-scope');
 vi.mocked(api).mockResolvedValue({key:'science',name:'科研',job:'jobs/science',outputs:[],script_ids:['a']});
 function Header(){const scope=useChainScope();return <span>{scope.data?.name}</span>}
 function Page(){const scope=useChainScope();return <><Link to="/video?chain=science">文稿</Link><p>{scope.data?.job}</p></>}
 render(<MemoryRouter initialEntries={['/outputs?chain=science']}><ChainScopeProvider><Header/><Page/></ChainScopeProvider></MemoryRouter>);
 await waitFor(()=>expect(screen.getByText('jobs/science')).toBeTruthy());
 expect(vi.mocked(api)).toHaveBeenCalledTimes(1);
 fireEvent.click(screen.getByText('文稿'));
 expect(screen.getByText('jobs/science')).toBeTruthy();
 expect(vi.mocked(api)).toHaveBeenCalledTimes(1);
});
it('cancels a pending chain read and never exposes it after switching to another chain',async()=>{
 const {ChainScopeProvider}=await import('./use-chain-scope');
 let finish!:(scope:unknown)=>void;
 vi.mocked(api).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve}))
  .mockResolvedValueOnce({key:'other',name:'另一链路',job:'jobs/other',outputs:[],script_ids:['b']});
 render(<MemoryRouter initialEntries={['/outputs?chain=science']}><ChainScopeProvider>
  <Link to="/video?chain=other">切换链路</Link><List/>
 </ChainScopeProvider></MemoryRouter>);
 const signal=vi.mocked(api).mock.calls[0][1]?.signal;
 fireEvent.click(screen.getByText('切换链路'));
 await waitFor(()=>expect(screen.getByText('视频 b')).toBeTruthy());
 expect(signal?.aborted).toBe(true);
 await import('@testing-library/react').then(({act})=>act(async()=>finish({key:'science',name:'科研',job:'jobs/science',outputs:[],script_ids:['a']})));
 expect(screen.queryByText('视频 a')).toBeNull();expect(screen.getByText('视频 b')).toBeTruthy();
 expect(vi.mocked(api)).toHaveBeenCalledTimes(2);
});
