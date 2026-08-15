import { contextBridge, ipcRenderer } from 'electron';
import type { EdvidDesktopApi } from './shared';

const api: EdvidDesktopApi = {
  getDesktopInfo: () => ipcRenderer.invoke('desktop:get-info'),
  checkRuntimes: () => ipcRenderer.invoke('runtime:check'),
  selectProjectDirectory: () => ipcRenderer.invoke('project:select-directory'),
};

contextBridge.exposeInMainWorld('edvidDesktop', api);
