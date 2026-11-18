import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ChainsPage } from './Chains';
import { useAuthStore } from '@/lib/auth-store';

const apiMock = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ api: apiMock }));
function open() {
  render(<MemoryRouter initialEntries={['/chains/new']}><Routes><Route path="/chains/:key" element={<ChainsPage/>}/></Routes></MemoryRouter>);
}
beforeEach(() => {
  localStorage.clear();
  useAuthStore.setState({ user: { id: 1, username: 'admin', role: 'admin', disabled: false, created_at: '', last_login_at: null } });
  apiMock.mockReset();
  apiMock.mockImplementation(async (path: string) => {
    if (path === '/api/chains') return { items: [], archived: [] };
    if (path === '/api/chains/catalog') return { chains: [], revision: '1', sources: { openai: { name: 'OpenAI News', spec: 'rss:https://openai.com/news/rss.xml', enabled: true, tags: [], note: '' } } };
    if (path === '/api/video/autovideo') return { voices: [{ id: 'zf_xiaoxiao', label: '小晓 · 普通话女声' }] };
    return {};
  });
});
afterEach(()=>{cleanup();vi.useRealTimers();});
it('keeps the newcomer on the current step until required research information is supplied', async () => {
  open(); await screen.findByText('这条链路要持续产出什么？');
  fireEvent.click(screen.getByRole('button', { name: '下一步' }));
  expect(screen.getByRole('alert').textContent).toContain('请填写链路名称');
  expect(apiMock.mock.calls.filter(c => c[1]?.method === 'POST')).toHaveLength(0);
});
it('preserves entered research information and requires an actual source selection', async () => {
  open(); await screen.findByText('这条链路要持续产出什么？');
  fireEvent.change(screen.getByLabelText('链路名称'), { target: { value: '科研进展' } });
  fireEvent.change(screen.getByLabelText(/英文标识/), { target: { value: 'science' } });
  fireEvent.change(screen.getByLabelText('研究目标与观众'), { target: { value: '向普通用户解释研究进展' } });
  fireEvent.click(screen.getByRole('button', { name: '下一步' }));
  fireEvent.click(screen.getByRole('button', { name: '下一步' }));
  expect(screen.getByRole('alert').textContent).toContain('至少选择一个来源');
  fireEvent.click(screen.getByRole('checkbox', { name: /OpenAI News/ }));
  fireEvent.click(screen.getByRole('button', { name: '下一步' }));
  await waitFor(() => expect((screen.getByLabelText('旁白声音') as HTMLSelectElement).value).toBe('zf_xiaoxiao'));
  const draft = JSON.parse(localStorage.getItem('chain-wizard-v1')!);
  expect(draft).toMatchObject({ name: '科研进展', sources: ['source:openai'], voice_id: 'zf_xiaoxiao' });
});
it('restores an unfinished creation form after a reload', async () => {
  localStorage.setItem('chain-wizard-v1', JSON.stringify({ name: '未完成链路', key: 'unfinished', prompt: '研究可用工具', sources: ['source:openai'] }));
  open(); await screen.findByText('这条链路要持续产出什么？');
  expect((screen.getByLabelText('链路名称') as HTMLInputElement).value).toBe('未完成链路');
  expect((screen.getByLabelText('研究目标与观众') as HTMLTextAreaElement).value).toBe('研究可用工具');
});

it('shows the actual shared schedule and window instead of editable independent settings', async () => {
  localStorage.setItem('chain-wizard-v1',JSON.stringify({name:'共享验收',key:'shared',existing_job:'chains/science',sources:[]}));
  apiMock.mockImplementation(async(path:string)=>{
    if(path==='/api/chains/catalog')return {chains:[{key:'science',job:'chains/science',name:'科研'}],sources:{},revision:'1'};
    if(path==='/api/chains/science')return {name:'科研',research:{sources:['source:openai']},sources:[{ref:'source:openai',name:'真实科研来源',enabled:true}],schedules:[{id:'s',schedule:'58 20 * * *',timezone:'Asia/Shanghai',enabled:false}],window_mode:'rolling',window_days:14};
    if(path==='/api/video/autovideo')return {voices:[]};
    return {};
  });
  open();await screen.findByText('这条链路要持续产出什么？');
  fireEvent.click(screen.getByRole('button',{name:'下一步'}));
  await screen.findByText('真实科研来源 · 已启用');
  expect(screen.queryByRole('button',{name:'添加网址来源'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'下一步'}));
  fireEvent.click(screen.getByRole('button',{name:'下一步'}));
  expect(screen.queryByRole('combobox',{name:'运行方式'})).toBeNull();
  expect(screen.queryByRole('combobox',{name:'研究时间范围'})).toBeNull();
  expect(screen.getByText(/每天 20:58.*已暂停/)).toBeTruthy();
  expect(screen.getByText('研究范围：最近 14 天')).toBeTruthy();
});

it('keeps deleted-chain history reachable without live configuration links or controls', async () => {
  const archived={key:'deleted',name:'已删除验收链路',archived:true,job:'chains/deleted',revision:'1',research:{name:'旧研究',sources:[],prompt:''},sources:[],schedules:[],shared_chains:[],scripts:[],script_count:0,outputs:[],outputs_detail:[],runs:[{id:'done-run',status:'DONE',started_at:'2026-11-17T20:00:00+08:00',note:'历史研究完成'}]};
  apiMock.mockImplementation(async(path:string)=>{
    if(path==='/api/chains/deleted')return archived;
    if(path==='/api/chains/catalog')return {access:{mode:"enabled",can_create:true,writable_chains:["*"]},chains:[],sources:{},revision:'1'};
    if(path==='/api/video/autovideo')return {voices:[]};
    if(path==='/api/chains/runs/done-run')return {...archived.runs[0],stage:'RESEARCH',report:'chains/deleted/report.md',report_view:'chains/deleted/report.md',snapshot:{}};
    return {};
  });
  render(<MemoryRouter initialEntries={['/chains/deleted']}><Routes><Route path="/chains/:key" element={<ChainsPage/>}/></Routes></MemoryRouter>);
  await screen.findByText(/下方是删除前的只读配置/);
  expect(screen.queryByRole('link',{name:'研究任务'})).toBeNull();
  expect(screen.queryByRole('button',{name:'修改链路'})).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'运行记录'}));
  fireEvent.click(screen.getByRole('button',{name:/历史研究完成/}));
  await screen.findByRole('link',{name:'打开本次报告'});
  expect(screen.queryByRole('link',{name:'研究日志'})).toBeNull();
});

it('clears a transient initial load failure when automatic polling recovers', async () => {
  vi.useFakeTimers();let attempts=0;
  apiMock.mockImplementation(async(path:string)=>{
    if(path==='/api/chains/recovered'){
      if(++attempts===1)throw new Error('服务响应超时，请重试');
      return {key:'recovered',name:'已恢复链路',job:'chains/recovered',revision:'1',research:{name:'研究',sources:[],prompt:''},sources:[],schedules:[],shared_chains:[],scripts:[],script_count:0,outputs:[],outputs_detail:[],runs:[]};
    }
    if(path==='/api/chains/catalog')return {access:{mode:"enabled",can_create:true,writable_chains:["*"]},chains:[],sources:{},revision:'1'};
    return {};
  });
  await act(async()=>{render(<MemoryRouter initialEntries={['/chains/recovered']}><Routes><Route path="/chains/:key" element={<ChainsPage/>}/></Routes></MemoryRouter>)});
  expect(screen.getByRole('alert').textContent).toContain('服务响应超时');
  await act(async()=>{await vi.advanceTimersByTimeAsync(6000)});
  expect(screen.getByRole('heading',{name:'已恢复链路'})).toBeTruthy();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows pending and failed retry feedback inside the open run drawer', async () => {
  let rejectRetry: (reason: Error) => void = () => {};
  const run={id:'retry-run',status:'FAILED',stage:'VIDEO',started_at:1794910000,note:'需要继续',batch:{items:[]}};
  const chain={key:'retry',name:'恢复验收',job:'chains/retry',revision:'1',research:{name:'研究',sources:[],prompt:''},sources:[],schedules:[],shared_chains:[],scripts:[],script_count:0,outputs:[],outputs_detail:[],runs:[run]};
  apiMock.mockImplementation(async(path:string)=>{
    if(path==='/api/chains/retry')return chain;
    if(path==='/api/chains/catalog')return {access:{mode:"enabled",can_create:true,writable_chains:["*"]},chains:[],sources:{},revision:'1'};
    if(path==='/api/chains/runs/retry-run')return run;
    if(path==='/api/chains/runs/retry-run/retry')return new Promise((_,reject)=>{rejectRetry=reject});
    return {};
  });
  render(<MemoryRouter initialEntries={['/chains/retry']}><Routes><Route path="/chains/:key" element={<ChainsPage/>}/></Routes></MemoryRouter>);
  await screen.findByRole('button',{name:'运行记录'});
  fireEvent.click(screen.getByRole('button',{name:'运行记录'}));
  fireEvent.click(await screen.findByRole('button',{name:/需要继续/}));
  const dialog=within(await screen.findByRole('dialog',{name:'链路运行详情'}));
  fireEvent.click(dialog.getByRole('button',{name:'重试未完成步骤'}));
  expect((dialog.getByRole('button',{name:'重试未完成步骤'}) as HTMLButtonElement).disabled).toBe(true);
  expect(dialog.getByRole('status').textContent).toContain('正在提交操作');
  await act(async()=>rejectRetry(new Error('服务暂时不可用，请重试')));
  expect(dialog.getByRole('alert').textContent).toContain('服务暂时不可用');
  expect((dialog.getByRole('button',{name:'重试未完成步骤'}) as HTMLButtonElement).disabled).toBe(false);
});

it('offers whole-report outputs for an existing report-based chain', async()=>{
  const chain={key:'economy',name:'经济插画',job:'economy/illustrated',revision:'1',research:{name:'经济',topics:{},sources:[],prompt:'解释经济机制'},sources:[],schedules:[],shared_chains:[],scripts:[],script_count:0,outputs:[],outputs_detail:[],runs:[]};
  apiMock.mockImplementation(async(path:string)=>{
    if(path==='/api/chains/catalog')return {access:{mode:'enabled',can_create:true,writable_chains:['*']},chains:[],sources:{},revision:'1'};
    if(path==='/api/chains/economy')return chain;
    return {};
  });
  render(<MemoryRouter initialEntries={['/chains/economy']}><Routes><Route path='/chains/:key' element={<ChainsPage/>}/></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button',{name:'＋ 添加输出'}));
  const dialog=within(screen.getByRole('dialog',{name:'添加视频输出'}));
  expect((dialog.getByLabelText('内容组织') as HTMLSelectElement).value).toBe('whole');
  expect(dialog.queryByRole('option',{name:'合集节目'})).toBeNull();
  expect(dialog.getByText(/沿用当前研究的现有讲稿配方/)).toBeTruthy();
  fireEvent.change(dialog.getByLabelText('输出名称'),{target:{value:'新插画输出'}});
  fireEvent.click(dialog.getByRole('button',{name:'添加输出'}));
  await waitFor(()=>expect(apiMock).toHaveBeenCalledWith('/api/chains/economy',expect.objectContaining({method:'PUT',body:{revision:'1',add_output:{name:'新插画输出',mode:'whole'}}})));
});
