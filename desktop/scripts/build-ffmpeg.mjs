import { availableParallelism } from 'node:os';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  access,
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  rm,
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
const ffmpegVersion = manifest.runtimes.ffmpeg.version;
const target = `${process.platform}-${process.arch}`;
const supportedTarget = 'darwin-arm64';
const deploymentTarget = '12.0';
const jobs = String(Math.min(8, availableParallelism()));

const x264Repository = 'https://code.videolan.org/videolan/x264.git';
const x264Commit = '0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee';

const cacheRoot = path.join(desktopRoot, '.runtime-cache');
const ffmpegCache = path.join(cacheRoot, 'ffmpeg', ffmpegVersion);
const ffmpegSource = path.join(ffmpegCache, 'source');
const sourceMetadataPath = path.join(ffmpegCache, 'source-metadata.json');
const x264Cache = path.join(cacheRoot, 'x264', x264Commit);
const x264Source = path.join(x264Cache, 'source');
const buildRoot = path.join(ffmpegCache, 'build', target);
const x264Build = path.join(buildRoot, 'x264');
const x264Prefix = path.join(buildRoot, 'x264-install');
const ffmpegBuild = path.join(buildRoot, 'ffmpeg');
const ffmpegPrefix = path.join(buildRoot, 'ffmpeg-install');
const runtimeDestination = path.join(
  desktopRoot,
  'resources',
  'runtimes',
  target,
  'ffmpeg',
);

if (target !== supportedTarget) {
  throw new Error(
    `O build FFmpeg esta implementado para ${supportedTarget}; target atual: ${target}.`,
  );
}

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

function run(command, args, options = {}) {
  console.log(`\n> ${command} ${args.join(' ')}`);
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout}\n${result.stderr}` : '';
    throw new Error(`${command} terminou com codigo ${result.status}.${detail}`);
  }
  return result.stdout ?? '';
}

async function sha256(filePath) {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

async function prepareX264Source() {
  if (!(await exists(path.join(x264Source, '.git')))) {
    await rm(x264Source, { recursive: true, force: true });
    await mkdir(x264Cache, { recursive: true });
    run('git', ['clone', '--filter=blob:none', '--no-checkout', x264Repository, x264Source]);
    run('git', ['-C', x264Source, 'fetch', '--depth=1', 'origin', x264Commit]);
    run('git', ['-C', x264Source, 'checkout', '--detach', x264Commit]);
  }

  const resolvedCommit = run(
    'git',
    ['-C', x264Source, 'rev-parse', 'HEAD'],
    { capture: true },
  ).trim();
  if (resolvedCommit !== x264Commit) {
    throw new Error(`Revisao x264 inesperada: ${resolvedCommit}`);
  }
}

async function validateDynamicLibraries(binaryPath) {
  const output = run('otool', ['-L', binaryPath], { capture: true });
  const dependencies = output
    .split(/\r?\n/u)
    .slice(1)
    .map((line) => line.trim().split(/\s+/u)[0])
    .filter(Boolean);
  const invalid = dependencies.filter(
    (dependency) =>
      !dependency.startsWith('/usr/lib/') &&
      !dependency.startsWith('/System/Library/'),
  );
  if (invalid.length > 0) {
    throw new Error(
      `Dependencias nao portaveis em ${path.basename(binaryPath)}: ${invalid.join(', ')}`,
    );
  }
  return dependencies;
}

async function validateBuild(ffmpegPath, ffprobePath) {
  const versionOutput = run(ffmpegPath, ['-hide_banner', '-version'], { capture: true });
  if (!versionOutput.startsWith(`ffmpeg version ${ffmpegVersion}`)) {
    throw new Error(`Versao FFmpeg inesperada:\n${versionOutput}`);
  }
  if (!versionOutput.includes('--enable-gpl') || !versionOutput.includes('--enable-libx264')) {
    throw new Error('O FFmpeg nao registra GPL e libx264 na configuracao.');
  }

  const encoders = run(ffmpegPath, ['-hide_banner', '-encoders'], { capture: true });
  if (!/\blibx264\b/u.test(encoders)) throw new Error('Encoder libx264 ausente.');

  const filters = run(ffmpegPath, ['-hide_banner', '-filters'], { capture: true });
  if (!/\bdeesser\b/u.test(filters)) throw new Error('Filtro deesser ausente.');

  const probeVersion = run(ffprobePath, ['-hide_banner', '-version'], { capture: true });
  if (!probeVersion.startsWith(`ffprobe version ${ffmpegVersion}`)) {
    throw new Error(`Versao FFprobe inesperada:\n${probeVersion}`);
  }

  const smokeDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-ffmpeg-smoke-'));
  try {
    const smokeVideo = path.join(smokeDirectory, 'smoke.mp4');
    run(ffmpegPath, [
      '-hide_banner',
      '-loglevel',
      'error',
      '-f',
      'lavfi',
      '-i',
      'testsrc2=size=320x180:rate=30',
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=1000:sample_rate=48000',
      '-t',
      '1',
      '-af',
      'deesser=i=0.35',
      '-c:v',
      'libx264',
      '-preset',
      'fast',
      '-crf',
      '20',
      '-pix_fmt',
      'yuv420p',
      '-c:a',
      'aac',
      '-shortest',
      smokeVideo,
    ]);
    const probe = run(
      ffprobePath,
      ['-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', smokeVideo],
      { capture: true },
    );
    if (!probe.includes('h264') || !probe.includes('aac')) {
      throw new Error(`Smoke test produziu codecs inesperados:\n${probe}`);
    }
  } finally {
    await rm(smokeDirectory, { recursive: true, force: true });
  }

  return {
    ffmpeg: await validateDynamicLibraries(ffmpegPath),
    ffprobe: await validateDynamicLibraries(ffprobePath),
  };
}

const sourceMetadata = JSON.parse(await readFile(sourceMetadataPath, 'utf8'));
if (!sourceMetadata.signatureVerified) {
  throw new Error('O fonte do FFmpeg ainda nao possui assinatura GPG validada.');
}
if (!(await exists(path.join(ffmpegSource, 'configure')))) {
  throw new Error('Fonte FFmpeg ausente. Execute npm run fetch:ffmpeg primeiro.');
}

const existingMetadataPath = path.join(runtimeDestination, 'build-metadata.json');
const existingFfmpeg = path.join(runtimeDestination, 'bin', 'ffmpeg');
const existingFfprobe = path.join(runtimeDestination, 'bin', 'ffprobe');
if (
  (await exists(existingMetadataPath)) &&
  (await exists(existingFfmpeg)) &&
  (await exists(existingFfprobe))
) {
  try {
    const existingMetadata = JSON.parse(await readFile(existingMetadataPath, 'utf8'));
    if (
      existingMetadata.target === target &&
      existingMetadata.ffmpeg?.version === ffmpegVersion &&
      existingMetadata.x264?.commit === x264Commit
    ) {
      await validateBuild(existingFfmpeg, existingFfprobe);
      console.log(`FFmpeg ${ffmpegVersion} ja esta preparado e validado para ${target}.`);
      process.exit(0);
    }
  } catch (error) {
    console.warn(`Runtime existente sera recompilado: ${error.message}`);
  }
}

await prepareX264Source();
await rm(buildRoot, { recursive: true, force: true });
await mkdir(buildRoot, { recursive: true });
await cp(x264Source, x264Build, {
  recursive: true,
  filter: (source) => path.basename(source) !== '.git',
});

const commonEnvironment = {
  ...process.env,
  CC: '/usr/bin/clang',
  MACOSX_DEPLOYMENT_TARGET: deploymentTarget,
};

run(
  './configure',
  [
    `--prefix=${x264Prefix}`,
    '--enable-static',
    '--disable-cli',
    '--disable-opencl',
    '--enable-pic',
  ],
  { cwd: x264Build, env: commonEnvironment },
);
run('make', [`-j${jobs}`], { cwd: x264Build, env: commonEnvironment });
run('make', ['install'], { cwd: x264Build, env: commonEnvironment });

await mkdir(ffmpegBuild, { recursive: true });
const ffmpegEnvironment = {
  ...commonEnvironment,
  PKG_CONFIG_PATH: path.join(x264Prefix, 'lib', 'pkgconfig'),
  PKG_CONFIG_LIBDIR: path.join(x264Prefix, 'lib', 'pkgconfig'),
};
const configureFlags = [
  `--prefix=${ffmpegPrefix}`,
  '--cc=/usr/bin/clang',
  '--arch=arm64',
  '--target-os=darwin',
  '--disable-autodetect',
  '--disable-debug',
  '--disable-doc',
  '--disable-ffplay',
  '--disable-shared',
  '--enable-static',
  '--enable-pic',
  '--enable-gpl',
  '--enable-libx264',
  '--enable-pthreads',
  '--enable-zlib',
  '--enable-bzlib',
  '--enable-iconv',
  '--enable-securetransport',
  '--enable-audiotoolbox',
  '--enable-videotoolbox',
  '--pkg-config-flags=--static',
  '--extra-libs=-liconv',
  `--extra-cflags=-I${path.join(x264Prefix, 'include')} -mmacosx-version-min=${deploymentTarget}`,
  `--extra-ldflags=-L${path.join(x264Prefix, 'lib')} -mmacosx-version-min=${deploymentTarget}`,
];
run(path.join(ffmpegSource, 'configure'), configureFlags, {
  cwd: ffmpegBuild,
  env: ffmpegEnvironment,
});
run('make', [`-j${jobs}`], { cwd: ffmpegBuild, env: ffmpegEnvironment });
run('make', ['install'], { cwd: ffmpegBuild, env: ffmpegEnvironment });

const builtFfmpeg = path.join(ffmpegPrefix, 'bin', 'ffmpeg');
const builtFfprobe = path.join(ffmpegPrefix, 'bin', 'ffprobe');
const dynamicLibraries = await validateBuild(builtFfmpeg, builtFfprobe);

await rm(runtimeDestination, { recursive: true, force: true });
await mkdir(path.join(runtimeDestination, 'bin'), { recursive: true });
await cp(builtFfmpeg, path.join(runtimeDestination, 'bin', 'ffmpeg'));
await cp(builtFfprobe, path.join(runtimeDestination, 'bin', 'ffprobe'));
await chmod(path.join(runtimeDestination, 'bin', 'ffmpeg'), 0o755);
await chmod(path.join(runtimeDestination, 'bin', 'ffprobe'), 0o755);

const licenseDestination = path.join(runtimeDestination, 'licenses');
await mkdir(path.join(licenseDestination, 'ffmpeg'), { recursive: true });
await mkdir(path.join(licenseDestination, 'x264'), { recursive: true });
for (const license of ['LICENSE.md', 'COPYING.GPLv2', 'COPYING.GPLv3']) {
  await cp(
    path.join(ffmpegSource, license),
    path.join(licenseDestination, 'ffmpeg', license),
  );
}
await cp(
  path.join(x264Source, 'COPYING'),
  path.join(licenseDestination, 'x264', 'COPYING'),
);

const installedFfmpeg = path.join(runtimeDestination, 'bin', 'ffmpeg');
const installedFfprobe = path.join(runtimeDestination, 'bin', 'ffprobe');
await writeFile(
  path.join(runtimeDestination, 'build-metadata.json'),
  `${JSON.stringify(
    {
      target,
      license: 'GPL-2.0-or-later',
      ffmpeg: {
        version: ffmpegVersion,
        sourceUrl: sourceMetadata.archiveUrl,
        sourceSha256: sourceMetadata.sha256,
        signatureFingerprint: sourceMetadata.signingFingerprint,
        configureFlags,
        binarySha256: await sha256(installedFfmpeg),
      },
      ffprobe: {
        version: ffmpegVersion,
        binarySha256: await sha256(installedFfprobe),
      },
      x264: {
        repository: x264Repository,
        commit: x264Commit,
      },
      toolchain: run('/usr/bin/clang', ['--version'], { capture: true })
        .split(/\r?\n/u)[0],
      macosDeploymentTarget: deploymentTarget,
      dynamicLibraries,
      builtAt: new Date().toISOString(),
    },
    null,
    2,
  )}\n`,
  'utf8',
);

console.log(`\nFFmpeg ${ffmpegVersion} preparado em ${runtimeDestination}`);
