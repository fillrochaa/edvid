import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import {
  access,
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const manifest = JSON.parse(
  await readFile(path.join(desktopRoot, 'resources/runtime-manifest.json'), 'utf8'),
);
const version = manifest.runtimes.uv.version;
const sourceCommit = manifest.runtimes.uv.sourceCommit;
const target = process.argv[2] ?? `${process.platform}-${process.arch}`;
const releaseBaseUrl = `https://releases.astral.sh/github/uv/releases/download/${version}`;
const sourceRepository = 'astral-sh/uv';

const artifactByTarget = {
  'darwin-arm64': 'uv-aarch64-apple-darwin.tar.gz',
  'darwin-x64': 'uv-x86_64-apple-darwin.tar.gz',
  'win32-x64': 'uv-x86_64-pc-windows-msvc.zip',
};

const artifactName = artifactByTarget[target];
if (!artifactName) throw new Error(`Target sem distribuicao uv configurada: ${target}`);

const archiveRoot = artifactName.replace(/\.tar\.gz$|\.zip$/u, '');
const executableSuffix = target.startsWith('win32-') ? '.exe' : '';
const cacheDirectory = path.join(desktopRoot, '.runtime-cache', 'uv', version);
const archivePath = path.join(cacheDirectory, artifactName);
const checksumPath = `${archivePath}.sha256`;
const licenseApachePath = path.join(cacheDirectory, 'LICENSE-APACHE');
const licenseMitPath = path.join(cacheDirectory, 'LICENSE-MIT');
const destination = path.join(desktopRoot, 'resources', 'runtimes', target, 'uv');

await mkdir(cacheDirectory, { recursive: true });

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function download(url, output) {
  const temporaryOutput = `${output}.part`;
  let lastError;

  for (let attempt = 1; attempt <= 3; attempt += 1) {
    await rm(temporaryOutput, { force: true });
    try {
      const response = await fetch(url, { redirect: 'follow' });
      if (!response.ok || !response.body) {
        throw new Error(`Download falhou (${response.status}): ${url}`);
      }
      await pipeline(Readable.fromWeb(response.body), createWriteStream(temporaryOutput));
      await rename(temporaryOutput, output);
      return;
    } catch (error) {
      lastError = error;
      if (attempt < 3) {
        console.warn(`Tentativa ${attempt} falhou; repetindo download...`);
        await new Promise((resolve) => setTimeout(resolve, attempt * 500));
      }
    }
  }

  await rm(temporaryOutput, { force: true });
  throw lastError;
}

async function downloadIfMissing(url, output, label) {
  if (await exists(output)) return;
  console.log(`Baixando ${label}...`);
  await download(url, output);
}

async function sha256(filePath) {
  const hash = createHash('sha256');
  await pipeline(createReadStream(filePath), hash);
  return hash.digest('hex');
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd,
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

function verifyAttestation() {
  const ghAvailable = spawnSync('gh', ['--version'], { stdio: 'ignore' }).status === 0;
  if (!ghAvailable) {
    throw new Error(
      'GitHub CLI ausente. Instale com `brew install gh` para verificar a attestation do uv.',
    );
  }
  run(
    'gh',
    [
      'attestation',
      'verify',
      archivePath,
      '--repo',
      sourceRepository,
      '--source-digest',
      sourceCommit,
    ],
    { capture: true },
  );
}

async function validateMacDependencies(executable) {
  if (!target.startsWith('darwin-')) return [];
  const output = run('otool', ['-L', executable], { capture: true });
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
    throw new Error(`Dependencias uv nao portaveis: ${invalid.join(', ')}`);
  }
  return dependencies;
}

await downloadIfMissing(`${releaseBaseUrl}/${artifactName}.sha256`, checksumPath, 'checksum uv');
const checksumText = await readFile(checksumPath, 'utf8');
const expectedHash = checksumText.match(/^[a-f0-9]{64}\b/iu)?.[0]?.toLowerCase();
if (!expectedHash) throw new Error(`Checksum oficial invalido para ${artifactName}.`);

let archiveHash = null;
if (await exists(archivePath)) archiveHash = await sha256(archivePath);
if (archiveHash !== expectedHash) {
  await rm(archivePath, { force: true });
  console.log(`Baixando uv ${version} para ${target}...`);
  await download(`${releaseBaseUrl}/${artifactName}`, archivePath);
  archiveHash = await sha256(archivePath);
}
if (archiveHash !== expectedHash) {
  await rm(archivePath, { force: true });
  throw new Error(`Checksum invalido para ${artifactName}.`);
}

verifyAttestation();

const licenseBaseUrl = `https://raw.githubusercontent.com/astral-sh/uv/${version}`;
await downloadIfMissing(`${licenseBaseUrl}/LICENSE-APACHE`, licenseApachePath, 'licenca Apache');
await downloadIfMissing(`${licenseBaseUrl}/LICENSE-MIT`, licenseMitPath, 'licenca MIT');
const apacheLicense = await readFile(licenseApachePath, 'utf8');
const mitLicense = await readFile(licenseMitPath, 'utf8');
if (!apacheLicense.includes('Apache License') || !mitLicense.startsWith('MIT License')) {
  throw new Error('Arquivos de licenca uv inesperados.');
}

const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-uv-'));
try {
  run('tar', ['-xf', archivePath, '-C', temporaryDirectory]);
  const extractedRoot = path.join(temporaryDirectory, archiveRoot);
  const extractedUv = path.join(extractedRoot, `uv${executableSuffix}`);
  const extractedUvx = path.join(extractedRoot, `uvx${executableSuffix}`);
  if (!(await exists(extractedUv)) || !(await exists(extractedUvx))) {
    throw new Error(`Executaveis uv ausentes em ${artifactName}.`);
  }

  if (!executableSuffix) {
    await chmod(extractedUv, 0o755);
    await chmod(extractedUvx, 0o755);
  }
  const uvVersion = run(extractedUv, ['--version'], { capture: true }).trim();
  const uvxVersion = run(extractedUvx, ['--version'], { capture: true }).trim();
  if (!uvVersion.startsWith(`uv ${version} `) || !uvxVersion.startsWith(`uvx ${version} `)) {
    throw new Error(`Versoes uv inesperadas: ${uvVersion}; ${uvxVersion}`);
  }
  const dynamicLibraries = await validateMacDependencies(extractedUv);

  await rm(destination, { recursive: true, force: true });
  await mkdir(path.join(destination, 'bin'), { recursive: true });
  await mkdir(path.join(destination, 'licenses'), { recursive: true });
  await cp(extractedUv, path.join(destination, 'bin', `uv${executableSuffix}`));
  await cp(extractedUvx, path.join(destination, 'bin', `uvx${executableSuffix}`));
  await cp(licenseApachePath, path.join(destination, 'licenses', 'LICENSE-APACHE'));
  await cp(licenseMitPath, path.join(destination, 'licenses', 'LICENSE-MIT'));
  if (!executableSuffix) {
    await chmod(path.join(destination, 'bin', 'uv'), 0o755);
    await chmod(path.join(destination, 'bin', 'uvx'), 0o755);
  }

  await writeFile(
    path.join(destination, 'build-metadata.json'),
    `${JSON.stringify(
      {
        target,
        version,
        distribution: manifest.runtimes.uv.distribution,
        artifactUrl: `${releaseBaseUrl}/${artifactName}`,
        checksumUrl: `${releaseBaseUrl}/${artifactName}.sha256`,
        archiveSha256: archiveHash,
        attestation: {
          verified: true,
          repository: sourceRepository,
          sourceCommit,
        },
        binaries: {
          uv: await sha256(path.join(destination, 'bin', `uv${executableSuffix}`)),
          uvx: await sha256(path.join(destination, 'bin', `uvx${executableSuffix}`)),
        },
        licenses: {
          apacheSha256: await sha256(licenseApachePath),
          mitSha256: await sha256(licenseMitPath),
        },
        dynamicLibraries,
        stagedAt: new Date().toISOString(),
      },
      null,
      2,
    )}\n`,
    'utf8',
  );
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}

console.log(`uv ${version} preparado em resources/runtimes/${target}/uv`);
