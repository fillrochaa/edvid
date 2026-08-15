import { availableParallelism } from 'node:os';
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import {
  access,
  cp,
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(
  await readFile(path.join(desktopRoot, 'resources/runtime-manifest.json'), 'utf8'),
);
const version = manifest.runtimes.whisperx.sharedFfmpegVersion;
const target = `${process.platform}-${process.arch}`;
const supportedTarget = 'darwin-arm64';
const deploymentTarget = '12.0';
const jobs = String(Math.min(8, availableParallelism()));
const cacheRoot = path.join(
  desktopRoot,
  '.runtime-cache',
  'ffmpeg-torchcodec',
  version,
);
const source = path.join(cacheRoot, 'source');
const sourceMetadataPath = path.join(cacheRoot, 'source-metadata.json');
const buildRoot = path.join(cacheRoot, 'build', target);
const buildDirectory = path.join(buildRoot, 'ffmpeg');
const prefix = path.join(buildRoot, 'install');
const metadataPath = path.join(prefix, 'build-metadata.json');

if (target !== supportedTarget) {
  throw new Error(
    `O FFmpeg compartilhado do TorchCodec esta implementado para ${supportedTarget}; atual: ${target}.`,
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

async function sha256(filePath) {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

function run(command, args, options = {}) {
  console.log(`\n> ${command} ${args.join(' ')}`);
  const result = spawnSync(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
    maxBuffer: 20 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout}\n${result.stderr}` : '';
    throw new Error(`${command} terminou com codigo ${result.status}.${detail}`);
  }
  return result.stdout ?? '';
}

const expectedLibraries = [
  'libavcodec.61.dylib',
  'libavdevice.61.dylib',
  'libavfilter.10.dylib',
  'libavformat.61.dylib',
  'libavutil.59.dylib',
  'libswresample.5.dylib',
  'libswscale.8.dylib',
];

async function validateLibraries(libraryDirectory) {
  const result = {};
  for (const libraryName of expectedLibraries) {
    const libraryPath = path.join(libraryDirectory, libraryName);
    if (!(await exists(libraryPath))) {
      throw new Error(`Biblioteca FFmpeg para TorchCodec ausente: ${libraryName}`);
    }
    const output = run('otool', ['-L', libraryPath], { capture: true });
    const linkedEntries = output
      .split(/\r?\n/u)
      .slice(1)
      .map((line) => line.trim().split(/\s+/u)[0])
      .filter(Boolean);
    const installId = linkedEntries[0];
    if (installId !== `@rpath/${libraryName}`) {
      throw new Error(`Install ID nao portavel em ${libraryName}: ${installId}`);
    }
    const dependencies = linkedEntries.slice(1);
    const invalid = dependencies.filter(
      (dependency) =>
        dependency.startsWith('/opt/homebrew/') ||
        dependency.startsWith('/usr/local/'),
    );
    if (invalid.length > 0) {
      throw new Error(`Dependencias nao portaveis em ${libraryName}: ${invalid.join(', ')}`);
    }
    result[libraryName] = {
      sha256: await sha256(libraryPath),
      dependencies,
    };
  }
  return result;
}

if (!(await exists(path.join(source, 'configure')))) {
  throw new Error('Fonte FFmpeg 7 ausente. Execute `npm run build:ffmpeg:torchcodec`.');
}
const sourceMetadata = JSON.parse(await readFile(sourceMetadataPath, 'utf8'));
if (!sourceMetadata.signatureVerified || sourceMetadata.version !== version) {
  throw new Error('O fonte FFmpeg 7 nao possui a assinatura GPG esperada.');
}

if (await exists(metadataPath)) {
  try {
    const metadata = JSON.parse(await readFile(metadataPath, 'utf8'));
    if (metadata.target === target && metadata.version === version) {
      await validateLibraries(path.join(prefix, 'lib'));
      console.log(`FFmpeg compartilhado ${version} ja esta preparado para o TorchCodec.`);
      process.exit(0);
    }
  } catch (error) {
    console.warn(`Build compartilhado existente sera refeito: ${error.message}`);
  }
}

await rm(buildRoot, { recursive: true, force: true });
await mkdir(buildDirectory, { recursive: true });

const buildEnvironment = {
  ...process.env,
  CC: '/usr/bin/clang',
  MACOSX_DEPLOYMENT_TARGET: deploymentTarget,
};
const configureFlags = [
  `--prefix=${prefix}`,
  '--cc=/usr/bin/clang',
  '--arch=arm64',
  '--target-os=darwin',
  '--disable-autodetect',
  '--disable-debug',
  '--disable-doc',
  '--disable-programs',
  '--disable-static',
  '--enable-shared',
  '--enable-pic',
  '--enable-pthreads',
  '--enable-zlib',
  '--enable-iconv',
  '--extra-libs=-liconv',
  '--disable-postproc',
  '--disable-network',
  '--install-name-dir=@rpath',
  `--extra-cflags=-mmacosx-version-min=${deploymentTarget}`,
  `--extra-ldflags=-mmacosx-version-min=${deploymentTarget}`,
];

run(path.join(source, 'configure'), configureFlags, {
  cwd: buildDirectory,
  env: buildEnvironment,
});
run('make', [`-j${jobs}`], { cwd: buildDirectory, env: buildEnvironment });
run('make', ['install'], { cwd: buildDirectory, env: buildEnvironment });

const libraries = await validateLibraries(path.join(prefix, 'lib'));
const licenseDirectory = path.join(prefix, 'licenses');
await mkdir(licenseDirectory, { recursive: true });
for (const license of ['LICENSE.md', 'COPYING.LGPLv2.1', 'COPYING.LGPLv3']) {
  await cp(path.join(source, license), path.join(licenseDirectory, license));
}

const installedFiles = (await readdir(path.join(prefix, 'lib')))
  .filter((name) => name.endsWith('.dylib'))
  .sort();
await writeFile(
  metadataPath,
  `${JSON.stringify(
    {
      target,
      version,
      purpose: 'TorchCodec shared-library ABI compatibility',
      license: 'LGPL-2.1-or-later',
      sourceUrl: sourceMetadata.archiveUrl,
      sourceSha256: sourceMetadata.sha256,
      signatureFingerprint: sourceMetadata.signingFingerprint,
      configureFlags,
      installedFiles,
      libraries,
      toolchain: run('/usr/bin/clang', ['--version'], { capture: true })
        .split(/\r?\n/u)[0],
      macosDeploymentTarget: deploymentTarget,
      builtAt: new Date().toISOString(),
    },
    null,
    2,
  )}\n`,
  'utf8',
);

console.log(`FFmpeg compartilhado ${version} preparado em ${prefix}.`);
