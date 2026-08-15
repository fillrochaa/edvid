import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  access,
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readlink,
  readFile,
  readdir,
  realpath,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(
  await readFile(path.join(desktopRoot, 'resources/runtime-manifest.json'), 'utf8'),
);
const pythonManifest = manifest.runtimes.python;
const whisperxManifest = manifest.runtimes.whisperx;
const target = process.argv[2] ?? `${process.platform}-${process.arch}`;
const currentTarget = `${process.platform}-${process.arch}`;
const runtimeLayoutVersion = 2;

const supportedTargets = new Set(['darwin-arm64', 'darwin-x64', 'win32-x64']);
if (!supportedTargets.has(target)) {
  throw new Error(`Target sem runtime Python + WhisperX configurado: ${target}`);
}
if (target !== currentTarget) {
  throw new Error(
    `Python + WhisperX deve ser preparado na plataforma alvo. Atual: ${currentTarget}; alvo: ${target}.`,
  );
}

const isWindows = target.startsWith('win32-');
const pythonProject = path.join(desktopRoot, 'python', 'whisperx');
const lockPath = path.join(pythonProject, 'uv.lock');
const pyprojectPath = path.join(pythonProject, 'pyproject.toml');
const uvExecutable = path.join(
  desktopRoot,
  'resources',
  'runtimes',
  target,
  'uv',
  'bin',
  `uv${isWindows ? '.exe' : ''}`,
);
const ffmpegDirectory = path.join(
  desktopRoot,
  'resources',
  'runtimes',
  target,
  'ffmpeg',
  'bin',
);
const ffmpegExecutable = path.join(ffmpegDirectory, `ffmpeg${isWindows ? '.exe' : ''}`);
const sharedFfmpegPrefix = path.join(
  desktopRoot,
  '.runtime-cache',
  'ffmpeg-torchcodec',
  whisperxManifest.sharedFfmpegVersion,
  'build',
  target,
  'install',
);
const sharedFfmpegLibraryDirectory = path.join(sharedFfmpegPrefix, 'lib');
const sharedFfmpegMetadataPath = path.join(sharedFfmpegPrefix, 'build-metadata.json');
const cacheRoot = path.join(desktopRoot, '.runtime-cache', 'python-whisperx', target);
const pythonInstallDirectory = path.join(cacheRoot, 'python-install');
const uvCacheDirectory = path.join(cacheRoot, 'uv-cache');
const exportedRequirements = path.join(cacheRoot, 'requirements.lock.txt');
const destination = path.join(
  desktopRoot,
  'resources',
  'runtimes',
  target,
  'python-whisperx',
);
const destinationPython = path.join(
  destination,
  'python',
  isWindows ? 'python.exe' : 'bin/python3.12',
);

await mkdir(cacheRoot, { recursive: true });

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function sha256(filePath) {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
    timeout: options.timeout,
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout}\n${result.stderr}` : '';
    throw new Error(`${command} terminou com codigo ${result.status}.${detail}`);
  }
  return result.stdout ?? '';
}

async function findFile(root, predicate) {
  const entries = await readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      const nested = await findFile(entryPath, predicate);
      if (nested) return nested;
    } else if (predicate(entryPath)) {
      return entryPath;
    }
  }
  return null;
}

async function collectNativeFiles(root) {
  const nativeFiles = [];
  const entries = await readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      nativeFiles.push(...(await collectNativeFiles(entryPath)));
    } else if (/\.(?:so|dylib)$/u.test(entry.name)) {
      nativeFiles.push(entryPath);
    }
  }
  return nativeFiles;
}

async function makeSymlinksBundleRelative(root, sourceRoot, destinationRoot) {
  const entries = await readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      await makeSymlinksBundleRelative(entryPath, sourceRoot, destinationRoot);
      continue;
    }
    if (!entry.isSymbolicLink()) continue;

    const currentTargetPath = await readlink(entryPath);
    if (!path.isAbsolute(currentTargetPath)) continue;
    const absoluteTarget = currentTargetPath;
    const relativeToSource = path.relative(sourceRoot, absoluteTarget);
    if (relativeToSource.startsWith('..') || path.isAbsolute(relativeToSource)) {
      throw new Error(`Link simbolico aponta para fora do runtime: ${entryPath}`);
    }

    const relocatedTarget = path.join(destinationRoot, relativeToSource);
    const bundleRelativeTarget = path.relative(path.dirname(entryPath), relocatedTarget);
    await rm(entryPath);
    await symlink(bundleRelativeTarget, entryPath);
  }
}

async function validateBundleSymlinks(root, bundleRoot = root) {
  const entries = await readdir(root, { withFileTypes: true });
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      await validateBundleSymlinks(entryPath, bundleRoot);
      continue;
    }
    if (!entry.isSymbolicLink()) continue;

    const linkTarget = await readlink(entryPath);
    if (path.isAbsolute(linkTarget)) {
      throw new Error(`Link simbolico absoluto nao permitido no bundle: ${entryPath}`);
    }
    const resolvedTarget = path.resolve(path.dirname(entryPath), linkTarget);
    const relativeToRoot = path.relative(bundleRoot, resolvedTarget);
    if (relativeToRoot.startsWith('..') || path.isAbsolute(relativeToRoot)) {
      throw new Error(`Link simbolico sai do bundle: ${entryPath}`);
    }
    if (!(await exists(resolvedTarget))) {
      throw new Error(`Link simbolico quebrado: ${entryPath}`);
    }
  }
}

function runtimeEnvironment(extra = {}) {
  return {
    ...process.env,
    PATH: `${ffmpegDirectory}${path.delimiter}${process.env.PATH ?? ''}`,
    UV_CACHE_DIR: uvCacheDirectory,
    UV_PYTHON_INSTALL_DIR: pythonInstallDirectory,
    UV_PYTHON_PREFERENCE: 'only-managed',
    UV_NO_PROGRESS: '1',
    ...extra,
  };
}

function versionProbe(pythonExecutable, options = {}) {
  const output = run(
    pythonExecutable,
    [
      '-c',
      [
        'from importlib.metadata import version',
        'import ctranslate2, faster_whisper, pyannote.audio, torch, torchcodec, whisperx',
        "print(version('whisperx'))",
        'print(torch.__version__)',
        'print(ctranslate2.__version__)',
        'print(faster_whisper.__version__)',
      ].join('; '),
    ],
    {
      capture: true,
      env: runtimeEnvironment(options.env),
      timeout: 120_000,
    },
  ).trim();
  const lines = output.split(/\r?\n/u);
  if (lines[0] !== whisperxManifest.version) {
    throw new Error(`Versao WhisperX inesperada: ${lines[0] ?? 'ausente'}`);
  }
  return {
    whisperx: lines[0],
    torch: lines[1],
    ctranslate2: lines[2],
    fasterWhisper: lines[3],
  };
}

async function isCurrentDestination(lockSha256) {
  if (!(await exists(destinationPython))) return false;
  try {
    const metadata = JSON.parse(
      await readFile(path.join(destination, 'build-metadata.json'), 'utf8'),
    );
    if (
      metadata.pythonVersion !== pythonManifest.version ||
      metadata.whisperxVersion !== whisperxManifest.version ||
      metadata.runtimeLayoutVersion !== runtimeLayoutVersion ||
      metadata.lockSha256 !== lockSha256
    ) {
      return false;
    }
    const pythonVersion = run(destinationPython, ['--version'], {
      capture: true,
      env: runtimeEnvironment(),
      timeout: 30_000,
    }).trim();
    if (pythonVersion !== `Python ${pythonManifest.version}`) return false;
    versionProbe(destinationPython);
    return true;
  } catch {
    return false;
  }
}

async function validateMacNativeDependencies(stagedRoot, pythonExecutable) {
  if (!target.startsWith('darwin-')) {
    return { nativeFileCount: 0, externalLibraries: [] };
  }

  const architecture = target.endsWith('-arm64') ? 'arm64' : 'x86_64';
  const pythonFile = run('file', [pythonExecutable], { capture: true });
  if (!pythonFile.includes(architecture)) {
    throw new Error(`Python nao corresponde a arquitetura ${architecture}.`);
  }

  const nativeFiles = [pythonExecutable, ...(await collectNativeFiles(stagedRoot))];
  const externalLibraries = new Set();
  for (const nativeFile of nativeFiles) {
    const result = spawnSync('otool', ['-L', nativeFile], { encoding: 'utf8' });
    if (result.status !== 0) continue;
    const linkedEntries = (result.stdout ?? '')
      .split(/\r?\n/u)
      .slice(1)
      .map((line) => line.trim().split(/\s+/u)[0])
      .filter(Boolean);
    // For a dylib, the first otool entry is its install ID, not a library that
    // the loader must find. PyTorch's bundled libomp keeps its build-time ID,
    // while consumers correctly link it through @rpath/libomp.dylib.
    const dependencies = nativeFile.endsWith('.dylib')
      ? linkedEntries.slice(1)
      : linkedEntries;
    for (const dependency of dependencies) {
      if (dependency.startsWith('/opt/homebrew/') || dependency.startsWith('/usr/local/')) {
        throw new Error(`Dependencia nao portavel em ${nativeFile}: ${dependency}`);
      }
      if (dependency.startsWith('/')) externalLibraries.add(dependency);
    }
  }

  return {
    nativeFileCount: nativeFiles.length,
    externalLibraries: [...externalLibraries].sort(),
  };
}

if (!(await exists(uvExecutable))) {
  throw new Error('uv interno ausente. Execute `npm run stage:uv` primeiro.');
}
if (!(await exists(ffmpegExecutable))) {
  throw new Error('FFmpeg interno ausente. Execute `npm run build:ffmpeg` primeiro.');
}
if (!(await exists(sharedFfmpegMetadataPath))) {
  throw new Error(
    'FFmpeg compartilhado do TorchCodec ausente. Execute `npm run build:ffmpeg:torchcodec` primeiro.',
  );
}

const lockSha256 = await sha256(lockPath);
const pyprojectSha256 = await sha256(pyprojectPath);
if (await isCurrentDestination(lockSha256)) {
  console.log(
    `Python ${pythonManifest.version} + WhisperX ${whisperxManifest.version} ja estao preparados para ${target}.`,
  );
  process.exit(0);
}

const pythonKeyByTarget = {
  'darwin-arm64': `cpython-${pythonManifest.version}-macos-aarch64-none`,
  'darwin-x64': `cpython-${pythonManifest.version}-macos-x86_64-none`,
  'win32-x64': `cpython-${pythonManifest.version}-windows-x86_64-none`,
};
const pythonKey = pythonKeyByTarget[target];
const pythonDownloads = JSON.parse(
  run(
    uvExecutable,
    [
      'python',
      'list',
      pythonKey,
      '--all-versions',
      '--all-platforms',
      '--all-arches',
      '--only-downloads',
      '--show-urls',
      '--output-format',
      'json',
    ],
    { capture: true, env: runtimeEnvironment() },
  ),
);
const pythonDownload = pythonDownloads.find((candidate) => candidate.key === pythonKey);
if (!pythonDownload?.url) {
  throw new Error(`Distribuicao Python gerenciada pelo uv ausente: ${pythonKey}`);
}

console.log(`Preparando Python ${pythonManifest.version} gerenciado pelo uv...`);
run(
  uvExecutable,
  [
    'python',
    'install',
    pythonManifest.version,
    '--install-dir',
    pythonInstallDirectory,
    '--no-progress',
  ],
  { capture: true, env: runtimeEnvironment() },
);

const managedPython = run(
  uvExecutable,
  ['python', 'find', pythonManifest.version],
  { capture: true, env: runtimeEnvironment() },
).trim();
const managedPythonRoot = run(
  managedPython,
  ['-c', 'import sys; print(sys.prefix)'],
  { capture: true, env: runtimeEnvironment() },
).trim();
const relativePythonExecutable = path.relative(managedPythonRoot, managedPython);

run(
  uvExecutable,
  [
    'export',
    '--project',
    pythonProject,
    '--locked',
    '--no-dev',
    '--no-emit-project',
    '--format',
    'requirements-txt',
    '--output-file',
    exportedRequirements,
  ],
  { capture: true, env: runtimeEnvironment() },
);

const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-python-whisperx-'));
try {
  const stagedRoot = path.join(temporaryDirectory, 'python-whisperx');
  const stagedPythonRoot = path.join(stagedRoot, 'python');
  await mkdir(stagedRoot, { recursive: true });
  await cp(managedPythonRoot, stagedPythonRoot, {
    recursive: true,
    verbatimSymlinks: true,
  });
  if (!isWindows) {
    await makeSymlinksBundleRelative(
      stagedPythonRoot,
      managedPythonRoot,
      stagedPythonRoot,
    );
  }
  const stagedPython = path.join(stagedPythonRoot, relativePythonExecutable);
  if (!isWindows) await chmod(stagedPython, 0o755);

  const pythonVersion = run(stagedPython, ['--version'], {
    capture: true,
    env: runtimeEnvironment(),
  }).trim();
  if (pythonVersion !== `Python ${pythonManifest.version}`) {
    throw new Error(`Versao Python inesperada: ${pythonVersion}`);
  }

  const sitePackages = run(
    stagedPython,
    ['-c', "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
    { capture: true, env: runtimeEnvironment() },
  ).trim();
  const realSitePackages = await realpath(sitePackages);
  const realStagedPythonRoot = await realpath(stagedPythonRoot);
  if (!realSitePackages.startsWith(`${realStagedPythonRoot}${path.sep}`)) {
    throw new Error(`site-packages Python nao relocavel: ${sitePackages}`);
  }

  const sharedFfmpegMetadata = JSON.parse(
    await readFile(sharedFfmpegMetadataPath, 'utf8'),
  );
  if (sharedFfmpegMetadata.version !== whisperxManifest.sharedFfmpegVersion) {
    throw new Error('Versao inesperada do FFmpeg compartilhado do TorchCodec.');
  }
  const sharedLibraryEntries = await readdir(sharedFfmpegLibraryDirectory, {
    withFileTypes: true,
  });
  const sharedLibraries = sharedLibraryEntries.filter(
    (entry) =>
      (entry.isFile() || entry.isSymbolicLink()) && entry.name.endsWith('.dylib'),
  );
  if (sharedLibraries.length === 0) {
    throw new Error('Bibliotecas compartilhadas do FFmpeg 7 ausentes.');
  }
  for (const library of sharedLibraries) {
    const sourceLibrary = path.join(sharedFfmpegLibraryDirectory, library.name);
    const destinationLibrary = path.join(stagedPythonRoot, 'lib', library.name);
    if (library.isSymbolicLink()) {
      const sourceTarget = await readlink(sourceLibrary);
      await symlink(path.basename(sourceTarget), destinationLibrary);
    } else {
      await cp(sourceLibrary, destinationLibrary);
    }
  }

  if (!isWindows) await validateBundleSymlinks(stagedPythonRoot);

  console.log(`Instalando WhisperX ${whisperxManifest.version} pelo lockfile...`);
  run(
    uvExecutable,
    [
      '--no-config',
      '--no-progress',
      'pip',
      'install',
      '--python',
      stagedPython,
      '--target',
      sitePackages,
      '--requirements',
      exportedRequirements,
      '--require-hashes',
      '--link-mode',
      'copy',
    ],
    { env: runtimeEnvironment() },
  );

  const versions = versionProbe(stagedPython);

  const smokeDirectory = path.join(temporaryDirectory, 'smoke');
  await mkdir(smokeDirectory, { recursive: true });
  const smokeAudio = path.join(smokeDirectory, 'tone.wav');
  run(
    ffmpegExecutable,
    [
      '-hide_banner',
      '-loglevel',
      'error',
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=440:sample_rate=48000',
      '-t',
      '0.25',
      smokeAudio,
    ],
    { env: runtimeEnvironment() },
  );
  const audioSamples = run(
    stagedPython,
    [
      '-c',
      `from whisperx.audio import load_audio; print(load_audio(${JSON.stringify(smokeAudio)}).shape[0])`,
    ],
    { capture: true, env: runtimeEnvironment(), timeout: 120_000 },
  ).trim();
  if (audioSamples !== '4000') {
    throw new Error(`Smoke test de audio WhisperX inesperado: ${audioSamples}`);
  }

  const nativeValidation = await validateMacNativeDependencies(stagedRoot, stagedPython);
  const pythonLicense = path.join(stagedPythonRoot, 'lib', 'python3.12', 'LICENSE.txt');
  const whisperxLicense = await findFile(
    sitePackages,
    (filePath) =>
      /whisperx-[^/\\]+\.dist-info[/\\]licenses[/\\]LICENSE$/iu.test(filePath),
  );
  if (!(await exists(pythonLicense)) || !whisperxLicense) {
    throw new Error('Licencas Python ou WhisperX ausentes no runtime preparado.');
  }

  const packageInventory = JSON.parse(
    run(
      stagedPython,
      [
        '-c',
        [
          'import importlib.metadata as m, json',
          "print(json.dumps(sorted([{'name': d.metadata['Name'], 'version': d.version} for d in m.distributions()], key=lambda p: p['name'].lower())))",
        ].join('; '),
      ],
      { capture: true, env: runtimeEnvironment(), timeout: 120_000 },
    ),
  );

  await mkdir(path.join(stagedRoot, 'licenses'), { recursive: true });
  await cp(pythonLicense, path.join(stagedRoot, 'licenses', 'PYTHON-LICENSE.txt'));
  await cp(whisperxLicense, path.join(stagedRoot, 'licenses', 'WHISPERX-LICENSE'));
  await cp(lockPath, path.join(stagedRoot, 'uv.lock'));
  await cp(pyprojectPath, path.join(stagedRoot, 'pyproject.toml'));
  await mkdir(path.join(stagedRoot, 'licenses', 'ffmpeg-torchcodec'), {
    recursive: true,
  });
  await cp(
    path.join(sharedFfmpegPrefix, 'licenses'),
    path.join(stagedRoot, 'licenses', 'ffmpeg-torchcodec'),
    { recursive: true },
  );

  const stagedStats = await stat(stagedPython);
  await writeFile(
    path.join(stagedRoot, 'build-metadata.json'),
    `${JSON.stringify(
      {
        target,
        runtimeLayoutVersion,
        pythonVersion: pythonManifest.version,
        pythonDistribution: pythonManifest.distribution,
        pythonKey,
        pythonDownloadUrl: pythonDownload.url,
        pythonExecutableSha256: await sha256(stagedPython),
        pythonExecutableBytes: stagedStats.size,
        whisperxVersion: whisperxManifest.version,
        whisperxDistribution: whisperxManifest.distribution,
        modelPolicy: whisperxManifest.modelPolicy,
        sharedFfmpeg: {
          version: sharedFfmpegMetadata.version,
          license: sharedFfmpegMetadata.license,
          sourceSha256: sharedFfmpegMetadata.sourceSha256,
          signatureFingerprint: sharedFfmpegMetadata.signatureFingerprint,
          libraryCount: sharedLibraries.length,
        },
        lockSha256,
        pyprojectSha256,
        versions,
        packageCount: packageInventory.length,
        packages: packageInventory,
        licenses: {
          pythonSha256: await sha256(pythonLicense),
          whisperxSha256: await sha256(whisperxLicense),
          dependencyNotices: 'Preserved inside each package .dist-info directory',
        },
        smokeTests: {
          imports: true,
          bundledFfmpegAudioDecode: true,
          decodedSamples: Number(audioSamples),
        },
        nativeValidation,
        stagedAt: new Date().toISOString(),
      },
      null,
      2,
    )}\n`,
    'utf8',
  );

  await rm(destination, { recursive: true, force: true });
  await cp(stagedRoot, destination, { recursive: true, verbatimSymlinks: true });
  if (!isWindows) await chmod(destinationPython, 0o755);
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}

console.log(
  `Python ${pythonManifest.version} + WhisperX ${whisperxManifest.version} preparados em resources/runtimes/${target}/python-whisperx.`,
);
