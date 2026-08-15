import type { EdvidDesktopApi } from './shared';

declare global {
  interface Window {
    edvidDesktop: EdvidDesktopApi;
  }
}

export {};
