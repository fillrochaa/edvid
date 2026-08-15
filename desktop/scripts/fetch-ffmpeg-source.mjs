import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import {
  access,
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
const version = process.argv[2] ?? manifest.runtimes.ffmpeg.version;
const cacheNamespace = process.argv[3] ?? 'ffmpeg';
const expectedSigningFingerprint = 'FCF986EA15E6E293A5644F10B4322F04D67658D8';
const releaseBaseUrl = 'https://ffmpeg.org/releases';
const archiveName = `ffmpeg-${version}.tar.xz`;
const cacheDirectory = path.join(desktopRoot, '.runtime-cache', cacheNamespace, version);
const archivePath = path.join(cacheDirectory, archiveName);
const signaturePath = `${archivePath}.asc`;
const signingKeyPath = path.join(cacheDirectory, 'ffmpeg-devel.asc');
const sourcePath = path.join(cacheDirectory, 'source');
const metadataPath = path.join(cacheDirectory, 'source-metadata.json');

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

function runGpg(homeDirectory, args, options = {}) {
  return spawnSync('gpg', ['--homedir', homeDirectory, '--batch', ...args], {
    encoding: 'utf8',
    ...options,
  });
}

function isGpgAvailable() {
  const result = spawnSync('gpg', ['--version'], { stdio: 'ignore' });
  return result.status === 0;
}

async function verifySignature() {
  if (!isGpgAvailable()) return false;

  const gpgHome = await mkdtemp(path.join(tmpdir(), 'edvid-ffmpeg-gpg-'));
  try {
    const imported = runGpg(gpgHome, ['--import', signingKeyPath]);
    if (imported.status !== 0) {
      throw new Error(`Falha ao importar a chave do FFmpeg:\n${imported.stderr}`);
    }

    const listed = runGpg(gpgHome, [
      '--with-colons',
      '--fingerprint',
      expectedSigningFingerprint,
    ]);
    if (listed.status !== 0) {
      throw new Error(`Falha ao ler a chave do FFmpeg:\n${listed.stderr}`);
    }

    const fingerprints = listed.stdout
      .split(/\r?\n/u)
      .filter((line) => line.startsWith('fpr:'))
      .map((line) => line.split(':')[9]);
    if (!fingerprints.includes(expectedSigningFingerprint)) {
      throw new Error('A chave baixada nao corresponde ao fingerprint oficial do FFmpeg.');
    }

    const verified = runGpg(gpgHome, ['--verify', signaturePath, archivePath]);
    if (verified.status !== 0) {
      throw new Error(`Assinatura invalida para ${archiveName}:\n${verified.stderr}`);
    }
    return true;
  } finally {
    await rm(gpgHome, { recursive: true, force: true });
  }
}

async function extractVerifiedSource() {
  const temporaryDirectory = await mkdtemp(path.join(tmpdir(), 'edvid-ffmpeg-source-'));
  try {
    const extracted = spawnSync(
      'tar',
      ['-xf', archivePath, '-C', temporaryDirectory],
      { stdio: 'inherit' },
    );
    if (extracted.status !== 0) throw new Error(`Falha ao extrair ${archiveName}.`);

    const extractedPath = path.join(temporaryDirectory, `ffmpeg-${version}`);
    await rm(sourcePath, { recursive: true, force: true });
    await rename(extractedPath, sourcePath);
  } finally {
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
}

await downloadIfMissing(
  `${releaseBaseUrl}/${archiveName}`,
  archivePath,
  `FFmpeg ${version}`,
);
await downloadIfMissing(
  `${releaseBaseUrl}/${archiveName}.asc`,
  signaturePath,
  'assinatura oficial',
);
await downloadIfMissing(
  'https://ffmpeg.org/ffmpeg-devel.asc',
  signingKeyPath,
  'chave publica oficial',
);

const archiveSha256 = await sha256(archivePath);
const signatureVerified = await verifySignature();

if (signatureVerified) {
  await extractVerifiedSource();
  console.log(`Assinatura valida. Fonte extraido em ${sourcePath}`);
} else {
  console.warn(
    'GnuPG nao esta instalado. O arquivo foi baixado, mas nao sera extraido nem compilado antes da verificacao.',
  );
  console.warn('No macOS, instale com: brew install gnupg');
}

await writeFile(
  metadataPath,
  `${JSON.stringify(
    {
      version,
      archive: archiveName,
      archiveUrl: `${releaseBaseUrl}/${archiveName}`,
      signatureUrl: `${releaseBaseUrl}/${archiveName}.asc`,
      signingKeyUrl: 'https://ffmpeg.org/ffmpeg-devel.asc',
      signingFingerprint: expectedSigningFingerprint,
      sha256: archiveSha256,
      signatureVerified,
      verifiedAt: signatureVerified ? new Date().toISOString() : null,
    },
    null,
    2,
  )}\n`,
  'utf8',
);

console.log(`SHA-256: ${archiveSha256}`);
console.log(`Metadados: ${metadataPath}`);
