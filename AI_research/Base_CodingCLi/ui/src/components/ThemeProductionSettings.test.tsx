import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ThemeProductionSettings } from './ThemeProductionSettings';
import type { ThemeProductionConfig } from '@/api/themes';

const config: ThemeProductionConfig = { enabled: false, daily_limit: 2, minutes: 4, timezone: 'Asia/Shanghai', starts_on: '',
  columns: { news: { name: '产品动态', period: 'day', count: 1 }, guide: { name: '使用教程', period: 'week', count: 2 } } };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('recommended production settings', () => {
  it('saves the chosen daily and column quotas without starting any video before saving', async () => {
    const saved = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, text: async () => JSON.stringify(config) } as Response)));
    render(<ThemeProductionSettings matrix="chatgpt_acquisition" config={config} canEdit onSaved={saved} />);
    fireEvent.click(screen.getByText(/推荐自动出片 ·/));
    fireEvent.click(screen.getByRole('checkbox', { name: '开启推荐自动出片' }));
    fireEvent.change(screen.getByLabelText('使用教程推荐配额'), { target: { value: '1' } });
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '保存推荐出片设置' }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe('/api/matrices/chatgpt_acquisition/themes/production');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(String(init?.body))).toEqual({ enabled: true, daily_limit: 2, minutes: 4,
      columns: { news: { period: 'day', count: 1 }, guide: { period: 'week', count: 1 } } });
  });

  it('keeps viewer controls read-only', () => {
    render(<ThemeProductionSettings matrix="demo" config={config} canEdit={false} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByText(/推荐自动出片 ·/));
    expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '保存推荐出片设置' })).toBeNull();
  });
});
