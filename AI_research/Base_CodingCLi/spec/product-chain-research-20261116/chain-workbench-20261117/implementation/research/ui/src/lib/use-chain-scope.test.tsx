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
