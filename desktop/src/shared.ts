export type RuntimeName =
  | 'node'
  | 'npm'
  | 'ffmpeg'
  | 'ffprobe'
  | 'uv'
  | 'yt-dlp'
  | 'python'
  | 'whisperx';

export type RuntimeCheck = {
  name: RuntimeName;
  available: boolean;
  version: string | null;
  expectedVersion: string;
  source: 'bundled' | 'system' | 'missing';
  executablePath: string | null;
  error?: string;
};

export type DesktopInfo = {
  platform: NodeJS.Platform;
  arch: string;
  electronVersion: string;
  embeddedNodeVersion: string;
};

export type EdvidDesktopApi = {
  getDesktopInfo: () => Promise<DesktopInfo>;
  checkRuntimes: () => Promise<RuntimeCheck[]>;
  selectProjectDirectory: () => Promise<string | null>;
};
