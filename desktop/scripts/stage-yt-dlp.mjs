import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import {
  access,
  chmod,
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
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
const runtimeManifest = manifest.runtimes['yt-dlp'];
const version = runtimeManifest.version;
const expectedFingerprint = runtimeManifest.signingFingerprint;
const target = process.argv[2] ?? `${process.platform}-${process.arch}`;
const releaseBaseUrl = `https://github.com/yt-dlp/yt-dlp/releases/download/${version}`;

const artifactByTarget = {
  'darwin-arm64': 'yt-dlp_macos',
  'darwin-x64': 'yt-dlp_macos',
  'win32-x64': 'yt-dlp.exe',
};

const artifactName = artifactByTarget[target];
if (!artifactName) throw new Error(`Target sem distribuicao yt-dlp configurada: ${target}`);

const isWindowsTarget = target.startsWith('win32-');
const outputName = isWindowsTarget ? 'yt-dlp.exe' : 'yt-dlp';
const sourceArchiveName = 'yt-dlp.tar.gz';
const cacheDirectory = path.join(desktopRoot, '.runtime-cache', 'yt-dlp', version);
const artifactPath = path.join(cacheDirectory, artifactName);
const checksumsPath = path.join(cacheDirectory, 'SHA2-256SUMS');
const signaturePath = path.join(cacheDirectory, 'SHA2-256SUMS.sig');
const publicKeyPath = path.join(cacheDirectory, 'public.key');
const sourceArchivePath = path.join(cacheDirectory, sourceArchiveName);
const thirdPartyLicensesPath = path.join(cacheDirectory, 'THIRD_PARTY_LICENSES.txt');
const destination = path.join(desktopRoot, 'resources', 'runtimes', target, 'yt-dlp');

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
    timeout: options.timeout,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    const detail = options.capture ? `\n${result.stdout}\n${result.stderr}` : '';
    throw new Error(`${command} terminou com codigo ${result.status}.${detail}`);
  }
  return result.stdout ?? '';
}

function checksumFor(checksums, filename) {
  for (const line of checksums.split(/\r?\n/u)) {
    const match = line.match(/^([a-f0-9]{64})\s+\*?(.+)$/iu);
    if (match?.[2] === filename) return match[1].toLowerCase();
  }
  throw new Error(`Checksum oficial ausente para ${filename}.`);
}

async function verifySignedChecksums() {
  const gpgAvailable = spawnSync('gpg', ['--version'], { stdio: 'ignore' }).status === 0;
  if (!gpgAvailable) {
    throw new Error('GnuPG ausente. Instale `gpg` para verificar a release do yt-dlp.');
  }

  const gpgHome = await mkdtemp(path.join(tmpdir(), 'edvid-yt-dlp-gpg-'));
  await chmod(gpgHome, 0o700);
  try {
    run('gpg', ['--batch', '--homedir', gpgHome, '--import', publicKeyPath], {
      capture: true,
    });
    const keyListing = run(
      'gpg',
      ['--batch', '--homedir', gpgHome, '--with-colons', '--fingerprint'],
      { capture: true },
    );
    const fingerprints = keyListing
      .split(/\r?\n/u)
      .filter((line) => line.startsWith('fpr:'))
      .map((line) => line.split(':')[9]?.toUpperCase())
      .filter(Boolean);
    if (!fingerprints.includes(expectedFingerprint)) {
      throw new Error(
        `Chave yt-dlp inesperada. Esperada ${expectedFingerprint}; recebidas ${fingerprints.join(', ')}.`,
      );
    }
    run(
      'gpg',
      ['--batch', '--homedir', gpgHome, '--verify', signaturePath, checksumsPath],
      { capture: true },
    );
  } finally {
    await rm(gpgHome, { recursive: true, force: true });
  }
}

async function ensureVerifiedDownload(filename, output, expectedHash, label) {
  let actualHash = (await exists(output)) ? await sha256(output) : null;
  if (actualHash !== expectedHash) {
    await rm(output, { force: true });
    console.log(`Baixando ${label}...`);
    await download(`${releaseBaseUrl}/${filename}`, output);
    actualHash = await sha256(output);
  }
  if (actualHash !== expectedHash) {
    await rm(output, { force: true });
    throw new Error(`Checksum invalido para ${filename}.`);
  }
  return actualHash;
}

async function ensurePinnedDownload(url, output, expectedHash, label) {
  let actualHash = (await exists(output)) ? await sha256(output) : null;
  if (actualHash !== expectedHash) {
    await rm(output, { force: true });
    console.log(`Baixando ${label}...`);
    await download(url, output);
    actualHash = await sha256(output);
  }
  if (actualHash !== expectedHash) {
    await rm(output, { force: true });
    throw new Error(`Checksum invalido para ${label}.`);
  }
  return actualHash;
}

function canExecuteTarget() {
  if (target.startsWith('darwin-')) return process.platform === 'darwin';
  return target === `${process.platform}-${process.arch}`;
}

async function validateMacBinary(executable) {
  if (!target.startsWith('darwin-')) return { architectures: [], dynamicLibraries: [] };

  const fileOutput = run('file', [executable], { capture: true });
  if (!fileOutput.includes('arm64') || !fileOutput.includes('x86_64')) {
    throw new Error('O binario yt-dlp para macOS nao e universal (arm64 + x86_64).');
  }

  const libraryOutput = run('otool', ['-L', executable], { capture: true });
  const dynamicLibraries = libraryOutput
    .split(/\r?\n/u)
    .filter((line) => /^\s+\//u.test(line))
    .map((line) => line.trim().split(/\s+/u)[0]);
  const invalid = dynamicLibraries.filter(
    (dependency) =>
      !dependency.startsWith('/usr/lib/') &&
      !dependency.startsWith('/System/Library/'),
  );
  if (invalid.length > 0) {
    throw new Error(`Dependencias yt-dlp nao portaveis: ${invalid.join(', ')}`);
  }

  return { architectures: ['arm64', 'x86_64'], dynamicLibraries };
}

await downloadIfMissing(`${releaseBaseUrl}/SHA2-256SUMS`, checksumsPath, 'checksums yt-dlp');
await downloadIfMissing(
  `${releaseBaseUrl}/SHA2-256SUMS.sig`,
  signaturePath,
  'assinatura dos checksums yt-dlp',
);
await downloadIfMissing(
  `https://raw.githubusercontent.com/yt-dlp/yt-dlp/${version}/public.key`,
  publicKeyPath,
  'chave publica yt-dlp',
);

await verifySignedChecksums();

const checksums = await readFile(checksumsPath, 'utf8');
const artifactExpectedHash = checksumFor(checksums, artifactName);
const sourceExpectedHash = checksumFor(checksums, sourceArchiveName);
const artifactHash = await ensureVerifiedDownload(
  artifactName,
  artifactPath,
  artifactExpectedHash,
  `yt-dlp ${version} para ${target}`,
);
const sourceArchiveHash = await ensureVerifiedDownload(
  sourceArchiveName,
  sourceArchivePath,
  sourceExpectedHash,
  'fonte e licencas yt-dlp',
);
const thirdPartyLicensesUrl =
  `https://raw.githubusercontent.com/yt-dlp/yt-dlp/${runtimeManifest.sourceCommit}` +
  '/THIRD_PARTY_LICENSES.txt';
const thirdPartyLicensesHash = await ensurePinnedDownload(
  thirdPartyLicensesUrl,
  thirdPartyLicensesPath,
  runtimeManifest.thirdPartyLicensesSha256,
  'licencas de terceiros yt-dlp',
);

const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-yt-dlp-'));
try {
  run('tar', ['-xf', sourceArchivePath, '-C', temporaryDirectory]);
  const entries = await readdir(temporaryDirectory, { withFileTypes: true });
  const sourceRootEntry = entries.find((entry) => entry.isDirectory());
  if (!sourceRootEntry) throw new Error('Diretorio fonte ausente no arquivo yt-dlp.');
  const sourceRoot = path.join(temporaryDirectory, sourceRootEntry.name);
  const licensePath = path.join(sourceRoot, 'LICENSE');
  const licenseText = await readFile(licensePath, 'utf8');
  const thirdPartyText = await readFile(thirdPartyLicensesPath, 'utf8');
  if (!licenseText.includes('public domain') || !thirdPartyText.includes('PyInstaller')) {
    throw new Error('Arquivos de licenca yt-dlp inesperados.');
  }

  const stagingDirectory = path.join(temporaryDirectory, 'staged-runtime');
  await mkdir(stagingDirectory, { recursive: true });
  const stagedExecutable = path.join(stagingDirectory, outputName);
  await cp(artifactPath, stagedExecutable);
  if (!isWindowsTarget) await chmod(stagedExecutable, 0o755);

  if (canExecuteTarget()) {
    const reportedVersion = run(stagedExecutable, ['--version'], {
      capture: true,
      timeout: 30_000,
    }).trim();
    if (reportedVersion !== version) {
      throw new Error(`Versao yt-dlp inesperada: ${reportedVersion}`);
    }

    const extractors = run(stagedExecutable, ['--list-extractors'], {
      capture: true,
      timeout: 60_000,
    });
    for (const extractor of ['youtube', 'Instagram', 'TikTok']) {
      if (!extractors.split(/\r?\n/u).includes(extractor)) {
        throw new Error(`Extrator essencial ausente no yt-dlp: ${extractor}`);
      }
    }
  }

  const macValidation = await validateMacBinary(stagedExecutable);

  await rm(destination, { recursive: true, force: true });
  await mkdir(path.join(destination, 'bin'), { recursive: true });
  await mkdir(path.join(destination, 'licenses'), { recursive: true });
  await cp(stagedExecutable, path.join(destination, 'bin', outputName));
  await cp(licensePath, path.join(destination, 'licenses', 'LICENSE'));
  await cp(
    thirdPartyLicensesPath,
    path.join(destination, 'licenses', 'THIRD_PARTY_LICENSES.txt'),
  );
  if (!isWindowsTarget) await chmod(path.join(destination, 'bin', outputName), 0o755);

  await writeFile(
    path.join(destination, 'build-metadata.json'),
    `${JSON.stringify(
      {
        target,
        version,
        distribution: runtimeManifest.distribution,
        sourceCommit: runtimeManifest.sourceCommit,
        artifactUrl: `${releaseBaseUrl}/${artifactName}`,
        artifactSha256: artifactHash,
        sourceArchiveUrl: `${releaseBaseUrl}/${sourceArchiveName}`,
        sourceArchiveSha256: sourceArchiveHash,
        signedChecksums: {
          verified: true,
          url: `${releaseBaseUrl}/SHA2-256SUMS`,
          signatureUrl: `${releaseBaseUrl}/SHA2-256SUMS.sig`,
          signingFingerprint: expectedFingerprint,
        },
        binarySha256: await sha256(path.join(destination, 'bin', outputName)),
        licenses: {
          projectSha256: await sha256(licensePath),
          thirdPartyUrl: thirdPartyLicensesUrl,
          thirdPartySha256: thirdPartyLicensesHash,
          bundledExecutableLicense: 'GPL-3.0-or-later',
        },
        architectures: macValidation.architectures,
        dynamicLibraries: macValidation.dynamicLibraries,
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

console.log(`yt-dlp ${version} preparado em resources/runtimes/${target}/yt-dlp`);
