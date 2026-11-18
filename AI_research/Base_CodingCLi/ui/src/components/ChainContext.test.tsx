import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ChainContext } from './ChainContext';
vi.mock('@/api/client',()=>({api:vi.fn(async()=>({items:[]}))}));
afterEach(cleanup);
it('exits chain filtering without discarding the report being viewed',()=>{
 render(<MemoryRouter initialEntries={['/reports/view?path=chains%2Fscience%2F2026-11-17.md&chain=science']}><ChainContext/></MemoryRouter>);
 expect(screen.getByRole('link',{name:'退出关联，查看全部'}).getAttribute('href')).toBe('/reports/view?path=chains%2Fscience%2F2026-11-17.md');
 expect(screen.getByRole('link',{name:'← 返回链路'}).getAttribute('href')).toBe('/chains/science');
});
