import { constants, existsSync, accessSync } from 'node:fs';
import path from 'node:path';
import manifest from '../resources/runtime-manifest.json';
import type { RuntimeName } from './shared';

export type RuntimeResolution = {
  name: RuntimeName;
  command: string | null;
  argsPrefix: string[];
  expectedVersion: string;
  source: 'bundled' | 'system' | 'missing';
};

type RuntimeContext = {
  appPath: string;
  resourcesPath: string;
  isPackaged: boolean;
  platform: NodeJS.Platform;
  arch: string;
};

const expectedVersions: Record<RuntimeName, string> = {
  node: manifest.runtimes.node.version,
  npm: manifest.runtimes.npm.version,
  ffmpeg: manifest.runtimes.ffmpeg.version,
  ffprobe: manifest.runtimes.ffprobe.version,
  uv: manifest.runtimes.uv.version,
  'yt-dlp': manifest.runtimes['yt-dlp'].version,
  python: manifest.runtimes.python.version,
  whisperx: manifest.runtimes.whisperx.version,
};

function isExecutable(filePath: string): boolean {
  if (!existsSync(filePath)) return false;
  try {
    accessSync(filePath, constants.X_OK);
    return true;
  } catch {
    return process.platform === 'win32';
  }
}

function targetRoot(context: RuntimeContext): string {
  const resourcesRoot = context.isPackaged
    ? context.resourcesPath
    : path.join(context.appPath, 'resources');
  return path.join(resourcesRoot, 'runtimes', `${context.platform}-${context.arch}`);
}

function bundledResolution(
  name: RuntimeName,
  context: RuntimeContext,
): RuntimeResolution | null {
  const root = targetRoot(context);
  const isWindows = context.platform === 'win32';
  const nodeExecutable = path.join(root, 'node', isWindows ? 'node.exe' : 'bin/node');

  if (name === 'node' && isExecutable(nodeExecutable)) {
    return {
      name,
      command: nodeExecutable,
      argsPrefix: [],
      expectedVersion: expectedVersions[name],
      source: 'bundled',
    };
  }

  if (name === 'npm') {
    const npmCli = path.join(
      root,
      'node',
      isWindows ? 'node_modules/npm/bin/npm-cli.js' : 'lib/node_modules/npm/bin/npm-cli.js',
    );
    if (isExecutable(nodeExecutable) && existsSync(npmCli)) {
      return {
        name,
        command: nodeExecutable,
        argsPrefix: [npmCli],
        expectedVersion: expectedVersions[name],
        source: 'bundled',
      };
    }
  }

  if (name === 'ffmpeg' || name === 'ffprobe') {
    const executable = path.join(
      root,
      'ffmpeg',
      'bin',
      `${name}${isWindows ? '.exe' : ''}`,
    );
    if (isExecutable(executable)) {
      return {
        name,
        command: executable,
        argsPrefix: [],
        expectedVersion: expectedVersions[name],
        source: 'bundled',
      };
    }
  }

  if (name === 'uv') {
    const executable = path.join(root, 'uv', 'bin', `uv${isWindows ? '.exe' : ''}`);
    if (isExecutable(executable)) {
      return {
        name,
        command: executable,
        argsPrefix: [],
        expectedVersion: expectedVersions[name],
        source: 'bundled',
      };
    }
  }

  if (name === 'yt-dlp') {
    const executable = path.join(
      root,
      'yt-dlp',
      'bin',
      `yt-dlp${isWindows ? '.exe' : ''}`,
    );
    if (isExecutable(executable)) {
      return {
        name,
        command: executable,
        argsPrefix: [],
        expectedVersion: expectedVersions[name],
        source: 'bundled',
      };
    }
  }

  if (name === 'python' || name === 'whisperx') {
    const executable = path.join(
      root,
      'python-whisperx',
      'python',
      isWindows ? 'python.exe' : 'bin/python3.12',
    );
    if (isExecutable(executable)) {
      return {
        name,
        command: executable,
        // A signed macOS app must remain immutable after launch. Python's -B
        // mode prevents imports from creating or updating __pycache__ files
        // inside the bundled runtime (and is harmless on Windows).
        argsPrefix: ['-B'],
        expectedVersion: expectedVersions[name],
        source: 'bundled',
      };
    }
  }

  return null;
}

export function resolveRuntime(
  name: RuntimeName,
  context: RuntimeContext,
): RuntimeResolution {
  const bundled = bundledResolution(name, context);
  if (bundled) return bundled;

  // Production never silently depends on software installed by the user. The
  // PATH fallback exists only while developing the desktop shell.
  if (!context.isPackaged) {
    const command = context.platform === 'win32' && name === 'npm' ? 'npm.cmd' : name;
    return {
      name,
      command,
      argsPrefix: [],
      expectedVersion: expectedVersions[name],
      source: 'system',
    };
  }

  return {
    name,
    command: null,
    argsPrefix: [],
    expectedVersion: expectedVersions[name],
    source: 'missing',
  };
}
