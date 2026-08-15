import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import started from 'electron-squirrel-startup';
import { spawnSync } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { resolveRuntime, type RuntimeResolution } from './runtime';
import type { RuntimeCheck, RuntimeName } from './shared';

if (started) {
  app.quit();
}

const runtimeCommands: Array<{
  name: RuntimeName;
  args: string[];
}> = [
  { name: 'node', args: ['--version'] },
  { name: 'npm', args: ['--version'] },
  { name: 'ffmpeg', args: ['-version'] },
  { name: 'ffprobe', args: ['-version'] },
  { name: 'uv', args: ['--version'] },
  { name: 'yt-dlp', args: ['--version'] },
  { name: 'python', args: ['--version'] },
  {
    name: 'whisperx',
    args: [
      '-c',
      "from importlib.metadata import version; print(version('whisperx'))",
    ],
  },
];

function checkRuntime(
  resolution: RuntimeResolution,
  args: string[],
): RuntimeCheck {
  if (!resolution.command) {
    return {
      name: resolution.name,
      available: false,
      version: null,
      expectedVersion: resolution.expectedVersion,
      source: 'missing',
      executablePath: null,
      error: 'Runtime interno ainda nao empacotado',
    };
  }

  const result = spawnSync(resolution.command, [...resolution.argsPrefix, ...args], {
    encoding: 'utf8',
    windowsHide: true,
    // The signed standalone yt-dlp binary can take longer on its first macOS
    // launch while Gatekeeper inspects the embedded PyInstaller payload.
    timeout: resolution.name === 'yt-dlp' ? 30_000 : 10_000,
  });

  if (result.error || result.status !== 0) {
    return {
      name: resolution.name,
      available: false,
      version: null,
      expectedVersion: resolution.expectedVersion,
      source: resolution.source,
      executablePath: resolution.command,
      error: result.error?.message ?? result.stderr?.trim() ?? 'Falha desconhecida',
    };
  }

  const output = `${result.stdout ?? ''}\n${result.stderr ?? ''}`.trim();
  const version = output.split(/\r?\n/, 1)[0] || null;

  return {
    name: resolution.name,
    available: true,
    version,
    expectedVersion: resolution.expectedVersion,
    source: resolution.source,
    executablePath: resolution.command,
  };
}

function registerIpcHandlers(): void {
  ipcMain.handle('desktop:get-info', () => ({
    platform: process.platform,
    arch: process.arch,
    electronVersion: process.versions.electron,
    embeddedNodeVersion: process.versions.node,
  }));

  ipcMain.handle('runtime:check', () =>
    runtimeCommands.map(({ name, args }) => {
      const resolution = resolveRuntime(name, {
        appPath: app.getAppPath(),
        resourcesPath: process.resourcesPath,
        isPackaged: app.isPackaged,
        platform: process.platform,
        arch: process.arch,
      });
      return checkRuntime(resolution, args);
    }),
  );

  ipcMain.handle('project:select-directory', async () => {
    const result = await dialog.showOpenDialog({
      title: 'Escolha a pasta do projeto de video',
      buttonLabel: 'Usar esta pasta',
      properties: ['openDirectory', 'createDirectory'],
    });

    return result.canceled ? null : result.filePaths[0] ?? null;
  });
}

function createWindow(): void {
  const mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    backgroundColor: '#090b10',
    title: 'Edvid',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  const pageLoad = MAIN_WINDOW_VITE_DEV_SERVER_URL
    ? mainWindow.loadURL(MAIN_WINDOW_VITE_DEV_SERVER_URL)
    : mainWindow.loadFile(
        path.join(__dirname, `../renderer/${MAIN_WINDOW_VITE_NAME}/index.html`),
      );

  // Opt-in visual regression hook for local/CI validation. It is inert for
  // users and avoids requiring macOS Screen Recording permission in tests.
  const screenshotPath = process.env.EDVID_SCREENSHOT_PATH;
  if (screenshotPath) {
    const requestedDelay = Number(process.env.EDVID_SCREENSHOT_DELAY_MS);
    const screenshotDelay = Number.isFinite(requestedDelay)
      ? Math.min(Math.max(requestedDelay, 0), 60_000)
      : 500;
    void pageLoad
      .then(() => new Promise((resolve) => setTimeout(resolve, screenshotDelay)))
      .then(() => mainWindow.webContents.capturePage())
      .then(async (capture) => {
        await writeFile(screenshotPath, capture.toPNG());
        app.exit(0);
      })
      .catch((error: unknown) => {
        console.error('Falha ao capturar screenshot de QA:', error);
        app.exit(1);
      });
  } else {
    void pageLoad;
  }
}

registerIpcHandlers();

void app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
