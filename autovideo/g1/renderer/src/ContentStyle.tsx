import React from 'react';

export type ContentStyle = {
  background: string; surface: string; text: string; muted: string; accent: string;
  line: string; soft: string; inactive: string; palette_source: 'brand';
};

// An absent palette preserves the original renderer for frozen historical jobs.
export const ContentStyleContext = React.createContext<ContentStyle | undefined>(undefined);

export type BrandHeader = {version:number;left:number;right:number;top:number;width:number;height:number;gap:number};
export const BrandHeaderContext = React.createContext<BrandHeader | undefined>(undefined);
